"""Safe test-identity cleanup contracts."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aiji.db import DB
from aiji.store import FactDraft, Ident, add_fact, add_media, add_turn, ensure_account
from tools.repair_data import (
    backfill_account_labels,
    cleanup_test_identities,
    fix_legacy_media_scopes,
    is_test_identity,
)


class DataRepairTest(unittest.TestCase):
    def test_test_identity_detection_never_matches_real_qq(self):
        self.assertFalse(is_test_identity("qq", "2216608312"))
        self.assertFalse(is_test_identity("qq", "2221813100"))
        self.assertTrue(is_test_identity("qq", "99000123"))
        self.assertTrue(is_test_identity("qq", "style_check_1"))
        self.assertTrue(is_test_identity("qq", "sunba_probe"))
        self.assertTrue(is_test_identity("qq_user_hist", "live"))
        self.assertFalse(is_test_identity("desktop", "me"))

    def test_cleanup_removes_only_synthetic_identity_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aiji.db"
            db = DB(path)
            real = Ident("qq", "2216608312", "default")
            fake = Ident("qq", "99000123", "default")
            with db.tx() as conn:
                ensure_account(conn, real, display_name="玛卡巴卡")
                ensure_account(conn, fake, display_name="测试")
                add_turn(conn, real, "private:2216608312", "user", "真实消息")
                add_turn(conn, fake, "default", "user", "测试消息")
            db.close()

            result = cleanup_test_identities(path, apply=True)

            self.assertEqual(result["identities"], [("qq", "99000123")])
            self.assertTrue(Path(result["backup"]).is_file())
            check = DB(path)
            try:
                rows = check.read(
                    "SELECT platform,platform_user_id FROM account"
                    " ORDER BY platform,platform_user_id"
                )
                self.assertEqual(
                    [(r["platform"], r["platform_user_id"]) for r in rows],
                    [("qq", "2216608312")],
                )
                turns = check.read(
                    "SELECT content FROM turn ORDER BY id"
                )
                self.assertEqual([r["content"] for r in turns], ["真实消息"])
            finally:
                check.close()

    def test_cleanup_removes_fact_links_and_logs_for_synthetic_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aiji.db"
            db = DB(path)
            fake = Ident("qq", "99000123", "default")
            with db.tx() as conn:
                first, _ = add_fact(
                    conn, fake,
                    FactDraft("user", "偏好", "测试甲", "测试甲"),
                )
                second, _ = add_fact(
                    conn, fake,
                    FactDraft("user", "偏好", "测试乙", "测试乙"),
                )
                conn.execute(
                    "INSERT INTO fact_link(platform,platform_user_id,scope,src_id,dst_id,"
                    "weight,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    ("qq", "99000123", "default", first, second, 0.8, "test", 1.0),
                )
            db.close()

            cleanup_test_identities(path, apply=True)

            check = DB(path)
            try:
                self.assertEqual(
                    check.read("SELECT COUNT(*) AS c FROM fact_log")[0]["c"], 0)
                self.assertEqual(
                    check.read("SELECT COUNT(*) AS c FROM fact_link")[0]["c"], 0)
            finally:
                check.close()

    def test_legacy_private_media_scope_is_moved_to_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aiji.db"
            db = DB(path)
            tag = Ident("qq", "2216608312", "private")
            with db.tx() as conn:
                ensure_account(conn, tag)
                add_media(
                    conn,
                    tag,
                    kind="image",
                    ref="legacy-media",
                    url="https://example.test/a.png",
                    status="unsupported",
                )
            db.close()

            result = fix_legacy_media_scopes(path, apply=True)

            self.assertEqual(result["updated"], 1)
            check = DB(path)
            try:
                row = check.read(
                    "SELECT scope FROM media WHERE ref='legacy-media'"
                )[0]
                self.assertEqual(row["scope"], "default")
            finally:
                check.close()

    def test_account_display_name_is_backfilled_from_latest_qq_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aiji.db"
            db = DB(path)
            ident = Ident("qq", "2216608312", "default")
            with db.tx() as conn:
                ensure_account(conn, ident)
                add_turn(
                    conn,
                    ident,
                    "private:2216608312",
                    "user",
                    "第一句",
                    extra={"sender_name": "旧昵称"},
                )
                add_turn(
                    conn,
                    ident,
                    "private:2216608312",
                    "user",
                    "第二句",
                    extra={"sender_name": "玛卡巴卡"},
                )
                add_turn(
                    conn,
                    ident,
                    "private:2216608312",
                    "assistant",
                    "知道了",
                )
            db.close()

            result = backfill_account_labels(path, apply=True)

            self.assertEqual(result["updated"], 1)
            check = DB(path)
            try:
                row = check.read(
                    "SELECT display_name FROM account WHERE platform='qq'"
                    " AND platform_user_id='2216608312'"
                )[0]
                self.assertEqual(row["display_name"], "玛卡巴卡")
            finally:
                check.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
