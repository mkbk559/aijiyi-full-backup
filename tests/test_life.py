"""WhaleGirl V5 生命卡七引擎：公式、边界、序列化。"""

from __future__ import annotations

import math
import shutil
import unittest
import uuid
from pathlib import Path

from aiji import Memory
from aiji.right_hand.config import Settings
from aiji.hippocampus.db import DB
from aiji.endocrine.life import ContextBudget, EndocrineEngine, LifeCard, load_life_card, OfflineCompensation, PersonalityKernel, save_life_card, infer_event
from aiji.hippocampus.store import Ident
from aiji.brain.head import Head


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
