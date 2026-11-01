"""异步写管道：调用方只往队列里丢事件（微秒级），识别/抽取/落库全在 worker 线程。

    turn    → 落 turn 表 → 抽取事实/知识/关系/画像/风格
    exchange→ 一问一答在同一事务内落 turn，保证顺序
    text    → 直接抽取（不落 turn）
    ingest  → 识别层降维（OCR/ASR/表情/链接/文件/视频元数据）→ **同一条**抽取管线

顺序保证：同一 (身份, session) 的事件按提交顺序处理（单队列 + FIFO）。
失败保证：单条抽取失败进 write_queue 死信表，不丢。
shortcut: 内存队列不落盘，进程被 kill 时最多丢最后一个未处理的事件；
shortcut: 摘要用本地压缩（heuristic_summary），没走模型——模型摘要要另算延迟与成本。
"""

from __future__ import annotations

import hashlib
import json
import queue
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

from . import store
from .config import Settings
from .db import DB
from .extract import (
    Extraction, build_messages, build_summary_messages, heuristic, heuristic_summary, parse,
)
from .pool import ModelPool, PoolExhausted
from .recall import Recaller
from .recognize import Envelope, Item, Recognizers
from .store import Ident

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


@dataclass(slots=True)
class Job:
    kind: str  # turn | exchange | text | ingest
    ident: Ident
    session: str
    text: str = ""
    role: str = "user"
    item: Item | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    seq: int = 0

    def dead_letter_payload(self) -> dict[str, Any]:
        """Preserve enough context to replay a failed job without storing bytes."""
        payload = {
            "job_kind": self.kind,
            "session": self.session,
            "role": self.role,
            "text": self.text,
            "extra": _json_safe(self.extra),
        }
        if self.item is not None:
            payload["item"] = {
                "kind": self.item.kind,
                "ref": self.item.ref,
                "text": self.item.text,
                "url": self.item.url,
                "mime": self.item.mime,
                "size_bytes": self.item.size_bytes,
                "extra": _json_safe(self.item.extra),
            }
        return payload


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): safe
            for k, v in value.items()
            if (safe := _json_safe(v)) is not None
        }
    if isinstance(value, (list, tuple)):
        return [safe for v in value if (safe := _json_safe(v)) is not None]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None
    return str(value)


@dataclass(slots=True)
class WriterStats:
    submitted: int = 0
    processed: int = 0
    failed: int = 0
    model_ok: int = 0
    heuristic: int = 0
    ingested: int = 0
    unrecognized: int = 0
    summaries: int = 0
    summaries_reused: int = 0  # 同哈希复用（没花模型调用）的摘要数
    summary_model: int = 0
    evicted: int = 0
    last_error: str = ""
    last_route: str = ""


