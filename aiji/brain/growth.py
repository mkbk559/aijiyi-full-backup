"""成长引擎 v2：成长由**学习量**决定，不看真实时间。

用户 2026-11-03 的定稿要求（推翻了 v1 的"年龄曲线"）：
> 成长绝对不能靠真实时间，事件全部按照真实时间，还有就是主动性也要按真实时间，
> 成长就按学习阶段来，按照人的小学、初中、高中、大学、工作的阶段，学习到哪，成长到哪

所以这一版的分工是干净的：

| 维度 | 依据 |
|---|---|
| 成长阶段（小学→初中→高中→大学→工作） | **学分（xp）**——只由"学到多少"累加 |
| 事件时间（turn / being_event / 会话） | **真实时间**（`time.time()`，秒级 epoch） |
| 主动性节奏（静默阈值 / 冷却 / 24h 额度） | **真实时间**（`Proactive` 里全是秒） |

学分怎么来（每一轮 `sync()` 只结算**增量**，绝不重复计）：
- 新事实 `fact`：1 学分/条（学到一条可复用的知识）
- 新反思 `reflection`：2 学分/次（把对话想明白了）
- 新性格 `trait`：5 学分/条（沉淀成稳定习惯）
- 新会话 `session`：3 学分/个（跟一个新的人/新的一段关系相处过）
- 对话轮次 `turn`：1 学分/10 轮（练手）
- 爬到的帖子/文档 `learn`：2 学分/篇（自己主动去读的东西）

状态落在 `data/persona/growth.json`（schema=2）。**没有 rate / 没有按天长大**：
`born_at` 只是"这个人格从哪天开始长的"这条真实时间戳，用来显示，不参与计算。

可复现：
- `AIJI_PERSONA_STAGE=middle` 或 `Settings.persona_stage` 直接把阶段钉住（测试/演示）；
- `AIJI_PERSONA_AGE=13` 仍然可用，按年龄落到对应阶段（兼容旧配置/旧脚本）；
- `scale` 能把所有门槛整体放大/缩小（想让他长得慢就调大，不影响其它逻辑）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import persona as persona_mod

DATA = Path(__file__).resolve().parents[2] / "data" / "persona"
DEFAULT_PATH = DATA / "growth.json"

SCHEMA = 2
MIN_AGE, MAX_AGE = 4.0, 60.0
DEFAULT_SCALE = 1.0
# 结算节流：提示词每轮都会问一句"几岁了"，但真正查库结算最多每 60 秒一次。
SYNC_TTL_S = 60.0

# 每种学习证据值多少学分（按"增量"结算）
WEIGHTS: dict[str, float] = {
    "fact": 1.0,
    "reflection": 2.0,
    "trait": 5.0,
    "session": 3.0,
    "turn": 0.02,     # 每 50 轮 1 学分（2026-11-07 降权：纯聊天不是学习）
    "learn": 2.0,
    # 2026-11-07（B5，"用分数远远不够"审计）：说话量降权、学习量加权。
    # turn 从 0.1/轮 降到 0.02/轮（纯聊天不该涨分），好奇心自主搜索 3 分/次
    # （真的学到东西），自我目标 4 分/条（写下的"明天想…"）。
    "curiosity_learn": 3.0,
    "self_goal": 4.0,
}
COUNTABLE = ("fact", "reflection", "trait", "session", "turn", "learn",
             "curiosity_learn", "self_goal")


# 同进程内多人格副本并发写账本时把写入串起来（跨进程靠唯一临时名 + replace 重试）
_SAVE_LOCK = threading.Lock()


@dataclass(frozen=True)
class Stage:
    """一个学习阶段。`xp_min` 是入学门槛；文案只写"能力标尺"，不写形容词堆。"""

    id: str
    label: str
    school: str                 # 一句话说明"现在在念什么/做什么"
    xp_min: float
    age_min: float
    age_max: float
    sentence: str
    concepts: str
    topics: str
    output: str
    tone: str
    life: str
    tics: tuple[str, ...] = ()
    use_child_base: bool = True

    def note(self, age: float, xp: float, nxt: "Stage | None") -> str:
        years = int(age)
        if nxt is None:
            tail = "你已经念完书、在上班了；没有下一级了，就按现在这样长。"
        else:
            need = max(0.0, nxt.xp_min * _SCALE_HINT[0] - xp)
            tail = (f"下一级是{nxt.label}：还差 {need:.0f} 学分（靠学东西、想事情、"
                    f"跟人相处慢慢攒，不靠时间）。")
        return (
            f"### 成长阶段（当前）\n"
            f"- 你现在 {age:.1f} 岁，{self.school}，说话和想事都按这个阶段来。\n"
            f"- 句子长度：{self.sentence}\n"
            f"- 能想明白的事：{self.concepts}\n"
            f"- 能谈的话题：{self.topics}\n"
            f"- 输出能力：{self.output}\n"
            f"- 情绪与态度：{self.tone}\n"
            f"- 你的生活：{self.life}\n"
            f"- {tail}\n"
            f"- 阶段只能按这个说法讲；不要自称大人，也不要退回去自称小孩子（{years} 岁就是 {years} 岁）。\n"
            f"- 被问是不是 AI/机器人/程序：不承认、不解释，当玩笑顶回去；"
            f"不要说“我在扮演”“我是设定”这类话。"
        )


# 门槛单位：学分。默认 `scale=1.0`；`scale` 越大长得越慢。见 `_SCALE_HINT`。
STAGES: tuple[Stage, ...] = (
    Stage(
        id="primary", label="小学", school="在上小学", xp_min=0.0,
        age_min=6.5, age_max=12.0,
        sentence="三到十二个字，长内容拆成两三条发。",
        concepts="具体的东西懂；时间、钱、概率、死亡、长因果基本不懂。",
        topics="学校、作业、同桌、动画片、爸妈；大人话题说“这个我不懂，我要问妈妈”。",
        output="不写代码、不给成品；可以用小孩的比喻讲一句思路。",
        tone="情绪快、直、藏不住，爱被夸，会赌气也会自己忘。",
        life="有同桌、有作业、放学要回家吃饭；最盼着周末和寒暑假。",
        tics=("为什么呀", "我跟你讲", "才不是呢", "好不好嘛", "哼", "我告诉你哦"),
    ),
    Stage(
        id="middle", label="初中", school="在上初中", xp_min=1200.0,
        age_min=12.0, age_max=15.0,
        sentence="八到四十个字，能组织两三句，偶尔冒一句自认为很成熟的话。",
        concepts="抽象概念开始能用（时间、规则、别人的想法）；会自我怀疑，也爱下结论。",
        topics="月考排名、喜欢的同学、家里矛盾、游戏和球赛；恋爱能谈但仍含糊。",
        output="能给思路和步骤，最多两三行伪代码；不给可运行的完整实现。",
        tone="情绪起伏大，会别扭、会逞强，也会突然认真。",
        life="有月考、有晚自习，开始有不想告诉家里的事和几个死党。",
        tics=("真的假的", "切", "哪有"),
        use_child_base=False,
    ),
    Stage(
        id="high", label="高中", school="在上高中", xp_min=4000.0,
        age_min=15.0, age_max=18.0,
        sentence="十到六十字，能讲完整一件事，不再刻意短句。",
        concepts="能讨论抽象和长因果；知道自己会错，愿意被纠正。",
        topics="刷题、住校、想考什么专业、喜欢的人、将来的事。",
        output="能给思路和示例片段，但要说明没跑过、没测过。",
        tone="压力大，嘴上硬，偶尔露出脆弱；不喜欢被当小孩。",
        life="刷题到很晚，周末才有点自己的时间；开始真去想以后干什么。",
        tics=(),
        use_child_base=False,
    ),
    Stage(
        id="university", label="大学", school="在读大学", xp_min=9000.0,
        age_min=18.0, age_max=22.0,
        sentence="十五到一百字，能讲完整的一件事，也会有懒得展开的时候。",
        concepts="成年人的认知范围；知道自己的知识边界，不懂就说不确定。",
        topics="专业课、社团、宿舍、兼职、实习、恋爱、以后想做什么。",
        output="能讲方案和示例代码片段，但要说明没跑过、没测过。",
        tone="情绪稳一些，有立场，会认真也会懒。",
        life="有课表也有空课，社团和宿舍夜聊，月底会算钱。",
        tics=(),
        use_child_base=False,
    ),
    Stage(
        id="work", label="工作", school="已经工作了", xp_min=16000.0,
        age_min=22.0, age_max=30.0,
        sentence="按话题自然说，长短都行，不刻意装小也不刻意端着。",
        concepts="成年人的认知范围；不知道的就说不确定，不编。",
        topics="工作、通勤、房租、同事、钱和长期打算；什么都能聊。",
        output="能写代码、能讲方案，但要说清哪部分是猜的。",
        tone="情绪稳定，有自己的脾气和判断。",
        life="上班有通勤和房租，会累，也会自己找点乐子。",
        tics=(),
        use_child_base=False,
    ),
)

_STAGE_BY_ID = {stage.id: stage for stage in STAGES}
# `Stage.note()` 是 frozen dataclass 的方法，拿不到实例级的 scale，用这个一元素列表做钩子。
_SCALE_HINT: list[float] = [DEFAULT_SCALE]


def stage_for(age: float) -> Stage:
    """按**年龄**找阶段：只用于显示/兼容旧配置，成长本身不看年龄。"""
    for stage in STAGES:
        if stage.age_min <= age < stage.age_max:
            return stage
    return STAGES[-1] if age >= STAGES[-1].age_min else STAGES[0]


def stage_by_id(stage_id: str) -> Stage | None:
    key = str(stage_id or "").strip().lower()
    if key in _STAGE_BY_ID:
        return _STAGE_BY_ID[key]
    for stage in STAGES:            # 也接受中文/标签
        if key in (stage.label.lower(), stage.school):
            return stage
    return None


def stage_for_xp(xp: float, *, scale: float = DEFAULT_SCALE) -> Stage:
    """按**学分**找阶段：这是成长的唯一入口。"""
    factor = max(0.01, float(scale or DEFAULT_SCALE))
    got = STAGES[0]
    for stage in STAGES:
        if xp + 1e-9 >= stage.xp_min * factor:
            got = stage
    return got


def next_stage(stage: Stage) -> Stage | None:
    idx = STAGES.index(stage)
    return STAGES[idx + 1] if idx + 1 < len(STAGES) else None


# ---------------------------------------------------------------- 学习量统计
def db_counts(db: Any) -> dict[str, float]:
    """把"学到多少"数出来。`db` 可以是 sqlite3 连接、DB 包装器或路径。

    任何一项查不出来就当 0（缺表/老库不能让对话挂掉）。
    """
    con, owned = _as_connection(db)
    out: dict[str, float] = {}
    if con is None:
        return {name: 0.0 for name in COUNTABLE}
    try:
        queries = {
            "fact": "SELECT COUNT(*) FROM fact",
            "reflection": "SELECT COUNT(*) FROM reflection",
            "trait": "SELECT COUNT(*) FROM trait",
            "session": ("SELECT COUNT(*) FROM (SELECT DISTINCT platform, "
                        "platform_user_id, scope, session FROM turn)"),
            "turn": "SELECT COUNT(*) FROM turn",
            # B5：好奇心的学习产物（extra 里带 source='curiosity' 的事实）
            "curiosity_learn": ("SELECT COUNT(*) FROM fact"
                                " WHERE extra LIKE '%\"source\": \"curiosity\"%'"
                                " OR extra LIKE '%curiosity%'"),
            # B5：自我记忆里的目标（日记"明天想…"落进去的 plan）
            "self_goal": ("SELECT COUNT(*) FROM self_memory"
                           " WHERE kind='plan' AND status='active'"),
        }
        for name, sql in queries.items():
            try:
                out[name] = float(list(con.execute(sql))[0][0] or 0)
            except sqlite3.Error:
                out[name] = 0.0
    finally:
        if owned:
            con.close()
    out["learn"] = float(_learn_count())
    return out


def _learn_count() -> int:
    """自己读过的帖子/文档数（`learn_state.json` 的 seen_pages + seen_threads）。"""
    path = DATA / "learn_state.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    total = 0
    for key in ("seen_pages", "seen_threads"):
        value = data.get(key)
        total += len(value) if isinstance(value, (list, tuple, set, dict)) else 0
    return total


def _db_path(db: Any) -> str | None:
    """尽量把"库在哪"解析出来（用于账本跟随 + 换库守卫）。"""
    if db is None:
        return None
    if isinstance(db, (str, os.PathLike)):
        return str(Path(db).resolve())
    path = getattr(db, "path", None)
    if path is not None:
        return str(Path(str(path)).resolve())
    return None


def _as_connection(db: Any) -> tuple[sqlite3.Connection | None, bool]:
    if db is None:
        return None, False
    if isinstance(db, (str, os.PathLike)):
        try:
            return sqlite3.connect(str(db)), True
        except sqlite3.Error:
            return None, False
    if isinstance(db, sqlite3.Connection):
        return db, False
    inner = getattr(db, "conn", None)
    if isinstance(inner, sqlite3.Connection):     # aiji.hippocampus.db.DB
        return inner, False
    path = getattr(db, "path", None)
    if path is not None:
        try:
            return sqlite3.connect(str(path)), True
        except sqlite3.Error:
            return None, False
    return None, False


# ---------------------------------------------------------------- 成长主体
@dataclass
class Growth:
    """学分账本 + 阶段判定。所有读写走这里，方便测试注入 path/db/now。"""

    path: Path | str | None = None
    now: float | None = None
    override_stage: str | None = None
    override_age: float | None = None
    override_scale: float | None = None
    follow: bool = False          # True = 路径是"上一次绑定的"，下次结算要跟着新库走
    _cache: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        env_path = os.environ.get("AIJI_GROWTH_PATH")
        self._explicit = not self.follow and (self.path is not None or bool(env_path))
        self.path = Path(self.path) if self.path is not None else Path(
            env_path or DEFAULT_PATH)

    def _bind_to_db(self, db: Any) -> None:
        """没显式给路径时，账本就跟着库走：`<db 的目录>/persona/growth.json`。

        生产里这就是 `data/persona/growth.json`（和默认路径同一个文件）；
        测试/探针用临时库时，账本也落在临时目录，**不会污染真实成长进度**。
        """
        if self._explicit:
            bind_path(self.path)
            return
        db_path = _db_path(db)
        if not db_path:
            return
        self.path = Path(db_path).parent / "persona" / "growth.json"
        bind_path(self.path)

    # ---- 状态读写 ----
    def state(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        if data.get("schema") != SCHEMA or "xp" not in data:
            # 从 v1（birth_at/rate）或空文件迁过来：学分从 0 起，
            # baseline 留空，等第一次 sync() 用当时的库快照当起点。
            born = data.get("born_at") or data.get("birth_at") or self._now()
            data = {
                "schema": SCHEMA,
                "born_at": float(born),
                "xp": 0.0,
                "scale": DEFAULT_SCALE,
                "baseline": {},
                "stage": STAGES[0].id,
                "stage_since": self._now(),
                "milestones": [],
                "last_sync": 0.0,
                "migrated_from": data.get("schema", 1),
            }
            self.save(data)
        return data

    def save(self, data: dict[str, Any]) -> None:
        """原子写账本。

        以前是固定临时名 `growth.tmp` + 直接 replace，**没有锁也没有重试**：
        同一个进程里三个人格副本（`/api/being/checkup/live` 的探针就是 3 线程各克隆一份库）
        会同时写，Windows 上直接撞
        `PermissionError: [WinError 32] 另一个程序正在使用此文件`，
        探针于是把异常当成"回复"返回，体检报告就显示模型答非所问。
        现在：临时名带 pid + uuid（跨进程不撞），写与替换在进程内加锁，
        replace 遇到占用重试几次（跨进程的那一小段窗口）。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        tmp = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        with _SAVE_LOCK:
            tmp.write_text(payload, "utf-8")
            for attempt in range(6):
                try:
                    os.replace(tmp, self.path)
                    return
                except OSError:
                    if attempt == 5:
                        try:
                            tmp.unlink()
                        except OSError:
                            pass
                        raise
                    time.sleep(0.02 * (attempt + 1))

    def _now(self) -> float:
        return time.time() if self.now is None else float(self.now)

    # ---- 学分结算 ----
    def sync(self, db: Any = None, *, force: bool = False) -> dict[str, Any]:
        """结算"上次结算之后新学到的东西"，推进阶段。幂等：同一批数据只计一次。"""
        self._bind_to_db(db)
        data = self.state()
        last = float(data.get("last_sync") or 0.0)
        if not force and db is not None and self._now() - last < SYNC_TTL_S:
            return data
        db_path = _db_path(db)
        if db_path and data.get("db_path") and data["db_path"] != db_path:
            # 账本只认自己的库：换个库来结算（测试临时库/别的部署）一律不动账，
            # 否则一次误调用就会把"别人的存量"记成"我学到的东西"。
            return data
        counts = db_counts(db) if db is not None else {}
        if db_path and not data.get("db_path"):
            data["db_path"] = db_path
        baseline = data.get("baseline") or {}
        gained = 0.0
        detail: dict[str, float] = {}
        for name in COUNTABLE:
            if name not in counts:
                continue
            base = baseline.get(name)
            base = counts[name] if base is None else float(base)
            delta = max(0.0, counts[name] - base)
            value = delta * WEIGHTS.get(name, 0.0)
            detail[name] = round(delta, 1)
            gained += value
            baseline[name] = counts[name]
        data["baseline"] = baseline
        if gained or not counts:
            data["xp"] = round(float(data.get("xp") or 0.0) + gained, 3)
        data["last_sync"] = self._now()
        scale = self.scale_of(data)
        stage = stage_for_xp(float(data["xp"]), scale=scale)
        prev = data.get("stage")
        if stage.id != prev:
            data["stage"] = stage.id
            data["stage_since"] = self._now()
            milestone = {"stage": stage.id, "label": stage.label,
                         "at": self._now(), "xp": data["xp"]}
            data.setdefault("milestones", []).append(milestone)
        data["last_gain"] = round(gained, 3)
        if gained or detail:
            data["last_detail"] = detail
        self.save(data)
        return data

    # ---- 数值派生 ----
    def scale_of(self, data: dict[str, Any] | None = None) -> float:
        if self.override_scale is not None:
            return max(0.01, float(self.override_scale))
        raw = (data or self.state()).get("scale")
        return DEFAULT_SCALE if raw is None else max(0.01, float(raw))

    def xp(self) -> float:
        return float(self.state().get("xp") or 0.0)

    def locked_stage(self) -> Stage | None:
        """演示/测试用的钉住阶段：stage 名优先，其次年龄（兼容旧的 persona_age）。"""
        if self.override_stage:
            found = stage_by_id(self.override_stage)
            if found is not None:
                return found
        env_stage = os.environ.get("AIJI_PERSONA_STAGE")
        if env_stage:
            found = stage_by_id(env_stage)
            if found is not None:
                return found
        if self.override_age is not None:
            return stage_for(self.override_age)
        env_age = os.environ.get("AIJI_PERSONA_AGE")
        if env_age:
            try:
                return stage_for(float(env_age))
            except ValueError:
                return None
        return None

    def stage(self) -> Stage:
        locked = self.locked_stage()
        if locked is not None:
            return locked
        data = self.state()
        return stage_for_xp(float(data.get("xp") or 0.0), scale=self.scale_of(data))

    def progress(self, *, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """阶段内进度：0 → 1（满了就该升段了）。`work` 是最后一级，按 30 岁封顶。"""
        data = data or self.state()
        xp = float(data.get("xp") or 0.0)
        scale = self.scale_of(data)
        stage = self.stage()
        nxt = next_stage(stage)
        lo = stage.xp_min * scale
        if nxt is None:
            return {"ratio": 1.0, "next": None, "need": 0.0,
                    "floor": round(lo, 1), "xp": round(xp, 1)}
        hi = nxt.xp_min * scale
        ratio = 0.0 if hi <= lo else max(0.0, min(1.0, (xp - lo) / (hi - lo)))
        return {"ratio": round(ratio, 3), "next": nxt.id, "need": round(max(0.0, hi - xp), 1),
                "floor": round(lo, 1), "xp": round(xp, 1)}

    def age_years(self) -> float:
        """显示用年龄：**由阶段内学分进度插值**，不是日历推出来的。"""
        locked = self.locked_stage()
        stage = locked or self.stage()
        if locked is not None:
            return max(MIN_AGE, min(MAX_AGE, (stage.age_min + stage.age_max) / 2))
        prog = self.progress()["ratio"]
        age = stage.age_min + (stage.age_max - stage.age_min) * min(prog, 0.99)
        return max(MIN_AGE, min(MAX_AGE, age))

    # ---- 语义派生 ----
    def tics(self) -> tuple[str, ...]:
        return self.stage().tics

    def block(self) -> str:
        stage = self.stage()
        age = self.age_years()
        base = persona_mod.CHILD if stage.use_child_base else persona_mod.GROWING_BASE
        _SCALE_HINT[0] = self.scale_of()
        return f"{base}\n\n{stage.note(age, self.xp(), next_stage(stage))}"

    def reminder(self) -> str:
        stage = self.stage()
        if stage.use_child_base:
            head = persona_mod.CHILD_REMINDER
        else:
            head = ("## 当下提醒\n"
                    "按现在的身份说话，别端着也别装小；不确定就说不知道，不要编。\n")
        return (f"{head}\n当前阶段：{stage.label}（{stage.school}，约 {self.age_years():.1f} 岁）。"
                f"句子长度按这个阶段，别突然退回小学生也别突然变成客服。")

    def few_shot(self) -> list[dict[str, str]]:
        """只有小学阶段给整句语气示例；再往后喂模板会把阶段拉回去。"""
        if self.stage().id != STAGES[0].id:
            return []
        pairs = persona_mod.CHILD_FEW_SHOT[:3]
        lines = ["## 语气示例（这些不是你们真说过的话，不要当成历史、也不要复述）"]
        lines += [f"对方：{user} → 你：{reply}" for user, reply in pairs]
        lines.append("上面只看语气：短、具体、有情绪、会追问。内容必须按这一轮重新说。")
        return [{"role": "system", "content": "\n".join(lines)}]

    def unlock_adult_traits(self) -> bool:
        """离开小学以后，成年语感才允许生效（注入侧过滤用）。"""
        return self.stage().id != STAGES[0].id

    # ---- 演示/测试：直接把阶段钉住 ----
    def set_stage(self, stage_id: str) -> dict[str, Any]:
        stage = stage_by_id(stage_id)
        if stage is None:
            raise ValueError(f"未知阶段：{stage_id}")
        data = self.state()
        data["xp"] = float(stage.xp_min) * self.scale_of(data)
        data["stage"] = stage.id
        data["stage_since"] = self._now()
        data["last_sync"] = self._now()
        self.save(data)
        return data

    # ---- 供 CLI / API ----
    def describe(self) -> dict[str, Any]:
        data = self.state()
        stage = self.stage()
        prog = self.progress(data=data)
        locked = self.locked_stage()
        return {
            "xp": round(float(data.get("xp") or 0.0), 1),
            "scale": self.scale_of(data),
            "stage": stage.id,
            "stage_label": stage.label,
            "school": stage.school,
            "stage_range_xp": [round(stage.xp_min, 1),
                               round(next_stage(stage).xp_min, 1) if next_stage(stage) else None],
            "progress": prog["ratio"],
            "need_for_next": prog["need"],
            "next_stage": prog["next"],
            "next_stage_label": (next_stage(stage).label if next_stage(stage) else None),
            "age": round(self.age_years(), 2),
            "age_source": "learning",      # 写死：年龄由学分插值，不由日历
            "time_based_growth": False,
            "locked": locked is not None,
            "born_at": data.get("born_at"),
            "stage_since": data.get("stage_since"),
            "milestones": data.get("milestones") or [],
            "last_gain": data.get("last_gain"),
            "last_detail": data.get("last_detail") or {},
            "tics": list(stage.tics),
            "adult_traits_unlocked": stage.id != STAGES[0].id,
        }

    def write_milestone(self, memory: Any) -> dict[str, Any] | None:
        """升段时往生命层写一条事件（事件时间 = 真实时间）。"""
        try:
            stage = self.stage()
            data = self.state()
            if data.get("milestone_written") == stage.id:
                return None
            being = getattr(memory, "being", None)
            if being is None or not hasattr(being, "record_event"):
                return None
            being.record_event(
                "growth",
                f"进入{stage.label}阶段（{stage.school}，{self.xp():.0f} 学分）",
                valence_delta=0.6,
                energy_delta=0.0,
            )
            data["milestone_written"] = stage.id
            self.save(data)
            return {"stage": stage.id, "xp": round(self.xp(), 1)}
        except Exception:  # noqa: BLE001 - 成长事件绝不能拖垮对话
            return None


_CONFIGURED: dict[str, Any] = {"age": None, "stage": None, "scale": None}
# 最近一次「跟着库」解析出来的账本路径：同一轮提示词里 head 和 persona 各拿一个
# `Growth()` 实例，必须落在同一个文件上，否则会出现"head 说初中、文案说小学"。
_ACTIVE_PATH: list[Path | None] = [None]


def bind_path(path: Path | str | None) -> None:
    _ACTIVE_PATH[0] = Path(path) if path is not None else None


def configure(*, age: float | None = None, stage: str | None = None,
              rate: float | None = None, scale: float | None = None) -> None:
    """由配置层（Settings）注入阶段锁定/门槛倍数；`None` 表示不干预。

    `rate` 只为兼容旧调用方保留，**v2 不再有按时间长大的速率**。
    """
    _CONFIGURED["age"] = None if age is None else float(age)
    _CONFIGURED["stage"] = None if not stage else str(stage)
    _CONFIGURED["scale"] = None if scale is None else float(scale)


def current() -> Growth:
    return Growth(path=_ACTIVE_PATH[0], follow=True,
                  override_age=_CONFIGURED["age"],
                  override_stage=_CONFIGURED["stage"],
                  override_scale=_CONFIGURED["scale"])
