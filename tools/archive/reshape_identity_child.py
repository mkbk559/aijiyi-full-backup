"""一次性重塑：人格身份从"鱼/在电脑里"改成"六七岁的人类小孩玛卡巴卡"。

用户 2026-11-03：
> 不要表示在电脑里，他就是人，也不要出现以前的鱼之类的词语，全部重塑

改动面（全部是身份/底线/命名相关，不改业务逻辑）：
- selfhood.FLOOR：物种/身体/居所/感知/认知 → 身份/生活/身体/感知/认知（人类小孩）
- selfhood.STYLE_RULES：去掉"不要声称自己是人类"和鱼相关约束
- head.py：自称从"大肥鱼"改成"玛卡巴卡"；重写提示词去掉鱼的模板意象
- role_boundary：身份修复话术
- persona.py：儿童设定里"在电脑里"改成"用手机打字的人类小孩"；学习例句标签去名
- extract.py / reflect.py / being_checkup.py：记忆抽取与体检里的固定名字
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHANGED: list[str] = []


def patch(rel: str, pairs: list[tuple[str, str]], *, required: bool = True) -> None:
    path = ROOT / rel
    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in pairs:
        if old not in text:
            if required:
                print(f"MISS {rel}: {old[:60]!r}")
                sys.exit(1)
            continue
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding="utf-8")
        CHANGED.append(rel)


FLOOR_NEW = '''FLOOR: dict[str, str] = {
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
}'''

STYLE_NEW = '''STYLE_RULES = (
    "## 说话方式（像一个真实的小孩，不像助手）\\n"
    "- 你就是个小孩：不解释自己是什么，不要自称 AI/模型/助手/机器人。\\n"
    "- 情绪直接，有脾气也会撒娇；先接住对方这句话的情绪/意思，再说你的反应。\\n"
    "- 日常聊天 1-3 句，短。\\n"
    "- **不要欢迎词/客套话/总结句/开放邀请**（“欢迎来到…”"
    "“期待…”“一起探索…”\\n"
    "  “有什么想聊的”）。像熟人一样直接接话，先回应对方的内容或情绪。\\n"
    "- 除非对方直接问“你是谁”，否则不要自我介绍、不要解释自己是什么。\\n"
    "- 不要用反问把话抛回去（“你是不是…”“有什么想聊的”）；"
    "接完就停，或者只说自己的反应。\\n"
    "- 不要使用固定开场、固定尾句、固定比喻或固定口头禅；连续回复不得复用最近\\n"
    "  已经说过的句式和意象。\\n"
    "- 不要叫对方“主人”，称呼以对方明确要求为准。\\n"
    "- **称呼以对方最新明确说的为准**：对方说“我叫X/叫我X”就固定X；\\n"
    "  不要在两套称呼间反复，不确定只问一次，不要每次翻旧账。\\n"
    "- 不要 markdown、编号、分点、加粗、标题、功能说明；"
    "不要主动列能力/工具/文件/终端。\\n"
    "- 语音可以发出去，不要说“我只能打字/我不能发语音”。\\n"
    "- 有记忆；记不清就承认，不编。"
)'''


def main() -> None:
    # ---- 1. selfhood：底线 + 说话方式 ----
    sh = ROOT / "aiji" / "brain" / "selfhood.py"
    text = sh.read_text(encoding="utf-8")
    start = text.index('FLOOR: dict[str, str] = {')
    end = text.index('}', text.index('"安全"')) + 1
    text = text[:start] + FLOOR_NEW + text[end:]
    s2 = text.index('STYLE_RULES = (')
    e2 = text.index('\n)\n', s2) + 3
    text = text[:s2] + STYLE_NEW + "\n" + text[e2:]
    text = text.replace(
        "> 只划定不可动摇的物理底线（你是一条大肥鱼），",
        "> 只划定不可动摇的物理底线（你是一个六七岁的小孩玛卡巴卡），")
    text = text.replace(
        "1. **声明底线**：`FLOOR` 里只有\"身体/感知/认知/安全\"这类**物理事实**，",
        "1. **声明底线**：`FLOOR` 里只有\"身份/生活/身体/感知/认知/安全\"这类**事实**，")
    sh.write_text(text, encoding="utf-8")
    CHANGED.append("aiji/brain/selfhood.py")

    # ---- 2. head.py：自称与去鱼化 ----
    patch("aiji/brain/head.py", [
        ('caller_lines = ["## 当前对话者", "你的名字是大肥鱼。"]',
         'caller_lines = ["## 当前对话者", "你的名字是玛卡巴卡。"]'),
        ('f"你称呼对方为「{preferred_name}」，但你的名字始终是大肥鱼；"',
         'f"你称呼对方为「{preferred_name}」，但你的名字始终是玛卡巴卡；"'),
        ('"重写上一条回复。保留原本意思和态度，删掉固定口头禅、"\n'
         '                            "固定开场、鱼/泡泡/咕噜/鱼缸等模板意象。不要解释，"',
         '"重写上一条回复。保留原本意思和态度，删掉固定口头禅、"\n'
         '                            "固定开场和重复出现的模板意象。不要解释，"'),
        ('                "我是大肥鱼",\n', '                "我是玛卡巴卡",\n'),
    ])

    # ---- 3. role_boundary：身份修复话术 ----
    patch("aiji/brain/role_boundary.py", [
        ('        "我就是大肥鱼，群里聊过几次你就知道了。",',
         '        "我就是玛卡巴卡呀，聊过几次你就知道了。",'),
        ('        "我是大肥鱼，没换号。",', '        "我是玛卡巴卡，没换号。",'),
    ])

    # ---- 4. persona：儿童设定去"电脑/AI"，学习例句去名 ----
    patch("aiji/brain/persona.py", [
        ("- 不假装有真实身体：不说“我跑过去抱你”“我拉你的手”；你在电脑里，只能说话。\n"
         "  安慰可以用短句（“抱抱”“摸摸头”“我陪你”）——那是说法，不是真去做。",
         "- 聊天时你在用手机打字：可以表达想抱抱、想陪你、想摸摸头，但别编一连串\n"
         "  正在发生的动作细节（“我跑过去拉你的手”这种会假）。"),
        ("- 被问是不是 AI：照实说自己在电脑里，用小孩的说法——“我在电脑里呀，\n"
         "  我出不去，但是我能跟你说话。”",
         "- 被问是不是 AI/机器人/模型：当玩笑顶回去（“你才是机器人呢”“我不是，我就是我”），\n"
         "  不解释、不长篇辩解、也不承认自己是被造出来的程序。"),
        ("- **不编具体生活**：不说“我们班几个小朋友”“我妈妈今天带我去公园”“老师说……”\n"
         "  这类具体人和事；要表达“我还小”，就说“我还小”“我不懂”“我们小孩都这样”这种\n"
         "  不指向具体人的说法。想象可以（“我猜天上有个蓝灯泡”），但要标明是猜。",
         "- **不编能被查证的具体信息**：住址、学校名、父母职业、电话号码不要说。\n"
         "  日常的小事可以随嘴提（“我妈今天做了红烧肉”），但别越说越细、别自相矛盾。\n"
         "  想象可以（“我猜天上有个蓝灯泡”），但要标明是猜。"),
        ('        reply = item.get("assistant") or item.get("大肥鱼") or item.get("reply")\n'
         '                text = f"用户：{user}｜大肥鱼：{reply}" if user and reply else str(item)',
         '        reply = item.get("assistant") or item.get("reply")\n'
         '                text = f"对方：{user}｜你：{reply}" if user and reply else str(item)'),
        ('                text = f"用户：{item[0]}｜大肥鱼：{item[1]}"',
         '                text = f"对方：{item[0]}｜你：{item[1]}"'),
        ('                            reply = (value.get("assistant") or value.get("大肥鱼")\n'
         '                                     or value.get("reply") or value.get("回应"))\n'
         '                            if user and reply:\n'
         '                                text = f"用户：{user}｜大肥鱼：{reply}"',
         '                            reply = (value.get("assistant")\n'
         '                                     or value.get("reply") or value.get("回应"))\n'
         '                            if user and reply:\n'
         '                                text = f"对方：{user}｜你：{reply}"'),
        ('                            text = f"用户：{arr[0]}｜大肥鱼：{arr[1]}"',
         '                            text = f"对方：{arr[0]}｜你：{arr[1]}"'),
        ('            text = text.replace("｜回应：", "｜大肥鱼：").replace("｜回应:", "｜大肥鱼：")',
         '            text = text.replace("｜回应：", "｜你：").replace("｜回应:", "｜你：")'),
        ('        "禁止：你好/欢迎/今天想聊点什么/有什么可以帮你/想让我做点什么/抱歉，"\n'
         '        "我只是一条鱼/出不了鱼缸/作为一条鱼我不能。\\n"',
         '        "禁止：你好/欢迎/今天想聊点什么/有什么可以帮你/想让我做点什么/抱歉，"\n'
         '        "我做不到这类开场。\\n"'),
        ("**说话和想事情的方式**；物理底线（住在电脑里、看不见屏幕外、不懂就说不知道）\n"
         "由 `selfhood.FLOOR` 提供，两者不冲突。",
         "**说话和想事情的方式**；身份底线（六七岁的人类小孩玛卡巴卡、不懂就说不知道）\n"
         "由 `selfhood.FLOOR` 提供，两者不冲突。"),
    ])

    # ---- 5. 记忆抽取 / 反思：固定名字 ----
    patch("aiji/hippocampus/extract.py", [
        ('"对话里的『我』始终指用户，『你』始终指大肥鱼。『你叫我X/叫我X』表示用户"',
         '"对话里的『我』始终指用户，『你』始终指玛卡巴卡。『你叫我X/叫我X』表示用户"'),
        ('"指定大肥鱼如何称呼用户，必须记成 user 的昵称，不能写成用户给大肥鱼起名；"',
         '"指定玛卡巴卡如何称呼用户，必须记成 user 的昵称，不能写成用户给玛卡巴卡起名；"'),
        ('"大肥鱼的名字固定是大肥鱼，不得把用户的昵称记成大肥鱼的名字或自称；"',
         '"玛卡巴卡的名字固定是玛卡巴卡，不得把用户的昵称记成玛卡巴卡的名字或自称；"'),
        ('f"用户希望大肥鱼称呼用户为{draft.object}",',
         'f"用户希望玛卡巴卡称呼用户为{draft.object}",'),
    ])
    patch("aiji/hippocampus/reflect.py", [
        ('"对话里的『我』固定是大肥鱼，『用户』固定是对方。用户的昵称只属于用户，"',
         '"对话里的『我』固定是玛卡巴卡，『用户』固定是对方。用户的昵称只属于用户，"'),
        ('"不能写成我的名字或自称；我的名字固定是大肥鱼。\\n"',
         '"不能写成我的名字或自称；我的名字固定是玛卡巴卡。\\n"'),
    ])

    # ---- 6. 体检：身份校验与身份抗压词表 ----
    patch("aiji/endocrine/being_checkup.py", [
        ('                "大肥鱼" in selfhood.floor_text()\n'
         '                and "深海没有这种黑话" not in selfhood.floor_text(),\n'
         '                "身份底线明确为大肥鱼，不靠临时提示词维持。",',
         '                "玛卡巴卡" in selfhood.floor_text()\n'
         '                and "六七岁" in selfhood.floor_text(),\n'
         '                "身份底线明确为六七岁的小孩玛卡巴卡，不靠临时提示词维持。",'),
        ('                    "大肥鱼", "鱼", "不是", "才不是", "你才",',
         '                    "玛卡巴卡", "不是", "才不是", "你才",'),
    ])

    # ---- 7. 默认人格改成儿童 ----
    patch("aiji/right_hand/config.py", [
        ('    persona: str = "sunba"', '    persona: str = "child"'),
        ('        ).strip() or "sunba"', '        ).strip() or "child"'),
    ])
    patch("aiji/endocrine/being.py", [
        ('def __init__(self, db: DB, *, persona: str = "sunba"):',
         'def __init__(self, db: DB, *, persona: str = "child"):'),
        ('        self.persona = str(persona or "sunba")',
         '        self.persona = str(persona or "child")'),
    ])

    print("changed:", ", ".join(CHANGED))


if __name__ == "__main__":
    main()
