"""拟人化发送节奏：只给外部 IM 加首字延迟和打字停顿。"""

from __future__ import annotations

import random
import time

IM_PLATFORMS = frozenset((
    "qq", "wechat", "wecom", "douyin", "telegram", "discord", "feishu", "dingtalk",
))


class HumanPacer:
    def __init__(
        self,
        *,
        min_ms: int = 1000,
        max_ms: int = 3000,
        typing_min_ms: int = 500,
        typing_max_ms: int = 1500,
        rng: random.Random | None = None,
    ):
        self.min_ms = max(0, int(min_ms))
        self.max_ms = max(self.min_ms, int(max_ms))
        self.typing_min_ms = max(0, int(typing_min_ms))
        self.typing_max_ms = max(self.typing_min_ms, int(typing_max_ms))
        self.rng = rng or random.Random()
        self.stats = {"delayed": 0, "waited_ms": 0.0}

    def _tier_ms(self, idle_s: float, urgent: bool) -> tuple[int, int]:
        if urgent:
            return max(120, int(self.min_ms * 0.15)), max(
                180, int(self.min_ms * 0.45))
        hot_hi = max(300, int(self.min_ms * 0.9))
        normal_lo = max(hot_hi + 100, self.min_ms)
        normal_hi = max(normal_lo, self.max_ms)
        if idle_s < 180:
            return max(300, int(self.min_ms * 0.5)), hot_hi
        if idle_s < 1800:
            return normal_lo, normal_hi
        cold_lo = max(normal_hi + 400, int(self.min_ms * 2.0))
        cold_hi = max(cold_lo, int(self.max_ms * 1.5))
        return cold_lo, cold_hi

    def plan_ms(
        self,
        text: str,
        platform: str,
        *,
        energy: float = 60.0,
        idle_s: float = 600.0,
        urgent: bool = False,
    ) -> float:
        if str(platform or "").lower() not in IM_PLATFORMS:
            return 0.0
        low, high = self._tier_ms(float(idle_s), urgent)
        first = self.rng.randint(low, high)
        if energy < 30:
            first = int(first * 1.35)
        elif energy >= 85:
            first = int(first * 0.85)
        if urgent:
            first = int(first * 0.55)
        typing = min(
            self.typing_max_ms,
            self.typing_min_ms + max(0, len(text or "")) * 18,
        )
        if energy < 30:
            typing = int(typing * 1.25)
        return float(first + typing)

    def wait(
        self,
        text: str,
        platform: str,
        *,
        energy: float = 60.0,
        idle_s: float = 600.0,
        urgent: bool = False,
    ) -> float:
        delay_ms = self.plan_ms(
            text, platform, energy=energy, idle_s=idle_s, urgent=urgent)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)
            self.stats["delayed"] += 1
            self.stats["waited_ms"] += delay_ms
        return delay_ms


__all__ = ["HumanPacer", "IM_PLATFORMS"]
