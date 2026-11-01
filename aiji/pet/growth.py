"""养成经济：亲密度 + 小鱼干（规则照抄插件 @linxin666/dsh-pet）。

对应插件源码：
- `lib/types/affinity.js`：亲密度分档、摸头/喂食奖励与冷却、回合奖励
- `lib/types/treats.js`：小鱼干两种来源（每 30 轮任务 / 每 30 分钟）、上限 20
- `lib/types/remarks.js`：原版中文台词库（摸头/摸头冷却/喂食/喂食冷却/没粮）

全部是**纯函数 + 显式时钟**（clock-injected），便于确定性测试：
调用方把 `now_ms` 传进来，这里不读系统时间。
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any


_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()

# ---- 亲密度（affinity）----

AFFINITY_MAX = 999_999_999

# 分档：分 → (名字, 星标)。原版前四档 0/25/50/80 保持不变，往上扩到长线陪伴。
AFFINITY_RANKS: tuple[tuple[int, str, str], ...] = (
    (0, "幼鲸", "*"),
    (25, "伙伴", "**"),
    (50, "挚友", "***"),
    (80, "深海羁绊", "****"),
    (200, "心有灵犀", "*****"),
    (500, "传说羁绊", "******"),
    (2_000, "神话羁绊", "*******"),
    (10_000, "永恒之契", "********"),
    (100_000, "鲸生共渡", "*********"),
)

DEFAULT_AFFINITY: dict[str, float] = {
    "turn_reward": 1,       # 每完成一轮对话
    "pet_reward": 1,        # 摸头
    "pet_cooldown_ms": 10_000,
    "feed_reward": 5,       # 喂食
    "feed_cooldown_ms": 30_000,
}

# ---- 小鱼干（treats）----

DEFAULT_TREATS: dict[str, float] = {
    "turns_per_treat": 30,              # 每 30 轮任务换 1 条
    "time_treat_ms": 300 * 60_000,      # 每 300 分钟（5 小时）给 1 条——原版就是"稀有"
    "max_treats": 20,
}


def empty_affinity() -> dict[str, Any]:
    return {"points": 0, "last_pet_at": 0.0, "last_feed_at": 0.0,
            "pets": 0, "feeds": 0, "turns": 0}


def empty_treat_ledger() -> dict[str, Any]:
    return {"treats": 0, "last_grant_at": 0.0, "turns_at_last_grant": 0}


def rank_of(points: int) -> tuple[int, str, str]:
    rank = AFFINITY_RANKS[0]
    for cand in AFFINITY_RANKS:
        if points >= cand[0]:
            rank = cand
    return rank


def next_rank(points: int) -> tuple[int, str, str] | None:
    for cand in AFFINITY_RANKS:
        if points < cand[0]:
            return cand
    return None


def _clamp_points(points: int) -> int:
    return max(0, min(AFFINITY_MAX, int(points)))


def affinity_view(state: dict[str, Any], now_ms: float,
                  config: dict[str, float] | None = None) -> dict[str, Any]:
    """只读视图（面板显示用）。"""
    cfg = {**DEFAULT_AFFINITY, **(config or {})}
    _, name, stars = rank_of(state["points"])
    nxt = next_rank(state["points"])
    return {
        "points": state["points"],
        "rank": name,
        "stars": stars,
        "pets": state["pets"],
        "feeds": state["feeds"],
        "turns": state["turns"],
        "pet_cooldown": (state["last_pet_at"] != 0
                         and now_ms - state["last_pet_at"] < cfg["pet_cooldown_ms"]),
        "feed_cooldown": (state["last_feed_at"] != 0
                          and now_ms - state["last_feed_at"] < cfg["feed_cooldown_ms"]),
        "next_rank": nxt[1] if nxt else None,
        "next_at": nxt[0] if nxt else None,
    }


def apply_interaction(state: dict[str, Any], kind: str, now_ms: float,
                      config: dict[str, float] | None = None,
                      picker: "RemarkPicker | None" = None
                      ) -> tuple[dict[str, Any], int, str, bool]:
    """一次互动（pet / feed）。返回 (新状态, 得分变化, 台词, 是否生效)。

    冷却只在"互动过一次之后"才生效（last*_at == 0 表示从未互动 → 第一次必成）。
    """
    cfg = {**DEFAULT_AFFINITY, **(config or {})}
    pick = picker.pick if picker is not None else builtin_remark
    if kind == "pet":
        if state["last_pet_at"] != 0 and now_ms - state["last_pet_at"] < cfg["pet_cooldown_ms"]:
            return state, 0, pick("petCooldown"), False
        nxt = dict(state)
        nxt["last_pet_at"] = now_ms
        nxt["pets"] = state["pets"] + 1
        nxt["points"] = _clamp_points(state["points"] + cfg["pet_reward"])
        return nxt, int(cfg["pet_reward"]), pick("pet"), True
    if kind == "feed":
        if state["last_feed_at"] != 0 and now_ms - state["last_feed_at"] < cfg["feed_cooldown_ms"]:
            return state, 0, pick("feedCooldown"), False
        nxt = dict(state)
        nxt["last_feed_at"] = now_ms
        nxt["feeds"] = state["feeds"] + 1
        nxt["points"] = _clamp_points(state["points"] + cfg["feed_reward"])
        return nxt, int(cfg["feed_reward"]), pick("feed"), True
    return state, 0, "", False


def apply_turn_reward(state: dict[str, Any],
                      config: dict[str, float] | None = None) -> dict[str, Any]:
    """每完成一轮对话 +1。"""
    cfg = {**DEFAULT_AFFINITY, **(config or {})}
    nxt = dict(state)
    nxt["turns"] = state["turns"] + 1
    nxt["points"] = _clamp_points(state["points"] + cfg["turn_reward"])
    return nxt


def settle_treat_grants(ledger: dict[str, Any], turns: int, now_ms: float,
                        config: dict[str, float] | None = None
                        ) -> tuple[dict[str, Any], int]:
    """结算小鱼干。返回 (ledger, 本次新增条数)。

    - 工作来源：自上次结算起每满 30 轮给 1 条，只推进轮次锚点
    - 时间来源：自时间锚点起每满 5 小时（300 分钟）给 1 条，只推进时间锚点
    - 两个来源互相独立（一直干活的人也照拿时间鱼干）
    - 第一次结算即使 0 收益也写时间锚点（否则永远等不到下一次）
    """
    cfg = {**DEFAULT_TREATS, **(config or {})}
    turn_delta = max(0, turns - ledger["turns_at_last_grant"])
    work_grants = int(turn_delta // cfg["turns_per_treat"])

    time_anchor = now_ms if ledger["last_grant_at"] == 0 else ledger["last_grant_at"]
    time_grants = int(max(0, now_ms - time_anchor) // cfg["time_treat_ms"])

    gained = work_grants + time_grants
    if gained <= 0:
        if ledger["last_grant_at"] == 0:
            return {**ledger, "last_grant_at": now_ms}, 0
        return ledger, 0

    new_treats = min(cfg["max_treats"], max(0, ledger["treats"] + gained))
    out = {
        "treats": new_treats,
        "last_grant_at": (time_anchor + time_grants * cfg["time_treat_ms"]
                          if time_grants > 0 else time_anchor),
        "turns_at_last_grant": (turns - (turn_delta % cfg["turns_per_treat"])
                                if work_grants > 0 else ledger["turns_at_last_grant"]),
    }
    return out, gained


def consume_treat(ledger: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """喂一口消耗一条；没粮就拒绝。"""
    if ledger["treats"] <= 0:
        return False, ledger
    return True, {**ledger, "treats": ledger["treats"] - 1}


# ---- 台词库（原版中文，逐字照抄；仓库禁 emoji，～ 是鲸鱼娘的语气符）----

REMARK_KINDS = ("pet", "petCooldown", "feed", "feedCooldown", "noTreats")

BUILTIN_REMARKS: dict[str, tuple[str, ...]] = {
    "pet": (
        "咕噜咕噜～被摸摸好舒服！",
        "再摸摸这里，痒痒的～",
        "头顶温度刚刚好，安心～",
        "被摸到耳朵啦，扑通扑通！",
        "你的手掌好温暖，舍不得你走～",
        "呼噜呼噜～就靠在这里不走了！",
        "今天的摸头也收货成功！",
        "蹭蹭你的手心，这是回礼～",
        "多摸摸我，亲密度会涨哦！",
        "闭眼享受中，请勿打扰～",
    ),
    "petCooldown": (
        "摸过头啦，让鲸鱼娘歇口气～",
        "羽毛都快被摸秃啦，缓一缓～",
        "呼……先让我喘口气嘛！",
        "再摸就要睡着了哦～",
        "稍微休息一下，待会儿再摸～",
        "头顶要冒烟啦，停一停！",
        "我知道你喜欢我，但也要节制呀～",
        "歇一歇，摸摸的手感会更好哦～",
        "咕……等我回个蓝～",
        "让我先消化一下刚才的爱！",
    ),
    "feed": (
        "呜哇！小鱼干好好吃！",
        "咔嚓咔嚓，美味到尾巴打结～",
        "这条小鱼干是刚晒好的，好香！",
        "谢谢你，胃里暖暖的～",
        "囤粮 +1，今天也有好好被爱！",
        "好吃到想转圈圈～",
        "小鱼干最好吃了，再来亿条！",
        "饱餐一顿，马上满血复活～",
        "这个味道，是幸福的味道！",
        "吃完了还不忘舔舔爪子～",
    ),
    "feedCooldown": (
        "吃饱啦，晚点再喂～",
        "肚子圆滚滚的，装不下啦～",
        "再喂就要变成球啦！",
        "让我慢慢消化这份心意～",
        "小鱼干的香气还没散呢～",
        "呼……满足得动不了了～",
        "先散步一圈再吃下一顿！",
        "肚皮已经鼓鼓的啦～",
        "好吃是好吃，可也得节制呀～",
        "等我饿了会告诉你哦～",
    ),
    "noTreats": (
        "没有小鱼干了，多陪我工作一会儿吧～",
        "粮仓空空，陪我完成几轮任务就会有小鱼干啦～",
        "小鱼干在路上啦，先一起加油工作！",
        "嘴巴寂寞了……快去完成一轮任务！",
        "陪我多工作一会儿，鱼干自动到账～",
        "现在喂我也只会饿着肚子说谢谢哦～",
        "粮仓见底啦，用几轮任务换一条鱼干吧～",
        "饿着肚子等你完成下一轮任务～",
        "小鱼干藏在你的工作里，去找找看！",
        "先工作后干饭，我们的约定哦～",
    ),
}


def builtin_remark(kind: str) -> str:
    return BUILTIN_REMARKS.get(kind, ("",))[0]


class RemarkPicker:
    """轮转选台词：同一槽位依次循环，测试可确定，不会随机。"""

    def __init__(self, overrides: dict[str, list[str]] | None = None):
        self.pools: dict[str, tuple[str, ...]] = {}
        for kind in REMARK_KINDS:
            custom = (overrides or {}).get(kind)
            self.pools[kind] = tuple(custom) if custom else BUILTIN_REMARKS[kind]
        self._counters: dict[str, int] = {}

    def pool(self, kind: str) -> tuple[str, ...]:
        return self.pools[kind]

    def pick(self, kind: str) -> str:
        pool = self.pools[kind]
        idx = self._counters.get(kind, 0) % len(pool)
        self._counters[kind] = idx + 1
        return pool[idx]


# ---- 落盘（JSON，原子写；不进 SQLite 免得动 schema 迁移）----
# shortcut: 宠物状态按"本机一只鱼"存 JSON；将来要多宠物/多平台再迁进 db.py。

class PetStore:
    """One pet's state inside a process-safe multi-pet JSON document."""

    def __init__(
        self,
        path: str | Path = "data/pet_state.json",
        name: str = "大肥鱼",
        *,
        pet_id: str = "default",
    ):
        self.path = Path(path)
        self.pet_id = str(pet_id or "default")[:80]
        self.name = name
        self.affinity = empty_affinity()
        self.treats = empty_treat_ledger()
        self._baseline_name = self.name
        self._baseline_affinity = dict(self.affinity)
        self._baseline_treats = dict(self.treats)
        key = str(self.path.resolve())
        with _PATH_LOCKS_GUARD:
            self._lock = _PATH_LOCKS.setdefault(key, threading.RLock())
        self.load()

    @contextmanager
    def _locked(self):
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load_unlocked(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return
        if isinstance(raw.get("pets"), dict):
            state = raw["pets"].get(self.pet_id)
            if not isinstance(state, dict):
                return
        elif self.pet_id == "default":
            state = raw
        else:
            return
        if isinstance(state.get("name"), str) and state["name"].strip():
            self.name = state["name"].strip()[:20]
        aff = state.get("affinity") or {}
        self.affinity = {**empty_affinity(), **{k: v for k, v in aff.items()
                                               if k in empty_affinity()}}
        tr = state.get("treats") or {}
        self.treats = {**empty_treat_ledger(), **{k: v for k, v in tr.items()
                                                 if k in empty_treat_ledger()}}
        self._baseline_name = self.name
        self._baseline_affinity = dict(self.affinity)
        self._baseline_treats = dict(self.treats)

    def _sync_unlocked(self) -> None:
        dirty_name = self.name != self._baseline_name
        dirty_affinity = self.affinity != self._baseline_affinity
        dirty_treats = self.treats != self._baseline_treats
        name = self.name
        affinity = dict(self.affinity)
        treats = dict(self.treats)
        self._load_unlocked()
        if dirty_name:
            self.name = name
        if dirty_affinity:
            self.affinity = affinity
        if dirty_treats:
            self.treats = treats

    def load(self) -> None:
        if not self.path.exists():
            return
        with self._locked():
            self._load_unlocked()

    def _save_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        state = {"name": self.name, "affinity": self.affinity, "treats": self.treats}
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            current = {}
        if isinstance(current.get("pets"), dict):
            pets = dict(current["pets"])
        elif current:
            pets = {"default": current}
        else:
            pets = {}
        pets[self.pet_id] = state
        payload = {"version": 2, "pets": pets}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        self._baseline_name = self.name
        self._baseline_affinity = dict(self.affinity)
        self._baseline_treats = dict(self.treats)

    def save(self) -> None:
        with self._locked():
            self._save_unlocked()

    def settle(self, now_ms: float) -> int:
        """懒结算小鱼干（每次拿快照/互动前调一次）。"""
        with self._locked():
            self._sync_unlocked()
            ledger, gained = settle_treat_grants(self.treats, self.affinity["turns"], now_ms)
            if ledger is not self.treats:
                self.treats = ledger
                self._save_unlocked()
            return gained

    def pet(self, now_ms: float, picker: RemarkPicker | None = None):
        with self._locked():
            self._sync_unlocked()
            state, delta, line, ok = apply_interaction(self.affinity, "pet", now_ms,
                                                       picker=picker)
            if ok:
                self.affinity = state
                self._save_unlocked()
            return delta, line, ok

    def feed(self, now_ms: float, picker: RemarkPicker | None = None):
        """喂食。顺序照抄插件的 ledger.interact：

        1) 先结算鱼干；2) **先判喂食冷却**（冷却中绝不能白扣鱼干）；
        3) 再扣鱼干；4) 扣不到 → 说没粮台词，且**不加分**；5) 成功才加分。
        """
        pick = picker.pick if picker else builtin_remark
        self.settle(now_ms)
        with self._locked():
            self._sync_unlocked()
            state, delta, line, ok = apply_interaction(self.affinity, "feed", now_ms,
                                                       picker=picker)
            if not ok:
                return 0, pick("feedCooldown"), False
            ok_feed, ledger = consume_treat(self.treats)
            if not ok_feed:
                return 0, pick("noTreats"), False
            self.treats = ledger
            self.affinity = state
            self._save_unlocked()
            return delta, line, True

    def reward_turn(self) -> None:
        """每完成一轮对话 +1 亲密度（并参与鱼干结算）。"""
        with self._locked():
            self._sync_unlocked()
            self.affinity = apply_turn_reward(self.affinity)
            self._save_unlocked()

    def rename(self, name: str) -> str:
        name = (name or "").strip()[:20]
        if name:
            with self._locked():
                self._sync_unlocked()
                self.name = name
                self._save_unlocked()
        return self.name

    def snapshot(self, now_ms: float) -> dict[str, Any]:
        self.settle(now_ms)
        with self._locked():
            self._sync_unlocked()
            view = affinity_view(self.affinity, now_ms)
            return {"name": self.name, "treats": self.treats["treats"], "affinity": view}


__all__ = [
    "AFFINITY_MAX",
    "AFFINITY_RANKS",
    "BUILTIN_REMARKS",
    "DEFAULT_AFFINITY",
    "DEFAULT_TREATS",
    "PetStore",
    "REMARK_KINDS",
    "RemarkPicker",
    "affinity_view",
    "apply_interaction",
    "apply_turn_reward",
    "builtin_remark",
    "consume_treat",
    "empty_affinity",
    "empty_treat_ledger",
    "next_rank",
    "rank_of",
    "settle_treat_grants",
]
