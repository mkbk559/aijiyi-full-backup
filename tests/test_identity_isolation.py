"""串号（跨身份串数据）隔离测试。

本项目的头号已知风险是"跨平台隔离靠 SQL 过滤，一次漏写 `platform` 条件就是串号事故"。
静态检查靠不住（条件常用 `ident.where()` 在运行时拼出来），所以这里用**行为**兜底：

给 A、B 两个身份灌**同一张表里的不同数据**（用可检索的独特标记），然后只以 A 的身份
调用每一个读取 API，断言结果里**绝不出现 B 的标记**。反过来再验一遍。

任何一条新查询漏了身份条件，这里就会红——不需要人去 review SQL。
"""
from __future__ import annotations

import pathlib
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.hippocampus import store  # noqa: E402
from aiji.hippocampus.db import DB  # noqa: E402
from aiji.hippocampus.store import FactDraft, Ident  # noqa: E402

# 两个身份：平台 / 用户 / scope 全不同（现实里最常见的三种串法都覆盖）
A = Ident("wechat", "alice", "personal")
B = Ident("qq", "bob", "group-42")

MARK_A = "紫罗兰记号甲"
MARK_B = "柠檬黄记号乙"


def marker_in_blob(mark: str, blob) -> bool:
    import json
    return mark in json.dumps(blob, ensure_ascii=False, default=str)


