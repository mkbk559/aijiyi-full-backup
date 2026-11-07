"""Global character life-state: energy, mood residue, private notes, events.

Unlike the per-contact LifeCard, this state belongs to the character herself.
It is persisted once per persona and then shared across QQ/private/group
conversations.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any

from aiji.hippocampus.db import DB
from aiji.endocrine.life import infer_event


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _hour_of(now: float) -> int:
    return int(time.localtime(float(now)).tm_hour)


def _mood_word(valence: float, stress: float) -> str:
    if stress >= 70:
        return "紧绷"
    if valence <= -12:
        return "低落"
    if valence <= -5:
        return "有点闷"
    if valence >= 12:
        return "很开心"
    if valence >= 5:
        return "心情不错"
    return "平静"


def _energy_word(energy: float) -> str:
    if energy < 20:
        return "很累"
    if energy < 40:
        return "有点累"
    if energy < 70:
        return "还有精神"
    if energy < 90:
        return "有精神"
    return "很活跃"


INTERACTION_KINDS = frozenset((
    "neutral", "anger", "stress", "comfort", "heal", "praise",
    "joy", "success", "curiosity",
))

# 工程巡检类事件（mcp_research/being_checkup 等）不属于"人设的生活"：
# 2026-11-05 真机事故——mcp_research 的"去 GitHub 翻了 68 个仓库"进了
# self_events 提示词，导致初中生在 QQ 里说"我刚才在翻 GitHub 看项目"。
ENGINEERING_KINDS = frozenset((
    "mcp_research", "being_checkup", "selftest", "maintenance",
))


@dataclass(frozen=True, slots=True)
class LifeActivity:
    id: str
    summary: str
    focus: str
    duration_min: int
    cooldown_min: int
    energy_delta: float = 0.0
    valence_delta: float = 0.0
    social_delta: float = 0.0
    note: str = ""
    hours: tuple[int, ...] = tuple(range(24))


ACTIVITIES: tuple[LifeActivity, ...] = (
    LifeActivity(
        "listen_music", "翻到几首以前的歌，戴着一只耳机听了一会儿",
        "在听歌", 18, 35, energy_delta=1.0, valence_delta=1.4,
        note="刚刚那首歌有点像我最近的心情。",
        hours=(8, 9, 10, 11, 14, 15, 16, 17, 19, 20, 21),
    ),
    LifeActivity(
        "watch_videos", "看了会儿收藏的视频，顺手记下一个想聊的话题",
        "在刷收藏的视频", 24, 45, energy_delta=-1.2, valence_delta=1.0,
        social_delta=0.8, note="刚才看到个挺有意思的说法，先记着。",
        hours=(10, 11, 13, 14, 15, 16, 19, 20, 21, 22),
    ),
    LifeActivity(
        "tidy_stickers", "把最近收着的表情包翻了一遍，顺手整理了几张",
        "在整理表情包", 14, 50, energy_delta=-0.5, valence_delta=0.8,
        note="有些图很适合下次聊天时拿出来。",
        hours=(9, 10, 11, 13, 14, 15, 16, 17, 19, 20),
    ),
    LifeActivity(
        "read", "安静看了几页书，脑子慢下来一点",
        "在看书", 30, 55, energy_delta=-0.8, valence_delta=1.1,
        note="刚才那句话值得再想一会儿。",
        hours=(8, 9, 10, 14, 15, 16, 20, 21),
    ),
    LifeActivity(
        "daydream", "靠在窗边发了会儿呆，没有急着找事做",
        "在发呆", 12, 40, energy_delta=0.4, valence_delta=0.3,
        social_delta=-0.4, note="安静一会儿也挺好，不一定要一直说话。",
        hours=(8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 20, 21),
    ),
    LifeActivity(
        "tea", "给自己泡了点喝的，慢慢缓了一会儿",
        "在喝东西", 10, 45, energy_delta=1.3, valence_delta=0.7,
        hours=(8, 9, 10, 14, 15, 16, 20, 21),
    ),
    LifeActivity(
        "write_note", "把刚才冒出来的念头写进了只属于自己的小本子",
        "在写小本子", 8, 60, energy_delta=-0.3, valence_delta=0.8,
        note="有些话先写给自己，不一定要马上说出来。",
        hours=(9, 10, 11, 14, 15, 16, 20, 21, 22),
    ),
    LifeActivity(
        "quiet_rest", "电量不太够，安静歇了一会儿，没有硬撑着聊天",
        "在休息", 25, 40, energy_delta=4.0, valence_delta=0.4,
        social_delta=-0.5, note="现在不太想多说，缓一缓再回。",
        hours=tuple(range(24)),
    ),
    LifeActivity(
        "sleep", "夜里困了，缩着睡了一会儿",
        "在睡觉", 45, 60, energy_delta=6.0, valence_delta=0.2,
        social_delta=-0.8,
        hours=(0, 1, 2, 3, 4, 5, 6, 7, 23),
    ),
)


@dataclass(slots=True)
class BeingState:
    energy: float = 72.0
    valence: float = 0.0
    social: float = 65.0
    stress: float = 10.0
    last_tick: float | None = None
    last_interaction: float = 0.0
    last_proactive: float = 0.0
    asleep_until: float = 0.0
    online_since: float = 0.0
    private_topic: str = ""
    activity: str = ""
    activity_started: float = 0.0
    activity_until: float = 0.0
    current_focus: str = ""
    next_life_at: float = 0.0
    last_activity: str = ""
    life_seq: int = 0

    def tick(self, *, now: float, hour: int | None = None) -> None:
        current = float(now)
        if self.last_tick is None:
            self.last_tick = current
            return
        elapsed_h = max(0.0, min(72.0, (current - self.last_tick) / 3600.0))
        if elapsed_h <= 0:
            return
        h = _hour_of(current) if hour is None else int(hour)
        asleep = h < 8 or h >= 23 or current < self.asleep_until
        if asleep:
            self.energy += 3.0 * elapsed_h
            self.social += 2.5 * elapsed_h
            self.stress *= math.exp(-0.12 * elapsed_h)
        else:
            self.energy -= 0.8 * elapsed_h
            self.social -= 0.35 * elapsed_h
            self.stress *= math.exp(-0.04 * elapsed_h)
        self.valence *= math.exp(-0.03 * elapsed_h)
        if self.last_interaction:
            silence_h = max(0.0, (current - self.last_interaction) / 3600.0)
            if silence_h >= 24 and self.valence > -3.0:
                self.valence -= min(1.5, 0.45 * elapsed_h / 24.0)
        self.energy = _clamp(self.energy, 0.0, 100.0)
        self.social = _clamp(self.social, 0.0, 100.0)
        self.stress = _clamp(self.stress, 0.0, 100.0)
        self.valence = _clamp(self.valence, -20.0, 20.0)
        self.last_tick = current

    def observe(
        self,
        event_type: str,
        intensity: float,
        *,
        now: float,
        hour: int | None = None,
    ) -> None:
        self.tick(now=now, hour=hour)
        s = _clamp(intensity, 0.0, 1.0)
        kind = str(event_type or "").lower()
        deltas = {
            "anger": (-6.0, 14.0),
            "stress": (-4.0, 10.0),
            "comfort": (3.0, -5.0),
            "heal": (3.5, -6.0),
            "praise": (2.0, -1.0),
            "joy": (3.0, -2.0),
            "success": (1.5, -1.0),
            "curiosity": (0.3, 0.0),
            "neutral": (0.0, 0.0),
        }
        valence_delta, stress_delta = deltas.get(kind, (0.0, 0.0))
        self.valence = _clamp(self.valence + valence_delta * s, -20.0, 20.0)
        self.stress = _clamp(self.stress + stress_delta * s, 0.0, 100.0)
        # Chat is a light activity, not a full task per message. Charging every
        # turn at the old 0.5-1.3 rate made ordinary group traffic drain a full
        # day of energy in under two hours and permanently disabled initiative.
        self.energy = _clamp(self.energy - (0.02 + 0.03 * s), 0.0, 100.0)
        self.social = _clamp(self.social + (0.03 + 0.05 * s), 0.0, 100.0)
        if self.energy <= 8:
            self.asleep_until = max(self.asleep_until, now + 8 * 3600)
        self.last_interaction = now

    def can_proactive(self, *, now: float, hour: int | None = None) -> bool:
        h = _hour_of(now) if hour is None else int(hour)
        return (
            self.energy >= 20
            and self.social >= 15
            and 8 <= h < 23
            and now >= self.asleep_until
        )

    def advance_life(self, *, now: float, roll: float) -> dict[str, Any] | None:
        current = float(now)
        self.tick(now=current)
        if current < self.next_life_at:
            return None
        hour = _hour_of(current)
        candidates = [
            activity for activity in ACTIVITIES
            if (
                hour in activity.hours
                and activity.id not in ("quiet_rest", "sleep")
                and activity.id != self.last_activity
            )
        ]
        if self.energy < 28:
            wanted = "sleep" if hour < 8 or hour >= 23 else "quiet_rest"
            candidates = [
                activity for activity in ACTIVITIES if activity.id == wanted
            ]
        elif self.stress >= 60:
            candidates = [
                activity for activity in ACTIVITIES
                if activity.id in ("daydream", "tea", "quiet_rest", "listen_music")
            ]
        if not candidates:
            candidates = [
                activity for activity in ACTIVITIES
                if (
                    activity.id not in ("quiet_rest", "sleep")
                    and activity.id != self.last_activity
                )
            ]
        if not candidates:
            candidates = list(ACTIVITIES)
        index = min(
            len(candidates) - 1,
            int(max(0.0, min(0.999999, float(roll))) * len(candidates)),
        )
        activity = candidates[index]
        before = self.snapshot(now=current)
        self.energy = _clamp(self.energy + activity.energy_delta, 0.0, 100.0)
        self.valence = _clamp(
            self.valence + activity.valence_delta, -20.0, 20.0)
        self.social = _clamp(
            self.social + activity.social_delta, 0.0, 100.0)
        self.activity = activity.summary
        self.activity_started = current
        self.activity_until = current + activity.duration_min * 60.0
        self.current_focus = activity.focus
        self.last_activity = activity.id
        self.next_life_at = current + activity.cooldown_min * 60.0
        self.life_seq += 1
        after = self.snapshot(now=current)
        return {
            "id": activity.id,
            "kind": "self_activity",
            "summary": activity.summary,
            "focus": activity.focus,
            "note": activity.note,
            "valence_delta": float(after["valence"]) - float(before["valence"]),
            "energy_delta": float(after["energy"]) - float(before["energy"]),
            "created_at": current,
            "activity_until": self.activity_until,
            "next_life_at": self.next_life_at,
        }

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        idle_s = max(0.0, current - self.last_interaction) if self.last_interaction else 0.0
        pace = "cold" if idle_s > 1800 else "hot" if idle_s < 180 else "normal"
        online_minutes = (
            round(max(0.0, current - self.online_since) / 60.0, 1)
            if self.online_since else 0.0
        )
        return {
            "energy": round(self.energy, 2),
            "energy_word": _energy_word(self.energy),
            "valence": round(self.valence, 2),
            "mood": _mood_word(self.valence, self.stress),
            "social": round(self.social, 2),
            "stress": round(self.stress, 2),
            "pace": pace,
            "idle_s": round(idle_s, 1),
            "last_interaction": self.last_interaction,
            "last_proactive": self.last_proactive,
            "proactive_ok": self.can_proactive(now=current),
            "online_minutes": online_minutes,
            "activity": self.activity,
            "activity_started": self.activity_started,
            "activity_until": self.activity_until,
            "current_focus": self.current_focus,
            "next_life_at": self.next_life_at,
            "last_activity": self.last_activity,
            "life_seq": self.life_seq,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "energy": self.energy,
            "valence": self.valence,
            "social": self.social,
            "stress": self.stress,
            "last_tick": self.last_tick,
            "last_interaction": self.last_interaction,
            "last_proactive": self.last_proactive,
            "asleep_until": self.asleep_until,
            "online_since": self.online_since,
            "private_topic": self.private_topic,
            "activity": self.activity,
            "activity_started": self.activity_started,
            "activity_until": self.activity_until,
            "current_focus": self.current_focus,
            "next_life_at": self.next_life_at,
            "last_activity": self.last_activity,
            "life_seq": self.life_seq,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> BeingState:
        raw = data or {}
        return cls(
            energy=_clamp(raw.get("energy", 72.0), 0, 100),
            valence=_clamp(raw.get("valence", 0.0), -20, 20),
            social=_clamp(raw.get("social", 65.0), 0, 100),
            stress=_clamp(raw.get("stress", 10.0), 0, 100),
            last_tick=float(raw["last_tick"]) if raw.get("last_tick") else None,
            last_interaction=float(raw.get("last_interaction") or 0.0),
            last_proactive=float(raw.get("last_proactive") or 0.0),
            asleep_until=float(raw.get("asleep_until") or 0.0),
            online_since=float(raw.get("online_since") or 0.0),
            private_topic=str(raw.get("private_topic") or "")[:500],
            activity=str(raw.get("activity") or "")[:1000],
            activity_started=float(raw.get("activity_started") or 0.0),
            activity_until=float(raw.get("activity_until") or 0.0),
            current_focus=str(raw.get("current_focus") or "")[:200],
            next_life_at=float(raw.get("next_life_at") or 0.0),
            last_activity=str(raw.get("last_activity") or "")[:80],
            life_seq=int(raw.get("life_seq") or 0),
        )


class BeingStore:
    def __init__(self, db: DB, *, persona: str = "child"):
        self.db = db
        self.persona = str(persona or "child")
        self.process_started = time.time()

    def load(self, *, now: float | None = None, hour: int | None = None) -> BeingState:
        current = time.time() if now is None else float(now)
        row = self.db.read(
            "SELECT state FROM being_state WHERE persona=?", (self.persona,))
        if not row:
            # 换人格不该等于失忆：这个 persona 还没落过状态时，继承**最近一份**
            # 状态（好感/信任/默契/心情都是"我们俩的关系"，不是某个标签的私有物）。
            # 以前这里直接 BeingState() 重置 → 换个 persona 名字就"什么都不记得了"
            # （2026-11-03 审计：换人格失忆的根因）。
            row = self.db.read(
                "SELECT state FROM being_state ORDER BY updated_at DESC LIMIT 1")
        try:
            state = BeingState.from_dict(json.loads(row[0]["state"])) if row else BeingState()
        except (TypeError, ValueError):
            state = BeingState()
        if not state.online_since or state.online_since < self.process_started:
            state.online_since = current
        state.tick(now=current, hour=hour)
        self.save(state, now=current)
        return state

    def save(self, state: BeingState, *, now: float | None = None) -> None:
        current = time.time() if now is None else float(now)
        with self.db.tx() as conn:
            conn.execute(
                "INSERT INTO being_state(persona,state,updated_at) VALUES(?,?,?)"
                " ON CONFLICT(persona) DO UPDATE SET"
                " state=excluded.state,updated_at=excluded.updated_at",
                (self.persona, json.dumps(state.to_dict(), ensure_ascii=False), current),
            )

    def snapshot(
        self, *, now: float | None = None, hour: int | None = None,
    ) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        return self.load(now=current, hour=hour).snapshot(now=current)

    def observe(
        self,
        text: str,
        *,
        now: float | None = None,
        hour: int | None = None,
    ) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        state = self.load(now=current, hour=hour)
        before = state.snapshot(now=current)
        event_type, intensity = infer_event(text)
        state.observe(event_type, intensity, now=current, hour=hour)
        self.save(state, now=current)
        after = state.snapshot(now=current)
        summary = " ".join(str(text or "").split())[:500]
        with self.db.tx() as conn:
            conn.execute(
                "INSERT INTO being_event(persona,kind,summary,valence_delta,"
                "energy_delta,created_at) VALUES(?,?,?,?,?,?)",
                (
                    self.persona, event_type, summary,
                    float(after["valence"]) - float(before["valence"]),
                    float(after["energy"]) - float(before["energy"]),
                    current,
                ),
            )
            conn.execute(
                "DELETE FROM being_event WHERE id IN ("
                " SELECT id FROM being_event WHERE persona=? ORDER BY id DESC LIMIT -1 OFFSET 1000"
                ")", (self.persona,),
            )
            private_note = {
                "anger": "刚才有点被刺到，先不全说出来。",
                "stress": "刚才有点压得慌，想缓一缓。",
                "comfort": "刚才被接住了，心里软了一点。",
                "heal": "刚才被安慰到，但不想立刻表现得太明显。",
                "praise": "刚才被夸得有点开心。",
                "joy": "刚才有件小事让我心情亮了。",
            }.get(event_type)
            if private_note and intensity >= 0.55:
                duplicate = conn.execute(
                    "SELECT 1 FROM being_note WHERE persona=? AND text=?"
                    " AND created_at>=? LIMIT 1",
                    (self.persona, private_note, current - 6 * 3600),
                ).fetchone()
                if duplicate is None:
                    conn.execute(
                        "INSERT INTO being_note(persona,text,visibility,created_at)"
                        " VALUES(?,?,?,?)",
                        (self.persona, private_note, "private", current),
                    )
                conn.execute(
                    "DELETE FROM being_note WHERE id IN ("
                    " SELECT id FROM being_note WHERE persona=? ORDER BY id DESC LIMIT -1 OFFSET 500"
                    ")", (self.persona,),
                )
        return after

    def mark_proactive(self, *, now: float | None = None) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        state = self.load(now=current)
        state.last_proactive = current
        state.energy = _clamp(state.energy - 1.0, 0.0, 100.0)
        self.save(state, now=current)
        return state.snapshot(now=current)

    def advance_life(
        self,
        *,
        now: float | None = None,
        roll: float = 0.5,
    ) -> dict[str, Any] | None:
        current = time.time() if now is None else float(now)
        state = self.load(now=current)
        event = state.advance_life(now=current, roll=roll)
        if event is None:
            return None
        self.save(state, now=current)
        with self.db.tx() as conn:
            conn.execute(
                "INSERT INTO being_event(persona,kind,summary,valence_delta,"
                "energy_delta,created_at) VALUES(?,?,?,?,?,?)",
                (
                    self.persona,
                    event["kind"],
                    event["summary"],
                    event["valence_delta"],
                    event["energy_delta"],
                    current,
                ),
            )
            note = str(event.get("note") or "").strip()
            if note and float(roll) < 0.18:
                conn.execute(
                    "INSERT INTO being_note(persona,text,visibility,created_at)"
                    " VALUES(?,?,?,?)",
                    (self.persona, note[:1000], "private", current),
                )
            conn.execute(
                "DELETE FROM being_event WHERE id IN ("
                " SELECT id FROM being_event WHERE persona=? ORDER BY id DESC LIMIT -1 OFFSET 1000"
                ")", (self.persona,),
            )
            conn.execute(
                "DELETE FROM being_note WHERE id IN ("
                " SELECT id FROM being_note WHERE persona=? ORDER BY id DESC LIMIT -1 OFFSET 500"
                ")", (self.persona,),
            )
        return event

    def record_event(
        self,
        kind: str,
        summary: str,
        *,
        valence_delta: float = 0.0,
        energy_delta: float = 0.0,
        now: float | None = None,
    ) -> int:
        """Record an externally supplied life trace (Qzone, music, diary...)."""
        current = time.time() if now is None else float(now)
        text = " ".join(str(summary or "").split())[:1000]
        if not text:
            return 0
        state = self.load(now=current)
        state.valence = _clamp(
            state.valence + float(valence_delta), -20.0, 20.0)
        state.energy = _clamp(
            state.energy + float(energy_delta), 0.0, 100.0)
        self.save(state, now=current)
        with self.db.tx() as conn:
            cur = conn.execute(
                "INSERT INTO being_event(persona,kind,summary,valence_delta,"
                "energy_delta,created_at) VALUES(?,?,?,?,?,?)",
                (self.persona, str(kind or "life")[:80], text,
                 float(valence_delta), float(energy_delta), current),
            )
            return int(cur.lastrowid)

    def note(
        self,
        text: str,
        *,
        visibility: str = "private",
        now: float | None = None,
    ) -> int:
        current = time.time() if now is None else float(now)
        value = " ".join(str(text or "").split())[:1000]
        if not value:
            return 0
        with self.db.tx() as conn:
            cur = conn.execute(
                "INSERT INTO being_note(persona,text,visibility,created_at)"
                " VALUES(?,?,?,?)",
                (self.persona, value, visibility, current),
            )
            return int(cur.lastrowid)

    def events(self, *, limit: int = 5) -> list[dict[str, Any]]:
        """最近发生的事。当前 persona 还没记过事时，回落到最近的整体历史——

        "昨天你说要考试"这种事属于这段关系，不该因为改了 persona 名字就抹掉。
        """
        rows = [
            dict(row) for row in self.db.read(
                "SELECT kind,summary,valence_delta,energy_delta,created_at"
                " FROM being_event WHERE persona=? ORDER BY id DESC LIMIT ?",
                (self.persona, max(1, int(limit))),
            )
        ]
        if rows:
            return rows
        return [
            dict(row) for row in self.db.read(
                "SELECT kind,summary,valence_delta,energy_delta,created_at"
                " FROM being_event ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            )
        ]

    def self_events(self, *, limit: int = 5) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in INTERACTION_KINDS)
        eng = ",".join("?" for _ in ENGINEERING_KINDS)
        return [
            dict(row) for row in self.db.read(
                "SELECT kind,summary,valence_delta,energy_delta,created_at"
                " FROM being_event WHERE persona=?"
                f" AND kind NOT IN ({marks})"
                f" AND kind NOT IN ({eng}) ORDER BY id DESC LIMIT ?",
                (
                    self.persona,
                    *sorted(INTERACTION_KINDS),
                    *sorted(ENGINEERING_KINDS),
                    max(1, int(limit)),
                ),
            )
        ]

    def notes(self, *, limit: int = 5, private_only: bool = False) -> list[dict[str, Any]]:
        sql = (
            "SELECT id,text,visibility,created_at FROM being_note"
            " WHERE persona=?"
        )
        if private_only:
            sql += " AND visibility='private'"
        sql += " ORDER BY id DESC LIMIT ?"
        return [
            dict(row) for row in self.db.read(
                sql, (self.persona, max(1, int(limit))))
        ]

    def prompt_block(
        self,
        snapshot: dict[str, Any],
        *,
        proactive: bool = False,
    ) -> str:
        events = self.self_events(limit=3)
        notes = self.notes(limit=2, private_only=True)
        lines = [
            "## 你自己的生活状态（不是用户事实，不要逐项报告）",
            (
                f"精力：{snapshot['energy_word']}（{snapshot['energy']:.0f}/100）。"
                f"情绪：{snapshot['mood']}（{snapshot['valence']:+.0f}/20）。"
                f"社交电量：{snapshot['social']:.0f}/100。"
            ),
        ]
        if float(snapshot.get("energy", 60)) < 30:
            lines.append("现在确实有点累：回复更短，少开新话题，不要硬装精神。")
        elif float(snapshot.get("valence", 0)) >= 8:
            lines.append("现在心情不错，可以自然轻快一点，但别无故刷屏。")
        elif float(snapshot.get("valence", 0)) <= -8:
            lines.append("现在情绪略低，措辞收一点；不要迁怒，也不要编造委屈原因。")
        if float(snapshot.get("online_minutes", 999)) < 30:
            lines.append("你刚上线不久；不用播报上线，除非当前语境自然接得上。")
        activity = str(snapshot.get("activity") or "").strip()
        focus = str(snapshot.get("current_focus") or "").strip()
        if activity:
            until = float(snapshot.get("activity_until") or 0.0)
            state = "正在" if until > time.time() else "刚才"
            lines.append(
                f"你自己的近况：{state}{focus or '做自己的事'}。"
                f"具体经过是「{activity}」。不要机械播报，只有自然接得上才提。")
        if proactive:
            lines.append("这是你主动开口，不是用户先说话。")
        if events:
            lines.append("最近你自己的经历：" + "；".join(
                str(event["summary"]) for event in reversed(events)
                if str(event.get("summary") or "").strip()))
        if notes:
            lines.append(
                "只属于你的小本子（不能直接照念，也不要声称用户知道）："
                + "；".join(str(note["text"]) for note in reversed(notes)))
        if not events and not activity:
            lines.append("你刚安静下来，还没有形成下一段自己的经历。")
        lines.append("你可以保留没说出口的想法，也有权说不想聊、需要安静。")
        return "\n".join(line for line in lines if line)


__all__ = ["BeingState", "BeingStore"]
