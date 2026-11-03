# -*- coding: utf-8 -*-
"""实例注册表（solid_queue 语义）单测：崩溃接管、活实例不被误收、双实例认领互斥。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiji.heart.heart import Heart, OutboxEvent, PRI_MENTION
from aiji.right_hand.params import HEART_INSTANCE_TTL, HEART_LEASE_SECONDS
from aiji.hippocampus.store import Ident
from aiji.hippocampus.db import DB

ROOT = Path(__file__).resolve().parents[1]


def workdir(name: str) -> Path:
    d = ROOT / ".tmp" / f"test_inst_{name}"
    d.mkdir(mode=0o777, parents=True, exist_ok=True)
    return d


def mkdb(name: str) -> DB:
    p = workdir(name) / "i.db"
    if p.exists():
        p.unlink()
    return DB(p)


class TestInstanceRegistry(unittest.TestCase):
    def _echo(self, ident, ev):
        return f"echo:{ev.text}"

    def test_crash_takeover_within_ttl(self):
        """实例 A 认领后崩溃（无 finish）；实例 B 一次 pump 即接管（≤TTL+SCAN，
        而非旧路径 ≤LEASE_SECONDS=120s）。"""
        db = mkdb("takeover")
        a = Heart(db, handler=self._echo, instance_id="inst-A")
        ident = Ident("qq", "u1")
        rid = a.enqueue(ident, OutboxEvent(kind="mention", text="hi", priority=PRI_MENTION),
                        now=1000.0)
        a._heartbeat(1000.0)
        a.pump(now=1000.5)  # A 认领：行 processing owner=A
        row = db.read("SELECT status, owner FROM outbox WHERE id=?", (rid,))[0]
        self.assertEqual(row["status"], "done")  # handler 正常，直接 done

        # 更真实的崩溃：A 认领后 handler 抛异常前的瞬间——手动造 processing 行
        with db.tx() as c:
            c.execute("UPDATE outbox SET status='processing', owner='inst-A',"
                      " lease_until=2000.0, attempts=1 WHERE id=?", (rid,))
        # B 的 pump 在 t=1100（A 心跳停在 1000.5，TTL=90 → 99.5s 已过期）
        b = Heart(db, handler=self._echo, instance_id="inst-B")
        n = b.pump(now=1100.0)
        self.assertGreaterEqual(n, 1, "B 必须接管 A 的遗留行")
        row = db.read("SELECT status, owner FROM outbox WHERE id=?", (rid,))[0]
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["owner"], None)
        # 死实例注册行被清
        self.assertEqual(db.read("SELECT COUNT(*) c FROM instance") [0]["c"], 1)

    def test_live_instance_not_reclaimed(self):
        """心跳新鲜的实例不会被接管（互斥前提）。"""
        db = mkdb("live")
        a = Heart(db, handler=self._echo, instance_id="inst-A")
        ident = Ident("qq", "u1")
        rid = a.enqueue(ident, OutboxEvent(kind="mention", text="x", priority=PRI_MENTION),
                        now=1000.0)
        with db.tx() as c:
            c.execute("UPDATE outbox SET status='processing', owner='inst-A',"
                      " lease_until=1120.0 WHERE id=?", (rid,))
        a._heartbeat(1060.0)  # 心跳新鲜
        n = a.reclaim_dead_instances(1061.0)
        self.assertEqual(n, 0)
        row = db.read("SELECT status, owner FROM outbox WHERE id=?", (rid,))[0]
        self.assertEqual(row["status"], "processing")  # 未被动
        self.assertEqual(row["owner"], "inst-A")

    def test_two_instances_claim_mutex(self):
        """双实例同时 pump：同一行只被一个实例认领（attempts 不翻倍）。"""
        db = mkdb("mutex")
        a = Heart(db, handler=lambda i, e: "ok", instance_id="A")
        b = Heart(db, handler=lambda i, e: "ok", instance_id="B")
        ident = Ident("qq", "u1")
        for i in range(5):
            a.enqueue(ident, OutboxEvent(kind="mention", text=f"m{i}", priority=PRI_MENTION),
                      now=1000.0 + i)
        a.pump(now=1010.0)
        b.pump(now=1011.0)
        row = db.read(
            "SELECT SUM(attempts) s, SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) d"
            " FROM outbox")[0]
        self.assertEqual(row["d"], 5)
        self.assertEqual(row["s"], 5, "5 行 5 次处理：无二次认领")

    def test_lease_and_ttl_params_coherent(self):
        self.assertLess(HEART_INSTANCE_TTL, HEART_LEASE_SECONDS,
                        "TTL 必须小于租约：接管路径要快于逐条租约过期路径")

    def test_no_owner_rows_untouched(self):
        """owner 为空的 processing 行（旧数据/兼容）不被 reclaim 误伤。"""
        db = mkdb("noowner")
        h = Heart(db, instance_id="inst-X")
        ident = Ident("qq", "u1")
        rid = h.enqueue(ident, OutboxEvent(kind="mention", text="x", priority=PRI_MENTION),
                        now=1000.0)
        with db.tx() as c:
            c.execute("UPDATE outbox SET status='processing', owner=NULL,"
                      " lease_until=2000.0 WHERE id=?", (rid,))
        n = h.reclaim_dead_instances(1100.0)
        self.assertEqual(n, 0, "无主行归逐条租约路径管，注册表不管")


if __name__ == "__main__":
    unittest.main()
