# -*- coding: utf-8 -*-
"""P1/P2 单测：goqite 式续租 + bi-temporal 当时视角回放。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiji.hippocampus.db import DB
from aiji.heart.heart import Heart, OutboxEvent, PRI_MENTION
from aiji.hippocampus.store import FactDraft, Ident, add_fact, facts_as_of

ROOT = Path(__file__).resolve().parents[1]


def workdir(name: str) -> Path:
    d = ROOT / ".tmp" / f"test_pp_{name}"
    d.mkdir(mode=0o777, parents=True, exist_ok=True)
    return d


def mkdb(name: str) -> DB:
    p = workdir(name) / "p.db"
    if p.exists():
        p.unlink()
    return DB(p)


IDENT = Ident("qq", "u_pp")


class TestExtendLease(unittest.TestCase):
    def test_extend_own_processing_row(self):
        """本实例 processing 行可续租：lease_until 被推到 now+LEASE。"""
        db = mkdb("ext")
        h = Heart(db, handler=lambda i, e: "ok", instance_id="X")
        rid = h.enqueue(IDENT, OutboxEvent(kind="mention", text="x", priority=PRI_MENTION),
                        now=1000.0)
        h._heartbeat(1000.0)
        h._claim(1000.5)  # X 认领
        ok = h.extend_lease(rid, now=1100.0)  # 处理 100s 后续租
        self.assertTrue(ok)
        row = db.read("SELECT lease_until, owner, status FROM outbox WHERE id=?", (rid,))[0]
        self.assertAlmostEqual(row["lease_until"], 1100.0 + 120.0, places=3)
        self.assertEqual(row["owner"], "X")
        self.assertEqual(row["status"], "processing")
        # 续租后另一实例不能按租约过期抢走
        b = Heart(db, handler=lambda i, e: "ok", instance_id="Y")
        b._heartbeat(1100.0)
        claimed = b._claim(1101.0)
        self.assertTrue(claimed is None or claimed[0] != rid, "续租后不得被租约过期路径抢走")

    def test_extend_other_instance_row_fenced(self):
        """别人的行续租被拒（fencing）：owner 不匹配 → False 且 lease 不动。"""
        db = mkdb("fence")
        a = Heart(db, handler=lambda i, e: "ok", instance_id="A")
        b = Heart(db, handler=lambda i, e: "ok", instance_id="B")
        rid = a.enqueue(IDENT, OutboxEvent(kind="mention", text="x", priority=PRI_MENTION),
                        now=1000.0)
        a._heartbeat(1000.0)
        a._claim(1000.5)  # A 认领
        ok = b.extend_lease(rid, now=1100.0)  # B 试图续 A 的行
        self.assertFalse(ok)
        row = db.read("SELECT lease_until, owner FROM outbox WHERE id=?", (rid,))[0]
        self.assertAlmostEqual(row["lease_until"], 1000.5 + 120.0, places=3, msg="B 不得改 A 的租约")
        self.assertEqual(row["owner"], "A")

    def test_extend_done_row_fenced(self):
        """已 done 的行续租被拒：状态不再是 processing。"""
        db = mkdb("done")
        h = Heart(db, handler=lambda i, e: "ok", instance_id="X")
        rid = h.enqueue(IDENT, OutboxEvent(kind="mention", text="x", priority=PRI_MENTION),
                        now=1000.0)
        h.pump(now=1000.5)  # 认领并完成
        ok = h.extend_lease(rid, now=1100.0)
        self.assertFalse(ok)

    def test_extend_send_queue(self):
        """send_queue 行同样可续租（出站发送也可能慢：附件上传）。"""
        db = mkdb("send")
        h = Heart(db, handler=lambda i, e: "ok", instance_id="X")
        h._heartbeat(1000.0)
        with db.tx() as c:
            cur = c.execute(
                "INSERT INTO send_queue(platform,platform_user_id,scope,kind,priority,payload,"
                "send_idem_key,status,attempts,next_retry_at,lease_until,owner,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("qq", "u_pp", "default", "reply", 0, "{}", "sid-1", "processing",
                 1, 0, 1050.0, "X", 1000.0),
            )
            sid = cur.lastrowid
        ok = h.extend_lease(int(sid), now=1005.0, table="send_queue")
        self.assertTrue(ok)
        row = db.read("SELECT lease_until, owner FROM send_queue WHERE id=?", (sid,))[0]
        self.assertAlmostEqual(row["lease_until"], 1005.0 + 120.0, places=3)


class TestFactsAsOf(unittest.TestCase):
    def _timeline(self, db: DB) -> tuple[int, int]:
        """t=1000 住杭州；t=2000 supersede 成苏州。"""
        with db.tx() as c:
            f1, _ = add_fact(c, IDENT, FactDraft("user", "居住地", "杭州", "用户住在杭州", 0.9),
                             now=1000.0)
            f2, _ = add_fact(c, IDENT, FactDraft("user", "居住地", "苏州", "用户住在苏州", 0.9),
                             now=2000.0)
        return f1, f2

    def test_before_change_sees_old(self):
        db = mkdb("t1")
        self._timeline(db)
        rows = facts_as_of(db.conn(), IDENT, 1500.0, predicate="居住地")
        self.assertEqual([r.object for r in rows], ["杭州"])

    def test_after_change_sees_new_only(self):
        db = mkdb("t2")
        self._timeline(db)
        rows = facts_as_of(db.conn(), IDENT, 2500.0, predicate="居住地")
        self.assertEqual([r.object for r in rows], ["苏州"])

    def test_boundary_exclusive(self):
        """valid_to 时刻本身已不真（supersede 瞬间切到新值）。"""
        db = mkdb("t3")
        self._timeline(db)
        rows = facts_as_of(db.conn(), IDENT, 2000.0, predicate="居住地")
        self.assertEqual([r.object for r in rows], ["苏州"])

    def test_identity_isolation(self):
        """回放也是按身份的：另一身份同谓词不串。"""
        db = mkdb("t4")
        self._timeline(db)
        other = Ident("qq", "u_other")
        with db.tx() as c:
            add_fact(c, other, FactDraft("user", "居住地", "成都", "用户住在成都", 0.9),
                     now=1200.0)
        rows = facts_as_of(db.conn(), other, 2500.0, predicate="居住地")
        self.assertEqual([r.object for r in rows], ["成都"])

    def test_v4_backfill_rows_replayable(self):
        """v4 迁移回填过的旧库行（valid_from=created_at）同样可回放。"""
        db = mkdb("t5")
        with db.tx() as c:
            fid, _ = add_fact(c, IDENT, FactDraft("user", "偏好", "爬山", "用户喜欢爬山", 0.8),
                              now=1000.0)
            # 手工模拟 v4 回填语义：老行 valid_from=created_at
            c.execute("UPDATE fact SET valid_from=1000.0 WHERE id=?", (fid,))
        rows = facts_as_of(db.conn(), IDENT, 1500.0)
        self.assertTrue(any(r.object == "爬山" for r in rows))


if __name__ == "__main__":
    unittest.main()
