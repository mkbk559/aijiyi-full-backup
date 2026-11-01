"""WhaleGirl V5 生命卡七引擎：公式、边界、序列化。"""

from __future__ import annotations

import math
import shutil
import unittest
import uuid
from pathlib import Path

from aiji import Memory
from aiji.config import Settings
from aiji.db import DB
from aiji.life import (
    ContextBudget,
    DeclarativeEngine,
    EndocrineEngine,
    LifeCard,
    load_life_card,
    OfflineCompensation,
    PersonalityKernel,
    ProceduralEngine,
    save_life_card,
    SynapticDiffusion,
    infer_event,
)
from aiji.store import Ident
from aiji.head import Head


class TestPersonality(unittest.TestCase):
    def test_delta_is_clamped_and_committed(self):
        kernel = PersonalityKernel(
            vector={"warmth": 0.5, "curiosity": 0.4},
            core={"identity": 0.25},
        )
        ok = kernel.apply_delta({"warmth": 0.5, "curiosity": -0.5})
        self.assertTrue(ok)
        self.assertAlmostEqual(kernel.vector["warmth"], 0.62)
        self.assertAlmostEqual(kernel.vector["curiosity"], 0.28)

    def test_core_strength_rejects_destructive_change_atomically(self):
        kernel = PersonalityKernel(
            vector={"identity": 0.2}, core={"identity": 0.2},
        )
        before = dict(kernel.vector)
        self.assertFalse(kernel.apply_delta({"identity": -0.2}))
        self.assertEqual(kernel.vector, before)


class TestEndocrine(unittest.TestCase):
    def test_hormones_regress_then_event_perturbs(self):
        engine = EndocrineEngine(oxy=0.8, cort=0.4)
        engine.regress(1.0)
        self.assertAlmostEqual(engine.oxy, 0.50 + 0.30 * math.exp(-0.02), places=6)
        self.assertAlmostEqual(engine.cort, 0.20 + 0.20 * math.exp(-0.02), places=6)
        engine.perturb("comfort", 1.0)
        self.assertGreater(engine.oxy, 0.5)
        self.assertLess(engine.cort, 0.4)

    def test_endocrine_factor_reduces_capacity_under_stress(self):
        calm = EndocrineEngine(cort=0.2, oxy=0.5)
        stress = EndocrineEngine(cort=0.8, oxy=0.3)
        self.assertGreater(calm.endo_factor(), stress.endo_factor())
        self.assertGreater(calm.skill_ceiling(), stress.skill_ceiling())
        self.assertAlmostEqual(EndocrineEngine().endo_factor(), 1.0)

    def test_neutral_event_does_not_change_hormones(self):
        engine = EndocrineEngine()
        before = (engine.sero, engine.dopa, engine.cort, engine.oxy)
        engine.perturb("neutral", 1.0)
        self.assertEqual(
            (engine.sero, engine.dopa, engine.cort, engine.oxy), before)


class TestDeclarative(unittest.TestCase):
    def test_piecewise_short_term_decay(self):
        engine = DeclarativeEngine()
        young = engine.decay_weight(1.0, 1.0)
        old = engine.decay_weight(1.0, 4.0)
        self.assertLess(young, 1.0)
        self.assertLess(old, young)

    def test_promotion_and_sink(self):
        engine = DeclarativeEngine()
        self.assertEqual(engine.promotion_region(0.34, 0), "SHORT")
        self.assertEqual(engine.promotion_region(0.50, 1), "MID")
        self.assertEqual(engine.promotion_region(0.80, 2), "LONG")
        self.assertEqual(engine.sink_region("LONG", 0.01, 400), "ARCHIVE")
        self.assertEqual(engine.sink_region("ARCHIVE", 0.001, 100), "DEEP")
        self.assertEqual(engine.sink_region("DEEP", 0.0001, 100), "TRACE")


class TestProcedural(unittest.TestCase):
    def test_sm2_updates_ef_and_reinforces(self):
        engine = ProceduralEngine()
        ef = engine.update_ef(1.5, 5)
        self.assertAlmostEqual(ef, 1.6)
        self.assertAlmostEqual(engine.update_ef(2.5, 5), 2.5)
        l0 = engine.decay(0.8, interval_hours=10, dt_hours=24)
        self.assertLess(l0, 0.8)
        l1 = engine.reinforce(l0, q=5, interval_hours=10)
        self.assertGreater(l1, l0)
        self.assertLessEqual(l1, 1.0)
        self.assertGreater(engine.recall_score(0.8, 0.8, 2.0), 0)


class TestOffline(unittest.TestCase):
    def test_offline_time_is_compressed_and_capped(self):
        engine = OfflineCompensation()
        self.assertEqual(engine.compress_days(3), 3)
        self.assertEqual(engine.compress_days(7), 7)
        self.assertAlmostEqual(engine.compress_days(17), 12)
        self.assertAlmostEqual(engine.compress_days(30), 18.5)
        self.assertEqual(engine.compress_days(10000), 90)


class TestBudget(unittest.TestCase):
    def test_weights_normalize_and_every_budget_has_floor(self):
        plan = ContextBudget().allocate(
            scenario="task", hormones=EndocrineEngine(), intent="coding",
            turns=20, offline_days=1, token_budget=6000,
        )
        self.assertGreaterEqual(plan["weights"]["identity"], 0.15)
        self.assertAlmostEqual(sum(plan["weights"].values()), 1.0, places=8)
        self.assertTrue(all(v >= 150 for v in plan["budgets"].values()))
        self.assertEqual(sum(plan["budgets"].values()) >= 6000, True)


