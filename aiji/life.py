"""WhaleGirl V5 生命卡七引擎。

纯确定性内核，不直接调用模型。模型只负责提出人格变化、回忆候选和行为；
这里负责钳位、衰减、晋升、预算、扩散和离线补偿。
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Any

from .db import DB
from .store import Ident


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _norm(values: dict[str, float]) -> float:
    return math.sqrt(sum(float(v) ** 2 for v in values.values()))


def infer_event(text: str) -> tuple[str, float]:
    value = str(text or "").lower()
    intensity = min(0.95, 0.45 + 0.1 * (value.count("!") + value.count("！")))
    anger = ("笨蛋", "气死", "滚", "讨厌", "生气", "傻", "垃圾", "fuck")
    comfort = ("谢谢", "抱", "安慰", "辛苦", "陪你", "喜欢", "爱你", "亲亲")
    praise = ("厉害", "聪明", "做得好", "棒", "优秀", "真强")
    if any(word in value for word in anger):
        return "anger", min(0.95, intensity + 0.2)
    if any(word in value for word in comfort):
        return "comfort", intensity
    if any(word in value for word in praise):
        return "praise", intensity
    if "?" in value or "？" in value:
        return "curiosity", max(0.3, intensity - 0.1)
    return "neutral", max(0.2, intensity - 0.2)


def mood_label(card: "LifeCard" | EndocrineEngine) -> str:
    endo = card.endo if isinstance(card, LifeCard) else card
    if endo.cort >= 0.55:
        return "紧张"
    if endo.oxy >= 0.65:
        return "亲近"
    if endo.dopa >= 0.60:
        return "活跃"
    return "平静"


@dataclass(slots=True)
class PersonalityKernel:
    vector: dict[str, float] = field(default_factory=lambda: {"identity": 0.25})
    core: dict[str, float] = field(default_factory=lambda: {"identity": 0.25})
    max_delta: float = 0.12
    core_floor: float = 0.15

    def apply_delta(self, delta: dict[str, float]) -> bool:
        candidate = {k: float(v) for k, v in self.vector.items()}
        core = {k: float(v) for k, v in self.core.items()}
        for key, value in delta.items():
            change = _clamp(float(value), -self.max_delta, self.max_delta)
            candidate[str(key)] = float(candidate.get(key, 0.0)) + change
            if str(key) in core:
                core[str(key)] = float(core[str(key)]) + change
        if _norm(core) < self.core_floor:
            return False
        self.vector = candidate
        self.core = core
        return True


@dataclass(slots=True)
class EndocrineEngine:
    sero: float = 0.60
    dopa: float = 0.40
    cort: float = 0.20
    oxy: float = 0.50
    lam_h: float = 0.02

    BASELINES = {"sero": 0.60, "dopa": 0.40, "cort": 0.20, "oxy": 0.50}

    def regress(self, dt_hours: float) -> None:
        factor = math.exp(-self.lam_h * max(0.0, float(dt_hours)))
        for name, baseline in self.BASELINES.items():
            value = baseline + (float(getattr(self, name)) - baseline) * factor
            setattr(self, name, _clamp(value, 0.0, 1.0))

    def perturb(self, event_type: str, intensity: float = 1.0) -> None:
        s = _clamp(intensity, 0.0, 1.0)
        kind = str(event_type or "").lower()
        if kind in ("joy", "praise", "positive", "success"):
            self.dopa += 0.12 * s
            self.sero += 0.04 * s
            self.cort -= 0.03 * s
        elif kind in ("anger", "insult", "negative", "stress"):
            self.cort += 0.18 * s
            self.sero -= 0.08 * s
            self.dopa -= 0.03 * s
        elif kind in ("comfort", "heal", "care"):
            self.oxy += 0.12 * s
            self.cort -= 0.12 * s
            self.sero += 0.04 * s
        elif kind in ("intimacy", "affection"):
            self.oxy += 0.15 * s
            self.dopa += 0.05 * s
        else:
            return
        for name in self.BASELINES:
            setattr(self, name, _clamp(float(getattr(self, name)), 0.0, 1.0))

    def endo_factor(self) -> float:
        stress = max(0.0, self.cort - 0.20) / 0.80
        return (1.0 + 0.30 * (self.oxy - 0.50)) * (1.0 - 0.35 * stress)

    def skill_ceiling(self) -> float:
        stress = max(0.0, self.cort - 0.20) / 0.80
        return 5.0 * (1.0 - 0.60 * stress)


class DeclarativeEngine:
    LAM_S1 = 1.80e-6
    LAM_S2 = 1.50e-5
    LAM_M = 2.70e-7
    LAM_L = 2.16e-8

    def decay_weight(self, weight: float, age_days: float, region: str = "SHORT") -> float:
        age_s = max(0.0, float(age_days)) * 86400.0
        if region == "MID":
            return max(0.0, weight * math.exp(-self.LAM_M * age_s))
        if region in ("LONG", "ARCHIVE", "DEEP", "TRACE"):
            return max(0.0, weight * math.exp(-self.LAM_L * age_s))
        if age_s <= 3 * 86400:
            return max(0.0, weight * math.exp(-self.LAM_S1 * age_s))
        short = weight * math.exp(-self.LAM_S1 * 3 * 86400)
        return max(0.0, short * math.exp(-self.LAM_S2 * (age_s - 3 * 86400)))

    @staticmethod
    def promotion_score(emotion: float, entities: float, recalls: float,
                        attachment: float) -> float:
        return (0.40 * _clamp(emotion, 0, 1)
                + 0.20 * _clamp(entities, 0, 1)
                + 0.25 * _clamp(recalls, 0, 1)
                + 0.15 * _clamp(attachment, 0, 1))

    @staticmethod
    def promotion_region(score: float, recall_count: int) -> str:
        if score >= 0.60 and recall_count >= 2:
            return "LONG"
        if score >= 0.35:
            return "MID"
        return "SHORT"

    @staticmethod
    def sink_region(region: str, weight: float, age_days: float) -> str:
        if region == "LONG" and weight < 0.02 and age_days > 365:
            return "ARCHIVE"
        if region == "ARCHIVE" and weight < 0.002:
            return "DEEP"
        if region == "DEEP" and weight < 0.0002:
            return "TRACE"
        return region


@dataclass(slots=True)
class ProceduralEngine:
    lam_b: float = 0.0012
    kq: float = 0.25
    dormant_l: float = 0.05

    def decay(self, proficiency: float, *, interval_hours: float, dt_hours: float) -> float:
        interval = max(1.0, float(interval_hours))
        lam_eff = self.lam_b * (1.0 / interval) ** 0.3
        return _clamp(float(proficiency) * math.exp(-lam_eff * max(0.0, dt_hours)), 0.0, 1.0)

    @staticmethod
    def update_ef(ef: float, quality: float) -> float:
        q = _clamp(quality, 0.0, 5.0)
        new = float(ef) + (0.1 - (5.0 - q) * (0.08 + (5.0 - q) * 0.02))
        return _clamp(new, 1.3, 2.5)

    def reinforce(self, proficiency: float, *, q: float, interval_hours: float) -> float:
        quality = _clamp(q, 0.0, 5.0)
        # 文档中 I 以小时计，强化增益用“占一天的比重”归一，保证不出现负增长。
        interval = min(1.0, max(0.0, float(interval_hours)) / 24.0)
        gain = self.kq * (quality / 5.0) * (1.0 - interval)
        return _clamp(float(proficiency) + gain, 0.0, 1.0)

    def recall_score(self, similarity: float, proficiency: float, ef: float,
                     *, attachment_bonus: bool = False) -> float:
        score = (_clamp(similarity, 0, 1) * math.sqrt(_clamp(proficiency, 0, 1))
                 * (1.0 + 0.15 * (_clamp(ef, 1.3, 2.5) - 1.3)))
        if proficiency < self.dormant_l:
            score *= 0.2
        if attachment_bonus:
            score *= 1.1
        return score


class OfflineCompensation:
    @staticmethod
    def compress_days(raw_days: float) -> float:
        raw = max(0.0, float(raw_days))
        if raw < 7:
            effective = raw
        elif raw < 30:
            effective = 7 + (raw - 7) * 0.5
        else:
            effective = 7 + 23 * 0.5 + (raw - 30) * 0.15
        return min(effective, 90.0)


class ContextBudget:
    DIMENSIONS = ("identity", "self", "recent", "longterm", "impression", "skill")
    SCENARIOS = {
        "chat": {"identity": .20, "self": .15, "recent": .25, "longterm": .15, "impression": .15, "skill": .10},
        "task": {"identity": .16, "self": .12, "recent": .20, "longterm": .12, "impression": .10, "skill": .30},
        "recall": {"identity": .18, "self": .12, "recent": .12, "longterm": .33, "impression": .15, "skill": .10},
        "repair": {"identity": .20, "self": .18, "recent": .20, "longterm": .17, "impression": .15, "skill": .10},
    }

    def allocate(
        self,
        *,
        scenario: str = "chat",
        hormones: EndocrineEngine | None = None,
        intent: str = "",
        turns: int = 0,
        offline_days: float = 0.0,
        token_budget: int = 6000,
    ) -> dict[str, Any]:
        weights = dict(self.SCENARIOS.get(scenario, self.SCENARIOS["chat"]))
        endo = hormones or EndocrineEngine()
        weights["impression"] *= 1.0 + 0.25 * max(0.0, endo.oxy - .5)
        weights["skill"] *= 1.0 - 0.25 * max(0.0, endo.cort - .2)
        if intent in ("coding", "task", "repair"):
            weights["skill"] *= 1.5
            weights["self"] *= 1.1
        elif intent in ("memory", "recall"):
            weights["longterm"] *= 1.6
        if turns > 20:
            weights["recent"] *= 1.15
        if offline_days > 7:
            weights["longterm"] *= 1.15
            weights["identity"] *= 1.05

        weights["identity"] = max(.15, weights["identity"])
        total = sum(weights.values())
        identity = max(.15, weights["identity"] / total)
        other_total = sum(v for k, v in weights.items() if k != "identity")
        scale = (1.0 - identity) / other_total if other_total > 0 else 0.0
        weights = {
            "identity": identity,
            **{k: v * scale for k, v in weights.items() if k != "identity"},
        }
        budgets = {
            k: max(150, round(float(token_budget) * v))
            for k, v in weights.items()
        }
        return {"weights": weights, "budgets": budgets}


class SynapticDiffusion:
    def __init__(self, graph: dict[str, dict[str, float]]):
        self.graph = graph

    def diffuse(
        self,
        seeds: dict[str, float],
        *,
        max_hops: int = 3,
        layer_decay: float = 0.75,
        min_potential: float = 0.05,
    ) -> dict[str, float]:
        potential = {str(k): float(v) for k, v in seeds.items()}
        frontier = dict(potential)
        for _ in range(max(0, int(max_hops))):
            next_frontier: dict[str, float] = {}
            for node, current in frontier.items():
                for neighbor, weight in self.graph.get(node, {}).items():
                    value = current * layer_decay * float(weight)
                    if value < min_potential:
                        continue
                    next_frontier[neighbor] = next_frontier.get(neighbor, 0.0) + value
            if not next_frontier:
                break
            for node, value in next_frontier.items():
                potential[node] = potential.get(node, 0.0) + value
            frontier = next_frontier
        return {k: v for k, v in potential.items() if v >= min_potential}


@dataclass(slots=True)
class LifeCard:
    personality: PersonalityKernel = field(default_factory=PersonalityKernel)
    endo: EndocrineEngine = field(default_factory=EndocrineEngine)
    declarative: DeclarativeEngine = field(default_factory=DeclarativeEngine)
    procedural: ProceduralEngine = field(default_factory=ProceduralEngine)
    offline: OfflineCompensation = field(default_factory=OfflineCompensation)
    last_tick: float | None = None

    def tick(self, *, now: float | None = None) -> float:
        current = time.time() if now is None else float(now)
        if self.last_tick is None:
            self.last_tick = current
            return 0.0
        raw_hours = max(0.0, (current - self.last_tick) / 3600.0)
        effective_days = self.offline.compress_days(raw_hours / 24.0)
        effective_hours = effective_days * 24.0
        self.endo.regress(effective_hours)
        self.last_tick = current
        return effective_hours

    def apply_event(self, event_type: str, intensity: float,
                    *, now: float | None = None) -> None:
        self.tick(now=now)
        self.endo.perturb(event_type, intensity)
        # Small, reversible changes accumulate from actual interactions.
        dimension = {
            "comfort": "warmth", "care": "warmth", "heal": "warmth",
            "praise": "confidence", "joy": "confidence",
            "curiosity": "curiosity", "anger": "caution",
        }.get(str(event_type or "").lower())
        if dimension:
            self.personality.apply_delta({dimension: 0.015 * _clamp(intensity, 0, 1)})

    def context_plan(self, *, scenario: str = "chat", intent: str = "",
                     turns: int = 0, token_budget: int = 6000) -> dict[str, Any]:
        current = time.time()
        offline_days = 0.0
        if self.last_tick is not None:
            offline_days = self.offline.compress_days(
                max(0.0, (current - self.last_tick) / 86400.0)
            )
        return ContextBudget().allocate(
            scenario=scenario, hormones=self.endo, intent=intent,
            turns=turns, offline_days=offline_days, token_budget=token_budget,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "personality": {"vector": dict(self.personality.vector),
                            "core": dict(self.personality.core)},
            "endo": {"sero": self.endo.sero, "dopa": self.endo.dopa,
                     "cort": self.endo.cort, "oxy": self.endo.oxy},
            "last_tick": self.last_tick,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LifeCard":
        data = data or {}
        personality = data.get("personality") or {}
        endo = data.get("endo") or {}
        return cls(
            personality=PersonalityKernel(
                vector={k: float(v) for k, v in (personality.get("vector") or {}).items()}
                or {"identity": .25},
                core={k: float(v) for k, v in (personality.get("core") or {}).items()}
                or {"identity": .25},
            ),
            endo=EndocrineEngine(
                sero=float(endo.get("sero", .60)),
                dopa=float(endo.get("dopa", .40)),
                cort=float(endo.get("cort", .20)),
                oxy=float(endo.get("oxy", .50)),
            ),
            last_tick=data.get("last_tick"),
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "personality": dict(self.personality.vector),
            "hormones": {
                "sero": self.endo.sero, "dopa": self.endo.dopa,
                "cort": self.endo.cort, "oxy": self.endo.oxy,
            },
            "endo_factor": self.endo.endo_factor(),
            "skill_ceiling": self.endo.skill_ceiling(),
            "last_tick": self.last_tick,
        }


def save_life_card(db: DB, ident: Any, card: LifeCard,
                   *, now: float | None = None) -> None:
    ident = Ident.coerce(ident)
    timestamp = time.time() if now is None else float(now)
    card.last_tick = timestamp
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO life_card(platform,platform_user_id,scope,card,updated_at)"
            " VALUES(?,?,?,?,?)"
            " ON CONFLICT(platform,platform_user_id,scope)"
            " DO UPDATE SET card=excluded.card,updated_at=excluded.updated_at",
            (ident.platform, ident.platform_user_id, str(ident.scope or "default"),
             json.dumps(card.to_dict(), ensure_ascii=False), timestamp),
        )


def load_life_card(db: DB, ident: Any) -> LifeCard:
    ident = Ident.coerce(ident)
    row = db.read(
        "SELECT card FROM life_card WHERE platform=? AND platform_user_id=? AND scope=?",
        (ident.platform, ident.platform_user_id, str(ident.scope or "default")),
    )
    if not row:
        return LifeCard()
    try:
        data = json.loads(row[0]["card"] or "{}")
    except (TypeError, ValueError):
        data = {}
    return LifeCard.from_dict(data if isinstance(data, dict) else {})


__all__ = [
    "ContextBudget", "DeclarativeEngine", "EndocrineEngine", "LifeCard",
    "infer_event", "load_life_card", "mood_label", "OfflineCompensation", "PersonalityKernel",
    "ProceduralEngine", "save_life_card",
    "SynapticDiffusion",
]
