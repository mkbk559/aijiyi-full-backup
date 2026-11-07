import os
os.environ.setdefault("AIJI_SHARED_MEMORY", "0")  # 只测身份隔离/统计，不测共享镜像
"""janitor 的两条**数据正确性**规则（去重合并 / 关联边封顶）——原先未覆盖。

它们真的会改数据（软删 + 吸收 mentions / 删多余边），错一次就是记忆丢失，
所以用例必须钉住"该删的删、不该动的别动、乐观锁冲突时跳过"。

注意 `FactDraft(subject, predicate, object, quote, confidence)`：
第一格是 subject（"user"、"aiji"…），要看"哪条事实"得看 predicate。
"""
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.hippocampus import janitor as janitor_mod  # noqa: E402
from aiji.hippocampus.db import DB  # noqa: E402
from aiji.hippocampus.store import FactDraft, Ident, add_fact  # noqa: E402

IDENT = Ident("qq", "u1", "g1")


class DedupMergeTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_tmp(self, "janitor-dedup")
        self.db = DB(self.dir / "j.db")
        self.janitor = janitor_mod.Janitor(self.db)

    def tearDown(self):
        self.db.close()

    def _add(self, predicate: str, obj: str, ident: Ident = IDENT,
             quote: str = "说明") -> int:
        with self.db.tx() as c:
            added = add_fact(c, ident, FactDraft("user", predicate, obj, quote, 0.9))
        return added[0] if isinstance(added, tuple) else added

    def _insert_dup(self, predicate: str, obj: str, mentions: int = 1) -> int:
        """直接插一条重复事实：`add_fact` 自身会合并，制造不了重复。"""
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT INTO fact (platform, platform_user_id, scope, subject,"
                " predicate, object, text, confidence, status, source, source_kind,"
                " extra, created_at, updated_at, kind, mentions, version)"
                " VALUES (?,?,?,'user',?,?,'说明',0.9,'active','test','test','{}',"
                " 1.0, 1.0, 'fact', ?, 1)",
                (IDENT.platform, IDENT.platform_user_id, IDENT.scope,
                 predicate, obj, mentions))
        return int(cur.lastrowid)

    def _rows(self, predicate: str) -> list[dict]:
        return [dict(r) for r in self.db.read(
            "SELECT id, status, mentions, version, platform_user_id FROM fact"
            " WHERE predicate=? ORDER BY id", (predicate,))]

    def test_exact_duplicates_are_soft_deleted_and_mentions_absorbed(self):
        first = self._add("喜欢的颜色", "蓝色", quote="说喜欢蓝色")
        second = self._insert_dup("喜欢的颜色", "蓝色", mentions=3)
        self.janitor._dedup_merge(1.0)
        rows = self._rows("喜欢的颜色")
        self.assertEqual([r["id"] for r in rows], [first, second])
        self.assertEqual(rows[0]["status"], "active", "最早那条要留")
        self.assertEqual(rows[1]["status"], "deleted", "重复的要软删，不是物理删")
        self.assertGreater(rows[0]["mentions"], 1, "提及数要合并到保留行")
        self.assertEqual(self.janitor.stats["dedup_merged"], 1)

    def test_different_object_is_not_merged(self):
        self._add("喜欢的颜色", "蓝色")
        self._add("喜欢的颜色", "红色")
        self.janitor._dedup_merge(1.0)
        rows = self._rows("喜欢的颜色")
        self.assertTrue(all(r["status"] == "active" for r in rows), rows)
        self.assertEqual(self.janitor.stats["dedup_merged"], 0)

    def test_other_identity_is_not_merged(self):
        self._add("喜欢的颜色", "蓝色")
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO fact (platform, platform_user_id, scope, subject,"
                " predicate, object, text, confidence, status, source, source_kind,"
                " extra, created_at, updated_at, kind, mentions, version)"
                " VALUES ('qq','u2','g1','user','喜欢的颜色','蓝色','说明',0.9,"
                " 'active','test','test','{}',1.0,1.0,'fact',1,1)")
        self.janitor._dedup_merge(1.0)
        rows = self._rows("喜欢的颜色")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["status"] == "active" for r in rows),
                        "跨身份绝不能合并（串号事故）")

    def test_version_conflict_skips_instead_of_clobbering(self):
        """模拟"读完之后、写之前"反思 Worker 改了这一行：version 不匹配就必须跳过。"""
        self._add("住在哪", "杭州")
        second = self._insert_dup("住在哪", "杭州", mentions=2)
        real_read = self.db.read

        def stale_read(sql, params=()):
            rows = real_read(sql, params)
            if "FROM fact WHERE status='active'" in sql:
                return [dict(r, version=int(r["version"]) + 1) for r in rows]
            return rows

        with mock.patch.object(self.db, "read", stale_read):
            self.janitor._dedup_merge(1.0)
        row = next(r for r in self._rows("住在哪") if r["id"] == second)
        self.assertEqual(row["status"], "active", "乐观锁冲突要跳过，不能硬删")
        self.assertEqual(self.janitor.stats["dedup_merged"], 0)


class CapLinksTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_tmp(self, "janitor-links")
        self.db = DB(self.dir / "j.db")
        self.janitor = janitor_mod.Janitor(self.db)
        # 直接插事实：`add_fact` 会顺手建关联边，撑不到"超过上限"的场景
        self.ids = [self._insert_fact(i) for i in range(6)]

    def tearDown(self):
        self.db.close()

    def _insert_fact(self, i: int) -> int:
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT INTO fact (platform, platform_user_id, scope, subject,"
                " predicate, object, text, confidence, status, source, source_kind,"
                " extra, created_at, updated_at, kind, mentions, version)"
                " VALUES ('qq','u1','g1','user',?,?,'说明',0.9,'active','test',"
                " 'test','{}',1.0,1.0,'fact',1,1)",
                (f"事实{i}", f"值{i}"))
        return int(cur.lastrowid)

    def _link_count(self, src: int) -> int:
        return int(self.db.read(
            "SELECT COUNT(*) n FROM fact_link WHERE src_id=?", (src,))[0]["n"])

    def test_extra_links_are_dropped_keeping_heaviest(self):
        cap = janitor_mod.LINK_CAP          # 别写死：上限由 params.py 决定
        src = self.ids[0]
        extra = [self._insert_fact(i) for i in range(10, 10 + cap + 2)]
        with self.db.tx() as c:
            for i, dst in enumerate(extra):
                c.execute(
                    "INSERT INTO fact_link (platform, platform_user_id, scope,"
                    " src_id, dst_id, weight, reason, created_at)"
                    " VALUES ('qq', 'u1', 'g1', ?, ?, ?, 'related', 1.0)",
                    (src, dst, float(i)))
        self.assertEqual(self._link_count(src), cap + 2)
        self.janitor._cap_links()
        rows = [dict(r) for r in self.db.read(
            "SELECT dst_id, weight FROM fact_link WHERE src_id=?"
            " ORDER BY weight DESC", (src,))]
        self.assertEqual(len(rows), cap)
        self.assertEqual([r["weight"] for r in rows],
                         [float(i) for i in range(cap + 1, 1, -1)],
                         "留下权重最高的那些")
        self.assertEqual(self.janitor.stats["links_capped"], 1)

    def test_links_under_cap_are_untouched(self):
        src = self.ids[1]
        dst = self._insert_fact(99)
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO fact_link (platform, platform_user_id, scope,"
                " src_id, dst_id, weight, reason, created_at)"
                " VALUES ('qq', 'u1', 'g1', ?, ?, 1.0, 'related', 1.0)",
                (src, dst))
        self.janitor._cap_links()
        self.assertEqual(self._link_count(src), 1)
        self.assertEqual(self.janitor.stats["links_capped"], 0)


if __name__ == "__main__":
    unittest.main()
