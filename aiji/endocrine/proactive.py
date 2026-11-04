"""主动搭话（方案 dafeiyu-plan 1.6 + 1.2 轮2补额度语义定稿）。

检查顺序（1.6）：静默 → 冷却 → 额度（预扣）→ 抢占（@在场取消）→ 生成 → 发送。
计数语义（定稿）：**预扣 + 失败退还**——触发即预扣，发送失败/被拦截时退还。
滚动 24h 额度用 persistent 键存最近 N 次送达时间戳；防 NTP 回拨在比较逻辑里做
（时间戳倒退时取 max）。
"""

from __future__ import annotations

import time
from typing import Callable

from aiji.right_hand import params
from aiji.hippocampus.db import DB
from aiji.brain.gate import check_reply, fit_reply, reply_char_limit
from aiji.heart.heart import Heart, OutboxEvent, PRI_PROACTIVE
from aiji.hippocampus.store import Ident


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


class Proactive:
    """沉默计时 + 额度预扣/退还 + 指数退避。由外部定时器周期调 `tick()`。"""

    def __init__(self, heart: Heart, handler: Callable[[Ident], str | None],
                 *, silence_seconds: float = params.PROACTIVE_SILENCE,
                 quota_24h: int = params.PROACTIVE_QUOTA_24H,
                 cooldown_seconds: float = params.PROACTIVE_COOLDOWN,
                 quota_provider: Callable[[], int] | None = None):
        self.heart = heart
        self.db: DB = heart.db
        self.handler = handler          # (ident) -> 想说的话（None = 放弃）
        self.silence = silence_seconds  # scope 静默多久才主动
        self.quota = quota_24h          # 滚动 24h 最多几次（quota_provider 缺席时的硬上限）
        # 动态配额：配额来自反代剩余积分（aiji/quota.py）。返回 None 表示"取不到"，按硬上限走。
        self.quota_provider = quota_provider
        self.cooldown = cooldown_seconds
        self.stats = {"fired": 0, "refunded": 0, "blocked_quota": 0,
                      "blocked_silence": 0, "blocked_cooldown": 0, "preempted": 0,
                      "enqueue_failed": 0, "handler_failed": 0}

    # ---- heart_state 键 ----
    # last_event_ts   persistent：该 scope 最后一次任意事件（判断静默）
    # recent_send_ts  persistent：滚动窗口时间戳列表（额度）
    # cooldown_until  persistent：退避下限
    # last_send_ok    day-scoped：当日成功次数（看板用）

    def note_event(self, ident: Ident, *, now: float | None = None) -> None:
        """任意事件到达都重置静默计时（含主动发送成功，1.6 轮1补）。"""
        t = time.time() if now is None else now
        last = self.heart.state_get(ident, "last_event_ts")
        # 防 NTP 回拨：倒退时取 max，时间戳不回退
        self.heart.state_set(ident, "last_event_ts", max(t, float(last or 0.0)))

    def _silence_ok(self, ident: Ident, t: float) -> bool:
        last = self.heart.state_get(ident, "last_event_ts")
        if not last:
            return False  # 从没互动过的 scope 不主动搭话
        return t - float(last) >= self.silence

    def _cooldown_ok(self, ident: Ident, t: float) -> bool:
        until = self.heart.state_get(ident, "cooldown_until")
        return not until or t >= float(until)

    def _cap(self) -> int:
        """当前生效的 24h 上限：优先问积分（quota.py），取不到就退回构造时的硬上限。"""
        if self.quota_provider is not None:
            try:
                got = self.quota_provider()
            except Exception:  # noqa: BLE001 - 配额源坏了不能挡住主动链路
                got = None
            if got is not None:
                return max(0, min(int(self.quota), int(got)))
        return self.quota

    def _quota_ok(self, ident: Ident, t: float) -> bool:
        cap = self._cap()
        if cap <= 0:
            return False
        ts = self.heart.state_get(ident, "recent_send_ts") or []
        window = [float(x) for x in ts if t - float(x) < 86400.0]
        return len(window) < cap

    def _quota_precharge(self, ident: Ident, t: float) -> list[float]:
        """预扣：把 t 压进窗口（失败时用返回的旧值退还）。"""
        ts = [float(x) for x in (self.heart.state_get(ident, "recent_send_ts") or [])]
        window = [x for x in ts if t - x < 86400.0]
        window.append(t)
        self.heart.state_set(ident, "recent_send_ts", window[-8:])
        return ts  # 返回预扣前的旧值，供退还

    def _quota_refund(self, ident: Ident, old: list[float]) -> None:
        """退还：恢复预扣前的窗口。"""
        self.heart.state_set(ident, "recent_send_ts", old)
        self.stats["refunded"] += 1

    def _backoff(self, ident: Ident, t: float, *, reset: bool = False) -> None:
        """无人回应指数退避；reset=True（有人回应）时清零。"""
        if reset:
            self.heart.state_set(ident, "cooldown_until", 0.0)
            return
        key = "backoff_n"
        n = int(self.heart.state_get(ident, key) or 0)
        n = min(n + 1, 6)
        self.heart.state_set(ident, key, n)
        self.heart.state_set(ident, "cooldown_until", t + self.cooldown * (2 ** (n - 1)))

    def tick(self, idents: list[Ident], *, now: float | None = None,
             preempted: Callable[[Ident], bool] | None = None) -> int:
        """对候选 scope 逐个检查，触发主动搭话。返回触发数。

        preempted(ident)：@在场/他人正在聊 → 取消本次主动（1.6 抢占检查）。
        """
        t = time.time() if now is None else now
        fired = 0
        for ident in idents:
            if not self._silence_ok(ident, t):
                self.stats["blocked_silence"] += 1
                continue
            if not self._cooldown_ok(ident, t):
                self.stats["blocked_cooldown"] += 1
                continue
            if not self._quota_ok(ident, t):
                self.stats["blocked_quota"] += 1
                continue
            if preempted is not None and preempted(ident):
                self.stats["preempted"] += 1
                continue

            # 额度预扣（定稿语义）
            old_window = self._quota_precharge(ident, t)

            text = None
            try:
                text = self.handler(ident)
            except Exception:
                # handler 崩溃不能和"没话说"混在一起：排查黑洞（第 28 轮审计）
                text = None
                self.stats["handler_failed"] += 1
                import traceback
                print("[proactive] handler 失败:", traceback.format_exc(limit=2),
                      flush=True)
            gate = check_reply(fit_reply(text, reply_char_limit(ident.platform))) if text else None
            if not text or gate is None or not gate.ok:
                self._quota_refund(ident, old_window)  # 拦截/放弃 → 退还
                self._backoff(ident, t)
                continue
            text = gate.text

            # 走标准链路：outbox（低优先级事件）→ pump → gate → send_queue
            try:
                queued = self.heart.enqueue(ident, OutboxEvent(
                    kind="proactive", text=text, priority=PRI_PROACTIVE,
                    extra={"proactive": True}), now=t)
            except Exception:
                self.stats["enqueue_failed"] += 1
                self._quota_refund(ident, old_window)
                self._backoff(ident, t)
                continue
            if queued is None:
                self.stats["enqueue_failed"] += 1
                self._quota_refund(ident, old_window)
                self._backoff(ident, t)
                continue
            fired += 1
            self.stats["fired"] += 1
            # 主动发送算事件：重置沉默计时（1.6 轮1补）
            self.note_event(ident, now=t)
        return fired
