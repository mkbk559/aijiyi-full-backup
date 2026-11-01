from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from tools import field_monitor


class FieldMonitorTest(unittest.TestCase):
    def test_agent_snapshot_reports_recent_tokens_and_balances(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "agent2api.db"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE requests ("
                "ts INTEGER, status INTEGER, total_tokens INTEGER)")
            connection.execute(
                "CREATE TABLE request_daily ("
                "date TEXT, requests INTEGER, successful INTEGER, tokens INTEGER)")
            connection.execute(
                "CREATE TABLE accounts ("
                "id TEXT, provider TEXT, enabled INTEGER)")
            connection.execute(
                "CREATE TABLE account_usage_records ("
                "account_id TEXT, remaining REAL)")
            now = int(time.time() * 1000)
            connection.executemany(
                "INSERT INTO requests VALUES (?, ?, ?)",
                [
                    (now - 60_000, 200, 700),
                    (now - 120_000, 200, 300),
                    (now - 180_000, 408, 0),
                    (now - 3_600_000, 200, 999),
                ],
            )
            connection.execute(
                "INSERT INTO request_daily VALUES ('2026-11-01', 12, 11, 5000)")
            connection.executemany(
                "INSERT INTO accounts VALUES (?, ?, 1)",
                (("a1", "alpha"), ("a2", "beta")),
            )
            connection.executemany(
                "INSERT INTO account_usage_records VALUES (?, ?)",
                (("a1", 12.5), ("a2", 7.5)),
            )
            connection.commit()
            connection.close()
            self.assertTrue(hasattr(field_monitor, "AGENT_DB"))
            original = field_monitor.AGENT_DB
            field_monitor.AGENT_DB = database
            try:
                snapshot = field_monitor.agent_snapshot()
            finally:
                field_monitor.AGENT_DB = original

        self.assertEqual(snapshot["last_30m_requests"], 3)
        self.assertEqual(snapshot["last_30m_tokens"], 1_000)
        self.assertEqual(snapshot["last_30m_200"], 2)
        self.assertEqual(snapshot["last_30m_408"], 1)
        self.assertEqual(snapshot["today"]["tokens"], 5_000)
        self.assertEqual(
            {item["provider"]: item["remaining"] for item in snapshot["balances"]},
            {"alpha": 12.5, "beta": 7.5},
        )

    def test_read_log_increment_returns_only_new_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "astrbot.log"
            first = "old line\n".encode("utf-8")
            second = "new error\n".encode("utf-8")
            path.write_bytes(first)
            reader = getattr(field_monitor, "read_log_increment", None)
            self.assertIsNotNone(reader)
            with path.open("ab") as stream:
                stream.write(second)
            self.assertEqual(reader(path, len(first)), "new error\n")

    def test_latest_log_includes_stable_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            older = directory / "astrbot-fieldtest-old.out.log"
            newer = directory / "astrbot-stable-new.out.log"
            older.write_text("old", encoding="utf-8")
            newer.write_text("new", encoding="utf-8")
            os.utime(older, (1_000, 1_000))
            os.utime(newer, (2_000, 2_000))
            original = field_monitor.LOG_DIR
            field_monitor.LOG_DIR = directory
            try:
                self.assertEqual(field_monitor.latest_log(), newer)
            finally:
                field_monitor.LOG_DIR = original

    def test_db_snapshot_reports_assistant_replies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "aiji.db"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE turn (role TEXT, ts REAL, platform TEXT)")
            connection.execute(
                "CREATE TABLE write_queue (state TEXT)")
            connection.executemany(
                "INSERT INTO turn (role, ts, platform) VALUES (?, ?, ?)",
                [
                    ("user", 100.0, "qq"),
                    ("assistant", 101.0, "qq"),
                    ("assistant", 103.0, "qq"),
                    ("assistant", 999.0, "other"),
                ],
            )
            connection.commit()
            connection.close()
            original = field_monitor.DB
            field_monitor.DB = database
            try:
                snapshot = field_monitor.db_snapshot()
            finally:
                field_monitor.DB = original

        self.assertEqual(snapshot, {
            "qq_turns": 3,
            "latest_turn": 103.0,
            "qq_assistant_turns": 2,
            "latest_assistant_turn": 103.0,
            "reply_stalled": False,
            "dead": 0,
        })

    def test_db_snapshot_flags_active_users_without_recent_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "aiji.db"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE turn (role TEXT, ts REAL, platform TEXT)")
            connection.execute(
                "CREATE TABLE write_queue (state TEXT)")
            now = time.time()
            connection.executemany(
                "INSERT INTO turn (role, ts, platform) VALUES (?, ?, ?)",
                [
                    ("assistant", now - 3_600, "qq"),
                    ("user", now, "qq"),
                ],
            )
            connection.commit()
            connection.close()
            original = field_monitor.DB
            field_monitor.DB = database
            try:
                snapshot = field_monitor.db_snapshot()
            finally:
                field_monitor.DB = original

        self.assertEqual(snapshot.get("reply_stalled"), True)

    def test_db_snapshot_does_not_flag_quiet_history_as_stalled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "aiji.db"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE turn (role TEXT, ts REAL, platform TEXT)")
            connection.execute(
                "CREATE TABLE write_queue (state TEXT)")
            old = time.time() - 3_600
            connection.executemany(
                "INSERT INTO turn (role, ts, platform) VALUES (?, ?, ?)",
                [
                    ("user", old, "qq"),
                    ("assistant", old + 1, "qq"),
                ],
            )
            connection.commit()
            connection.close()
            original = field_monitor.DB
            field_monitor.DB = database
            try:
                snapshot = field_monitor.db_snapshot()
            finally:
                field_monitor.DB = original

        self.assertEqual(snapshot.get("reply_stalled"), False)


if __name__ == "__main__":
    unittest.main()
