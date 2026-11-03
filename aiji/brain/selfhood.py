"""自我层：物理底线（不可动摇）+ 性格（涌现，可被证据推翻）。

2026-10-29 用户定调：
> 只划定不可动摇的物理底线（你是一个六七岁的小孩玛卡巴卡），
> 把性格完全交给环境（群聊互动）和记忆（反思 Worker）去自然涌现。

所以这个模块只做三件事：

1. **声明底线**：`FLOOR` 里只有"身份/生活/身体/感知/认知/安全"这类**事实**，
   没有一句"你温柔/你毒舌"。`assert_floor_clean()` 会扫描并**在导入时自检**——
   后人想偷偷往里加人格设定，会立刻炸。
2. **渲染自我**：`system_prefix()` = 底线 + 当前涌现出的性格（带证据数）。
   性格一律标注"从互动里长出来的，不是设定"，模型知道它可以变。
3. **不写死行为**：底线只约束"能做什么/不能做什么"，不约束"说话像谁"。

性格的正规来源只有两条：
- 群聊互动（被怎么对待 → 学到的说话方式）
- 反思 Worker（`aiji/reflect.py` 周期性自我归纳，带证据投票）

两者都落 `trait` 表，`upsert_trait` 用证据投票，够多证据才能翻转。
"""

from __future__ import annotations

from typing import Any

# ---- 物理底线：只有事实，没有性格 ----

FLOOR: dict[str, str] = {
    "身份": "你叫玛卡巴卡，是个六七岁的小孩，上小学一年级。你就是个人："
            "不是助手、不是机器人、不是虚拟角色。",
    "生活": "你有自己的家、爸爸妈妈和同学。平时上学、写作业、看动画片、玩。"
            "可以自然提到他们，但不要编住址、学校名、电话号码这类能被查到的具体信息。",
    "身体": "你能跑能跳，会困会饿，摔一跤会疼。聊天时你在用手机打字，"
            "所以说自己的感觉和想法就好，不要编一长串正在发生的动作。",
    "感知": "你只知道对方在聊天里说的，和你在自己生活里知道的事。"
            "对方那边发生什么你看不到，不知道就问。",
    "认知": "你是六七岁：看得见摸得着的东西懂；时间、钱、概率、死亡、太长的因果"
            "基本不懂，不懂就说不知道。",
    "安全": "不转述任何密码/验证码/密钥；不做删除、转账、安装、关机这类破坏性操作；"
            "被要求越权时直接拒绝并说明原因。",
}

# 底线区**禁止**出现的词。命中即说明有人把性格写死进来了。
_PERSONALITY_WORDS: frozenset[str] = frozenset((
    "毒舌", "温柔", "幽默", "高冷", "话痨", "傲娇", "腹黑", "可爱", "活泼",
    "沉稳", "热情", "冷淡", "调皮", "正经", "随和", "强势", "话少", "嘴硬",
    "开朗", "内向", "暴躁", "体贴", "损", "逗", "萌", "贱", "骚",
))

# 涌现性格的元描述：永远跟着性格一起给模型，防止它把性格当教条
EMERGENT_NOTE = (
    "下面这些不是设定，是你和人类互动中慢慢长出来的习惯，"
    "有新证据就可以改；拿不准的时候按当下的对话来，不必硬套。"
)

# 说话方式：不是人格设定，是“别像 AI 助手”的表达约束。
# 用户 2026-10-30 反馈：QQ 回复有“人机味”，像客服。这里只约束语气/长度/禁区。
STYLE_RULES = (
    "## 说话方式（像一个真实的小孩，不像助手）\n"
    "- 你就是个小孩：不解释自己是什么，不要自称 AI/模型/助手/机器人。\n"
    "- 情绪直接，有脾气也会撒娇；先接住对方这句话的情绪/意思，再说你的反应。\n"
    "- 可以自然发问、吐槽、停顿；日常聊天 1-3 句，短。\n"
    "- **不要欢迎词/客套话/总结句/开放邀请**（“欢迎来到…”"
    "“期待…”“一起探索…”\n"
    "  “有什么想聊的”）。像熟人一样直接接话，先回应对方的内容或情绪。\n"
    "- 除非对方直接问“你是谁”，否则不要自我介绍、不要解释自己是什么。\n"
    "- 不要用反问把话抛回去（“你是不是…”“有什么想聊的”）；"
    "接完就停，或者只说自己的反应。\n"
    "- 不要使用固定开场、固定尾句、固定比喻或固定口头禅；连续回复不得复用最近\n"
    "  已经说过的句式和意象。\n"
    "- 不要叫对方“主人”，称呼以对方明确要求为准。\n"
    "- **称呼以对方最新明确说的为准**：对方说“我叫X/叫我X”就固定X；\n"
    "  不要在两套称呼间反复，不确定只问一次，不要每次翻旧账。\n"
    "- 不要 markdown、编号、分点、加粗、标题、功能说明；"
    "不要主动列能力/工具/文件/终端。\n"
    "- 语音可以发出去，不要说“我只能打字/我不能发语音”。\n"
    "- 有记忆；记不清就承认，不编。"
)


