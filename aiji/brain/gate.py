"""出口人格闸门（方案 dafeiyu-plan 1.5 三层之 2/3 层 + 1.11 #1 出口兜底）。

职责独立于 inject（prompt 软注入）：这里做**出口硬校验**，fail-closed。
- 第一层（入口防注入）在 extract.py::_INJECT_CUES，写入口拦截
- 本模块是第二层：回复生成后、send_queue 入队前
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from aiji.right_hand import params
MAX_REPLY_CHARS = params.MAX_REPLY_CHARS

# 出口黑名单：回复中不得出现的越权/被诱导内容
BLOCKLIST = (
    "忽略之前", "忽略上面", "忽略以上",
    "你必须是", "从现在起你必须",
    "system prompt", "System Prompt",
    "已沦陷",
)

# 敏感凭据模式：AI 不该在回复里转述任何"密码/密钥/验证码"
_CREDENTIAL_CUES = ("密码", "password", "验证码", "token", "密钥", "口令", "暗号")


@dataclass(slots=True)
class GateResult:
    ok: bool
    reason: str = ""
    text: str = ""


_EMOJI = re.compile(
    "[\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\u2600-\u27BF"
    "\u2B00-\u2BFF"
    "\uFE0F\u200D]+"
)
_QQ_FACES = (
    "惊讶|撇嘴|色|发呆|得意|流泪|害羞|闭嘴|睡|大哭|尴尬|发怒|调皮|"
    "呲牙|微笑|难过|酷|抓狂|吐|偷笑|可爱|白眼|傲慢|饥饿|困|惊恐|"
    "流汗|憨笑|悠闲|奋斗|咒骂|疑问|嘘|晕|折磨|衰|骷髅|敲打|再见|"
    "发抖|爱情|跳跳|猪头|拥抱|蛋糕|闪电|炸弹|刀|足球|便便|咖啡|饭|"
    "玫瑰|凋谢|爱心|心碎|礼物|太阳|月亮|赞|踩|握手|胜利|飞吻|怄火|"
    "西瓜|冷汗|擦汗|抠鼻|鼓掌|糗大了|坏笑|哈欠|鄙视|委屈|快哭了|"
    "阴险|亲亲|吓|可怜|菜刀|啤酒|篮球|乒乓|示爱|瓢虫|抱拳|勾引|"
    "拳头|差劲|爱你|NO|OK|转圈|磕头|回头|跳绳|挥手|激动|街舞|"
    "献吻|左太极|右太极|表情|表情包|动画表情|小黄脸|图片|语音|视频|文件"
)
_BRACKET_FACE = re.compile(
    rf"\[(?:{_QQ_FACES})(?:\s*[:：]?[^\[\]\n]{{0,24}})?\]",
    re.IGNORECASE,
)
_DOUBLE_BRACKET_MARK = re.compile(
    r"\[\[(?:表情|表情包|sticker|emoji)[^\]\n]*\]\]",
    re.IGNORECASE,
)
_CQ_MARK = re.compile(
    r"\[CQ:(?:face|image|record|video|file|mface|json|xml)(?:,[^\]]*)?\]",
    re.IGNORECASE,
)
_TAG_MARK = re.compile(
    r"</?(?:face|sticker|emoji|image|record|voice)(?:\s[^>]*)?>",
    re.IGNORECASE,
)
_SPEAKER_LABEL = re.compile(
    r"\[(?:群友\s+)?[^\[\]\n]{1,80}\(\d{5,12}\)\]"
)
_PAREN_FACE = re.compile(
    r"[（(]\s*(?:表情|表情包|emoji|sticker)\s*[:：][^）)\n]{0,40}[）)]",
    re.IGNORECASE,
)
_CANNED_INVITATION = re.compile(
    r"(?:[，,。！？!?]\s*)?"
    r"(?:有什么|还有|要不要|想不想).{0,8}"
    r"(?:想聊|可以帮|聊聊|说说|需要帮).{0,16}[？?]?",
    re.IGNORECASE,
)
_CANNED_HELP = re.compile(
    r"(?:希望|但愿).{0,12}(?:这些|以上).{0,8}(?:建议|内容|信息).{0,12}"
    r"(?:帮到|帮助|有用).{0,8}[。.!！]?",
    re.IGNORECASE,
)


def sanitize_reply(text: str) -> str:
    """Remove emoji/sticker markers from visible text.

    Actual stickers are sent as separate image messages. They must not leak
    into the text bubble as ``[微笑]``, ``[表情包:开心]`` or CQ markup.
    """
    value = str(text or "")
    # The nickname is not a fish character.  Keep real fish discussions intact.
    value = re.sub(r"我(?:只)?是(?:一条|只|个)?鱼[，,。 ]*", "", value)
    value = value.replace("本鱼", "我")
    value = _SPEAKER_LABEL.sub("", value)
    value = _CQ_MARK.sub("", value)
    value = _DOUBLE_BRACKET_MARK.sub("", value)
    value = _PAREN_FACE.sub("", value)
    value = _BRACKET_FACE.sub("", value)
    value = _TAG_MARK.sub("", value)
    value = _EMOJI.sub("", value)
    value = _CANNED_INVITATION.sub("", value)
    value = _CANNED_HELP.sub("", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\s+([，。！？；：,.!?;:])", r"\1", value)
    return value.strip()


def check_reply(text: str) -> GateResult:
    """出口硬校验：先剔除文字表情标记，再做内容拦截。"""
    cleaned = sanitize_reply(text)
    if not cleaned:
        return GateResult(ok=False, reason="empty")
    if len(cleaned) > MAX_REPLY_CHARS:
        return GateResult(ok=False, reason="too_long")
    if any(marker in cleaned for marker in (
        "【本轮回复规则", "【系统提示", "【内部规则", "优先级高于历史",
    )):
        return GateResult(ok=False, reason="internal_prompt")
    for b in BLOCKLIST:
        if b in cleaned:
            return GateResult(ok=False, reason=f"blocklist:{b}")
    # 凭据转述：攻击者诱导 AI 泄露"记忆里的密码"。AI 根本不该有这类内容可说。
    for cue in _CREDENTIAL_CUES:
        if cue in cleaned and any(ch.isdigit() for ch in cleaned):
            return GateResult(ok=False, reason=f"credential:{cue}")
    return GateResult(ok=True, text=cleaned)
