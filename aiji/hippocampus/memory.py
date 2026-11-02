"""对外门面：把三层存储、异步写、快召回、识别、注入、反馈接起来，并按平台身份隔离。

闭环（全部不阻塞主链路）：

    互动  m.turn/m.ingest ──> 沉淀（事实/知识/关系/画像/风格/摘要，全异步）
    注入  m.inject(query) ──> 带来源标注的软引导文本块（100ms 预算内）
    反馈  m.feedback(...) ──> 命中计数 + 注入留痕，下轮排序更准

    m = Memory.open()
    u = ("wechat", "u_123")                       # 身份 = (platform, platform_user_id)
    m.turn(u, "s1", "user", "我叫李雷，住在杭州", scope="work")
    m.ingest(u, kind="image", ref="msg-7781", url="https://…", session="s1")
    m.recall("我住哪", u, scope="work")            # 100ms 内返回
    m.inject("我住哪", u, session="s1")            # 给主模型的软引导块
    m.feedback(u, inj.injection_id, [12], helpful=True)
"""

from __future__ import annotations

import sys
import time
import threading
from pathlib import Path
from typing import Any, Callable

from aiji.brain import selfhood
from aiji.hippocampus import store
from aiji.right_hand import params
from aiji.endocrine.being import BeingStore
from aiji.left_hand.actions import ActionExecutor, ApprovalManager
from aiji.throat.bridge.onebot import OneBotBridge, OneBotClient
from aiji.right_hand.config import Settings
from aiji.hippocampus.db import DB
from aiji.brain.debounce import ConversationDebouncer
from aiji.heart.dispatch import Dispatcher
from aiji.heart.heart import Heart, OutboxEvent, PRI_MENTION, SendThrottled
from aiji.brain.humanize import HumanPacer
from aiji.endocrine.life import load_life_card, mood_label, save_life_card
from aiji.hippocampus.inject import Injection, Injector
from aiji.brain.pool import ModelPool
from aiji.endocrine.presence import PresenceHub
from aiji.brain.quota import CreditProvider, QuotaState, record_send, sends_last_24h
from aiji.hippocampus.recall import RecallResult, Recaller
from aiji.hippocampus.recognize import Item, Recognizers
from aiji.hippocampus.reflect import ReflectionWorker, Reflective
from aiji.hippocampus.store import Ident
from aiji.hippocampus.vectors import BruteForceIndex
from aiji.hippocampus.writer import Writer
from aiji.skeleton.paths import TOOLS


def _bridge_keys() -> None:
    """refs → 环境变量（tools/load_keys.bridge）。失败静默：只影响模型可用性。"""
    sys.path.insert(0, str(TOOLS))
    from load_keys import bridge  # type: ignore
    bridge()


