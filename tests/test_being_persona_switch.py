"""换人格不能等于失忆（2026-11-03 审计项：being_* 按 persona 分区导致"一改名字全忘"）。

状态（心情/精力/社交/正在忙什么）属于"这段关系"，事件属于"发生过的事"，
都不该因为 `persona` 这个标签变了就被清零。
"""
from __future__ import annotations

import unittest

from aiji.endocrine.being import BeingStore
from aiji.hippocampus.db import DB
from tests._tempdir import make_tmp


class PersonaSwitchTheoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = make_tmp(self, "aiji-being-persona-")
        self.db = DB(self.dir / "being.db")
        self.addCleanup(self.db.close)

    def test_new_persona_inherits_state(self):
        old = BeingStore(self.db, persona="sunba")
        state = old.load(now=1000.0)
        state.social = 88.0
        state.valence = 0.45
        state.stress = 33.0
        state.private_topic = "考试"
        old.save(state, now=1000.0)
        old.observe("聊了很久", now=1010.0)

        fresh = BeingStore(self.db, persona="child")
        loaded = fresh.load(now=1020.0)
        self.assertAlmostEqual(loaded.social, 88.0, delta=0.5)   # tick 会微小回充
        self.assertAlmostEqual(loaded.valence, 0.45, delta=0.05)
        self.assertAlmostEqual(loaded.stress, 33.0, delta=0.5)
        self.assertEqual(loaded.private_topic, "考试")

    def test_events_fall_back_to_history(self):
        old = BeingStore(self.db, persona="sunba")
        old.observe("用户说要考试", now=1000.0)
        fresh = BeingStore(self.db, persona="child")
        summaries = [e["summary"] for e in fresh.events(limit=3)]
        self.assertTrue(any("考试" in str(x) for x in summaries), summaries)

    def test_brand_new_identity_still_starts_clean(self):
        store = BeingStore(self.db, persona="child")
        state = store.load(now=1000.0)
        self.assertAlmostEqual(state.social, 65.0, places=3)   # 默认值，没有可继承的
        self.assertEqual(state.private_topic, "")
        self.assertEqual(store.events(limit=3), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
