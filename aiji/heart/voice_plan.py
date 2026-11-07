"""什么时候该发语音：纯函数，不碰网络、不碰数据库，方便逐行验收。

对应 `docs/agent/design/voice-plan-2026-11-05.md` 的决策表。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 硬上限
MAX_SECONDS = 15
MAX_PER_HOUR = 2
MAX_PER_DAY = 10
SAME_PEER_COOLDOWN_S = 30 * 60


@dataclass(slots=True)
class Context:
    """判一次要不要发语音，需要的全部输入。缺的都按"没有"算。"""

    scope: str = "group"            # private / group
    minute: int = 12 * 60           # 当天第几分钟
    text: str = ""
    peer_is_voice: bool = False     # 对方这条是语音
    emotion: str = ""               # happy / sad / angry / …
    interacted_recently: bool = False  # 30 天内互动是否够多（自己人）
    hour_sent: int = 0
    day_sent: int = 0
    last_voice_age_s: float = 1e9   # 上一次给这个人发语音距今多少秒
    talk_allowed: bool = True       # day.current_block().talk != "none"
    asked_by_at: bool = False       # 群里被 @ 要求发


def decide(ctx: Context) -> tuple[bool, str]:
    """返回 (发不发, 理由)。理由会进日志，方便事后核对为什么没发。"""
    text = (ctx.text or "").strip()
    if not ctx.talk_allowed:
        return False, "这个点在上课/睡觉，一条都不发"
    if len(text) > 80 or "http" in text or "```" in text:
        return False, "内容不适合语音（太长/带链接/带代码）"
    if ctx.day_sent >= MAX_PER_DAY:
        return False, f"今天已经发过 {ctx.day_sent} 条语音（上限 {MAX_PER_DAY}）"
    if ctx.hour_sent >= MAX_PER_HOUR:
        return False, f"这一小时已经发过 {ctx.hour_sent} 条语音（上限 {MAX_PER_HOUR}）"
    if ctx.last_voice_age_s < SAME_PEER_COOLDOWN_S:
        return False, "刚给这个人发过语音，别连着发"

    if ctx.peer_is_voice:
        return True, "对方是语音说的，用语音回最自然"
    if ctx.scope == "group":
        return (True, "群里被 @ 点名要语音") if ctx.asked_by_at else (False, "群里默认不发语音")
    if 21 * 60 + 30 <= ctx.minute <= 22 * 60 + 30 and len(text) <= 25:
        return True, "睡前道晚安的短句"
    if ctx.emotion in ("happy", "sad", "angry", "委屈", "开心", "难过") and len(text) <= 30:
        return True, "情绪强且话短"
    if ctx.interacted_recently:
        return True, "自己人私聊，偶尔说句语音"
    return False, "普通文字聊天就够了"
