"""记忆代谢层单测（2026-11-02 borrow-plan P0/P1/P2）。

覆盖：读→强化（含 1h 冷却与跨天/同日增量）、冻结与解冻、turn 归档、
摘要哈希复用、显式遗忘、自我记忆淘汰、RRF 影子排名、事实键、反思批处理触发器。

跑法： python -m unittest tests.test_metabolism -v
"""
from __future__ import annotations

import json
import shutil
import sys
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiji import params, store  # noqa: E402
from aiji.config import Settings  # noqa: E402
from aiji.db import DB  # noqa: E402
from aiji.memory import Memory  # noqa: E402
from aiji.reflect import ReflectionWorker  # noqa: E402
from aiji.store import FactDraft, Ident, add_fact, add_turn, evict_turns  # noqa: E402

WX = Ident("wechat", "u1", "default")


def workdir(name: str) -> Path:
    d = ROOT / ".tmp" / f"test_meta_{name}"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(mode=0o777, parents=True, exist_ok=True)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = workdir(self._testMethodName[:8])
        self.db_path = self.dir / "t.db"
        self._mem: list[Memory] = []

    def tearDown(self):
        for m in self._mem:
            m.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def mem(self, **kw) -> Memory:
        kw.setdefault("db_path", self.db_path)
        kw.setdefault("routes", ())
        m = Memory(Settings(**kw), engines="auto")
        self._mem.append(m)
        return m

    def fact(self, db, conf=0.9, obj="杭州", pred="居住地", text="用户住在杭州") -> int:
        with db.tx() as c:
            fid, _ = add_fact(c, WX, FactDraft("user", pred, obj, text, conf))
        return fid


class TestReadReinforces(Base):
    def test_inject_bumps_hit_count_once_per_hour(self):
        m = self.mem()
        fid = self.fact(m.db)
        m.recaller.refresh_profile(WX)

        m.inject("我住哪", WX)
        row = m.db.read("SELECT hit_count, last_used_at, stability FROM fact WHERE id=?",
                        (fid,))[0]
        self.assertEqual(row["hit_count"], 1)
        self.assertIsNotNone(row["last_used_at"])
        s_first = float(row["stability"])
        self.assertGreater(s_first, params.STABILITY_INIT_DAYS)

        # 1 小时内再注入：只算一次强化（reviewCooldownMs=1h）
        m.inject("我住哪", WX)
        self.assertEqual(
            m.db.read("SELECT hit_count FROM fact WHERE id=?", (fid,))[0]["hit_count"], 1)

    def test_spaced_review_uses_spaced_gain(self):
        db = DB(self.db_path)
        fid = self.fact(db)
        t0 = 1_800_000_000.0
        with db.tx() as c:
            self.assertEqual(store.bump_usage(c, WX, [fid], now=t0), 1)
            s1 = float(c.execute("SELECT stability FROM fact WHERE id=?", (fid,)).fetchone()[0])
            # 同一天再想起：+5%
            store.bump_usage(c, WX, [fid], now=t0 + 7200)
            s2 = float(c.execute("SELECT stability FROM fact WHERE id=?", (fid,)).fetchone()[0])
            # 隔天想起：+20%
            store.bump_usage(c, WX, [fid], now=t0 + 30 * 3600)
            s3 = float(c.execute("SELECT stability FROM fact WHERE id=?", (fid,)).fetchone()[0])
        self.assertAlmostEqual(s2, s1 * (1 + params.FSRS_SAME_DAY_GAIN), places=6)
        self.assertAlmostEqual(s3, s2 * (1 + params.FSRS_SPACED_GAIN), places=6)
        # 封顶：一次强化不能变成"永不遗忘"
        with db.tx() as c:
            c.execute("UPDATE fact SET stability=? WHERE id=?", (params.STABILITY_MAX_DAYS, fid))
            store.bump_usage(c, WX, [fid], now=t0 + 100 * 86400)
            self.assertEqual(
                float(c.execute("SELECT stability FROM fact WHERE id=?",
                                (fid,)).fetchone()[0]), params.STABILITY_MAX_DAYS)


class TestFreeze(Base):
    def test_frozen_fact_is_not_injected_but_queryable_and_thaws(self):
        m = self.mem()
        fid = self.fact(m.db, conf=0.3, obj="旧号", pred="备注", text="用户以前用旧号")
        with m.db.tx() as c:
            c.execute("UPDATE fact SET frozen=1 WHERE id=?", (fid,))
        m.recaller.refresh_profile(WX)

        inj = m.inject("旧号", WX)
        self.assertEqual(inj.text, "")  # 冻结的不注入
        self.assertEqual(store.search_facts(m.db.conn(), WX, "旧号"), [])

        # 显式想起（管理接口/CLI）→ 解冻
        with m.db.tx() as c:
            self.assertTrue(store.thaw_fact(c, WX, fid))
        inj2 = m.inject("旧号", WX)
        self.assertIn("旧号", inj2.text)


