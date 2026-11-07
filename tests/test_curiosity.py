"""B 组学习闭环测试：好奇心搜索 / 日记目标提取 / 学分新口径。

B1: curiosity.wants_to_learn —— 回复"我不知道"且问题有主题 → 触发学习
B4: day.extract_tomorrow_plan —— 日记结尾的"明天想…"能抠出来
B5: growth.db_counts —— curiosity_learn / self_goal 计入学分
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiji.brain import curiosity
from aiji.brain import day as day_mod


class CuriosityTest(unittest.TestCase):
    def test_what_is_question_extracts_topic(self):
        self.assertEqual(curiosity.extract_unknown_topic("什么是黑洞呀？"),
                         "黑洞")

    def test_x_is_what_extracts_topic(self):
        self.assertIn("变声器", curiosity.extract_unknown_topic(
            "变声器是什么东西"))

    def test_plain_chat_extracts_nothing(self):
        self.assertEqual(curiosity.extract_unknown_topic("在吗"), "")
        self.assertEqual(curiosity.extract_unknown_topic(
            "你好呀，你吃饭了吗"), "")

    def test_wants_to_learn_triggers_on_unknown_reply(self):
        topic = curiosity.wants_to_learn(
            "这个嘛……我不知道呢。",
            "变声器是什么呀？")
        self.assertEqual(topic, "变声器")

    def test_wants_to_learn_ignores_confident_reply(self):
        self.assertEqual(curiosity.wants_to_learn(
            "变声器就是把声音变掉的软件呀", "变声器是什么呀？"), "")

    def test_rate_limit_blocks_second_learn_in_hour(self):
        import time
        now = time.time()
        # 直接测内部限流：重置状态
        curiosity._recent_learns.clear()
        first = curiosity._rate_ok(now)
        second = curiosity._rate_ok(now + 1)
        third = curiosity._rate_ok(now + 2)
        self.assertTrue(first)
        self.assertTrue(second)   # cap = 2
        self.assertFalse(third)  # 第 3 次被拦


class TomorrowPlanTest(unittest.TestCase):
    def test_extracts_plan_from_diary_end(self):
        text = ("今天聊了很多。晚上早点睡吧。"
                "明天想把那个变声器的原理弄明白。")
        self.assertIn("变声器", day_mod.extract_tomorrow_plan(text))

    def test_no_plan_returns_empty(self):
        self.assertEqual(
            day_mod.extract_tomorrow_plan("今天挺平常，早点睡吧。"), "")

    def test_plan_too_short_rejected(self):
        self.assertEqual(
            day_mod.extract_tomorrow_plan("明天想去。"), "")


class GrowthWeightsTest(unittest.TestCase):
    def test_new_evidence_kinds_counted(self):
        from aiji.brain import growth
        self.assertIn("curiosity_learn", growth.COUNTABLE)
        self.assertIn("self_goal", growth.COUNTABLE)
        self.assertEqual(growth.WEIGHTS["turn"], 0.02)
        self.assertGreater(growth.WEIGHTS["curiosity_learn"],
                           growth.WEIGHTS["turn"] * 10)

    def test_db_counts_tolerates_missing_tables(self):
        # 老库（没有 self_memory 表）不该崩
        from aiji.brain import growth
        d = Path(".tmp/growth-old-db")
        d.mkdir(parents=True, exist_ok=True)
        p = d / "old.db"
        try:
            con = sqlite3.connect(p)
            con.execute("CREATE TABLE fact(id INTEGER PRIMARY KEY, extra TEXT)")
            con.execute("CREATE TABLE reflection(id INTEGER PRIMARY KEY)")
            con.execute("CREATE TABLE trait(x TEXT)")
            con.execute("CREATE TABLE turn(x TEXT)")
            con.commit()
            con.close()
            counts = growth.db_counts(p)
            self.assertEqual(counts["self_goal"], 0.0)
            self.assertEqual(counts["curiosity_learn"], 0.0)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
