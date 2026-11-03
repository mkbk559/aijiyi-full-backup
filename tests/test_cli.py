"""CLI 入口的命令级用例（`aiji.senses.cli`）。

为什么补这一组：`aiji/senses/cli.py` 之前是 0% 覆盖——它是唯一的人类入口，
参数名写错、`--db` 忘了透传、命令里 `print` 的 JSON 结构变了，测试都发现不了。

写法：直接调 `cli.main([...])`（连 argparse 一起测），输出用 redirect_stdout 抓。
所有命令都跑在临时库上；`grow` 额外用 `AIJI_GROWTH_PATH` 把账本也隔离掉，
免得像 2026-11-03 那样把生产成长进度写脏。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import unittest
from unittest import mock

from aiji.senses import cli
from tests._tempdir import make_tmp


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = make_tmp(self, "aiji-cli-")
        self.db = self.dir / "aiji.db"
        self.growth = self.dir / "growth.json"
        env = mock.patch.dict(os.environ, {
            "AIJI_GROWTH_PATH": str(self.growth),
            "AIJI_PLATFORM": "cli",
        })
        env.start()
        self.addCleanup(env.stop)
        from aiji.brain import growth

        growth.bind_path(None)
        self.addCleanup(growth.bind_path, None)

    def run_cli(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["--db", str(self.db), *argv])
        return code, out.getvalue()

    def run_json(self, *argv: str) -> dict:
        code, text = self.run_cli(*argv)
        self.assertEqual(code, 0, text)
        return json.loads(text)

    # ---- 写入/读取 ----

    def test_remember_then_recall(self):
        got = self.run_json("remember", "我住在", "杭州", "--session", "s1", "--timeout", "5")
        self.assertTrue(got["queued"])
        self.assertGreaterEqual(got["facts"], 0)
        res = self.run_json("recall", "我住哪", "--session", "s1")
        self.assertIn("hits", res)
        self.assertIsInstance(res["hits"], list)
        self.assertEqual(res["hits"][0]["kind"], "fact")
        self.assertIn("杭州", res["hits"][0]["text"])

    def test_inject_returns_block_and_ids(self):
        self.run_json("remember", "我喜欢喝拿铁", "--session", "s1", "--timeout", "5")
        got = self.run_json("inject", "喝什么", "--session", "s1")
        self.assertIn("text", got)
        self.assertIn("items", got)

    def test_style_returns_three_sections(self):
        got = self.run_json("style")
        self.assertEqual(set(got), {"style", "summary", "profile"})

    def test_feedback_reports_updated_count(self):
        got = self.run_json("feedback", "--injection-id", "1", "--used", "1", "2")
        self.assertIn("updated", got)

    def test_identity_flags_partition_data(self):
        self.run_json("remember", "我的狗叫豆豆", "--user", "u-a", "--session", "s1",
                      "--timeout", "5")
        mine = self.run_json("export", "--user", "u-a")
        other = self.run_json("export", "--user", "u-b")
        self.assertGreaterEqual(len(mine), 0)
        self.assertEqual(other, [])

    def test_export_writes_file(self):
        out = self.dir / "facts.json"
        code, text = self.run_cli("export", "--out", str(out))
        self.assertEqual(code, 0)
        self.assertIn("wrote", text)
        self.assertTrue(out.exists())

    def test_forget_by_like(self):
        got = self.run_json("forget", "--like", "不存在的片段")
        self.assertIn("forgotten", got)

    def test_archive_lists_archived_turns(self):
        got = self.run_json("archive")
        self.assertIsInstance(got, list)

    def test_self_memory_add_then_list(self):
        added = self.run_json("self-memory", "--add", "我今天学会了自己关灯")
        self.assertIn("id", added)
        rows = self.run_json("self-memory")
        self.assertIsInstance(rows, list)
        self.assertTrue(any("关灯" in json.dumps(r, ensure_ascii=False) for r in rows))

    # ---- 媒体 ----

    def test_media_register_and_list(self):
        got = self.run_json("media", "--ref", "img-1", "--url", "https://x/y.png")
        self.assertEqual(got["stored"], "reference_only")
        rows = self.run_json("media")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ref"], "img-1")

    def test_media_gc_is_dry_run_by_default(self):
        self._seed_media()
        got = self.run_json("media-gc")
        self.assertFalse(got["applied"])
        self.assertEqual(got["deletable"], 2)
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM media").fetchone()[0], 4)
        conn.close()

    def test_media_gc_apply_keeps_text_rows(self):
        self._seed_media()
        got = self.run_json("media-gc", "--apply")
        self.assertTrue(got["applied"])
        self.assertEqual(got["deleted"], 2)
        conn = sqlite3.connect(self.db)
        statuses = dict(conn.execute("SELECT status, COUNT(*) FROM media GROUP BY status"))
        conn.close()
        self.assertEqual(statuses, {"ok": 1, "low_confidence": 1})

    def _seed_media(self) -> None:
        self.run_json("media", "--ref", "keep-1", "--url", "https://x/1.png")
        conn = sqlite3.connect(self.db)
        rows = [
            ("keep-1", "ok", "图里是一杯咖啡", 0.9),
            ("dead-1", "error", None, 0.0),
            ("dead-2", "timeout", "", 0.0),
            ("keep-2", "low_confidence", "看不太清的字", 0.2),
        ]
        for ref, status, text, conf in rows:
            conn.execute(
                "UPDATE media SET status=?, text=?, confidence=? WHERE ref=?", 
                (status, text, conf, ref))
        for ref, status, text, conf in rows[1:]:
            if ref in ("dead-1", "dead-2", "keep-2"):
                conn.execute(
                    "INSERT INTO media(platform,platform_user_id,scope,session,kind,ref,url,"
                    "text,confidence,status,created_at) VALUES('cli','default','default','s1',"
                    "'image',?,?,?,?,?,?)",
                    (ref, "https://x/" + ref, text, conf, status, 0.0))
        conn.commit()
        conn.close()

    # ---- 统计/自检 ----

    def test_stats_and_accounts(self):
        st = self.run_json("stats")
        self.assertIn("store", st)
        self.assertIn("pool", st)
        accts = self.run_json("accounts")
        self.assertIsInstance(accts, list)

    def test_doctor_reports_version_and_db(self):
        got = self.run_json("doctor")
        self.assertIn("version", got)
        self.assertIn("db", got)
        self.assertIn("sqlite", got)

    def test_quota_reports_allowance(self):
        got = self.run_json("quota")
        self.assertIn("identity", got)
        self.assertIn("left_now", got)
        self.assertIn("ok", got)

    # ---- 成长 ----

    def test_grow_creates_learning_ledger(self):
        got = self.run_json("grow")
        self.assertEqual(got["stage"], "primary")
        self.assertEqual(got["xp"], 0.0)
        self.assertFalse(got["time_based_growth"])
        self.assertTrue(self.growth.exists(), "账本要落在 AIJI_GROWTH_PATH")

    def test_grow_set_stage_and_scale(self):
        got = self.run_json("grow", "--set-stage", "university")
        self.assertEqual(got["stage"], "university")
        scaled = self.run_json("grow", "--scale", "2.0")
        self.assertEqual(scaled["scale"], 2.0)

    def test_grow_rejects_bad_scale(self):
        with self.assertRaises(SystemExit):
            self.run_cli("grow", "--scale", "0")

    def test_ingest_text_kind_stores_media(self):
        got = self.run_json("ingest", "--kind", "text", "--text", "这段话是转好的语音",
                            "--ref", "voice-1", "--timeout", "5")
        self.assertTrue(got["queued"])
        self.assertIsNotNone(got["latest"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