class TestArchive(Base):
    def test_evict_writes_archive_before_delete(self):
        db = DB(self.db_path)
        for i in range(6):
            with db.tx() as c:
                add_turn(c, WX, "s1", "user" if i % 2 else "assistant", f"发言{i}")
        with db.tx() as c:
            n_evicted, _ = evict_turns(c, WX, "s1", keep=2)
        self.assertEqual(n_evicted, 4)
        self.assertEqual(db.read("SELECT COUNT(*) c FROM turn")[0]["c"], 2)
        rows = store.archived_turns(db.conn(), WX, "s1")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[-1]["content"], "发言0")  # 归档按 id 逆序返回，最后一条是最早的
        # 别的身份看不到
        self.assertEqual(store.archived_turns(db.conn(), Ident("qq", "u9"), "s1"), [])
        # 归档到期才物理清
        with db.tx() as c:
            self.assertEqual(store.prune_archive(c, keep_days=90), 0)
            self.assertEqual(store.prune_archive(c, keep_days=-1), 4)
        self.assertEqual(db.read("SELECT COUNT(*) c FROM turn_archive")[0]["c"], 0)


class TestSummaryReuse(Base):
    def test_same_source_hash_reuses_summary_without_model(self):
        m = self.mem(summary_model=False)
        lines = ["我叫李雷", "我在杭州工作"]
        src_hash = m.writer._hash_lines(lines)
        with m.db.tx() as c:
            store.put_summary(c, WX, "s1", 2, "李雷在杭州工作",
                              extra={"source_kind": "model"}, source_hash=src_hash)
        h2, text, kind = m.writer._summary_for(WX, "s1", lines)
        self.assertEqual(h2, src_hash)
        self.assertEqual(text, "李雷在杭州工作")
        self.assertEqual(kind, "reuse")
        self.assertEqual(m.writer.stats.summaries_reused, 1)
        # 原文变了就不命中
        _, _, kind2 = m.writer._summary_for(WX, "s1", lines + ["我又搬去上海了"])
        self.assertNotEqual(kind2, "reuse")


class TestForgetAndSelfMemory(Base):
    def test_forget_hides_fact_and_keeps_audit(self):
        m = self.mem()
        fid = self.fact(m.db)
        m.recaller.refresh_profile(WX)
        self.assertIn("杭州", m.inject("我住哪", WX).text)

        res = m.forget(WX, fact_id=fid)
        self.assertEqual(res["forgotten"], 1)
        self.assertEqual(m.inject("我住哪", WX).text, "")
        self.assertEqual(m.db.read("SELECT status FROM fact WHERE id=?", (fid,))[0]["status"],
                         "deleted")
        ops = [r["op"] for r in m.db.read("SELECT op FROM fact_log WHERE fact_id=?", (fid,))]
        self.assertIn("forget", ops)

    def test_self_memory_trims_oldest_beyond_cap(self):
        m = self.mem()
        for i in range(5):
            m.remember_self(f"自我{i}", kind="pref")
        with m.db.tx() as c:
            store.trim_self_memory(c, cap=3)
        rows = m.self_memory(limit=10)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["text"], "自我4")  # 新的在前


class TestRankModes(Base):
    def test_rrf_shadow_records_without_changing_hits(self):
        m = self.mem()
        for i, (obj, text) in enumerate((("杭州", "用户住在杭州"), ("钓鱼", "用户喜欢钓鱼"),
                                         ("李雷", "用户叫李雷"))):
            self.fact(m.db, obj=obj, pred=f"属性{i}", text=text)
        m.recaller.refresh_profile(WX)

        m.recaller.rank_mode = "weighted"
        base = m.recall("我住哪", WX)
        m.recaller.rank_mode = "rrf_shadow"
        shadowed = m.recall("我住哪", WX)
        self.assertEqual([h.id for h in base.hits], [h.id for h in shadowed.hits])
        self.assertEqual(base.shadow, [])
        self.assertTrue(shadowed.shadow)
        self.assertTrue(all(isinstance(x["id"], int) for x in shadowed.shadow))

        m.recaller.rank_mode = "rrf"
        fused = m.recall("我住哪", WX)
        self.assertTrue(fused.hits)
        self.assertTrue(all(0.0 <= h.score <= 1.0 for h in fused.hits))


class TestFactKeys(Base):
    def test_add_fact_stores_keys(self):
        db = DB(self.db_path)
        fid = self.fact(db, obj="杭州", text="用户住在杭州，2026年搬的")
        extra = json.loads(db.read("SELECT extra FROM fact WHERE id=?", (fid,))[0]["extra"])
        keys = extra["keys"]
        self.assertIn("杭州", keys)
        self.assertTrue(any("2026" in k for k in keys))
        self.assertLessEqual(len(keys), 14)


class TestReflectBatch(unittest.TestCase):
    def _worker(self) -> ReflectionWorker:
        fake_db = types.SimpleNamespace(read=lambda *a, **k: [])
        reflective = types.SimpleNamespace(db=fake_db, every_turns=30)
        return ReflectionWorker(reflective, interval_s=0.01)

    def test_trigger_needs_backlog_or_idle(self):
        w = self._worker()
        now = time.time()
        hot = [(WX, "s1", 40, now), (WX, "s2", 35, now)]
        self.assertTrue(w._should_run(hot))                       # 积压 2 个会话
        self.assertFalse(w._should_run([(WX, "s1", 5, now)]))     # 刚聊完、也没积压
        self.assertTrue(w._should_run([(WX, "s1", 5, now - 3600)]))  # 空闲 >15min


if __name__ == "__main__":
    unittest.main()