class Writer:
    def __init__(
        self,
        db: DB,
        pool: ModelPool,
        recaller: Recaller,
        settings: Settings,
        recognizers: Recognizers | None = None,
    ):
        self.db = db
        self.pool = pool
        self.recaller = recaller
        self.settings = settings
        self.recognizers = recognizers or Recognizers(
            timeout_s=settings.recognize_timeout_s, min_confidence=settings.min_confidence
        )
        self._q: queue.Queue[Job | None] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._cv = threading.Condition()
        self._pending = 0
        self._closed = False
        self._turn_counts: dict[tuple[str, str, str, str], int] = {}
        self._summary_lock = threading.Lock()
        self._count_lock = threading.Lock()
        self._session_seq: dict[tuple[str, str, str, str], int] = {}
        self._session_next: dict[tuple[str, str, str, str], int] = {}
        self._order_cv = threading.Condition()
        self.stats = WriterStats()

    # ---- 生命周期 ----

    def start(self) -> "Writer":
        for i in range(max(1, self.settings.workers)):
            t = threading.Thread(target=self._loop, name=f"aiji-writer-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def stop(self, flush_timeout: float = 5.0) -> None:
        self.flush(flush_timeout)
        self._closed = True
        for _ in self._threads:
            self._q.put(None)
        for t in self._threads:
            t.join(timeout=2.0)

    # ---- 提交（主链路：只做内存操作） ----

    def submit_turn(self, ident: Ident, session: str, role: str, content: str,
                    extra: dict[str, Any] | None = None) -> int:
        self.recaller.note_turn(ident, session, role, content, time.time())
        return self._submit(Job("turn", ident, session, content, role, None, extra or {}))

    def submit_exchange(self, ident: Ident, session: str, user_text: str,
                        assistant_text: str, extra: dict[str, Any] | None = None) -> int:
        """一次异步写入完整问答：多 worker 下也不会出现 assistant 先于 user。"""
        now = time.time()
        self.recaller.note_turn(ident, session, "user", user_text, now)
        self.recaller.note_turn(ident, session, "assistant", assistant_text, now)
        payload = dict(extra or {})
        payload["assistant"] = assistant_text
        return self._submit(Job("exchange", ident, session, user_text,
                                "user", None, payload))

    def submit_text(self, ident: Ident, text: str, session: str = "default",
                    extra: dict[str, Any] | None = None) -> int:
        return self._submit(Job("text", ident, session, text, "user", None, extra or {}))

    def submit_ingest(self, ident: Ident, item: Item, session: str = "default") -> int:
        """任意输入（图片/语音/表情/链接/文件/视频）：识别与抽取都在后台。"""
        return self._submit(Job("ingest", ident, session, "", "user", item, dict(item.extra)))

    def _submit(self, job: Job) -> int:
        key = self._job_key(job)
        with self._count_lock:
            job.seq = self._session_seq.get(key, 0)
            self._session_seq[key] = job.seq + 1
        self._q.put(job)
        with self._cv:
            self._pending += 1
            self.stats.submitted += 1
        return self._q.qsize()

    def flush(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._cv:
            while self._pending > 0:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cv.wait(left)
            return True

    # ---- worker ----

    @staticmethod
    def _job_key(job: Job) -> tuple[str, str, str, str]:
        return (
            job.ident.platform,
            job.ident.platform_user_id,
            str(job.ident.scope or "default"),
            job.session,
        )

    def _wait_turn(self, job: Job) -> tuple[str, str, str, str]:
        key = self._job_key(job)
        with self._order_cv:
            while job.seq != self._session_next.get(key, 0):
                self._order_cv.wait()
        return key

    def _complete_turn(self, key: tuple[str, str, str, str], seq: int) -> None:
        with self._order_cv:
            self._session_next[key] = seq + 1
            self._order_cv.notify_all()

    def _loop(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                self._q.task_done()
                return
            key = self._wait_turn(job)
            try:
                self._handle(job)
            except Exception as exc:  # noqa: BLE001 - worker 永不退出
                self.stats.failed += 1
                self.stats.last_error = f"{type(exc).__name__}: {exc}"
                try:
                    with self.db.tx() as c:
                        store.enqueue(c, job.ident, "retry",
                                      job.dead_letter_payload())
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
            finally:
                self._complete_turn(key, job.seq)
                self._q.task_done()
                with self._cv:
                    self._pending -= 1
                    self.stats.processed += 1
                    self._cv.notify_all()

    def _handle(self, job: Job) -> None:
        if job.kind == "ingest" and job.item is not None:
            self._handle_ingest(job)
            return
        if job.kind == "exchange":
            with self.db.tx() as c:
                display_name, labels = self._account_labels(job.extra)
                store.ensure_account(
                    c, job.ident, display_name=display_name, extra=labels)
                store.add_turn(c, job.ident, job.session, "user", job.text,
                               extra=job.extra)
                store.add_turn(c, job.ident, job.session, "assistant",
                               str(job.extra.get("assistant") or ""))
            self._bump(job.ident, job.session, "user")
            if job.text.strip():
                self._ingest(job.text, job.ident, job.session)
            self._maybe_summarize(job.ident, job.session)
            return
        if job.kind == "turn":
            with self.db.tx() as c:
                display_name, labels = self._account_labels(job.extra)
                store.ensure_account(
                    c, job.ident, display_name=display_name, extra=labels)
                external_id = str(job.extra.get("message_id") or "")
                if store.turn_by_external_id(
                    c, job.ident, job.session, external_id,
                ) is not None:
                    return
                store.add_turn(c, job.ident, job.session, job.role, job.text, extra=job.extra)
            self._bump(job.ident, job.session, job.role)
        if job.text.strip():
            self._ingest(
                job.text,
                job.ident,
                job.session,
                force_heuristic=self._ambient_backlog(job.ident),
            )
        self._maybe_summarize(job.ident, job.session)

    def _handle_ingest(self, job: Job) -> None:
        """识别 → 落引用+识别结果 → 若可用则走同一条抽取管线（此处只有这一步分叉）。"""
        item = job.item
        assert item is not None
        env: Envelope = self.recognizers.recognize(item)
        with self.db.tx() as c:
            display_name, labels = self._account_labels(job.extra)
            store.ensure_account(
                c, job.ident, display_name=display_name, extra=labels)
            store.add_media(
                c, job.ident, kind=item.kind, ref=item.ref or f"{item.kind}-{int(time.time()*1000)}",
                url=item.url, session=job.session, mime=item.mime, size_bytes=item.size_bytes,
                text=env.text or None, confidence=env.confidence, status=env.status,
                recognizer=env.recognizer, elapsed_ms=env.elapsed_ms, extra=env.meta,
            )
        self.stats.ingested += 1
        if not env.usable:
            self.stats.unrecognized += 1
            return
        # 之后与文本完全同一条路：不按类型分叉（只有风格学习关掉，见 _ingest）
        self._ingest(env.text, job.ident, job.session, source=f"{item.kind}:{item.ref}",
                     learn_style=False, force_heuristic=self._ambient_backlog(job.ident))
        self._maybe_summarize(job.ident, job.session)

    def _ambient_backlog(self, ident: Ident) -> bool:
        return (
            str(ident.scope or "").startswith("group:")
            and self._q.qsize() >= max(
                1, int(self.settings.writer_backlog_heuristic_at))
        )

    @staticmethod
    def _account_labels(extra: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        """Nickname/avatar/card are display labels; the identity key stays numeric."""
        name = " ".join(str(extra.get("sender_name") or "").split())[:80]
        labels: dict[str, Any] = {}
        avatar = str(extra.get("sender_avatar") or "").strip()
        card = " ".join(str(extra.get("sender_card") or "").split())[:80]
        if avatar:
            labels["avatar"] = avatar[:2000]
        if card:
            labels["card"] = card
        return (name or None), labels

    def _ingest(
        self,
        text: str,
        ident: Ident,
        session: str,
        source: str = "",
        learn_style: bool = True,
        *,
        force_heuristic: bool = False,
    ) -> None:
        """抽取 → 落库。模型池挂了就规则兜底，保证写入不丢。

        learn_style=False 用于识别层的产物（OCR/ASR/链接标题）：那是**别人的文本**，
        不该算成用户的表达风格。风格只从用户自己说的话里学。
        """
        ext = self._extract(
            text, ident, session, force_heuristic=force_heuristic)
        with self.db.tx() as c:
            res = store.apply_extraction(
                c, ident, ext.facts, ext.profile,
                style=ext.style if learn_style else None,
                source=source or session, source_kind=ext.source_kind, session=session,
            )
        self.stats.model_ok += 1 if ext.source_kind == "model" else 0
        self.stats.heuristic += 1 if ext.source_kind == "heuristic" else 0
        if self.recaller.semantic:
            # 被覆盖/否定的旧事实要从索引里删掉，否则语义召回还会把旧值带出来
            for oid in res["superseded"]:
                self.recaller.index.delete(int(oid))
            for fid in res["fact_ids"]:
                self._index_fact(fid)
        self.recaller.refresh_profile(ident)

    def _extract(
        self,
        text: str,
        ident: Ident,
        session: str,
        *,
        force_heuristic: bool = False,
    ) -> Extraction:
        if force_heuristic:
            return heuristic(text)
        errors: list[str] = []
        for _ in range(1, max(1, self.settings.max_attempts) + 1):
            try:
                res = self.pool.chat(
                    build_messages(text, session), background=True)
                self.stats.last_route = res.route
                return parse(res.text, source_text=text)
            except PoolExhausted as exc:
                errors.append(str(exc))
                break  # 池子整体不可用，马上兜底，别干等
            except ValueError as exc:  # 模型给了非 JSON
                errors.append(f"parse:{exc}")
        self.stats.last_error = "; ".join(errors)[:300]
        return heuristic(text)

    # ---- 摘要 ----

    def _bump(self, ident: Ident, session: str, role: str) -> None:
        if role != "user":
            return
        key = (ident.platform, ident.platform_user_id, str(ident.scope or "default"), session)
        with self._count_lock:  # read-modify-write 必须原子，否则并发下计数会被覆盖
            self._turn_counts[key] = self._turn_counts.get(key, 0) + 1

    def _maybe_summarize(self, ident: Ident, session: str) -> None:
        """距上次摘要够 N 轮就写一条；会话超过上限时强制压缩并淘汰旧原文。

        不能用「n 恰好是 N 的倍数」判断：多 worker 并发下计数会跳值，
        一旦跳过那个倍数就再也不触发（实测 n 序列 1,1,1,3,3,5，永远等不到 4）。
        """
        every = max(4, self.settings.summary_every)
        key = (ident.platform, ident.platform_user_id, str(ident.scope or "default"), session)
        n = self._turn_counts.get(key, 0)
        if n <= 0:
            return
        with self._summary_lock:  # 判定+写入+淘汰必须原子，否则会写重复摘要
            w, wargs = ident.where()
            done = int(self.db.conn().execute(
                f"SELECT COALESCE(MAX(upto_turn), 0) FROM summary WHERE {w} AND session=?",
                (*wargs, session),
            ).fetchone()[0])
            if n - done >= every:
                rows = store.recent_turns(self.db.conn(), ident, session, max(every, n - done))
                user_rows = [r for r in rows if r["role"] == "user"]
                user_lines = [r["content"] for r in user_rows]
                if user_lines:
                    src_hash, text, source_kind = self._summary_for(
                        ident, session, user_lines)
                    with self.db.tx() as c:
                        store.put_summary(
                            c, ident, session, n, text,
                            extra={"turns": len(user_lines), "source_kind": source_kind},
                            source_hash=src_hash,
                            source_turn_ids=json.dumps([int(r["id"]) for r in user_rows]),
                        )
                    self.stats.summaries += 1
                    done = n
            self._compact_session(ident, session, n, done)

    def _compact_session(self, ident: Ident, session: str, n: int, done: int) -> None:
        """超长会话强制压缩：先把将被淘汰的发言摘要好，再删原文（不持写锁做模型调用）。"""
        cap = int(self.settings.max_turns_per_session or 0)
        if cap <= 0:
            return
        conn = self.db.conn()
        if store.turn_count(conn, ident, session) <= cap:
            return
        keep = max(1, int(self.settings.turn_keep))
        ids = store.session_turn_ids(conn, ident, session)
        drop = ids[: max(0, len(ids) - keep)]
        lines: list[str] = []
        if drop:
            q = ",".join("?" * len(drop))
            lines = [
                r["content"] for r in conn.execute(
                    f"SELECT role, content FROM turn WHERE id IN ({q})", drop
                ).fetchall() if r["role"] == "user"
            ]
        text, source_kind = self._summarize(lines) if lines else ("", "heuristic")
        src_hash = self._hash_lines(lines)
        with self.db.tx() as c:
            sid = 0
            if text and n > done:
                sid = store.put_summary(
                    c, ident, session, n, text,
                    extra={"forced": True, "evicted": len(drop),
                           "turns": len(lines), "source_kind": source_kind},
                    source_hash=src_hash,
                    source_turn_ids=json.dumps(drop),
                )
                self.stats.summaries += 1
            n_evicted, _ = store.evict_turns(c, ident, session, keep, summary_id=sid or None)
        if n_evicted:
            self.stats.evicted += n_evicted
            self.recaller.forget_session(ident, session)  # 别从内存缓冲里召回已删原文

    @staticmethod
    def _hash_lines(lines: list[str]) -> str:
        """原文哈希：同一段原文只总结一次（memory-brain 的 hash-idempotent 摘要）。

        为什么不用"第几次摘要"当幂等键：重启/重放/并发会让序号漂移，
        而原文内容不变就是同一个哈希——重跑只是把 upto_turn 游标推到新位置。
        """
        clean = [x for x in lines if (x or "").strip()]
        if not clean:
            return ""
        return hashlib.sha1("\n".join(clean).encode("utf-8")).hexdigest()

    def _summary_for(
        self, ident: Ident, session: str, lines: list[str]
    ) -> tuple[str, str, str]:
        """有同哈希旧摘要就复用（0 次模型调用），否则现算。"""
        src_hash = self._hash_lines(lines)
        old = store.summary_by_hash(self.db.conn(), ident, session, src_hash)
        if old and old.get("text"):
            self.stats.summaries_reused += 1
            return src_hash, str(old["text"]), "reuse"
        text, kind = self._summarize(lines)
        return src_hash, text, kind

    def _summarize(self, lines: list[str]) -> tuple[str, str]:
        """优先模型摘要（免费池），不可用/输出不合法就退回本地压缩。"""
        clean = [x for x in lines if (x or "").strip()]
        if not clean:
            return "", "heuristic"
        if self.settings.summary_model:
            try:
                res = self.pool.chat(
                    build_summary_messages(clean), background=True)
                self.stats.last_route = res.route
                raw = (res.text or "").strip()
                m = _FENCE.search(raw)
                if m:
                    raw = m.group(1).strip()
                data = json.loads(raw)
                text = str(data.get("summary") or "").strip()
                if text:
                    self.stats.summary_model += 1
                    return text[:600], "model"
            except (PoolExhausted, ValueError, TypeError, KeyError, AttributeError):
                pass
        return heuristic_summary(clean), "heuristic"

    def _index_fact(self, fact_id: int) -> None:
        row = self.db.conn().execute(
            "SELECT text, subject, predicate, object, platform, platform_user_id, scope"
            " FROM fact WHERE id=?", (fact_id,)
        ).fetchone()
        if row:
            self.recaller.index.upsert(
                fact_id,
                row["text"],
                {
                    "text": row["text"],
                    "subject": row["subject"],
                    "predicate": row["predicate"],
                    "platform": row["platform"],
                    "platform_user_id": row["platform_user_id"],
                    "scope": row["scope"],
                },
            )

    def stats_dict(self) -> dict[str, Any]:
        return {
            "submitted": self.stats.submitted,
            "processed": self.stats.processed,
            "failed": self.stats.failed,
            "model_ok": self.stats.model_ok,
            "heuristic": self.stats.heuristic,
            "ingested": self.stats.ingested,
            "unrecognized": self.stats.unrecognized,
            "summaries": self.stats.summaries,
            "summary_model": self.stats.summary_model,
            "evicted": self.stats.evicted,
            "queue_depth": self._q.qsize(),
            "pending": self._pending,
            "last_route": self.stats.last_route,
            "last_error": self.stats.last_error,
            "recognizers": self.recognizers.stats(),
        }