class TestSynaptic(unittest.TestCase):
    def test_three_hop_diffusion_and_threshold(self):
        graph = {"A": {"B": 1.0}, "B": {"C": 1.0}, "C": {"D": 1.0}, "D": {}}
        result = SynapticDiffusion(graph).diffuse(
            {"A": 1.0}, max_hops=3, layer_decay=0.75, min_potential=0.05,
        )
        self.assertAlmostEqual(result["A"], 1.0)
        self.assertAlmostEqual(result["B"], 0.75)
        self.assertAlmostEqual(result["C"], 0.5625)
        self.assertAlmostEqual(result["D"], 0.421875)


class TestLifeCard(unittest.TestCase):
    def test_roundtrip_and_tick(self):
        card = LifeCard()
        card.apply_event("comfort", 1.0, now=1000.0)
        data = card.to_dict()
        restored = LifeCard.from_dict(data)
        self.assertAlmostEqual(restored.endo.oxy, card.endo.oxy)
        restored.tick(now=1000.0 + 3600)
        self.assertIsNotNone(restored.last_tick)

    def test_life_card_persists_per_identity(self):
        root = Path(__file__).resolve().parent.parent / ".tmp"
        root.mkdir(mode=0o777, exist_ok=True)
        path = root / f"life-{uuid.uuid4().hex[:8]}.db"
        ident = Ident("desktop", "owner", "default")
        try:
            db = DB(path)
            card = LifeCard()
            card.endo.oxy = 0.77
            save_life_card(db, ident, card, now=1000.0)
            loaded = load_life_card(db, ident)
            self.assertAlmostEqual(loaded.endo.oxy, 0.77)
            db.close()
            db2 = DB(path)
            self.assertAlmostEqual(load_life_card(db2, ident).endo.oxy, 0.77)
            db2.close()
        finally:
            path.unlink(missing_ok=True)

    def test_event_inference_and_memory_integration(self):
        self.assertEqual(infer_event("谢谢你，抱着你真好")[0], "comfort")
        self.assertEqual(infer_event("你个笨蛋，气死我了")[0], "anger")
        root = Path(__file__).resolve().parent.parent / ".tmp"
        path = root / f"life-memory-{uuid.uuid4().hex[:8]}"
        path.mkdir(mode=0o777)
        memory = Memory(Settings(
            db_path=path / "life.db", routes=(), reflection_interval_s=60,
            debounce_ms=0, humanize_min_ms=0, humanize_max_ms=0,
            typing_min_ms=0, typing_max_ms=0,
        ), engines="none")
        ident = Ident("desktop", "owner", "default")
        try:
            before = memory.life_snapshot(ident)
            after = memory.life_event(ident, "comfort", 1.0, now=1000.0)
            self.assertGreater(after["hormones"]["oxy"], before["hormones"]["oxy"])
            self.assertGreater(
                after["personality"].get("warmth", 0),
                before["personality"].get("warmth", 0),
            )
            self.assertGreater(
                memory.life_snapshot(ident, now=1000.0)["hormones"]["oxy"],
                before["hormones"]["oxy"],
            )
            self.assertGreater(
                memory.life_snapshot(ident, now=1000.0)["personality"].get("warmth", 0),
                0,
            )
        finally:
            memory.close()
            shutil.rmtree(path, ignore_errors=True)

    def test_head_uses_life_card_event_and_exposes_snapshot(self):
        root = Path(__file__).resolve().parent.parent / ".tmp"
        path = root / f"life-head-{uuid.uuid4().hex[:8]}"
        path.mkdir(mode=0o777)
        memory = Memory(Settings(
            db_path=path / "life.db", routes=(), reflection_interval_s=60,
            debounce_ms=0, humanize_min_ms=0, humanize_max_ms=0,
            typing_min_ms=0, typing_max_ms=0,
        ), engines="none")

        class Pool:
            _states = [object()]

            def chat(self, messages, *, timeout_s=None):
                return type("R", (), {
                    "text": "抱住你", "route": "fake", "latency_ms": 1.0,
                })()

        memory.pool = Pool()
        memory.reflective.pool = memory.pool
        try:
            reply = Head(memory).reply(
                [{"role": "user", "content": "谢谢你，抱着你真好"}],
                Ident("desktop", "owner", "default"),
                session="s1",
            )
            self.assertIsNotNone(reply.life)
            self.assertGreater(reply.life["hormones"]["oxy"], 0.5)
        finally:
            memory.close()
            shutil.rmtree(path, ignore_errors=True)

    def test_neutral_reply_does_not_add_success_hormone_boost(self):
        root = Path(__file__).resolve().parent.parent / ".tmp"
        path = root / f"life-neutral-{uuid.uuid4().hex[:8]}"
        path.mkdir(mode=0o777)
        memory = Memory(Settings(
            db_path=path / "life.db", routes=(), reflection_interval_s=60,
            debounce_ms=0, humanize_min_ms=0, humanize_max_ms=0,
            typing_min_ms=0, typing_max_ms=0,
        ), engines="none")

        class Pool:
            _states = [object()]

            def chat(self, messages, *, timeout_s=None, **_kwargs):
                return type("R", (), {
                    "text": "嗯，知道了", "route": "fake", "latency_ms": 1.0,
                })()

        memory.pool = Pool()
        ident = Ident("desktop", "owner", "default")
        try:
            before = memory.life_snapshot(ident)["hormones"]["dopa"]
            reply = Head(memory).reply(
                [{"role": "user", "content": "今天只是随便说说"}],
                ident,
                session="neutral",
            )
            self.assertAlmostEqual(
                reply.life["hormones"]["dopa"], before, places=6)
        finally:
            memory.close()
            shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
