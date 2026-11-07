"""作息 / 日记：纯函数部分必须确定、可复现。"""
from __future__ import annotations

import time
import unittest
from pathlib import Path

from aiji.brain import day as day_mod


class DayPlanTest(unittest.TestCase):
    def test_plan_covers_24_hours_without_gaps(self):
        blocks = day_mod.plan(stage_id="middle")
        self.assertGreaterEqual(len(blocks), 8)
        for block in blocks:
            self.assertRegex(block["start"], r"^\d{2}:\d{2}$")
            self.assertRegex(block["end"], r"^\d{2}:\d{2}$")
            self.assertIn(block["talk"], ("none", "short", "normal"))

    def test_current_block_at_school_time(self):
        # 2026-11-05 15:21 本地时间 → 下午的课
        ts = time.mktime((2026, 11, 5, 15, 21, 0, 0, 0, -1))
        block = day_mod.current_block(ts, stage_id="middle")
        self.assertEqual(block["kind"], "school")
        self.assertEqual(block["talk"], "none")

    def test_current_block_at_night_sleep(self):
        ts = time.mktime((2026, 11, 5, 2, 0, 0, 0, 0, -1))
        block = day_mod.current_block(ts, stage_id="middle")
        self.assertEqual(block["kind"], "sleep")

    def test_current_block_evening_is_free(self):
        ts = time.mktime((2026, 11, 5, 21, 30, 0, 0, 0, -1))
        block = day_mod.current_block(ts, stage_id="middle")
        self.assertEqual(block["kind"], "social")
        self.assertEqual(block["talk"], "normal")


class SleepReplyTest(unittest.TestCase):
    def test_sleep_reply_mentions_left_hours(self):
        # 02:00 → 睡到 06:40，还有 4h40m
        ts = time.mktime((2026, 11, 6, 2, 0, 0, 0, 0, -1))
        block = day_mod.current_block(ts, stage_id="middle")
        text = day_mod.restated_sleep_reply(block)
        self.assertIn("睡了", text)
        self.assertIn("留言", text)

    def test_sleep_reply_no_block_info_still_works(self):
        text = day_mod.restated_sleep_reply(None)
        self.assertIn("睡了", text)


class DiaryTest(unittest.TestCase):
    def test_diary_without_material_is_still_honest(self):
        text = day_mod.diary_text({"turns": 0, "said": [], "facts": []},
                                  stage_label="初中", day="2026-11-05")
        self.assertIn("2026-11-05", text)
        self.assertIn("没人来找我说话", text)

    def test_diary_quotes_what_was_said(self):
        text = day_mod.diary_text(
            {"turns": 3, "said": ["你好呀", "我在写作业"], "facts": ["喜欢猫"]},
            stage_label="初中", day="2026-11-05")
        self.assertIn("你好呀", text)
        self.assertIn("喜欢猫", text)
        self.assertIn("3 句", text)

    def test_write_diary_creates_file(self):
        root = Path(".tmp/diary-test")
        path = day_mod.write_diary("# hi", day="2026-11-05", root=root)
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), "# hi")
        self.assertIn("2026-11-05", day_mod.list_diaries(root))

    def test_publish_targets_are_honest(self):
        targets = day_mod.publish_targets()
        self.assertEqual(targets["bilibili"], "ok")
        self.assertEqual(targets["douyin"], "unsupported")  # 抖音网页端没有投稿入口


if __name__ == "__main__":
    unittest.main()
