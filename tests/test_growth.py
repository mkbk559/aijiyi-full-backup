"""成长引擎测试：年龄是唯一真源，人格文案与能力标尺从年龄渲染。"""
from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest import mock

from aiji.brain import growth, persona, selfhood
from aiji.right_hand.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def workdir() -> Path:
    path = ROOT / ".tmp" / f"growth-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o777)
    return path


class GrowthClockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = workdir()
        self.path = self.tmp / "growth.json"

    def tearDown(self) -> None:
        growth.configure(age=None, rate=None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_run_writes_state_with_defaults(self):
        g = growth.Growth(self.path, now=1000.0)
        state = g.state()
        self.assertEqual(state["start_age"], growth.START_AGE)
        self.assertEqual(state["rate"], growth.DEFAULT_RATE)
        self.assertTrue(self.path.exists())
        self.assertAlmostEqual(g.age_years(), growth.START_AGE, places=3)

    def test_age_grows_with_rate(self):
        g = growth.Growth(self.path, now=0.0)
        g.state()
        # 12 个真实天、速率 30 → 约 1 角色年
        g2 = growth.Growth(self.path, now=12 * 86400.0)
        self.assertAlmostEqual(g2.age_years(), growth.START_AGE + 30 * 12 / 365.25,
                               places=2)

    def test_rate_override_from_config(self):
        g = growth.Growth(self.path, now=0.0)
        g.state()
        slow = growth.Growth(self.path, now=10 * 86400.0, override_rate=1.0)
        fast = growth.Growth(self.path, now=10 * 86400.0, override_rate=365.25)
        self.assertLess(slow.age_years(), fast.age_years())
        self.assertAlmostEqual(fast.age_years(), growth.START_AGE + 10, places=2)

    def test_locked_age_wins_over_clock(self):
        g = growth.Growth(self.path, now=0.0, override_age=13.0)
        self.assertEqual(g.age_years(), 13.0)
        self.assertEqual(g.stage().id, "teen")

    def test_age_clamped(self):
        g = growth.Growth(self.path, now=0.0, override_age=999.0)
        self.assertEqual(g.age_years(), growth.MAX_AGE)
        g2 = growth.Growth(self.path, now=0.0, override_age=0.0)
        self.assertEqual(g2.age_years(), growth.MIN_AGE)

    def test_env_lock(self):
        with mock.patch.dict("os.environ", {"AIJI_PERSONA_AGE": "15.5"}):
            g = growth.Growth(self.path)
            self.assertEqual(g.age_years(), 15.5)
            self.assertEqual(g.stage().id, "youth")


class StageTest(unittest.TestCase):
    def test_no_gaps_and_ordered(self):
        for lower, upper in zip(growth.STAGES, growth.STAGES[1:]):
            self.assertLess(lower.age_min, upper.age_min, f"{lower.id} 顺序错")
            self.assertEqual(lower.age_max, upper.age_min, f"{lower.id}→{upper.id} 断档")

    def test_stage_boundaries(self):
        cases = {4.0: "early", 6.49: "early", 6.5: "child", 9.99: "child",
                 10.0: "kid10", 12.0: "teen", 15.0: "youth", 18.0: "adult"}
        for age, expect in cases.items():
            self.assertEqual(growth.stage_for(age).id, expect, age)

    def test_sentence_rule_gets_longer_with_age(self):
        def chars(stage_id: str) -> int:
            stage = next(s for s in growth.STAGES if s.id == stage_id)
            return len(stage.sentence)
        self.assertLess(chars("early"), chars("teen"))

    def test_every_stage_declares_rules(self):
        for stage in growth.STAGES:
            for field in ("sentence", "concepts", "topics", "output", "tone"):
                self.assertTrue(getattr(stage, field).strip(), f"{stage.id}.{field} 为空")


class PersonaFromAgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = workdir()
        os.environ["AIJI_GROWTH_PATH"] = str(self.tmp / "g.json")

    def tearDown(self) -> None:
        growth.configure(age=None, rate=None)
        os.environ.pop("AIJI_GROWTH_PATH", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_kid_uses_child_base_with_age_note(self):
        growth.configure(age=8.0)
        block = persona.style_block("child")
        self.assertTrue(block.startswith(persona.CHILD))
        self.assertIn("成长阶段", block)
        self.assertIn("8.0 岁", block)

    def test_teen_switches_to_growing_base(self):
        growth.configure(age=13.0)
        block = persona.style_block("child")
        self.assertTrue(block.startswith(persona.GROWING_BASE))
        self.assertNotIn("叠词", block)
        self.assertIn("13.0 岁", block)

    def test_few_shot_stops_after_ten(self):
        growth.configure(age=7.0)
        self.assertEqual(len(persona.few_shot("child")), 1)
        growth.configure(age=13.0)
        self.assertEqual(persona.few_shot("child"), [])

    def test_reminder_follows_age(self):
        growth.configure(age=7.0)
        self.assertIn("六七岁", persona.reminder("child"))
        growth.configure(age=16.0)
        text = persona.reminder("child")
        self.assertNotIn("六七岁", text)
        self.assertIn("16.0 岁", text)

    def test_tics_shrink_with_age(self):
        self.assertGreater(len(growth.Growth().tics(override=6.0)),
                           len(growth.Growth().tics(override=13.0)))

    def test_adult_traits_unlock_at_ten(self):
        traits = [{"value": "常带调侃语气损对方", "confidence": 0.95, "evidence": 9}]
        self.assertEqual(len(selfhood.kid_safe_traits(traits, "child", 9.9)), 0)
        self.assertEqual(len(selfhood.kid_safe_traits(traits, "child", 10.0)), 1)
        text = selfhood.system_prefix(traits, persona="child", age=12)
        self.assertIn("调侃", text)


class GrowthConfigTest(unittest.TestCase):
    def test_operations_accepts_age_fields(self):
        from aiji.right_hand import operations

        tmp = workdir()
        try:
            path = tmp / "cfg.json"
            operations.save_config(path, {"persona_age": 14})
            self.assertEqual(json.loads(path.read_text("utf-8"))["persona_age"], 14)
            # 传 null 表示"恢复自然长大"，不能报错
            operations.save_config(path, {"persona_age": None})
            data = json.loads(path.read_text("utf-8"))
            self.assertIsNone(data["persona_age"])
            with self.assertRaises(ValueError):
                operations.save_config(path, {"persona_age": 999})
            with self.assertRaises(ValueError):
                operations.save_config(path, {"aging_rate": 0})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_settings_pick_up_age_and_rate(self):
        tmp = workdir()
        try:
            path = tmp / "cfg.json"
            path.write_text(json.dumps(
                {"routes": [], "persona": "child", "persona_age": 11.5,
                 "aging_rate": 60}), encoding="utf-8")
            settings = Settings.load(path)
            self.assertEqual(settings.persona_age, 11.5)
            self.assertEqual(settings.aging_rate, 60.0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_describe_shape(self):
        tmp = workdir()
        try:
            g = growth.Growth(tmp / "g.json", now=0.0, override_age=11.0)
            snap = g.describe()
            for key in ("age", "stage", "stage_label", "next_stage_in_days",
                        "locked", "tics", "adult_traits_unlocked"):
                self.assertIn(key, snap)
            self.assertEqual(snap["stage"], "kid10")
            self.assertTrue(snap["adult_traits_unlocked"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import unittest.mock  # noqa: F401  (供 test_env_lock 用)

    unittest.main()
