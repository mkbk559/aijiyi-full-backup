"""平台适配层（2026-11-05 起）。

三平台（QQ / B站 / 抖音）× 七类动作（私聊 / 群聊 / 评论 / 刷内容 / 续火花 / 建群 / 点赞）
的**唯一入口**。能力矩阵、闸门、留痕、账本都在这里，业务侧不再自己拼接口。

用法：
    from aiji.heart.platforms import build, matrix
    adapters = build(dry_run=True)
    adapters["bilibili"].feed(5)
    adapters["qq"].dm("12345", "在吗")          # dry_run=True 时只预演
"""

from aiji.heart.platforms.base import (
    CAPABILITIES,
    Adapter,
    Guard,
    PlatformError,
    Result,
)
from aiji.heart.platforms.bilibili import BiliAdapter
from aiji.heart.platforms.douyin import DouyinAdapter, learn_blob
from aiji.heart.platforms.engine import feed_text, learn_feed, streak_sweep
from aiji.heart.platforms.qq import QQAdapter
from aiji.heart.platforms.registry import PLATFORMS, build, matrix, summary_lines
from aiji.heart.platforms.streak import StreakBook, day_of, observe_turns, plan_touches

__all__ = [
    "CAPABILITIES", "PLATFORMS", "Adapter", "BiliAdapter", "DouyinAdapter",
    "Guard", "PlatformError", "QQAdapter", "Result", "StreakBook",
    "build", "day_of", "feed_text", "learn_blob", "learn_feed", "matrix",
    "observe_turns", "plan_touches", "streak_sweep", "summary_lines",
]