class IsolationCase(unittest.TestCase):
    def setUp(self):
        self.db = DB(make_tmp(self, "ident") / "a.db")
        now = time.time()
        with self.db.tx() as conn:
            self.fact_a, _ = store.add_fact(conn, A, FactDraft(
                "user", "喜欢的颜色", MARK_A, f"用户喜欢 {MARK_A}", 0.95,
                extra={"mark": MARK_A}), now=now)
            self.fact_b, _ = store.add_fact(conn, B, FactDraft(
                "user", "喜欢的颜色", MARK_B, f"用户喜欢 {MARK_B}", 0.95,
                extra={"mark": MARK_B}), now=now)
            store.add_turn(conn, A, "s-a", "user", f"我在 A 会话说 {MARK_A}", now=now)
            store.add_turn(conn, B, "s-b", "user", f"我在 B 会话说 {MARK_B}", now=now)
            store.upsert_profile(conn, A, "称呼", MARK_A, now=now)
            store.upsert_profile(conn, B, "称呼", MARK_B, now=now)
            store.upsert_style(conn, A, {"tone": MARK_A})
            store.upsert_style(conn, B, {"tone": MARK_B})
            store.upsert_trait(conn, A, "口头禅", MARK_A, now=now)
            store.upsert_trait(conn, B, "口头禅", MARK_B, now=now)
            store.put_summary(conn, A, "s-a", 1, f"摘要 {MARK_A}", now=now)
            store.put_summary(conn, B, "s-b", 1, f"摘要 {MARK_B}", now=now)
            store.log_injection(conn, A, f"问 {MARK_A}", [self.fact_a],
                                f"注入 {MARK_A}", session="s-a", now=now)
            store.log_injection(conn, B, f"问 {MARK_B}", [self.fact_b],
                                f"注入 {MARK_B}", session="s-b", now=now)
            store.log_reflection(conn, A, "s-a", 1, 1, f"反思 {MARK_A}",
                                 [{"kind": "mood", "delta": 0.1}], now=now)
            store.log_reflection(conn, B, "s-b", 1, 1, f"反思 {MARK_B}",
                                 [{"kind": "mood", "delta": -0.1}], now=now)
            store.add_media(conn, A, kind="image", ref="a.jpg", session="s-a",
                            text=f"图里写着 {MARK_A}", now=now)
            store.add_media(conn, B, kind="image", ref="b.jpg", session="s-b",
                            text=f"图里写着 {MARK_B}", now=now)

    def tearDown(self):
        self.db.close()

    # ---- 读接口逐个验 ----

    def test_search_facts(self):
        with self.db.tx() as conn:
            for mark, ident, other in ((MARK_A, A, MARK_B), (MARK_B, B, MARK_A)):
                for f in store.search_facts(conn, ident, "颜色", limit=10):
                    self.assertNotIn(other, f.text, f"{ident.platform} 看到了别人的事实")

    def test_facts_as_of(self):
        with self.db.tx() as conn:
            items = store.facts_as_of(conn, A, time.time() + 60)
            self.assertFalse(marker_in_blob(MARK_B, [f.text for f in items]))

    def test_recent_turns(self):
        with self.db.tx() as conn:
            turns = store.recent_turns(conn, A, "s-a", 10)
            self.assertTrue(turns)
            self.assertFalse(marker_in_blob(MARK_B, turns))
            self.assertEqual(store.turn_count(conn, A, "s-b"), 0,
                             "A 不该看到 B 的会话")
            self.assertNotIn(self.fact_b, store.session_turn_ids(conn, A, "s-b"))

    def test_scope_turns_only_same_platform_and_scope(self):
        with self.db.tx() as conn:
            same = store.recent_scope_turns(conn, A.platform, A.scope, "s-a", 10)
            self.assertTrue(same)
            leak = store.recent_scope_turns(conn, A.platform, A.scope, "s-b", 10)
            self.assertEqual(leak, [], "同平台不同会话也不能串")

    def test_profile_style_trait_snapshots(self):
        with self.db.tx() as conn:
            profile = store.profile_snapshot(conn, A)
            self.assertFalse(marker_in_blob(MARK_B, profile))
            style = store.style_snapshot(conn, A)
            self.assertFalse(marker_in_blob(MARK_B, style))
            traits = store.trait_snapshot(conn, A)
            self.assertFalse(marker_in_blob(MARK_B, traits))

    def test_summaries(self):
        with self.db.tx() as conn:
            latest = store.latest_summary(conn, A, "s-b")
            self.assertIsNone(latest, "A 不该有 B 会话的摘要")
            mine = store.latest_summary(conn, A, "s-a")
            self.assertIsNotNone(mine)
            self.assertNotIn(MARK_B, mine["text"])
            self.assertIsNone(store.summary_by_hash(conn, A, "s-b", "任意"))

    def test_injections(self):
        with self.db.tx() as conn:
            row_id, ids = store.last_injection_with_feedback(conn, A, "s-b")
            self.assertIsNone(row_id, "A 不该看到 B 的注入记录")
            self.assertEqual(ids, [])
            row_id, ids = store.last_injection_with_feedback(conn, A, "s-a")
            self.assertIsNotNone(row_id)
            self.assertEqual(ids, [self.fact_a])

    def test_reflections(self):
        with self.db.tx() as conn:
            mine = store.last_reflection(conn, A, "s-a")
            self.assertIsNotNone(mine)
            self.assertFalse(marker_in_blob(MARK_B, mine))
            self.assertIsNone(store.last_reflection(conn, A, "s-b"))

    def test_media(self):
        with self.db.tx() as conn:
            mine = store.media_for(conn, A, "s-a", 10)
            self.assertTrue(mine)
            self.assertFalse(marker_in_blob(MARK_B, mine))
            self.assertEqual(store.media_for(conn, A, "s-b", 10), [])

    def test_links(self):
        with self.db.tx() as conn:
            store.link_facts(conn, A, self.fact_a, "user", "喜欢的颜色",
                             f"用户喜欢 {MARK_A}")
            links = store.links_for(conn, A, [self.fact_a])
            self.assertFalse(marker_in_blob(MARK_B, links))
            cross = store.links_for(conn, A, [self.fact_b])
            self.assertEqual(cross, [], "不能顺着 id 摸到别人的事实")

    def test_stats_are_scoped(self):
        with self.db.tx() as conn:
            mine = store.stats(conn, A)
            self.assertFalse(marker_in_blob(MARK_B, mine))
            facts = mine.get("store", {}).get("facts", {})
            if isinstance(facts, dict) and "active" in facts:
                self.assertEqual(facts["active"], 1,
                                 "按身份统计只该数到自己那一条")

    # ---- 写接口只动自己 ----

    def test_forget_only_touches_own_rows(self):
        with self.db.tx() as conn:
            store.forget_facts(conn, A, fact_id=self.fact_b)
            still = conn.execute("SELECT status FROM fact WHERE id=?",
                                 (self.fact_b,)).fetchone()
            self.assertEqual(still["status"], "active", "A 忘了不 B 的事")
            store.forget_facts(conn, A, fact_id=self.fact_a)
            mine = conn.execute("SELECT status FROM fact WHERE id=?",
                                (self.fact_a,)).fetchone()
            self.assertNotEqual(mine["status"], "active")

    def test_bump_usage_only_touches_own_rows(self):
        with self.db.tx() as conn:
            before = conn.execute("SELECT hit_count FROM fact WHERE id=?",
                                  (self.fact_b,)).fetchone()["hit_count"]
            store.bump_usage(conn, A, [self.fact_b])
            after = conn.execute("SELECT hit_count FROM fact WHERE id=?",
                                 (self.fact_b,)).fetchone()["hit_count"]
            self.assertEqual(before, after, "A 命中不该给 B 的事实加分")


if __name__ == "__main__":
    unittest.main()
