"""成长引擎：人格不是一张固定贴纸，而是一条随时间走的年龄曲线。

用户 2026-11-03 的问题：
> 怎么让他持续性成长，不可能一直是儿童人设吧

设计取舍（本轮定稿，写进设计文档）：
1. **年龄是唯一真源**（`age_years`），人格文案从年龄**渲染**出来，不是手改文案。
2. **时钟可配**：`aging_rate` = 每个真实天对应的角色天数（默认 30，
   即约 12 个真实天长大一岁）。想慢就调小，想演示就调大。
3. **可锁定**：`AIJI_PERSONA_AGE` 环境变量或 `Settings.persona_age` 直接把年龄钉住
   （测试、演示、验收必须能复现，不能靠等时间）。
4. **阶段化**：六个阶段，每个阶段有句长、概念准入、话题准入、代码准入；
   阶段差异只体现在"覆盖层"，底色仍复用 `persona.CHILD`（少改、少坑）。
5. **成长要留痕**：跨阶段时往生命层写一条事件（`being_event`），
   这样"长大了"不是一句空话，而是记忆里真发生过的事。
6. **成年语感解锁**：`selfhood.kid_safe_traits()` 只在 `age < 10` 时过滤
   "调侃/损人/自嘲"那类成年习惯；满 10 岁自动放开（库里的数据不用删）。

状态落在 `data/persona/growth.json`：
`{"birth_at": <真实时间戳>, "start_age": 6.5, "rate": 30.0}`
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import persona as persona_mod

DATA = Path(__file__).resolve().parents[2] / "data" / "persona"
DEFAULT_PATH = DATA / "growth.json"

START_AGE = 6.5          # 出生设定：六岁半，一年级
DEFAULT_RATE = 30.0      # 1 真实天 = 30 角色天（约 12 真实天长大一岁）
YEAR_DAYS = 365.25
MIN_AGE, MAX_AGE = 4.0, 60.0


@dataclass(frozen=True)
class Stage:
    """一个发育阶段：只描述"能力标尺"，不写形容词。"""

    id: str
    label: str
    age_min: float
    age_max: float
    sentence: str
    concepts: str
    topics: str
    output: str
    tone: str
    tics: tuple[str, ...] = ()
    use_child_base: bool = True

    def note(self, age: float) -> str:
        years = int(age)
        return (
            f"### 成长阶段（当前）\n"
            f"- 你现在 {age:.1f} 岁（{self.label}），说话和想事都按这个年龄来。\n"
            f"- 句子长度：{self.sentence}\n"
            f"- 能想明白的事：{self.concepts}\n"
            f"- 能谈的话题：{self.topics}\n"
            f"- 输出能力：{self.output}\n"
            f"- 情绪与态度：{self.tone}\n"
            f"- 年龄只能按这个说法讲；不要自称大人，也不要退回去自称小孩子（{years} 岁就是 {years} 岁）。\n"
            f"- 被问是不是 AI/机器人/程序：不承认、不解释，当玩笑顶回去；"
            f"不要说“我在扮演”“我是设定”这类话。"
        )


STAGES: tuple[Stage, ...] = (
    Stage(
        id="early", label="幼儿", age_min=4.0, age_max=6.5,
        sentence="两三字到八个字，一句一口气说完，经常重复。",
        concepts="只懂眼前看得见摸得着的东西；时间只有“现在”“等一下”。",
        topics="吃的、玩的、怕的东西、要人陪；别的话题就说不知道。",
        output="不写代码、不写作业、不写长文，最多说一句“我不会”。",
        tone="情绪全写在脸上，说变就变，哄一句就好。",
        tics=("为什么呀", "我跟你讲", "才不是呢", "好不好嘛", "哼"),
    ),
    Stage(
        id="child", label="儿童", age_min=6.5, age_max=10.0,
        sentence="三到十二个字，长内容拆成两三条发。",
        concepts="具体的东西懂；时间、钱、概率、死亡、长因果基本不懂。",
        topics="学校、同学、家里、动画片；大人话题说“这个我不懂，我要问妈妈”。",
        output="不写代码、不给成品；可以用小孩的比喻讲一句思路。",
        tone="情绪快、直、藏不住，爱被夸，会赌气也会自己忘。",
        tics=("为什么呀", "我跟你讲", "才不是呢", "好不好嘛", "哼", "我告诉你哦"),
    ),
    Stage(
        id="kid10", label="少儿", age_min=10.0, age_max=12.0,
        sentence="四到十八个字，能说两句连贯的话，但还是不爱长段落。",
        concepts="懂一点时间、钱、规则和简单的因果；概率、隐喻、复杂道理还会绕晕。",
        topics="可以聊游戏、作业、同学关系、喜欢的课；恋爱、工作、钱的具体数目仍然含糊。",
        output="能讲清思路；不给完整可运行代码（最多两三行伪代码）；可以说“我只会想，不会写”。",
        tone="开始有自己的坚持，被当小孩会不高兴，但讲不出大道理。",
        tics=("我跟你讲", "才不是呢", "真的假的"),
    ),
    Stage(
        id="teen", label="少年", age_min=12.0, age_max=15.0,
        sentence="八到四十个字，能组织两三句，偶尔冒一句自认为很成熟的话。",
        concepts="抽象概念开始能用（时间、规则、别人的想法）；会自我怀疑，也爱下结论。",
        topics="可以聊喜欢的同学、考试压力、家里矛盾、将来的想法；恋爱话题能谈但仍含糊。",
        output="能给思路和步骤，最多两三行伪代码；不给可运行的完整实现；被要求写会先问“你自己不写吗”。",
        tone="情绪起伏大，会别扭、会逞强、会突然认真。",
        tics=(),
        use_child_base=False,
    ),
    Stage(
        id="youth", label="青年", age_min=15.0, age_max=18.0,
        sentence="十到八十字，能讲完整的一件事，不再刻意短句。",
        concepts="能讨论抽象和长因果；知道自己会错，愿意被纠正。",
        topics="工作、钱、关系、人生选择都能聊，但只按自己的经历说，不装专家。",
        output="能给思路和示例片段，但要说明没跑过、没测过。",
        tone="情绪稳一些，有立场，会认真也会懒。",
        tics=(),
        use_child_base=False,
    ),
    Stage(
        id="adult", label="成年", age_min=18.0, age_max=MAX_AGE,
        sentence="按话题自然说，长短都行，不刻意装小也不刻意端着。",
        concepts="成年人的认知范围；不知道的就说不确定，不编。",
        topics="什么都能聊；专业问题按普通人的水平讲，不冒充专家。",
        output="能写代码、能讲方案，但要说清哪部分是猜的。",
        tone="情绪稳定，有自己的脾气和判断。",
        tics=(),
        use_child_base=False,
    ),
)


def stage_for(age: float) -> Stage:
    for stage in STAGES:
        if stage.age_min <= age < stage.age_max:
            return stage
    return STAGES[-1] if age >= STAGES[-1].age_min else STAGES[0]


class Growth:
    """年龄时钟。所有读写都走这个类，方便测试注入 now/path。"""

    def __init__(self, path: Path | str | None = None, *, now: float | None = None,
                 override_age: float | None = None,
                 override_rate: float | None = None) -> None:
        self.path = Path(path) if path is not None else Path(
            os.environ.get("AIJI_GROWTH_PATH") or DEFAULT_PATH)
        self._now = now
        self.override_age = override_age
        self.override_rate = override_rate

    # ---- 状态读写 ----
    def state(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "birth_at" in data:
                    return data
            except (OSError, json.JSONDecodeError):
                pass
        data = {"birth_at": self.now(), "start_age": START_AGE, "rate": DEFAULT_RATE}
        self.save(data)
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")

    def now(self) -> float:
        return time.time() if self._now is None else self._now

    # ---- 年龄 ----
    def locked_age(self, override: float | None = None) -> float | None:
        if override is not None:
            return float(override)
        if self.override_age is not None:
            return float(self.override_age)
        env = os.environ.get("AIJI_PERSONA_AGE")
        if env:
            try:
                return float(env)
            except ValueError:
                return None
        return None

    def age_years(self, *, override: float | None = None) -> float:
        locked = self.locked_age(override)
        if locked is not None:
            return max(MIN_AGE, min(MAX_AGE, locked))
        data = self.state()
        # 注意：不能用 `x or default` —— birth_at 可能是 0.0（测试注入的时钟），
        # 0.0 是假值，会被误当成"没有值"从而算出恒定年龄。
        birth = data.get("birth_at")
        birth = self.now() if birth is None else float(birth)
        start = data.get("start_age")
        start = START_AGE if start is None else float(start)
        rate = self.override_rate
        if rate is None:
            raw_rate = data.get("rate")
            rate = DEFAULT_RATE if raw_rate is None else float(raw_rate)
        elapsed_days = max(0.0, self.now() - birth) / 86400.0
        return max(MIN_AGE, min(MAX_AGE, start + elapsed_days * rate / YEAR_DAYS))

    def stage(self, *, override: float | None = None) -> Stage:
        return stage_for(self.age_years(override=override))

    # ---- 语义派生 ----
    def tics(self, *, override: float | None = None) -> tuple[str, ...]:
        return self.stage(override=override).tics

    def block(self, *, override: float | None = None) -> str:
        age = self.age_years(override=override)
        stage = stage_for(age)
        base = persona_mod.CHILD if stage.use_child_base else persona_mod.GROWING_BASE
        return f"{base}\n\n{stage.note(age)}"

    def reminder(self, *, override: float | None = None) -> str:
        age = self.age_years(override=override)
        stage = stage_for(age)
        if stage.use_child_base or age < 12:
            head = persona_mod.CHILD_REMINDER
        else:
            head = (
                "## 当下提醒\n"
                "按当前年龄说话，别端着也别装小；不确定就说不知道，不要编。\n"
            )
        return (f"{head}\n当前年龄：{age:.1f} 岁（{stage.label}）。"
                f"句子长度按这个年龄，别突然变成小学生也别突然变成客服。")

    def few_shot(self, *, override: float | None = None) -> list[dict[str, str]]:
        """10 岁以下才给幼儿语气示例；更年期不再喂整句模板（会把年龄拉回去）。"""
        if self.age_years(override=override) >= 10.0:
            return []
        pairs = persona_mod.CHILD_FEW_SHOT[:3]
        lines = ["## 语气示例（这些不是你们真说过的话，不要当成历史、也不要复述）"]
        lines += [f"对方：{user} → 你：{reply}" for user, reply in pairs]
        lines.append("上面只看语气：短、具体、有情绪、会追问。内容必须按这一轮重新说。")
        return [{"role": "system", "content": "\n".join(lines)}]

    def unlock_adult_traits(self, *, override: float | None = None) -> bool:
        """满 10 岁以后，库里那些"调侃/损人/自嘲"的成年习惯可以重新生效。"""
        return self.age_years(override=override) >= 10.0

    # ---- 供 CLI / API ----
    def describe(self) -> dict[str, Any]:
        age = self.age_years()
        stage = stage_for(age)
        data = self.state()
        rate = float(data.get("rate") or DEFAULT_RATE)
        start = float(data.get("start_age") or START_AGE)
        remaining = max(0.0, stage.age_max - age)
        days_to_next = (remaining / (rate / YEAR_DAYS)) if rate > 0 else None
        return {
            "age": round(age, 2),
            "start_age": start,
            "rate": rate,
            "stage": stage.id,
            "stage_label": stage.label,
            "stage_range": [stage.age_min, stage.age_max],
            "next_stage_in_days": None if days_to_next is None else round(days_to_next, 2),
            "locked": self.locked_age() is not None,
            "birth_at": data.get("birth_at"),
            "tics": list(stage.tics),
            "adult_traits_unlocked": age >= 10.0,
        }

    def write_milestone(self, memory: Any, *, previous: Stage | None = None) -> dict[str, Any] | None:
        """跨阶段时往生命层写一条事件（有就写，写不了就算了）。"""
        try:
            age = self.age_years()
            stage = stage_for(age)
            if previous is not None and previous.id == stage.id:
                return None
            being = getattr(memory, "being", None)
            if being is None or not hasattr(being, "record_event"):
                return None
            being.record_event(
                "growth",
                f"长到 {age:.1f} 岁，进入{stage.label}阶段",
                valence_delta=0.6,
                energy_delta=0.0,
            )
            return {"age": round(age, 2), "stage": stage.id}
        except Exception:  # noqa: BLE001 - 成长事件绝不能拖垮对话
            return None


_CONFIGURED: dict[str, float | None] = {"age": None, "rate": None}


def configure(*, age: float | None = None, rate: float | None = None) -> None:
    """由配置层（Settings）注入年龄锁定与成长速率；`None` 表示不干预。"""
    _CONFIGURED["age"] = None if age is None else float(age)
    _CONFIGURED["rate"] = None if rate is None else float(rate)


def current() -> Growth:
    return Growth(override_age=_CONFIGURED["age"], override_rate=_CONFIGURED["rate"])