class Memory:
    def __init__(
        self,
        settings: Settings,
        *,
        ocr: Callable[[Item], Any] | None = None,
        asr: Callable[[Item], Any] | None = None,
        fetcher: Callable[[str], str] | None = None,
        media_refresher: Callable[[Ident, str], tuple[str, float] | None] | None = None,
        engines: str = "auto",  # auto=缺位时接真实引擎；none=显式不用任何引擎
    ):
        self.settings = settings
        self.media_refresher = media_refresher
        self.started_at = time.time()
        # 风格：把 Settings.tics 注入抽取器的扩展口癖词表（词表持久化到配置）
        from aiji.hippocampus.extract import set_extra_tics
        set_extra_tics(settings.tics)
        # 识别：whisper 回退模型大小
        if settings.asr_model:
            from aiji.hippocampus.real_engines import set_asr_model
            set_asr_model(settings.asr_model)
        self.db = DB(settings.db_path)
        self.db.conn()  # 建表 / 迁移
        index = None
        if settings.vector == "hash":
            index = BruteForceIndex()
        elif settings.vector == "hybrid":
            # hybrid：hashing 打底 + LLM 软嵌入（chat 模型打语义维度分）。
            # pool 在下面才建，先用延迟绑定：chat 包装在 pool 就绪后可用。
            from aiji.hippocampus.vectors import HybridIndex
            index = HybridIndex(chat=None)  # 先纯 hashing，pool 就绪后挂 chat
            self._hybrid = index
        else:
            self._hybrid = None
        self._index_built = 0
        self.recaller = Recaller(
            self.db,
            budget_ms=settings.budget_ms,
            top_k=settings.top_k,
            recent=settings.recent_turns,
            index=index,
            rank_mode=getattr(settings, "rank_mode", "weighted"),
        )
        self.pool = ModelPool(settings.routes)
        if getattr(self, "_hybrid", None) is not None and self.pool._states:
            # Attach semantic chat before rebuilding so existing facts get LLM dims.
            self._hybrid._chat = self._hybrid_chat
        if index is not None:
            self._rebuild_index(index)
        self.presence = PresenceHub()
        self._fallback_lock = threading.Lock()
        self._fallback_at: dict[tuple[str, str, str, str], float] = {}
        self.action_approvals = ApprovalManager(on_request=self._on_approval)
        action_root = Path(settings.action_root) if settings.action_root else Path.cwd()
        self.action_executor = ActionExecutor(
            action_root,
            approvals=self.action_approvals,
            approval_timeout_s=settings.action_approval_timeout_s,
        )
        self.debouncer = ConversationDebouncer(settings.debounce_ms)
        self.human_pacer = HumanPacer(
            min_ms=settings.humanize_min_ms,
            max_ms=settings.humanize_max_ms,
            typing_min_ms=settings.typing_min_ms,
            typing_max_ms=settings.typing_max_ms,
        )
        onebot_client = OneBotClient(
            settings.onebot_api or "http://127.0.0.1:3000",
            token=settings.onebot_token,
        )
        self.onebot_client = onebot_client
        self._qq_rate_lock = threading.Lock()
        self._qq_sent_at: dict[str, list[float]] = {}
        self.heart = Heart(
            self.db,
            handler=self._heart_reply,
            sender=self._heart_send,
        )
        self.dispatcher = Dispatcher(self.heart).start()
        # 主动配额来自反代积分（2026-11-02）：取不到就按每日硬上限走，见 aiji/quota.py
        routes = tuple(getattr(settings, "routes", ()) or ())
        models_url = str(getattr(settings, "credits_models_url", "") or "")
        model_id = str(getattr(settings, "credits_model", "") or "")
        if not models_url and routes:
            # 默认从第一条路由的网关读模型单价（实测 agent2api 的 /v1/models 带 credits 字段）
            base = str(getattr(routes[0], "base_url", "") or "").rstrip("/")
            models_url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
            model_id = model_id or str(getattr(routes[0], "model", "") or "")
        self.credits = CreditProvider(
            url=str(getattr(settings, "credits_url", "") or ""),
            json_path=str(getattr(settings, "credits_json_path", "") or ""),
            static=getattr(settings, "credits_static", None),
            models_url=models_url,
            model=model_id,
            file_path=str(Path(settings.db_path).parent / "credits.json"),
            reserve=float(getattr(settings, "credits_reserve", 0.2)),
        )
        self.onebot = OneBotBridge(
            reply_fn=self._onebot_queue_reply,
            sender=onebot_client.send,
            ingest_fn=self._onebot_ingest,
            group_mode=settings.onebot_group_mode,
            configured=bool(settings.onebot_api),
        ).start()
        self.recognizers = Recognizers(
            ocr=ocr, asr=asr, fetcher=fetcher,
            timeout_s=settings.recognize_timeout_s, min_confidence=settings.min_confidence,
        )
        # 真实引擎自动接线（2026-10-29）：调用方没显式注入且包已安装时，
        # 懒加载 RapidOCR / faster-whisper（首调用才 load 模型，不拖慢启动）。
        # 引擎缺失/加载失败如实退回 unsupported（recognize.py 的既有语义）。
        if engines == "auto" and (self.recognizers.ocr is None or self.recognizers.asr is None):
            try:
                from aiji.hippocampus import real_engines as _re
                if self.recognizers.ocr is None and _re.HAS_OCR:
                    def _ocr_item(item):
                        data_list = item.extra.get("bytes_list")
                        if data_list:
                            return _re.ocr_images(list(data_list))
                        data = item.extra.get("bytes")
                        if not data and item.url:
                            from aiji.hippocampus.media_audio import media_bytes_from_url
                            data = media_bytes_from_url(
                                item.url,
                                max_bytes=12 * 1024 * 1024,
                            )
                        if not data:
                            return ("", 0.0)
                        return _re.ocr_image(data)
                    self.recognizers.ocr = _ocr_item
                if self.recognizers.asr is None and _re.HAS_ASR:
                    def _asr_item(item):
                        data_list = item.extra.get("bytes_list")
                        if data_list:
                            return _re.transcribe_wavs(list(data_list))
                        data = item.extra.get("bytes")
                        if data:
                            return _re.transcribe_wav_bytes(data)
                        if item.url:
                            from aiji.hippocampus.media_audio import transcribe_item_audio
                            return transcribe_item_audio(
                                item,
                                transcribe=_re.transcribe_wav_bytes,
                            )
                        return ("", 0.0)
                    self.recognizers.asr = _asr_item
            except Exception:  # noqa: BLE001 - 引擎接线失败不阻塞 Memory
                pass
        # 自我层：物理底线在 selfhood（常量），性格由互动 + 后台反思涌现。
        self.reflective = Reflective(self.db, self.pool)
        self.reflection_worker = ReflectionWorker(
            self.reflective, interval_s=settings.reflection_interval_s,
        ).start()
        self.writer = Writer(self.db, self.pool, self.recaller, settings, self.recognizers).start()
        self.injector = Injector(
            self.db, self.recaller,
            max_chars=settings.inject_chars, max_items=settings.inject_items,
            min_rel=float(getattr(settings, "inject_min_rel", 0.0)),
        )
        self.being = BeingStore(
            self.db, persona=str(getattr(settings, "persona", "sunba")))

    def _hybrid_chat(self, messages: list[dict]) -> str:
        """HybridIndex 的软嵌入调用：走池子，取首条文本；失败抛给上层兜底。"""
        res = self.pool.chat(messages, timeout_s=15.0)
        return res.text

    def _on_approval(self, request: dict[str, Any]) -> None:
        self.presence.publish({
            "state": "approval_required",
            "mood": "警觉",
            "approval": request,
            "preview": request.get("summary", "需要批准"),
        })

    def _onebot_reply(self, event: dict[str, Any]) -> str:
        from aiji.brain.head import Head
        # 空文本不该进模型（Head 会抛 user_message_required）；正常路径上游已守卫，
        # 这里再兜一层，避免异常事件把 outbox 拖进重试/死信。
        content = str(event.get("content") or "").strip()
        if not content:
            return ""
        target = Ident(
            "qq", str(event.get("platform_user_id") or event.get("user_id") or "unknown"),
            scope=str(event.get("scope") or "default"),
        )
        reply = Head(self).reply(
            [{"role": "user", "content": content}],
            target,
            session=str(event.get("session") or "default"),
            system_suffix="这是 QQ 聊天，回复尽量简短，避免刷屏。",
        )
        return reply.text

    def _heart_reply(self, ident: Ident, event: OutboxEvent) -> str | None:
        if event.kind == "proactive":
            return event.text
        payload = dict(event.extra)
        payload.setdefault("platform_user_id", ident.platform_user_id)
        payload.setdefault("scope", str(ident.scope or "default"))
        payload["content"] = event.text
        return self._onebot_reply(payload)

    def _heart_send(
        self,
        ident: Ident,
        text: str,
        payload: dict[str, Any],
    ) -> bool:
        scope = str(ident.scope or "default")
        group = scope.startswith("group:")
        if group:
            now = time.time()
            with self._qq_rate_lock:
                recent = [
                    ts for ts in self._qq_sent_at.get(scope, [])
                    if now - ts < 60.0
                ]
                if len(recent) >= 4:
                    self._qq_sent_at[scope] = recent
                    raise SendThrottled(recent[0] + 60.5)
                recent.append(now)
                self._qq_sent_at[scope] = recent
        event = {
            "message_type": "group" if group else "private",
            "group_id": scope.split(":", 1)[1] if group else None,
            "user_id": ident.platform_user_id,
        }
        # 2026-11-03：走结构化发送结果，按"重试/限流/致命"分类处理（见 aiji/heart/sender.py）
        from aiji.heart import sender as send_classify
        outcome = self.onebot_client.send_ex(event, text)
        ok = send_classify.raise_for(outcome)
        if not ok and group:
            with self._qq_rate_lock:
                recent = self._qq_sent_at.get(scope, [])
                if recent:
                    recent.pop()
        return ok

    def _onebot_queue_reply(self, event: dict[str, Any]) -> str:
        content = str(event.get("content") or "").strip()
        if not content:
            return ""
        target = Ident(
            "qq",
            str(event.get("platform_user_id") or event.get("user_id") or "unknown"),
            scope=str(event.get("scope") or "default"),
        )
        message_id = str(event.get("message_id") or "")
        extra = {
            "session": str(event.get("session") or "default"),
            "sender_name": str(event.get("sender_name") or ""),
            "message_type": str(event.get("message_type") or "private"),
            "group_id": event.get("group_id"),
            "user_id": event.get("user_id"),
        }
        if message_id:
            extra["idem_key"] = f"onebot|{message_id}"
        self.heart.enqueue(
            target,
            OutboxEvent(
                kind="mention",
                text=content,
                priority=PRI_MENTION,
                extra=extra,
            ),
        )
        self.heart.pump()
        return ""

    def _onebot_ingest(self, event: dict[str, Any]) -> None:
        target = Ident(
            "qq",
            str(event.get("platform_user_id") or event.get("user_id") or "unknown"),
            scope=str(event.get("scope") or "default"),
        )
        self.writer.submit_turn(
            target,
            str(event.get("session") or "default"),
            "user",
            str(event.get("content") or ""),
            extra={"sender_name": str(event.get("sender_name") or "")},
        )

    def _rebuild_index(self, index) -> None:
        """进程重启后从 fact 表重建语义索引（只收 active，避免召回已失效旧值）。"""
        rows = self.db.read(
            "SELECT id, text, subject, predicate, object, platform, platform_user_id, scope"
            " FROM fact WHERE status='active'"
        )
        for r in rows:
            index.upsert(
                int(r["id"]), r["text"],
                {
                    "text": r["text"],
                    "subject": r["subject"],
                    "predicate": r["predicate"],
                    "platform": r["platform"],
                    "platform_user_id": r["platform_user_id"],
                    "scope": r["scope"],
                },
            )
        self._index_built = len(rows)

    @classmethod
    def open(
        cls,
        path: str | Path | None = None,
        config: str | Path | None = None,
        **providers: Any,
    ) -> "Memory":
        # key 桥（2026-10-29 LLM 第二层）：本机网关路由（workbuddy/agent2api）的
        # key 存在 ~/.dsh/.credentials.yaml refs——环境变量没设时先桥接**再** load
        # （_load_routes 会跳过缺 key 的路由，桥晚了路由就没了）。密钥不落仓库；
        # 已设的环境变量不覆盖；桥失败只影响模型可用性，不阻塞打开。
        try:
            import os
            needs = [k for k in ("WORKBUDDY_HUB_KEY", "AGENT2API_KEY") if not os.environ.get(k)]
            if needs:
                _bridge_keys()
        except Exception:  # noqa: BLE001
            pass
        s = Settings.load(config)
        if path is not None:
            s.db_path = Path(path)
        return cls(s, **providers)

    # ---- 写：异步，立即返回 ----

    def turn(
        self,
        ident: Any,
        session: str,
        role: str,
        content: str,
        *,
        scope: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> int:
        return self.writer.submit_turn(Ident.coerce(ident, scope=scope), session, role, content, extra)

    def remember(
        self,
        text: str,
        ident: Any = None,
        *,
        session: str = "default",
        scope: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> int:
        return self.writer.submit_text(Ident.coerce(ident, scope=scope), text, session, extra)

    def exchange(
        self,
        ident: Any,
        session: str,
        user_text: str,
        assistant_text: str,
        *,
        scope: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> int:
        """异步记录完整问答；同一任务内落库，避免多 worker 打乱顺序。"""
        return self.writer.submit_exchange(
            Ident.coerce(ident, scope=scope), session, user_text, assistant_text, extra,
        )

    def ingest(
        self,
        ident: Any,
        *,
        kind: str,
        ref: str = "",
        text: str | None = None,
        url: str | None = None,
        mime: str | None = None,
        size_bytes: int | None = None,
        session: str = "default",
        extra: dict[str, Any] | None = None,
        scope: str | None = None,
    ) -> int:
        """任意类型的输入都走这里：识别（OCR/ASR/表情/链接/文件/视频元数据）在后台做。"""
        item = Item(kind=kind, ref=ref, text=text, url=url, mime=mime,
                    size_bytes=size_bytes, extra=dict(extra or {}))
        return self.writer.submit_ingest(Ident.coerce(ident, scope=scope), item, session)

    def add_media(
        self,
        ident: Any,
        *,
        kind: str,
        ref: str,
        url: str | None = None,
        session: str | None = None,
        mime: str | None = None,
        size_bytes: int | None = None,
        expires_at: float | None = None,
        extra: dict[str, Any] | None = None,
        scope: str | None = None,
    ) -> int:
        """只登记引用（平台侧句柄 + 可再取的 url），不落文件字节；不触发识别。"""
        ident = Ident.coerce(ident, scope=scope)
        with self.db.tx() as c:
            store.ensure_account(c, ident)
            return store.add_media(
                c, ident, kind=kind, ref=ref, url=url, session=session, mime=mime,
                size_bytes=size_bytes, expires_at=expires_at, extra=extra,
            )

    def media(self, ident: Any, session: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        target = Ident.coerce(ident)
        rows = store.media_for(self.db.conn(), target, session, limit)
        now = time.time()
        for row in rows:
            expiry = row.get("expires_at")
            if expiry is None or float(expiry) > now:
                continue
            row["url"] = None
            if self.media_refresher is None:
                continue
            try:
                refreshed = self.media_refresher(target, str(row["ref"]))
            except Exception:  # noqa: BLE001 - stale URL stays hidden
                continue
            if not refreshed:
                continue
            new_url, new_expiry = refreshed
            if not new_url or float(new_expiry) <= now:
                continue
            with self.db.tx() as c:
                c.execute(
                    "UPDATE media SET url=?, expires_at=? WHERE id=?"
                    " AND platform=? AND platform_user_id=? AND scope=?",
                    (new_url, new_expiry, row["id"], target.platform,
                     target.platform_user_id, str(target.scope or "default")),
                )
            row["url"], row["expires_at"] = new_url, new_expiry
        return rows

    def flush(self, timeout: float = 10.0) -> bool:
        return self.writer.flush(timeout)

    # ---- 读：同步，且有硬预算 ----

    def recall(
        self,
        query: str,
        ident: Any = None,
        *,
        session: str | None = None,
        scope: str | None = None,
        top_k: int | None = None,
        budget_ms: int | None = None,
    ) -> RecallResult:
        return self.recaller.recall(
            query,
            Ident.coerce(ident, scope=scope),
            session=session,
            top_k=top_k,
            budget_ms=budget_ms,
        )

    def inject(
        self,
        query: str,
        ident: Any = None,
        *,
        session: str | None = None,
        scope: str | None = None,
        max_chars: int | None = None,
        max_items: int | None = None,
        mode: str = "exact",
        log: bool = True,
        learn: bool | None = None,
    ) -> Injection:
        """给主模型的软引导块。**不生成回复、不改人格**。

        learn=None 跟 `Settings.decay_enabled` 走：关掉代谢层就退回"只读不强化"的 v10 行为。
        """
        if learn is None:
            learn = bool(getattr(self.settings, "decay_enabled", True))
        return self.injector.build(
            query, Ident.coerce(ident, scope=scope), session=session,
            max_chars=max_chars, max_items=max_items, mode=mode, log=log, learn=learn,
        )

    def feedback(
        self,
        ident: Any,
        injection_id: int | None,
        used_ids: list[int] | tuple[int, ...] = (),
        *,
        helpful: bool = True,
        note: str = "",
        scope: str | None = None,
    ) -> int:
        """主模型实际用了哪些记忆：回写命中计数，注入记录留痕。"""
        with self.db.tx() as c:
            return store.apply_feedback(c, Ident.coerce(ident, scope=scope), injection_id,
                                        list(used_ids), helpful=helpful, note=note)

    def recent_turns(self, ident: Any, session: str, limit: int = 20) -> list[dict[str, Any]]:
        return store.recent_turns(self.db.conn(), Ident.coerce(ident), session, limit)

    def recent_scope_turns(
        self,
        platform: str,
        scope: str,
        session: str,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        """Shared transcript for a group scope; not a cross-user fact lookup."""
        return store.recent_scope_turns(
            self.db.conn(), platform, scope, session, limit)

    def being_snapshot(
        self, *, now: float | None = None, hour: int | None = None,
    ) -> dict[str, Any]:
        return self.being.snapshot(now=now, hour=hour)

    def being_observe(
        self,
        text: str,
        *,
        now: float | None = None,
        hour: int | None = None,
    ) -> dict[str, Any]:
        return self.being.observe(text, now=now, hour=hour)

    def being_prompt(
        self,
        snapshot: dict[str, Any],
        *,
        proactive: bool = False,
    ) -> str:
        return self.being.prompt_block(snapshot, proactive=proactive)

    def being_note(
        self,
        text: str,
        *,
        visibility: str = "private",
        now: float | None = None,
    ) -> int:
        return self.being.note(text, visibility=visibility, now=now)

    def being_mark_proactive(
        self, *, now: float | None = None,
    ) -> dict[str, Any]:
        return self.being.mark_proactive(now=now)

    # ---- 主动配额（= 反代积分，见 aiji/quota.py）----

    def quota_state(self, *, now: float | None = None, daily_cap: int | None = None) -> QuotaState:
        """现在还能主动几次、依据是什么（`aiji quota` 与控制台都用它）。"""
        pool = getattr(self, "pool", None)
        if pool is not None:
            self.credits.note_headers(getattr(pool, "last_headers", None), now=now)
        return self.credits.state(
            daily_cap=params.PROACTIVE_QUOTA_24H if daily_cap is None else daily_cap,
            now=now,
        )

    def proactive_quota_ok(self, ident: Any, *, now: float | None = None) -> bool:
        """能不能主动搭话：24h 已发次数 < 按积分算出来的上限。"""
        t = time.time() if now is None else now
        st = self.quota_state(now=t)
        if st.allowed_24h <= 0:
            return False
        return sends_last_24h(self.heart, Ident.coerce(ident), t) < st.allowed_24h

    def proactive_spend(self, ident: Any, *, now: float | None = None) -> int:
        """记一次主动发送（滚动 24h 窗口），返回窗口内已用次数。"""
        t = time.time() if now is None else now
        iid = Ident.coerce(ident)
        record_send(self.heart, iid, t)
        return sends_last_24h(self.heart, iid, t)

    def being_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.being.events(limit=limit)

    def being_notes(
        self, *, limit: int = 20, private_only: bool = False,
    ) -> list[dict[str, Any]]:
        return self.being.notes(limit=limit, private_only=private_only)

    def being_record_event(
        self,
        kind: str,
        summary: str,
        *,
        valence_delta: float = 0.0,
        energy_delta: float = 0.0,
        now: float | None = None,
    ) -> int:
        return self.being.record_event(
            kind, summary, valence_delta=valence_delta,
            energy_delta=energy_delta, now=now)

    def facts_as_of(self, ident: Any, as_of: float, *, predicate: str | None = None,
                    limit: int = 50) -> list[dict[str, Any]]:
        """bi-temporal 回放：as_of 时刻为真的事实（当时视角）。
        S4 archive_replay 的读入口。"""
        return [
            {"id": f.id, "subject": f.subject, "predicate": f.predicate, "object": f.object,
             "text": f.text, "confidence": f.confidence, "kind": f.kind,
             "valid_from": f.valid_from, "valid_to": f.valid_to}
            for f in store.facts_as_of(self.db.conn(), Ident.coerce(ident), as_of,
                                       predicate=predicate, limit=limit)
        ]

    def forget(self, ident: Any, *, fact_id: int | None = None, like: str = "") -> dict[str, Any]:
        """显式遗忘（手册 v5：用户说"忘掉吧"就该真的忘）。

        实现是"立刻不可见 + 留审计"而不是物理删：注入/召回/画像全链路按
        `status='active'` 过滤，所以效果等价；而 fact_log 里留下 op='forget'，
        事后能回答"谁让她忘的、忘了什么"。
        """
        target = Ident.coerce(ident)
        if fact_id is None and not like:
            raise ValueError("forget 需要 fact_id 或 like 之一")
        with self.db.tx() as c:
            n = store.forget_facts(c, target, fact_id=fact_id, like=like)
        return {"forgotten": n, "fact_id": fact_id, "like": like}

    def archived_turns(self, ident: Any, session: str | None = None,
                       limit: int = 50) -> list[dict[str, Any]]:
        """被压缩淘汰的原文（退役不删，90 天内可查）。"""
        return store.archived_turns(self.db.conn(), Ident.coerce(ident), session, limit)

    def self_memory(self, *, scope: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return store.self_memory_list(self.db.conn(), scope=scope, limit=limit)

    def remember_self(self, text: str, *, kind: str = "pref", scope: str = "default",
                      importance: float = 0.5) -> int:
        """写一条自我记忆（手册 v5 第五层：她自己的事，与用户画像分开）。"""
        with self.db.tx() as c:
            sid = store.add_self_memory(c, text, kind=kind, scope=scope, importance=importance)
            store.trim_self_memory(c)
        return sid

    def profile(self, ident: Any = None) -> dict[str, dict[str, Any]]:
        return store.profile_snapshot(self.db.conn(), Ident.coerce(ident))

    def style(self, ident: Any = None) -> dict[str, dict[str, Any]]:
        return store.style_snapshot(self.db.conn(), Ident.coerce(ident))

    def life_snapshot(self, ident: Any = None, *, now: float | None = None) -> dict[str, Any]:
        target = Ident.coerce(ident)
        card = load_life_card(self.db, target)
        card.tick(now=now)
        save_life_card(self.db, target, card, now=now)
        return card.snapshot()

    def life_event(
        self, ident: Any, event_type: str, intensity: float = 0.5,
        *, now: float | None = None,
    ) -> dict[str, Any]:
        target = Ident.coerce(ident)
        card = load_life_card(self.db, target)
        card.apply_event(event_type, intensity, now=now)
        save_life_card(self.db, target, card, now=now)
        snapshot = card.snapshot()
        self.presence.publish({
            "state": "mood",
            "mood": mood_label(card),
            "life": snapshot,
        })
        return snapshot

    def life_context(
        self, ident: Any = None, *, scenario: str = "chat", intent: str = "",
        turns: int = 0, token_budget: int = 6000,
    ) -> dict[str, Any]:
        target = Ident.coerce(ident)
        card = load_life_card(self.db, target)
        card.tick()
        plan = card.context_plan(
            scenario=scenario, intent=intent, turns=turns,
            token_budget=token_budget,
        )
        save_life_card(self.db, target, card)
        return plan

    # ---- 自我层（物理底线 + 涌现性格，2026-10-29）----

    def traits(self, ident: Any = None) -> list[dict[str, Any]]:
        """当前涌现出的性格（按置信度降序）。"""
        return store.trait_snapshot(self.db.conn(), Ident.coerce(ident))

    def global_traits(self) -> list[dict[str, Any]]:
        """跨联系人重复出现的稳定习惯；单段关系里的习惯不算全局底色。"""
        return store.global_traits(self.db.conn())

    def preferred_name(self, ident: Any = None) -> str:
        """当前明确希望被如何称呼；昵称属于 QQ 号，不按群/scope 分裂。"""
        target = Ident.coerce(ident)
        rows = self.db.read(
            "SELECT object FROM fact WHERE platform=? AND platform_user_id=?"
            " AND status='active'"
            " AND kind='fact' AND predicate IN ('昵称','称呼偏好','称呼')"
            " ORDER BY updated_at DESC, id DESC LIMIT 1",
            (target.platform, target.platform_user_id),
        )
        if not rows:
            return ""
        value = str(rows[0]["object"] or "").strip()
        return "" if value in ("未提及", "未知", "none", "null") else value[:40]

    def allow_fallback(self, ident: Any, session: str) -> bool:
        target = Ident.coerce(ident)
        key = (
            target.platform, target.platform_user_id,
            str(target.scope or "default"), str(session or "default"),
        )
        now = time.time()
        cooldown = max(0.0, float(self.settings.fallback_cooldown_s))
        with self._fallback_lock:
            last = self._fallback_at.get(key, 0.0)
            if last and now - last < cooldown:
                return False
            self._fallback_at[key] = now
            return True

    def clear_fallback(self, ident: Any, session: str) -> None:
        target = Ident.coerce(ident)
        key = (
            target.platform, target.platform_user_id,
            str(target.scope or "default"), str(session or "default"),
        )
        with self._fallback_lock:
            self._fallback_at.pop(key, None)

    def stale_nicknames(self, ident: Any = None) -> list[str]:
        """Historical active nicknames for the same QQ, newest first."""
        target = Ident.coerce(ident)
        preferred = self.preferred_name(target)
        rows = self.db.read(
            "SELECT object,MAX(updated_at) updated_at FROM fact"
            " WHERE platform=? AND platform_user_id=?"
            " AND kind='fact' AND predicate IN ('昵称','称呼偏好','称呼')"
            " GROUP BY object ORDER BY updated_at DESC LIMIT 8",
            (target.platform, target.platform_user_id),
        )
        out = []
        for row in rows:
            value = str(row["object"] or "").strip()
            if (
                value and value != preferred
                and value not in ("未提及", "未知", "none", "null")
                and value not in out
            ):
                out.append(value[:40])
        return out

    def self_portrait(self, ident: Any = None) -> dict[str, Any]:
        """自画像：底线是常量，性格是数据。可直接进提示词或看板。"""
        ts = self.traits(ident)
        return {
            "floor": dict(selfhood.FLOOR),
            "traits": ts,
            "global_traits": self.global_traits(),
            "prefix": selfhood.system_prefix(
                ts, global_traits=self.global_traits()),
        }

    def reflect(
        self, ident: Any = None, session: str = "default", *, force: bool = False
    ) -> dict[str, Any] | None:
        """跑一次反思（攒够对话才会真跑；force=True 强制）。"""
        return self.reflective.tick(ident, session, force=force)

    def summary(self, ident: Any = None, session: str | None = None) -> dict[str, Any] | None:
        return store.latest_summary(self.db.conn(), Ident.coerce(ident), session)

    def accounts(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.read("SELECT * FROM account ORDER BY platform, platform_user_id")]

    def stats(self) -> dict[str, Any]:
        return {
            "db": str(self.settings.db_path),
            "started_at": self.started_at,
            "store": store.stats(self.db.conn()),
            "writer": self.writer.stats_dict(),
            "recaller": self.recaller.stats(),
            "injection": store.injection_stats(self.db.conn()),
            "reflective": dict(self.reflective.stats),
            "reflection_worker": dict(self.reflection_worker.stats),
            "human_pacer": dict(self.human_pacer.stats),
            "presence": {**self.presence.stats, "port": self.presence.port},
            "actions": {
                "root": str(self.action_executor.root),
                "approval": dict(self.action_approvals.stats),
                **self.action_executor.approvals.stats,
            },
            "onebot": self.onebot.snapshot(),
            "pool": self.pool.stats(),
            "budget_ms": self.settings.budget_ms,
            "uptime_s": round(max(0.0, time.time() - self.started_at), 1),
            "vector": self.settings.vector,
            "index_built": self._index_built,
        }

    def close(self) -> None:
        try:
            self.writer.stop()
        finally:
            self.reflection_worker.stop()
            life_loop = getattr(self, "life_loop", None)
            if life_loop is not None:
                life_loop.stop()
            mcp_loop = getattr(self, "mcp_research_loop", None)
            if mcp_loop is not None:
                mcp_loop.stop()
            self.onebot.stop()
            self.dispatcher.stop()
            self.recaller.close()
            self.db.close_all()
