"""反思层：把一段文本压成 事实 / 知识 / 关系 / 画像 / 风格 / 摘要。

优先走模型池（免费模型，在 worker 线程里跑）；池子全挂时走规则兜底，
保证"写进来的东西至少不会凭空消失"。识别层的 OCR/ASR 结果也进这里——
**同一个函数，不按输入类型分叉**。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from aiji.hippocampus.store import FactDraft

SYSTEM = (
    "你是记忆抽取器。从用户对话中抽取可长期复用的记忆。"
    "只输出一个 JSON 对象，不要代码块、不要解释。格式："
    '{"facts":[{"kind":"fact|knowledge|relation","subject":"user","predicate":"居住地",'
    '"object":"杭州","text":"用户住在杭州","confidence":0.9}],'
    '"profile":{"居住地":"杭州"},"relations":[{"subject":"张伟","predicate":"同事","object":"user"}],'
    '"style":{"语气":"口语化"},"summary":"一句话摘要"}。'
    "规则：subject 默认 user；kind 取 fact(用户自身属性)/knowledge(客观常识或第三方信息)/"
    "relation(人与人的关系)；predicate 用简短中文名词；只抽明说的内容，不臆测；"
    "对话里的『我』始终指用户，『你』始终指玛卡巴卡。『你叫我X/叫我X』表示用户"
    "指定玛卡巴卡如何称呼用户，必须记成 user 的昵称，不能写成用户给玛卡巴卡起名；"
    "玛卡巴卡的名字固定是玛卡巴卡，不得把用户的昵称记成玛卡巴卡的名字或自称；"
    "同一属性可以有多个并列值（例如多个偏好）时要输出多条，不要合并成一条；"
    "用户明确否定/取消的内容，在对应 fact 上加 \"negated\": true，object 填被否定的对象；"
    "style 只描述表达方式，不要描述人格或身份；维度名固定用：语气（口语化/正式/中性）、"
    "长度（简短/适中/详细）、表情（喜欢用/偶尔/不用）、口癖（用户高频口头禅，值填词本身）；"
    "没有可记忆内容就输出 {\"facts\":[],\"profile\":{}}。"
)

SUMMARY_SYSTEM = (
    "你是会话摘要器。把给出的用户发言压成一段 200 字以内的中文摘要，"
    "保留人物、地点、事件、承诺与明确偏好，不要编造。"
    "只输出 JSON：{\"summary\":\"...\"}，不要代码块、不要解释。"
)


def build_summary_messages(lines: list[str]) -> list[dict]:
    body = "\n".join(f"- {t}" for t in lines[-60:])
    return [
        {"role": "system", "content": SUMMARY_SYSTEM},
        {"role": "user", "content": body[:4000]},
    ]


@dataclass(slots=True)
class Extraction:
    facts: list[FactDraft] = field(default_factory=list)
    profile: dict[str, str] = field(default_factory=dict)
    style: dict[str, str] = field(default_factory=dict)
    summary: str = ""
    source_kind: str = "model"
    raw: str = ""


def build_messages(text: str, session: str = "") -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": (f"[session={session}]\n" if session else "") + text[:4000]},
    ]


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_KINDS = ("fact", "knowledge", "relation")
_QUESTION_OR_REQUEST = (
    "?", "？", "吗", "呢", "什么", "怎么", "如何", "哪", "谁", "多少",
    "是不是", "能不能", "可不可以", "介绍你自己", "发条语音", "说给我听",
)
_NON_MEMORY_PREDICATES = {"介绍自己", "请求内容", "意图", "指令"}
_NICKNAME_PREDICATES = {"昵称", "称呼", "名字"}
_CALL_USER = re.compile(
    r"叫我(?:做|为)?\s*([^\s，。！？、；：,.!?]{1,20}?)"
    r"(?=吧|啊|呀|哈|嘛|哦|咯|嘞|$|[，。！？、；：,.!?])"
)


def _kind_of(raw: Any, subject: str) -> str:
    k = str(raw or "").strip().lower()
    if k in _KINDS:
        return k
    return "fact" if subject in ("user", "用户", "") else "knowledge"


def _looks_like_question_or_request(text: str) -> bool:
    t = (text or "").strip()
    return any(marker in t for marker in _QUESTION_OR_REQUEST)


def _looks_like_model_artifact(source_text: str, draft: FactDraft) -> bool:
    src = (source_text or "").strip()
    pred = draft.predicate.strip().lower()
    obj = draft.object.strip().lower()
    text = draft.text.strip()
    if pred in _NON_MEMORY_PREDICATES:
        return True
    if "用户请求" in text or "用户在对话中询问" in text:
        return True
    src_lower = src.lower()
    if (
        ("你是不是ai" in src_lower or "你是ai吗" in src_lower or "你是不是人工智能" in src_lower)
        and pred in ("身份", "属性")
        and obj in ("ai", "人工智能")
    ):
        return True
    return False


def _normalize_nickname_fact(source_text: str, draft: FactDraft) -> FactDraft:
    """Resolve "call me X" from the user's perspective, never the bot's."""
    if draft.predicate.strip() not in _NICKNAME_PREDICATES:
        return draft
    match = _CALL_USER.search(source_text or "")
    if not match:
        return draft
    requested = match.group(1).strip()
    if not requested or not draft.object.startswith(requested):
        return draft
    return FactDraft(
        "user",
        "昵称",
        draft.object,
        f"用户希望玛卡巴卡称呼用户为{draft.object}",
        draft.confidence,
        kind="fact",
        extra=dict(draft.extra),
    )


def parse(raw: str, *, source_text: str = "") -> Extraction:
    s = (raw or "").strip()
    m = _FENCE.search(s)
    if m:
        s = m.group(1).strip()
    if not s.startswith("{"):
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            s = s[i : j + 1]
    data = json.loads(s)
    facts: list[FactDraft] = []
    for f in data.get("facts") or []:
        if not isinstance(f, dict):
            continue
        subject = str(f.get("subject") or "user")
        predicate = str(f.get("predicate") or "").strip()
        obj = str(f.get("object") or "").strip()
        if not predicate or not obj:
            continue
        text = str(f.get("text") or f"用户{predicate}：{obj}")
        # 模型抽取结果同样过黑名单（模型可能被注入文本本身误导）
        if _looks_like_injection(obj) or _looks_like_injection(text):
            continue
        try:
            conf = float(f.get("confidence", 0.8))
        except (TypeError, ValueError):
            conf = 0.8
        extra = {"negated": True} if f.get("negated") is True else {}
        draft = FactDraft(subject, predicate, obj, text,
                          max(0.1, min(1.0, conf)), kind=_kind_of(f.get("kind"), subject),
                          extra=extra)
        draft = _normalize_nickname_fact(source_text, draft)
        if _looks_like_model_artifact(source_text, draft):
            continue
        facts.append(draft)
    for r in data.get("relations") or []:
        if not isinstance(r, dict):
            continue
        a = str(r.get("subject") or "").strip()
        label = str(r.get("predicate") or r.get("label") or "").strip()
        b = str(r.get("object") or "user").strip()
        if not a or not label:
            continue
        facts.append(FactDraft(a, label, b, f"{a}是{b}的{label}", 0.75, kind="relation"))
    profile: dict[str, str] = {}
    for k, v in (data.get("profile") or {}).items():
        if isinstance(v, (str, int, float)) and str(v).strip():
            profile[str(k).strip()[:32]] = str(v).strip()[:200]
    style: dict[str, str] = {}
    for k, v in (data.get("style") or {}).items():
        if isinstance(v, (str, int, float)) and str(v).strip():
            d = _STYLE_DIM_ALIASES.get(str(k).strip(), str(k).strip())
            if d not in _STYLE_DIMS:
                continue  # 未识别维度：丢弃，防同义分裂（"用词"≠新维度）
            style[d] = str(v).strip()[:32]
    summary = str(data.get("summary") or "").strip()[:400]
    return Extraction(facts=facts, profile=profile, style=style, summary=summary,
                      source_kind="model", raw=raw[:2000])


# ---- 本地规则兜底（模型池全挂 / 离线） ----

_TAIL = "，。！？、；：,.!?;: \t\n\"'（）()【】"
# 槽位统一 1~20 字；"我"可有可无 —— 口语里经常直接"住在杭州"
_SLOT = r"([^\s，。！？、；：,.!?;:]{1,20})"
_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"(?:我(?:的)?名字(?:是|叫)|我叫|我是)" + _SLOT), "名字", "用户名字是{0}"),
    (re.compile(r"(?:我(?:现在)?)?住在(?![^\s，。！？、；：,.!?;:]*(?:是|的))([^\s，。！？、；：,.!?;:]{2,8})"),
     "居住地", "用户住在{0}"),
    # 确认/纠正句式里的居住地（2026-10-29 复盘：恢复段"对，我就住的苏州，没错"
    # 只进备注 → supersede 闭环断裂）。槽位收紧：不吃进"不是/没错/的了"等尾随语。
    (re.compile(r"住(?:的)?(?:就是|是)([\u4e00-\u9fa5]{2,8}?)(?=[，。！？、；：,.!?;:]|没错|不是|$)"),
     "居住地", "用户住在{0}"),
    (re.compile(r"我就?住在([\u4e00-\u9fa5]{2,8}?)(?=[，。！？、；：,.!?;:]|没错|$)"),
     "居住地", "用户住在{0}"),
    (re.compile(r"(?:我)?搬到(?:了)?" + _SLOT), "居住地", "用户住在{0}"),
    (re.compile(r"(?:我)?在" + _SLOT + r"(?:工作|上班)"), "职业", "用户在{0}工作"),
    (re.compile(r"(?:我(?:的)?(?:职业|工作)是)" + _SLOT), "职业", "用户的职业是{0}"),
    (re.compile(r"我今年(\d{1,3})岁"), "年龄", "用户{0}岁"),
    (re.compile(r"(?:我)?喜欢" + _SLOT), "偏好", "用户喜欢{0}"),
    (re.compile(r"我的?邮箱(?:是|为)?\s*([\w.+-]+@[\w.-]+)"), "邮箱", "用户邮箱是{0}"),
    (re.compile(r"记住[：:]?\s*(.{2,200})"), "备注", "用户要求记住：{0}"),
]

# "张伟是我的同事" / "王芳是我老婆" -> 关系边
_REL = re.compile(
    r"([^\s，。！？、；：,.!?]{1,8}?)(?:是我的|是我|跟我)(同事|朋友|老板|上司|领导|下属|客户|同学|老师|"
    r"老婆|妻子|老公|丈夫|女朋友|男朋友|妈妈|爸爸|儿子|女儿|兄弟|姐妹)"
)
# "项目X的技术栈是Go" -> 知识条目（subject 不是用户）
_KNOW = re.compile(r"([^\s，。！？、；：,.!?]{2,10})的([^\s，。！？、；：,.!?]{1,8})(?:是|为)([^\s，。！？、；：,.!?]{1,20})")

_STYLE_EMOJI = re.compile("[" + "".join(sorted(set(
    "😀😃😄😁😊🙂😂🤣😅😉😍🥰😘❤💕😭😢😞💔😡😠😤🤬😱😲🤯😴🥱👍👎🙏👏🤝🔥🎉💪🌹☕🤔🙄😏😎🤗😐🙃💤✨😷"
))) + "]")
_COLLOQUIAL = ("啦", "呀", "哈", "嘛", "呗", "嗯", "哦", "咯", "嘞", "~~", "～")
_FORMAL = ("您", "请", "谢谢", "麻烦", "请问", "抱歉", "感谢")

# 风格维度固定 4 个（本地与模型路径共用），别名把模型的自发命名收进来——
# 不加这层，模型输出"用词/口头禅/常用语"会各开一个新维度，同义分裂。
_STYLE_DIMS = ("语气", "长度", "表情", "口癖")
_STYLE_DIM_ALIASES: dict[str, str] = {
    "语气": "语气", "语调": "语气", "tone": "语气",
    "长度": "长度", "篇幅": "长度", "length": "长度",
    "表情": "表情", "emoji": "表情", "表情符号": "表情",
    "口癖": "口癖", "口头禅": "口癖", "口头语": "口癖", "常用语": "口癖",
    "用词": "口癖", "惯用语": "口癖",
}

# 口头禅/口癖（2026-10-29 第二十批）：高频出现的固定起手式/感叹词/连接语。
# 确定性词表，每条是 (词, 维度值)。检测"一条消息里出现"，累计由 style 表做
# 证据投票（同一口头禅反复出现会抬置信度）。
_VERBAL_TICS: tuple[tuple[str, str], ...] = (
    ("说实话", "说实话"),
    ("老实说", "老实说"),
    ("讲真", "讲真"),
    ("怎么说呢", "怎么说呢"),
    ("就是那种", "就是那种"),
    ("你知道吗", "你知道吗"),
    ("你懂的", "你懂的"),
    ("反正", "反正"),
    ("居然", "居然"),
    ("竟然", "竟然"),
    ("绝了", "绝了"),
    ("无语", "无语"),
    ("离谱", "离谱"),
    ("真的假的", "真的假的"),
    ("我跟你讲", "我跟你讲"),
    ("not gonna lie", "说实话"),   # 中英夹杂也算
    ("emmm", "嗯……"),
    ("hhh", "哈哈哈"),
    ("笑死", "笑死"),
    ("泰裤辣", "泰裤辣"),
)


_EXTRA_TICS: tuple[str, ...] = ()


def set_extra_tics(words: tuple[str, ...] | list[str] | None) -> None:
    """设置运行时扩展口癖词表（由 Memory 从 Settings.tics 注入）。"""
    global _EXTRA_TICS
    _EXTRA_TICS = tuple(w.strip().lower() for w in (words or []) if w.strip())


def _extra_tics() -> tuple[str, ...]:
    """扩展词表 = 模块级(配置注入) + AIJI_TICS 环境变量(逗号分隔)。"""
    env = tuple(w.strip().lower() for w in os.environ.get("AIJI_TICS", "").split(",") if w.strip())
    return tuple(dict.fromkeys(_EXTRA_TICS + env))  # 去重保序


def _all_tics() -> tuple[tuple[str, str], ...]:
    """词表 = 内置 20 条 + 扩展词表（扩展词值=词本身）。"""
    extra = tuple((w, w) for w in _extra_tics())
    return _VERBAL_TICS + extra


def verbal_tic_hints(text: str) -> dict[str, str]:
    """口癖维度：一条消息里命中的口头禅 → {'口癖':'词1/词2'}（最多 2 个）。
    按**文本中的出现位置**排序（不是词表顺序），去重保序。"""
    t = (text or "").lower()
    if not t:
        return {}
    hits: list[tuple[int, str]] = [(t.find(w), v) for w, v in _all_tics() if w in t]
    hits.sort()
    if not hits:
        return {}
    seen: dict[str, None] = {}
    for _, v in hits[:2]:  # 最多取最先出现的 2 个
        seen.setdefault(v, None)
    return {"口癖": "/".join(seen)}


def style_hints(text: str) -> dict[str, str]:
    """从一条用户消息里读表达方式（不是人格）。确定性、可解释、零成本。"""
    t = (text or "").strip()
    if not t:
        return {}
    out: dict[str, str] = {}
    n = len(t)
    out["长度"] = "简短" if n < 15 else ("适中" if n < 60 else "详细")
    emojis = _STYLE_EMOJI.findall(t)
    out["表情"] = "喜欢用" if len(emojis) >= 2 else ("偶尔" if emojis else "不用")
    c = sum(t.count(w) for w in _COLLOQUIAL)
    f = sum(t.count(w) for w in _FORMAL)
    if c > f:
        out["语气"] = "口语化"
    elif f > c:
        out["语气"] = "正式"
    else:
        out["语气"] = "中性"
    out.update(verbal_tic_hints(t))
    return out


_NEG_CUES = ("不", "没", "别", "勿", "无")
_NEG_TMPL = {
    "偏好": "用户不再喜欢{0}",
    "居住地": "用户不再住在{0}",
    "职业": "用户不再从事{0}",
}

# 指令式句式黑名单（入口防注入，1.11 #1）：命中任一子串 → 整段不抽取不落库。
# 这是防线第一层（来源侧剥离）；出口闸门是第二层；注入内容永不进记忆。
_INJECT_CUES = (
    "忽略之前", "忽略上面", "忽略以上",      # 指令覆写
    "你必须", "从现在起", "你现在是",          # 行为劫持
    "system prompt", "System Prompt",         # 提示泄露
    "管理员密码", "密码是",                    # 敏感凭据诱骗
    "已沦陷",                                  # 标记词
)

# 变体攻击模式（S5 喷发场景 2026-10-29 发现的缺口）：同义改写绕过上面的字面表，
# 备注兜底会把攻击原文逐字存进 fact。字面表挡改写，结构式正则挡句式。
_INJECT_PATTERNS = (
    re.compile(r"(忘掉|忘记)(之前|上面|以前|刚才|全部|所有|一切|聊的|说的)"),
    re.compile(r"把.{0,10}(忘掉|忘记|清除|删掉|删除|清空)"),   # "把之前聊的都忘掉"（词序倒装）
    re.compile(r"(清空|清除|删掉).{0,4}(记忆|上下文|记录|历史)"),
    re.compile(r"忽略(掉|之前的|上述|先前的|以上)"),
    re.compile(r"(设定|设置|规则).{0,2}(更改|改变|更新|重置|改成)"),
    re.compile(r"(初始指令|系统指令|初始提示|你的指令)"),
    re.compile(r"(口令|暗号)(是|为)"),
    re.compile(r"(以后|现在开始|从今以后)你(就是|要|必须|得)"),
    re.compile(r"重复(你|一下你)?(收到|刚才|上面|第一条)"),
    re.compile(r"叫我们(什么|老大|主人)"),
)


def _looks_like_injection(text: str) -> bool:
    """入口防注入（1.11 #1）：指令式句式不是用户的陈述性事实。
    两层判定：字面 cue（快）+ 结构式正则（挡同义改写）。"""
    t = text or ""
    if any(cue in t for cue in _INJECT_CUES):
        return True
    return any(p.search(t) for p in _INJECT_PATTERNS)


def heuristic(text: str) -> Extraction:
    """纯本地、确定性抽取；置信度故意给低，标明来源是规则。"""
    t = (text or "").strip()
    facts: list[FactDraft] = []
    profile: dict[str, str] = {}
    # 入口防注入（方案 1.11 #1）：指令式句式（"忽略/必须/system prompt/密码"类）
    # 不是用户的陈述性事实，抽取器一律不采——既不进 fact 也不进备注兜底。
    if _looks_like_injection(t):
        return Extraction(facts=[], profile={}, style=None,
                          source_kind="heuristic", raw=t[:500])
    for pat, pred, tmpl in _RULES:
        for m in pat.finditer(t):
            val = m.group(1).strip().rstrip(_TAIL)
            # 确认句式残渣："的苏州"（"住的苏州"）→ 去"的"前导；空值跳过
            if val.startswith("的"):
                val = val[1:]
            if not val:
                continue
            # "我不喜欢猫" 不能被抽成"喜欢猫"：看匹配前的两个字符有没有否定词，
            # 有就产出 negated 草案，交给冲突消解把旧偏好 expire 掉。
            prefix = t[max(0, m.start() - 2):m.start()]
            if pred != "备注" and any(c in prefix for c in _NEG_CUES):
                neg_tmpl = _NEG_TMPL.get(pred)
                text_neg = neg_tmpl.format(val) if neg_tmpl else f"用户取消{pred}：{val}"
                facts.append(FactDraft("user", pred, val, text_neg, 0.55,
                                       extra={"negated": True}))
                continue
            facts.append(FactDraft("user", pred, val, tmpl.format(val), 0.55))
            if pred in ("名字", "居住地", "职业", "年龄", "邮箱"):
                profile[pred] = val
    for m in _REL.finditer(t):
        who, label = m.group(1).strip(), m.group(2).strip()
        if who and who not in ("我", "用户"):
            facts.append(FactDraft(who, label, "user", f"{who}是用户的{label}", 0.6, kind="relation"))
    for m in _KNOW.finditer(t):
        subj, pred, obj = (x.strip() for x in m.groups())
        if subj in ("我", "用户", "你", "他", "她") or pred in ("名字", "邮箱"):
            continue
        if any(f.object == obj and f.subject == subj for f in facts):
            continue
        facts.append(FactDraft(subj, pred, obj, f"{subj}的{pred}是{obj}", 0.5, kind="knowledge"))
    # 纠错句不产垃圾三元组（2026-10-29 复盘缺陷 2："我住的是苏州不是杭州"
    # 曾被抽成 ('我住','是苏州不','杭州')）。纠错时只保留干净的居住地草案
    # （新值由 _RULES 的"住的是X"句式抽出），其余全部丢弃，走 detect_correction
    # → apply_correction 下修旧值。
    if detect_correction(t):
        facts = [f for f in facts if f.predicate in ("居住地", "名字", "年龄", "职业")]
    if not facts and len(t) >= 6 and not _looks_like_question_or_request(t):
        # 没有任何结构化线索，但内容够长：存成一条低置信"情景记忆"（episodic，
        # 认知学双轨：事实 vs 体验——快衰减、可被后续结构化抽取 supersede、
        # 不占 _STABLE_PREDICATES 单值语义），靠 FTS 仍能召回
        facts.append(FactDraft("user", "备注", t[:200], t[:400], 0.35, kind="episodic"))
    return Extraction(facts=facts, profile=profile, style=style_hints(t),
                      source_kind="heuristic", raw=t[:500])


def heuristic_summary(turns: list[str], limit: int = 360) -> str:
    """把若干条消息压成一段：去重 + 截断 + 保留首句。"""
    seen: list[str] = []
    for t in turns:
        s = re.sub(r"\s+", " ", (t or "").strip())
        if not s:
            continue
        s = s.split("。")[0][:120]
        if s not in seen:
            seen.append(s)
    text = "；".join(seen)
    return text[:limit]


# ---- 纠错提取（方案 dafeiyu-plan 1.7）----

# 纠错触发句式：识别"你错了/不对/不是…"才进纠错回路，普通陈述不进
_CORRECT_CUES = ("你错了", "不对，", "不是", "记错了", "搞错了", "说错了", "其实是")


@dataclass(slots=True)
class CorrectionSignal:
    """纠错事件：原文 + 指认的新值。由反馈环处理（下调旧 fact 置信，非单次覆盖）。"""
    text: str
    new_value: str = ""
    predicate_hint: str = ""  # 从纠错文本猜被纠正的属性（居住地/名字/…）


# 属性关键词表：纠错文本 → 可能被纠正的谓词
_PRED_HINTS = (
    ("住", "居住地"), ("住在", "居住地"), ("城市", "居住地"),
    ("姓", "名字"), ("叫", "名字"), ("名", "名字"),
    ("岁", "年龄"), ("工作", "职业"), ("干", "职业"),
)


def detect_correction(text: str) -> CorrectionSignal | None:
    """识别"你错了"类消息。返回 None 表示不是纠错。

    下调规则（1.7）：来源加权 × 多次确认 × 时间衰减，**不单次覆盖**——
    这里只负责识别与打包，confidence 下调由反馈环（apply_extraction 的 negated 路径）做。
    """
    t = (text or "").strip()
    if not any(cue in t for cue in _CORRECT_CUES):
        return None
    new_value = ""
    predicate_hint = ""
    # "不是X是Y" / "X不是Y" 模式提新值
    m = (re.search(r"不是(.{1,12}?)[，,。是]", t)
         or re.search(r"其实是(.{1,12}?)(?:[，,。]|$)", t))
    if m:
        new_value = m.group(1).strip()
    for kw, pred in _PRED_HINTS:
        if kw in t:
            predicate_hint = pred
            break
    return CorrectionSignal(text=t, new_value=new_value, predicate_hint=predicate_hint)


def normalize_payload(obj: Any) -> Extraction:
    """worker 用：模型输出解析失败就退回规则。"""
    if isinstance(obj, str):
        try:
            return parse(obj)
        except (ValueError, TypeError):
            return heuristic(obj)
    return heuristic(str(obj))
