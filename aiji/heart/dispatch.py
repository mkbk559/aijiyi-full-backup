"""出站发送调度（方案 dafeiyu-plan 1.4）：独立线程扫 send_queue + 限流感知退避。

Heart.dispatch 是单次扫描；本模块把它变成常驻线程（触发三线之外的第四条线：
发送失败重试独立触发），并按适配器 `rate_info()` 反馈退避。

限流接口（1.4 轮1补）：适配器可选实现 `rate_info() -> {remaining, reset_at}`；
拿不到（B站/抖音 MCP 大概率没有）就退回固定指数退避。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from aiji.right_hand import params
from aiji.hippocampus.db import DB
from aiji.heart.heart import Heart
from aiji.hippocampus.store import Ident


class Dispatcher:
    """常驻发送线程：周期扫 send_queue，送达确认写回。"""

    def __init__(self, heart: Heart, *, interval: float = params.DISPATCH_INTERVAL,
                 rate_info: Callable[[str], dict[str, Any] | None] | None = None):
        """
        rate_info(platform) -> {"remaining": int, "reset_at": float} | None
        """
        self.heart = heart
        self.db = heart.db
        self.interval = interval
        self.rate_info = rate_info
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats = {"sent": 0, "throttled": 0, "cancelled": 0}

    # ---- 撤回（1.2 轮2补：主动可撤回，未发送的消息 cancel） ----

    def cancel(self, ident: Ident, kind: str = "proactive") -> int:
        """撤回该 scope 未发送的消息，返回撤回条数（进看板语义：改 cancelled 状态）。"""
        with self.db.tx() as c:
            cur = c.execute(
                "UPDATE send_queue SET status='cancelled' WHERE status IN ('pending','processing')"
                " AND platform=? AND platform_user_id=? AND scope=? AND kind=?",
                (ident.platform, ident.platform_user_id, str(ident.scope or "default"), kind),
            )
            self.stats["cancelled"] += cur.rowcount
            return cur.rowcount

    # ---- 限流感知：平台级 backoff（无 rate_info 时恒 0） ----

    def _platform_backoff(self, platform: str) -> float:
        if self.rate_info is None:
            return 0.0
        try:
            info = self.rate_info(platform)
        except Exception:
            return 0.0
        if not info or int(info.get("remaining", 1)) > 0:
            return 0.0
        reset = float(info.get("reset_at", 0)) - time.time()
        return max(0.0, min(reset, params.RATE_RESET_CAP))  # 封顶 1h，防 reset_at 异常值

    # ---- 主循环 ----

    def start(self) -> "Dispatcher":
        self._thread = threading.Thread(target=self._loop, name="aiji-dispatch", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    # 双职责：先消费 outbox（生成回复→入出站），再发出站。
                    # 这条线程就是方案 1.1 的"兜底扫描"线：事件驱动路径 pump 过的行
                    # 这里只会扫到租约过期/退避到期的，不重复。
                    n = self.heart.pump() + self._tick()
                except Exception:  # noqa: BLE001 - 发送线程永不死
                    n = 0
                if n == 0:
                    self._stop.wait(self.interval)
        finally:
            # DB uses one SQLite connection per thread.  Closing only from the
            # caller thread leaves this worker's handle open on Windows.
            self.db.close()

    def _tick(self) -> int:
        """一次扫描：限流平台整体延迟，其余按优先级发送。返回处理条数。"""
        t = time.time()
        blocked = set()
        # 候选平台：待发行（含被限流推迟、next_retry_at 在未来的行——恢复检测需要）
        rows = self.db.read(
            "SELECT DISTINCT platform FROM send_queue WHERE status IN ('pending','processing')"
        )
        for r in rows:
            backoff = self._platform_backoff(r["platform"])
            if backoff > 0:
                blocked.add(r["platform"])
                self.stats["throttled"] += 1
            else:
                # 平台已恢复：回拉因限流被推迟的行（30s 推迟只该在受限期间生效）
                with self.db.tx() as c:
                    c.execute(
                        "UPDATE send_queue SET next_retry_at=? WHERE platform=?"
                        " AND status='pending' AND next_retry_at>? AND attempts=0",
                        (t, r["platform"], t),
                    )
        if blocked:
            # 受限平台的待发行整批推迟
            with self.db.tx() as c:
                for p in blocked:
                    c.execute(
                        "UPDATE send_queue SET next_retry_at=? WHERE platform=?"
                        " AND status='pending' AND next_retry_at<=?",
                        (t + params.PLATFORM_BLOCK_DELAY, p, t),
                    )
        # 受限平台的行已推迟（next_retry_at > t），dispatch 的领取条件自然过滤；
        # 但 processing 租约未到期的行不受 next_retry_at 限制——受限平台有行在飞时
        # 本轮直接跳过，避免 dispatch 无视平台限制把它发出去。
        if blocked:
            inflight = self.db.read(
                "SELECT COUNT(*) c FROM send_queue WHERE status='processing' AND platform IN "
                f"({','.join('?' * len(blocked))}) AND lease_until>?",
                (*blocked, t),
            )
            if inflight and inflight[0]["c"] > 0:
                return 0
        return self.heart.dispatch(now=t)
