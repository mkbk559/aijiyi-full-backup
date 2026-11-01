"""Janitor 单测：过期/地板/去重/边封顶/租约/状态清理。"""
from __future__ import annotations

import sys
import json
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiji import store
from aiji.db import DB
from aiji.janitor import Janitor, LEASE_KEY
from aiji.store import FactDraft, Ident, add_fact


def workdir(name: str) -> Path:
    d = ROOT / ".tmp" / f"test_jan_{name}"
    d.mkdir(mode=0o777, parents=True, exist_ok=True)
    return d


def mkdb(name: str) -> DB:
    p = workdir(name) / "j.db"
    if p.exists():
        p.unlink()
    return DB(p)


IDENT = Ident("qq", "u1")


class TestJanitor(unittest.TestCase):
    def test_floor_delete_soft(self):
        db = mkdb("floor")
        with db.tx() as c:
            c.execute("INSERT INTO fact(platform,platform_user_id,scope,subject,predicate,"
                      "object,text,confidence,status,created_at,updated_at)"
                      " VALUES('qq','u1','default','user','居住地','杭州','用户住在杭州',"
                      "0.05,'active',1,1)")
        j = Janitor(db)
        j.run(now=1750000000.0)
        row = db.read("SELECT status FROM fact")[0]
        self.assertEqual(row["status"], "deleted")  # 软删不物理删
        self.assertEqual(db.read("SELECT COUNT(*) c FROM fact")[0]["c"], 1)

    def test_expire_valid_to(self):
        db = mkdb("expire")
        with db.tx() as c:
            c.execute("INSERT INTO fact(platform,platform_user_id,scope,subject,predicate,"
                      "object,text,confidence,status,valid_to,created_at,updated_at)"
                      " VALUES('qq','u1','default','user','备注','x','x',0.9,'active',"
                      "50.0,1,1)")
        Janitor(db).run(now=1750000000.0)
        self.assertEqual(db.read("SELECT status FROM fact")[0]["status"], "expired")

    def test_stale_low_confidence_unhit_fact_is_frozen_not_deleted(self):
        """v11 语义（borrow-plan P0-2）：低置信陈事实**冻结**而不是软删。

        对 prompt 的效果相同（都不注入），但冻结可回滚、可事后核对——
        "遗忘是可访问性，不是删除"。
        """
        db = mkdb("stale")
        now = 1750000000.0

        def insert(conf: float, days: int, text: str) -> None:
            with db.tx() as c:
                c.execute(
                    "INSERT INTO fact(platform,platform_user_id,scope,subject,predicate,"
                    "object,text,confidence,status,hit_count,created_at,updated_at)"
                    " VALUES('qq','u1','default','user','备注',?,?,"
                    "?, 'active',0,?,?)",
                    (text, text, conf, now - days * 86400, now - days * 86400),
                )

        insert(0.3, 120, "旧猜测")
        insert(0.3, 31, "较新判断")  # 刚过 30 天地板，但 S=7 时 R≈0.70 > 0.5 → 还不冻

        janitor = Janitor(db)
        janitor.run(now=now)

        row = db.read("SELECT status, frozen FROM fact WHERE object='旧猜测'")[0]
        self.assertEqual(row["status"], "active")  # 不软删
        self.assertEqual(row["frozen"], 1)         # 只冻结
        # 31 天的那条还没到冻结线（R≈0.70）：冻结是"可提取度"判据，不是单纯看天数
        self.assertEqual(db.read("SELECT frozen FROM fact WHERE object='较新判断'")[0]["frozen"], 0)
        self.assertEqual(janitor.stats["stale_frozen"], 1)
        # 默认召回看不到；显式查询仍查得到（"查到即解冻"的前提是能查到）
        ident = store.Ident("qq", "u1")
        self.assertEqual(store.search_facts(db.conn(), ident, "旧猜测"), [])
        self.assertEqual(len(store.search_facts(db.conn(), ident, "旧猜测",
                                                include_frozen=True)), 1)

    def test_dedup_merge(self):
        db = mkdb("dedup")
        # 两条同三元组 active（绕过 add_fact 的合并逻辑直接插）
        with db.tx() as c:
            for i, m in ((1, 2), (2, 5)):
                c.execute("INSERT INTO fact(platform,platform_user_id,scope,subject,predicate,"
                          "object,text,confidence,status,mentions,created_at,updated_at)"
                          " VALUES('qq','u1','default','user','爱好','钓鱼','爱钓鱼',0.8,"
                          f"'active',{m},1,1)")
        j = Janitor(db)
        j.run(now=1750000000.0)
        rows = db.read("SELECT id, status, mentions FROM fact ORDER BY id")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "active")       # 保留最早
        self.assertEqual(rows[1]["status"], "deleted")      # 后来的软删
        self.assertEqual(rows[0]["mentions"], 2 + 5)        # 吸收提及数
        self.assertEqual(j.stats["dedup_merged"], 1)

    def test_link_cap(self):
        db = mkdb("cap")
        with db.tx() as c:
            for i in range(1, 9):  # 8 条边，上限 params.LINK_CAP=6
                c.execute("INSERT INTO fact_link(platform,platform_user_id,scope,src_id,dst_id,"
                          "weight,reason,created_at) VALUES('qq','u1','default',1,?,?,'r',1)",
                          (i, i / 10.0))
        j = Janitor(db)
        j.run(now=1750000000.0)
        from aiji.params import LINK_CAP as _cap
        n = db.read("SELECT COUNT(*) c FROM fact_link")[0]["c"]
        self.assertEqual(n, _cap)  # 封顶 LINK_CAP（实测调参后=6），保留权重最高的 N 条
        self.assertEqual(j.stats["links_capped"], 1)

    def test_lease_single_instance(self):
        db = mkdb("lease")
        j1, j2 = Janitor(db), Janitor(db)
        r1 = j1.run(now=1750000000.0)
        self.assertFalse(r1["skipped"])
        # 正常完成即释放租约：第二次可立即抢（无活锁）
        r2 = j2.run(now=1750000110.0)
        self.assertFalse(r2["skipped"])
        # 崩溃语义：人为模拟持锁不释放（lease_until 在未来），另一实例跳过
        import json as _json
        with db.tx() as c:
            c.execute(
                "UPDATE heart_state SET value=? WHERE platform='_sys'"
                " AND state_key='janitor_lease'",
                (_json.dumps({"process": "dead", "lease_until": 1750000110.0 + 300}),))
        r3 = Janitor(db).run(now=1750000110.0)
        self.assertTrue(r3["skipped"])
        # TTL 过期后可抢：停摆上限 = TTL，无死锁面
        r4 = Janitor(db).run(now=1750000110.0 + 301)
        self.assertFalse(r4["skipped"])

    def test_lease_release_does_not_clear_another_owner(self):
        db = mkdb("lease-owner")
        j = Janitor(db)
        with db.tx() as c:
            c.execute(
                "INSERT INTO heart_state(platform,platform_user_id,scope,state_key,"
                "day_bucket,value,updated_at) VALUES('_sys','_sys','default',?,'0',?,?)",
                (LEASE_KEY, '{"process":"other-owner","lease_until":9999999999.0}', 1.0),
            )
        j._release_lease(now=2.0)
        value = json.loads(
            db.read(
                "SELECT value FROM heart_state WHERE platform='_sys' AND state_key=?",
                (LEASE_KEY,),
            )[0]["value"]
        )
        self.assertEqual(value["process"], "other-owner")

    def test_prune_state_and_eventlog(self):
        db = mkdb("prune")
        old_day = time.strftime("%Y-%m-%d", time.localtime(1750000000.0 - 3 * 86400))
        with db.tx() as c:
            c.execute("INSERT INTO heart_state(platform,platform_user_id,scope,state_key,"
                      "day_bucket,value,updated_at) VALUES('qq','u1','default','cooldown',"
                      f"'{old_day}','1',1)")
            c.execute("INSERT INTO heart_state(platform,platform_user_id,scope,state_key,"
                      "day_bucket,value,updated_at) VALUES('qq','u1','default','recent',"
                      "'0','[1]',1)")
            c.execute("INSERT INTO event_log(platform,platform_user_id,scope,kind,payload,ts)"
                      " VALUES('qq','u1','default','mention','{}',50.0)")
            c.execute("INSERT INTO event_log(platform,platform_user_id,scope,kind,payload,ts)"
                      " VALUES('qq','u1','default','mention','{}',1749999999.0)")
        j = Janitor(db)
        j.run(now=1750000000.0)
        # day-scoped 旧桶被清；persistent 键保留（qq 侧剩 recent 1 行 + janitor 租约 1 行）
        rows = db.read("SELECT state_key, day_bucket FROM heart_state")
        self.assertEqual(len(rows), 2, f"rows={rows}")
        keys = {(r["state_key"], r["day_bucket"]) for r in rows}
        self.assertIn(("recent", "0"), keys)
        self.assertNotIn(("cooldown", old_day), keys)
        # 旧 event_log 清，新保留
        self.assertEqual(db.read("SELECT COUNT(*) c FROM event_log")[0]["c"], 1)

    def test_normal_facts_survive(self):
        """正常置信、不过期的 fact 一条不动（软删只打低置信/过期/重复）。"""
        db = mkdb("safe")
        with db.tx() as c:
            add_fact(c, IDENT, FactDraft("user", "居住地", "杭州", "用户住在杭州", 0.9))
        Janitor(db).run(now=1750000000.0)
        row = db.read("SELECT status, confidence FROM fact")[0]
        self.assertEqual(row["status"], "active")
        self.assertGreaterEqual(row["confidence"], 0.5)


if __name__ == "__main__":
    unittest.main()
