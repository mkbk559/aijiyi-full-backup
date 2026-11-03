"""反思 Worker：从群聊互动里归纳"我长成了什么样"。

这是 2026-10-29 定下的架构里**性格的唯一合法来源**之一（另一条是直接互动）：

    物理底线（selfhood.py，代码常量，不可动摇）
        + 互动（turn 表：人怎么跟我说话、我回了什么）
        → 反思（本模块，周期性自我归纳，带证据投票）
        → trait 表（可被新证据推翻）
        → system_prefix() 渲染回提示词

三条硬约束（和摘要 Worker 同源，都是踩过坑的）：
- **幂等**：reflection 表记 `up_to_turn` 游标，攒够 `every_turns` 条新对话才跑，
  重启/重跑不会把同一段对话反思两遍。
- **不写人设**：提示词明确"你在回顾自己的说话习惯，不是写人设"；输出只收
  「倾向 + 证据数」，拒收"你是一个温柔的人"这类断言。
- **可推翻**：trait 用症状投票（`store.upsert_trait`），旧值证据不足就让位。

失败一律静默降级（模型不可用时不反思，不编造性格）。
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from aiji.right_hand import params
from aiji.hippocampus.db import DB
from aiji.hippocampus.store import Ident, last_reflection, log_reflection, trait_snapshot, upsert_trait

SYSTEM = (
    "你在回顾自己和人类的一段相处记录，目的是更新『你在这段关系里养成的说话习惯』。\n"
    "这不是写人设，也不是写角色设定。只写你**真的**从这些对话里表现出来的倾向，并且要有证据。\n"
    "对话里的『我』固定是玛卡巴卡，『用户』固定是对方。用户的昵称只属于用户，"
    "不能写成我的名字或自称；我的名字固定是玛卡巴卡。\n"
    "规则：\n"
    "1. name 是 2-6 字的行为标签（如『爱吐槽』『爱用省略号』『习惯反问』），不是形容词堆砌。\n"
    "2. value 是具体倾向的一句话（≤40 字），要能被对话原文佐证。\n"
    "3. evidence 是这段对话里支持它的次数（整数，≥1）。\n"
    "4. 没有可归纳的新倾向就返回空数组，不要硬凑。\n"
    "只输出 JSON：{\"traits\":[{\"name\":\"...\",\"value\":\"...\",\"evidence\":2}],\"summary\":\"一句话\"}"
)

_JSON_RE = re.compile(r"\{.*\}", re.S)
_BAD_PREFIX = ("你是", "你是一个", "你是个", "你应当", "你必须", "你的人设", "请扮演")
_BANNED_TRAIT_MARKERS = (
    "自称", "称呼", "主人", "鱼", "泡泡", "咕噜", "鱼缸", "鳍", "鳃",
    "emoji", "表情结尾",
)


def _parse(raw: str) -> dict[str, Any]:
    """从模型输出里抠 JSON；抠不出就返回空（不编造）。"""
    if not raw:
        return {}
    m = _JSON_RE.search(raw)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _clean_trait(item: Any) -> tuple[str, str, int] | None:
    """校验一条 trait：拒收"你是XX"式人设断言，只收倾向。"""
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()
    value = str(item.get("value") or "").strip()
    if not name or not value:
        return None
    if any(name.startswith(p) or value.startswith(p) for p in _BAD_PREFIX):
        return None
    combined = f"{name} {value}".lower()
    if any(marker in combined for marker in _BANNED_TRAIT_MARKERS):
        return None
    try:
        ev = max(1, int(item.get("evidence") or 1))
    except (TypeError, ValueError):
        ev = 1
    return name[:16], value[:60], min(ev, 20)  # 单次反思最多记 20 次证据，防刷分


class Reflective:
    """周期性自我归纳。由外部定时器调 `tick()`；也可 `force=True` 手动跑一次。"""

    def __init__(
        self,
        db: DB,
        pool: Any = None,
        *,
        every_turns: int = params.REFLECT_EVERY_TURNS,
        min_interval_s: float = params.REFLECT_MIN_INTERVAL,
        max_turns: int = params.REFLECT_MAX_TURNS,
    ):
        self.db = db
        self.pool = pool
        self.every_turns = max(1, int(every_turns))
        self.min_interval = float(min_interval_s)
        self.max_turns = max(1, int(max_turns))
        self.stats = {"runs": 0, "skipped": 0, "traits_new": 0,
                      "traits_reinforced": 0, "traits_changed": 0, "errors": 0}

    # ---- 内部 ----

    def _max_turn_id(self, ident: Ident, session: str) -> int:
        r = self.db.read(
            "SELECT MAX(id) AS m FROM turn"
            " WHERE platform=? AND platform_user_id=? AND scope=? AND session=?",
            (ident.platform, ident.platform_user_id, str(ident.scope or "default"), session),
        )
        return int(r[0]["m"] or 0) if r else 0

    def _turns(self, ident: Ident, session: str, after: int, upto: int) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self.db.read(
                "SELECT id,role,content FROM turn"
                " WHERE platform=? AND platform_user_id=? AND scope=? AND session=?"
                " AND id>? AND id<=? ORDER BY id ASC LIMIT ?",
                (ident.platform, ident.platform_user_id, str(ident.scope or "default"),
                 session, int(after), int(upto), self.max_turns),
            )
        ]

    def _prompt(self, turns: list[dict[str, Any]], traits: list[dict[str, Any]]) -> list[dict]:
        cur = "\n".join(
            f"- {t['value']}（证据 {t['evidence']} 次，置信 {float(t['confidence']):.2f}）"
            for t in traits
        ) or "（还是一张白纸）"
        convo = "\n".join(
            f"{'用户' if t.get('role') == 'user' else '我'}: {str(t.get('content'))[:200]}"
            for t in turns
        )
        return [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content":
                f"我现在的习惯：\n{cur}\n\n最近的相处记录：\n{convo}\n\n"
                f"请更新我的习惯（只输出 JSON）。"},
        ]

    # ---- 对外 ----

    def tick(
        self, ident: Any, session: str = "default", *, force: bool = False
    ) -> dict[str, Any] | None:
        """攒够对话就反思一次。返回本轮结果；不够/失败返回 None。

        注意 `force=True` 只绕过「轮次阈值 + 最小间隔」，**不绕过幂等游标**：
        没有新对话时仍然返回 None——重跑同一段对话会重复累加证据，那是作弊。
        """
        ident = Ident.coerce(ident)
        sess = session or "default"
        upto = self._max_turn_id(ident, sess)
        if upto <= 0:
            self.stats["skipped"] += 1
            return None
        last = last_reflection(self.db.conn(), ident, sess)
        after = int(last["up_to_turn"]) if last else 0
        fresh = upto - after
        if not force and fresh < self.every_turns:
            self.stats["skipped"] += 1
            return None
        if not force and last and (time.time() - float(last["created_at"])) < self.min_interval:
            self.stats["skipped"] += 1
            return None

        turns = self._turns(ident, sess, after, upto)
        if not turns:
            self.stats["skipped"] += 1
            return None
        if not any(str(turn.get("role") or "") == "assistant" for turn in turns):
            self.stats["skipped"] += 1
            return None

        traits = trait_snapshot(self.db.conn(), ident)
        if self.pool is None or not getattr(self.pool, "_states", None):
            self.stats["errors"] += 1
            return None
        # Low-priority work must not compete with live replies or create a
        # three-request storm when the upstream is already timing out.
        data: dict[str, Any] = {}
        last_err: str = ""
        try:
            res = self.pool.chat(
                self._prompt(turns, traits), timeout_s=60.0,
                background=True, attempts_per_route=1,
            )
            data = _parse(getattr(res, "text", "") or "")
        except Exception as e:  # noqa: BLE001 - model failure cannot block replies
            last_err = type(e).__name__
        if last_err:
            self.stats["errors"] += 1
            self.stats.setdefault("last_error", last_err)
            return None

        cleaned = [c for c in (_clean_trait(i) for i in (data.get("traits") or [])) if c]
        summary = str(data.get("summary") or "")[:200]
        if not cleaned and not summary.strip():
            self.stats["skipped"] += 1
            return None
        deltas: list[dict[str, Any]] = []
        with self.db.tx() as c:
            for name, value, ev in cleaned:
                action = upsert_trait(c, ident, name, value, origin="reflect", weight=ev)
                deltas.append({"name": name, "value": value, "evidence": ev, "action": action})
            log_reflection(c, ident, sess, upto, len(turns), summary, deltas)
        self.stats["runs"] += 1
        for d in deltas:
            if d["action"] == "new":
                self.stats["traits_new"] += 1
            elif d["action"] == "reinforce":
                self.stats["traits_reinforced"] += 1
            elif d["action"] in ("replaced", "weakened"):
                self.stats["traits_changed"] += 1
        return {"up_to_turn": upto, "turns_seen": len(turns), "summary": summary,
                "deltas": deltas}


class ReflectionWorker:
    """常驻后台反思：周期扫描有新对话的会话，调 `Reflective.tick()`。

    反思是低优先级任务，失败只记统计，不影响主对话链路。扫描只读 turn 表；
    是否值得调用模型由 `Reflective` 的轮次阈值、最小间隔和幂等游标决定。
    """

    def __init__(self, reflective: Reflective, *, interval_s: float = 5.0,
                 batch_size: int = 25):
        self.reflective = reflective
        self.interval_s = max(0.01, float(interval_s))
        self.batch_size = max(1, int(batch_size))
        self.stats = {"scans": 0, "sessions": 0, "reflections": 0, "errors": 0,
                      "batches": 0, "deferred": 0}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "ReflectionWorker":
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._loop, name="aiji-reflection", daemon=True,
            )
            self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _pending(self) -> list[tuple[Ident, str, int, float]]:
        """有未反思对话的会话：返回 (ident, session, 未反思条数, 最后一轮时间)。

        一条 SQL 算完（不做 N+1 查询）：`upto` 是该会话最大 turn id，
        `after` 是上一次反思的游标；差值就是"欠着的"条数。
        """
        rows = self.reflective.db.read(
            "SELECT t.platform, t.platform_user_id, t.scope, t.session,"
            " MAX(t.id) AS upto, MAX(t.ts) AS last_ts,"
            " COALESCE((SELECT r.up_to_turn FROM reflection r"
            "  WHERE r.platform=t.platform AND r.platform_user_id=t.platform_user_id"
            "  AND r.scope=t.scope AND r.session=t.session"
            "  ORDER BY r.id DESC LIMIT 1),0) AS after"
            " FROM turn t GROUP BY t.platform, t.platform_user_id, t.scope, t.session"
            " ORDER BY MAX(t.id) DESC LIMIT ?",
            (self.batch_size,),
        )
        out: list[tuple[Ident, str, int, float]] = []
        for r in rows:
            fresh = int(r["upto"] or 0) - int(r["after"] or 0)
            if fresh <= 0:
                continue
            out.append((
                Ident(r["platform"], r["platform_user_id"], scope=r["scope"]),
                str(r["session"] or "default"),
                fresh,
                float(r["last_ts"] or 0.0),
            ))
        return out

    def _should_run(self, pending: list[tuple[Ident, str, int, float]]) -> bool:
        """两个触发器（borrow-plan P0-4，来自 dsh-somni 的 dream 调度）：

        ① **积压**：至少 REFLECT_BACKLOG_SESSIONS 个会话攒够了 every_turns —— 一次批处理；
        ② **空闲**：最近一条对话已过去 REFLECT_IDLE_S（默认 15min）—— 不跟实时回复抢算力。

        原来"每个会话各自攒 30 轮"的做法在同一时间只服务一个会话，
        刷屏快的时候反而更晚才反思；批处理把 N 次调用压成"现在欠着的都跑一遍"。
        """
        if not pending:
            return False
        hot = [p for p in pending if p[2] >= self.reflective.every_turns]
        if len(hot) >= params.REFLECT_BACKLOG_SESSIONS:
            return True
        newest = max(p[3] for p in pending)
        return (time.time() - newest) >= params.REFLECT_IDLE_S

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.stats["scans"] += 1
            try:
                pending = self._pending()
                self.stats["sessions"] += len(pending)
                if not self._should_run(pending):
                    self.stats["deferred"] += 1
                else:
                    self.stats["batches"] += 1
                    # 欠得最多的先跑；每跑完一个会话检查一次停止信号（协作式让路）
                    for ident, session, _fresh, _ts in sorted(pending, key=lambda p: -p[2]):
                        if self._stop.is_set():
                            break
                        try:
                            if self.reflective.tick(ident, session) is not None:
                                self.stats["reflections"] += 1
                        except Exception:  # noqa: BLE001 - 后台反思永不拖垮进程
                            self.stats["errors"] += 1
            except Exception:  # noqa: BLE001
                self.stats["errors"] += 1
            self._stop.wait(self.interval_s)


__all__ = ["ReflectionWorker", "Reflective", "SYSTEM"]
