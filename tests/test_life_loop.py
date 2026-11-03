"""Autonomous life-loop behavior and state semantics."""

from __future__ import annotations

import time
import unittest

from aiji.endocrine.being import BeingStore
from aiji.hippocampus.db import DB
from aiji.endocrine.life_loop import LifeLoop
from tests._tempdir import make_tmp


def noon() -> float:
    return time.mktime((2026, 10, 31, 12, 0, 0, 0, 0, -1))


class LifeLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_tmp(self, "aiji-life-loop-")
        self.db = DB(self.tmp / "life.db")
        self.store = BeingStore(self.db, persona="sunba")

    def tearDown(self):
        self.db.close()

    def test_self_activity_creates_own_event_and_schedules_next_tick(self):
        event = self.store.advance_life(now=noon(), roll=0.2)

        self.assertIsNotNone(event)
        self.assertEqual(event["kind"], "self_activity")
        self.assertTrue(event["summary"])
        snapshot = self.store.snapshot(now=noon(), hour=12)
        self.assertEqual(snapshot["activity"], event["summary"])
        self.assertTrue(snapshot["current_focus"])
        self.assertGreater(snapshot["next_life_at"], noon())

        own = self.store.self_events(limit=5)
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["kind"], "self_activity")

    def test_activity_does_not_repeat_before_next_tick(self):
        first = self.store.advance_life(now=noon(), roll=0.2)
        second = self.store.advance_life(now=noon() + 60, roll=0.8)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(self.store.self_events(limit=10)), 1)

    def test_prompt_keeps_user_text_out_of_own_experiences(self):
        user_text = "群友说今天去河边钓鱼"
        self.store.observe(user_text, now=noon(), hour=12)
        event = self.store.advance_life(now=noon() + 1, roll=0.1)
        self.assertIsNotNone(event)

        snapshot = self.store.snapshot(now=noon() + 2, hour=12)
        block = self.store.prompt_block(snapshot)
        self.assertIn(event["summary"], block)
        self.assertNotIn(user_text, block)

    def test_life_loop_step_uses_persisted_schedule(self):
        loop = LifeLoop(self.store, poll_seconds=60)
        first = loop.step(now=noon(), roll=0.3)
        second = loop.step(now=noon() + 60, roll=0.3)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertGreaterEqual(loop.snapshot()["steps"], 2)

    def test_start_checks_overdue_life_immediately(self):
        loop = LifeLoop(self.store, poll_seconds=60)
        try:
            loop.start()
            deadline = time.monotonic() + 2
            while not loop.snapshot()["steps"] and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreaterEqual(loop.snapshot()["steps"], 1)
        finally:
            loop.stop()

    def test_low_energy_during_day_rests_instead_of_sleeping(self):
        state = self.store.load(now=noon(), hour=12)
        state.energy = 10
        state.last_tick = noon()
        state.next_life_at = 0
        self.store.save(state, now=noon())

        event = self.store.advance_life(now=noon() + 1, roll=0.999)

        self.assertIsNotNone(event)
        self.assertEqual(event["id"], "quiet_rest")

    def test_normal_energy_does_not_choose_rest_activity(self):
        state = self.store.load(now=noon(), hour=12)
        state.energy = 72
        state.stress = 10
        state.last_tick = noon()
        state.next_life_at = 0
        self.store.save(state, now=noon())

        event = self.store.advance_life(now=noon() + 1, roll=0.999)

        self.assertIsNotNone(event)
        self.assertNotIn(event["id"], ("quiet_rest", "sleep"))


if __name__ == "__main__":
    unittest.main()
