"""运行时 SQL 审计：抓一次"单身份"业务场景执行过的**所有** SQL，
凡碰身份表又没有 `platform` 谓词的，全部报出来。

静态工具（`tools/audit_identity_sql.py`）看不到
`f"SELECT … WHERE {w}" + ident.where()` 这种运行时拼条件；
这里直接挂 `set_trace_callback`，看的是**真正执行**的语句。
"""
from __future__ import annotations
import os
os.environ.setdefault("AIJI_SHARED_MEMORY", "0")  # 只测身份隔离/统计，不测共享镜像

import pathlib
import re
import sqlite3
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.hippocampus import store  # noqa: E402
from aiji.hippocampus.db import DB  # noqa: E402
from aiji.hippocampus.store import FactDraft, Ident  # noqa: E402

IDENTITY_TABLES = {
    "fact", "fact_link", "fact_log", "turn", "turn_archive", "profile", "trait",
    "injection", "reflection", "summary", "outbox", "send_queue", "media",
    "heart_state", "self_memory", "style", "account",
}
TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)


def touches_identity(sql: str) -> list[str]:
    tables = {t.lower() for t in TABLE_REF.findall(sql)}
    return sorted(tables & IDENTITY_TABLES)


class RuntimeIdentityAuditTest(unittest.TestCase):
    def test_every_statement_in_a_single_identity_flow_is_scoped(self):
        path = pathlib.Path(make_tmp(self, "trace")) / "trace.db"
        statements: list[str] = []
        real_connect = sqlite3.connect

        def traced_connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            conn.set_trace_callback(statements.append)
            return conn

        with mock.patch.object(sqlite3, "connect", side_effect=traced_connect):
            db = DB(path)
        # 建库时的 schema 回填（`UPDATE fact SET valid_from=created_at ...` 之类）
        # 是全局数据整形，有意不带身份条件；它们只在初始化时跑一次。
        statements.clear()

        ident = Ident("wechat", "alice", "personal")
        now = time.time()
        with db.tx() as conn:
            fact_id, _ = store.add_fact(conn, ident, FactDraft(
                "user", "居住地", "杭州", "用户住在杭州", 0.9), now=now)
            store.add_turn(conn, ident, "s1", "user", "我住在杭州", now=now)
            store.add_turn(conn, ident, "s1", "assistant", "知道啦", now=now)
            store.upsert_profile(conn, ident, "城市", "杭州", now=now)
            store.upsert_style(conn, ident, {"tone": "口语化"}, now=now)
            store.upsert_trait(conn, ident, "爱好", "画画", now=now)
            store.add_media(conn, ident, kind="image", ref="x.jpg", session="s1",
                            text="一张画", now=now)
            store.log_injection(conn, ident, "我住哪", [fact_id], "注入块",
                                session="s1", now=now)
            store.put_summary(conn, ident, "s1", 2, "摘要文本", now=now)
            store.log_reflection(conn, ident, "s1", 2, 2, "反思文本", [], now=now)
            store.link_facts(conn, ident, fact_id, "user", "居住地", "用户住在杭州",
                             now=now)
            store.bump_usage(conn, ident, [fact_id], now=now)
        with db.tx() as conn:
            store.search_facts(conn, ident, "杭州", limit=8)
            store.recent_turns(conn, ident, "s1", 10)
            store.profile_snapshot(conn, ident)
            store.style_snapshot(conn, ident)
            store.trait_snapshot(conn, ident)
            store.media_for(conn, ident, "s1", 10)
            store.latest_summary(conn, ident, "s1")
            store.last_reflection(conn, ident, "s1")
            store.last_injection_with_feedback(conn, ident, "s1")
            store.links_for(conn, ident, [fact_id])
            store.facts_as_of(conn, ident, now + 60)
        db.close()

        self.assertGreater(len(statements), 30, "trace 没挂上（语句太少）")
        violations = []
        for sql in statements:
            tables = touches_identity(sql)
            if not tables:
                continue
            if "platform" in sql.lower():
                continue
            violations.append(f"[{','.join(tables)}] {' '.join(sql.split())[:160]}")
        self.assertEqual(
            violations, [],
            "这些语句碰了身份表却没有 platform 谓词（串号风险）：\n" + "\n".join(violations))

    def test_audit_would_catch_an_unscoped_query(self):
        """检查器本身要有牙：手写一条漏 platform 的查询必须被判定为违规。"""
        bad = "SELECT * FROM fact WHERE status='active'"
        self.assertEqual(touches_identity(bad), ["fact"])
        self.assertNotIn("platform", bad.lower())

    def test_store_stats_is_intentionally_global(self):
        """`store.stats()` 报的是**全库**规模（外加按平台的那一个键），不是泄漏。

        它不是"某身份的行内容"，而是聚合计数；要按身份看条数得用
        `store.search_facts(...)` 或 `stats()["facts_active_<platform>"]`。
        这条测试把"有意为之"写下来，免得以后有人把它当 bug 改掉，
        也免得有人误以为它已经做了身份隔离。
        """
        path = pathlib.Path(make_tmp(self, "stats")) / "stats.db"
        db = DB(path)
        alice = Ident("wechat", "alice", "personal")
        bob = Ident("qq", "bob", "default")
        now = time.time()
        with db.tx() as conn:
            store.add_fact(conn, alice, FactDraft("user", "a", "1", "甲的", 0.9), now=now)
            store.add_fact(conn, bob, FactDraft("user", "b", "2", "乙的", 0.9), now=now)
            mine = store.stats(conn, alice)
        self.assertEqual(mine["facts"], 2, "全库计数")
        self.assertEqual(mine["facts_active_wechat"], 1, "按平台的那一个是自己的")
        self.assertEqual(sorted(mine["platforms"]), ["qq", "wechat"])
        db.close()


if __name__ == "__main__":
    unittest.main()