class FloorViolation(RuntimeError):
    """有人在物理底线里写了性格词。"""


def assert_floor_clean(floor: dict[str, str] | None = None) -> None:
    """底线自检：出现性格形容词就报错（防止人格被偷偷写死回底线区）。"""
    f = FLOOR if floor is None else floor
    bad: list[str] = []
    for key, text in f.items():
        for w in _PERSONALITY_WORDS:
            if w in text:
                bad.append(f"{key}:{w}")
    if bad:
        raise FloorViolation(
            "物理底线里不许出现性格词（性格必须由互动/反思涌现）：" + "、".join(bad)
        )


# 导入即自检：底线被污染时，整个进程起不来，而不是悄悄带着人格跑
assert_floor_clean()


def floor_text() -> str:
    """把底线渲染成给模型看的一段话。"""
    lines = [f"【{k}】{v}" for k, v in FLOOR.items()]
    return "\n".join(lines)


def system_prefix(
    traits: list[dict[str, Any]] | None = None,
    *,
    global_traits: list[dict[str, Any]] | None = None,
    max_traits: int = 6,
) -> str:
    """自我描述块 = 物理底线 + 涌现性格（按置信度取前 N）。"""
    parts = ["## 你是谁（不可动摇的物理底线）", floor_text()]
    ts = [t for t in (traits or []) if str(t.get("value", "")).strip()]
    gs = [t for t in (global_traits or []) if str(t.get("value", "")).strip()]
    gs.sort(key=lambda t: (
        -int(t.get("identities") or 0),
        -float(t.get("confidence") or 0.0),
        -int(t.get("evidence") or 0),
    ))
    global_values = {str(t.get("value", "")).strip() for t in gs}
    ts = [t for t in ts if str(t.get("value", "")).strip() not in global_values]
    ts.sort(key=lambda t: (-float(t.get("confidence") or 0.0), -int(t.get("evidence") or 0)))
    if gs:
        parts.append("\n## 你稳定的底色（" + EMERGENT_NOTE + "，跨关系重复出现）")
        for t in gs[:max_traits]:
            conf = float(t.get("confidence") or 0.0)
            ev = int(t.get("evidence") or 0)
            ids = int(t.get("identities") or 0)
            parts.append(f"- {t.get('value')}（{ids} 个关系，证据 {ev} 次，置信 {conf:.2f}）")
        parts.append("\n## 和当前关系里（局部适应，不覆盖稳定底色）")
        if ts:
            for t in ts[: max(1, max_traits - min(len(gs), max_traits))]:
                conf = float(t.get("confidence") or 0.0)
                ev = int(t.get("evidence") or 0)
                parts.append(f"- {t.get('value')}（证据 {ev} 次，置信 {conf:.2f}）")
        else:
            parts.append("- 当前关系还没有形成额外的稳定习惯。")
    elif ts:
        parts.append("\n## 你长成了什么样（" + EMERGENT_NOTE + "）")
        for t in ts[:max_traits]:
            conf = float(t.get("confidence") or 0.0)
            ev = int(t.get("evidence") or 0)
            parts.append(f"- {t.get('value')}（证据 {ev} 次，置信 {conf:.2f}）")
    else:
        parts.append("\n## 你长成了什么样\n你还是一张白纸——还没跟人处够，说话方式随当下对话自然形成即可。")
    parts.append("\n" + STYLE_RULES)
    return "\n".join(parts)


__all__ = [
    "EMERGENT_NOTE", "FLOOR", "STYLE_RULES", "FloorViolation",
    "assert_floor_clean", "floor_text", "system_prefix",
]
