"""硬缺口验证：cited_ids 作用域正确性 + injection 口径 + 软删读路径一致性。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiji.hippocampus.db import DB
from aiji.heart.heart import Heart, OutboxEvent
from aiji.hippocampus.store import FactDraft, Ident, add_fact, apply_correction, apply_feedback, log_injection, last_injection_with_feedback


def workdir(name: str) -> Path:
    d = ROOT / ".tmp" / f"test_scope_{name}"
    d.mkdir(mode=0o777, parents=True, exist_ok=True)
    return d


def mkdb(name: str) -> DB:
    p = workdir(name) / "s.db"
    if p.exists():
        p.unlink()
    return DB(p)


IDENT = Ident("qq", "u1")


class TestCitedScope(unittest.TestCase):
    """S3 前置条件：×0.6 只施加在 cited 上，不误伤同用户其他 fact。"""

    def _seed(self, db):
        with db.tx() as c:
            fid_city, _ = add_fact(c, IDENT, FactDraft("user", "居住地", "杭州", "用户住在杭州", 0.9))
            fid_name, _ = add_fact(c, IDENT, FactDraft("user", "名字", "李雷", "用户叫李雷", 0.9))
            fid_hobby, _ = add_fact(c, IDENT, FactDraft("user", "爱好", "钓鱼", "爱钓鱼", 0.8))
        return fid_city, fid_name, fid_hobby

    def test_cited_precise_scope(self):
        from aiji.right_hand import params
        db = mkdb("cited")
        fid_city, fid_name, fid_hobby = self._seed(db)
        # 用户纠正居住地：cited 只有 fid_city
        with db.tx() as c:
            res = apply_correction(c, IDENT, cited_ids=[fid_city],
                                   predicate_hint="居住地", text="我住苏州不是杭州")
        self.assertEqual(res["cited"], [fid_city])
        self.assertEqual(res["fallback"], [])
        rows = {r["id"]: (r["confidence"], r["status"]) for r in
                db.read("SELECT id, confidence, status FROM fact")}
        # 只有 cited 降了（0.9×NEG_FACTOR），其他两个不动
        self.assertAlmostEqual(rows[fid_city][0], 0.9 * params.NEG_FACTOR, places=3)
        self.assertAlmostEqual(rows[fid_name][0], 0.9)
        self.assertAlmostEqual(rows[fid_hobby][0], 0.8)
        self.assertTrue(all(v[1] == "active" for v in rows.values()))  # 不删行

    def test_no_cited_fallback_halved(self):
        from aiji.right_hand import params
        db = mkdb("fallback")
        fid_city, fid_name, fid_hobby = self._seed(db)
        # 无 cited：按 predicate_hint 降级定位，幅度减半（×FALLBACK）
        with db.tx() as c:
            res = apply_correction(c, IDENT, cited_ids=[],
                                   predicate_hint="居住地", text="我住苏州不是杭州")
        self.assertEqual(res["cited"], [])
        self.assertIn(fid_city, res["fallback"])
        rows = {r["id"]: r["confidence"] for r in db.read("SELECT id, confidence FROM fact")}
        self.assertAlmostEqual(rows[fid_city], 0.9 * params.NEG_FACTOR_FALLBACK, places=3)  # 减半
        self.assertAlmostEqual(rows[fid_name], 0.9)                  # 不误伤
        self.assertAlmostEqual(rows[fid_hobby], 0.8)

    def test_fallback_text_overlap(self):
        db = mkdb("overlap")
        fid_city, _, _ = self._seed(db)
        with db.tx() as c:
            res = apply_correction(c, IDENT, cited_ids=[], predicate_hint="",
                                   text="你记岔了，用户住在杭州这事不对")
        self.assertIn(fid_city, res["fallback"])

    def test_last_injection_cited_semantics(self):
        """口径固化：cited = feedback.used（生成方回报）；feedback 存在但 used=[]
        表示"明确没引用"，不得退化到 item_ids；仅无 feedback 时才退化。"""
        db = mkdb("inj_sem")
        with db.tx() as c:
            inj_id = log_injection(c, IDENT, "我住哪", [10, 11], "prompt...", session="s1")
        # 无 feedback：cited 退化为 item_ids
        with db.tx() as c:
            iid, cited = last_injection_with_feedback(c, IDENT, session="s1")
        self.assertEqual(iid, inj_id)
        self.assertEqual(cited, [10, 11])
        # 生成方回报 used=[10]：cited 精确为 [10]
        with db.tx() as c:
            apply_feedback(c, IDENT, inj_id, [10], helpful=True)
        with db.tx() as c:
            iid2, cited2 = last_injection_with_feedback(c, IDENT, session="s1")
        self.assertEqual(cited2, [10])
        self.assertNotEqual(cited2, [10, 11])
        # 生成方回报 used=[]（明确没引用）：不得退化到 item_ids
        with db.tx() as c:
            inj2 = log_injection(c, IDENT, "在吗", [20, 21], "prompt...", session="s1")
            apply_feedback(c, IDENT, inj2, [], helpful=False)
        with db.tx() as c:
            iid3, cited3 = last_injection_with_feedback(c, IDENT, session="s1")
        self.assertEqual(iid3, inj2)
        self.assertEqual(cited3, [], f"used=[] 不得退化 item_ids，got {cited3}")

    def test_feedback_cannot_update_injection_from_another_scope(self):
        db = mkdb("feedback-scope")
        scope_a = Ident("qq", "u1", "group:a")
        scope_b = Ident("qq", "u1", "group:b")
        with db.tx() as c:
            log_injection(c, scope_a, "q", [], "prompt-a", session="group:a")
            id_b = log_injection(c, scope_b, "q", [], "prompt-b", session="group:b")

        with db.tx() as c:
            apply_feedback(c, scope_a, id_b, [], helpful=True, note="wrong-scope")

        feedback = db.read("SELECT feedback FROM injection WHERE id=?", (id_b,))[0]["feedback"]
        self.assertIsNone(feedback)


class TestSoftDeleteReadPaths(unittest.TestCase):
    """软缺口 4：软删后所有读路径不得返回该行。"""

    def test_soft_deleted_never_returns(self):
        db = mkdb("softread")
        with db.tx() as c:
            fid, _ = add_fact(c, IDENT, FactDraft("user", "居住地", "杭州", "用户住在杭州", 0.9))
            add_fact(c, IDENT, FactDraft("user", "名字", "李雷", "用户叫李雷", 0.9))
        # 软删（janitor 地板路径）
        with db.tx() as c:
            c.execute("UPDATE fact SET status='deleted' WHERE id=?", (fid,))
        # 读路径 1：search_facts（FTS 四级降级）
        from aiji.hippocampus.store import search_facts
        hits = search_facts(db.conn(), IDENT, "杭州")
        self.assertTrue(all(h.id != fid for h in hits), f"search_facts 泄漏: {[(h.id, h.text) for h in hits]}")
        # 读路径 2：inject 按 id 回查（kinds 映射）
        from aiji.hippocampus.inject import Injector
        rows = db.read("SELECT id, kind, confidence FROM fact WHERE id IN (?) AND status='active'", (fid,))
        self.assertEqual(len(rows), 0)
        # 读路径 3：recall 主链路
        from aiji import Memory
        mem = Memory.open(path=str(workdir("softread") / "m.db"))
        r = mem.recall("杭州", IDENT, session="s")
        self.assertTrue(all(h.id != fid for h in r.hits if h.kind in ("fact", "semantic")))
        # 读路径 4：last_injection / correction 定位也不命中已删行
        with db.tx() as c:
            res = apply_correction(c, IDENT, cited_ids=[fid], predicate_hint="", text="")
        self.assertEqual(res["cited"], [])  # 软删行不可再降


class TestFloorSlope(unittest.TestCase):
    """软缺口 6：累积斜率记录——10 轮矛盾纠错后地板行为 + 可回升（锁死反断言）。"""

    def test_slope_10_rounds(self):
        db = mkdb("slope")
        with db.tx() as c:
            fid, _ = add_fact(c, IDENT, FactDraft("user", "居住地", "杭州", "用户住在杭州", 0.9))
        trace = []
        for i in range(10):
            with db.tx() as c:
                res = apply_correction(c, IDENT, cited_ids=[fid], text=f"不对，第{i}轮")
            row = db.read("SELECT confidence FROM fact WHERE id=?", (fid,))[0]
            trace.append(round(row["confidence"], 4))
        # 0.9 ×0.7^n：0.63 → 0.441 → 0.3087 → 0.2161 → 0.1513（近地板）→ 之后恒 ≤0.1513
        self.assertAlmostEqual(trace[0], 0.63, places=3)
        self.assertAlmostEqual(trace[1], 0.441, places=3)
        self.assertAlmostEqual(trace[2], 0.3087, places=3)
        self.assertAlmostEqual(trace[3], 0.2161, places=3)
        self.assertAlmostEqual(trace[4], 0.1513, places=3)
        self.assertTrue(all(0.15 <= x <= trace[0] for x in trace), f"斜率: {trace}")
        self.assertTrue(all(x <= trace[4] for x in trace[5:]), "之后不再反弹")
        print(f"    [slope] 10 轮: {trace}")

    def test_recovery_after_floor(self):
        """可回升（2026-10-29 复盘）：触地板后正向确认 ×1.25 拉回，封顶 0.95。
        "到底但能回升"才是绿；恒定 0.15 = 锁死红。"""
        from aiji.right_hand import params
        db = mkdb("recover")
        with db.tx() as c:
            fid, _ = add_fact(c, IDENT, FactDraft("user", "居住地", "杭州", "用户住在杭州", 0.9))
        # 施压到地板
        for i in range(10):
            with db.tx() as c:
                apply_correction(c, IDENT, cited_ids=[fid], text=f"不对，第{i}轮")
        floor_val = db.read("SELECT confidence FROM fact WHERE id=?", (fid,))[0]["confidence"]
        self.assertLessEqual(floor_val, 0.20, "施压后应接近地板")
        # 正向确认 4 次：0.15×1.25^4 ≈ 0.366（>0.19 明显回升）
        vals = []
        for _ in range(4):
            with db.tx() as c:
                apply_feedback(c, IDENT, None, [fid], helpful=True)
            vals.append(round(db.read("SELECT confidence FROM fact WHERE id=?",
                                      (fid,))[0]["confidence"], 4))
        self.assertGreater(vals[-1], 0.19, f"正向确认必须拉离地板: {vals}")
        self.assertLess(vals[-1], params.POS_CAP, "回升封顶 0.95")
        # 未被下调过的行：正向确认只计数不动置信
        with db.tx() as c:
            fid2, _ = add_fact(c, IDENT, FactDraft("user", "名字", "李雷", "用户叫李雷", 0.9))
        with db.tx() as c:
            apply_feedback(c, IDENT, None, [fid2], helpful=True)
        v2 = db.read("SELECT confidence, hit_count FROM fact WHERE id=?", (fid2,))[0]
        self.assertAlmostEqual(v2["confidence"], 0.9, places=6)  # 未下调不动
        self.assertEqual(v2["hit_count"], 1)                      # 计数 +1
        print(f"    [recovery] floor={floor_val} → {vals}")


class TestReplayIdempotent(unittest.TestCase):
    """P1：event_log 回放幂等 + 收敛（崩溃恢复语义）。"""

    def _mk(self, name):
        from aiji.heart.heart import Heart, OutboxEvent
        db = mkdb(f"replay_{name}")
        h = Heart(db)
        a = Ident("qq", "u_a")
        for i in range(5):
            h.enqueue(a, OutboxEvent(kind="mention", text=f"m{i}"))
        return db, h, a

    def test_replay_twice_identical(self):
        """重复回放结果一致：不重复计数、不改变库状态。"""
        db, h, a = self._mk("twice")
        r1 = h.replay_recent(a, limit=100)
        r2 = h.replay_recent(a, limit=100)
        self.assertEqual(r1, r2)  # 幂等：两次逐项相等
        n = db.read("SELECT COUNT(*) c FROM event_log")[0]["c"]
        self.assertEqual(n, 5)  # 回放不写库

    def test_replay_after_crash_new_instance(self):
        """崩溃恢复语义：新建 Heart 实例（同库）回放结果一致。"""
        db, h, a = self._mk("crash")
        before = h.replay_recent(a, limit=100)
        h2 = Heart(db)  # 模拟重启
        after = h2.replay_recent(a, limit=100)
        self.assertEqual(before, after)

    def test_replay_converges_with_new_events(self):
        """收敛：回放后新事件追加，再次回放 = 旧事件 + 新事件（正序、无重复）。"""
        from aiji.heart.heart import Heart, OutboxEvent
        db, h, a = self._mk("conv")
        old = h.replay_recent(a, limit=100)
        h.enqueue(a, OutboxEvent(kind="comment", text="new1"))
        h.enqueue(a, OutboxEvent(kind="comment", text="new2"))
        new = h.replay_recent(a, limit=100)
        self.assertEqual(len(new), len(old) + 2)
        texts = [p["payload"]["text"] for p in new]
        self.assertEqual(len(texts), len(set(texts)))  # 无重复
        self.assertEqual(texts[-2:], ["new1", "new2"])  # 正序收敛


class TestAggregateSoftDelete(unittest.TestCase):
    """P0 聚合路径补充：统计/聚合也不得把软删行算进 active。"""

    def test_fact_stats_excludes_deleted(self):
        from aiji.hippocampus.store import stats as fact_stats
        db = mkdb("agg")
        with db.tx() as c:
            fid, _ = add_fact(c, IDENT, FactDraft("user", "居住地", "杭州", "用户住在杭州", 0.9))
            add_fact(c, IDENT, FactDraft("user", "名字", "李雷", "用户叫李雷", 0.9))
        with db.tx() as c:
            c.execute("UPDATE fact SET status='deleted' WHERE id=?", (fid,))
        s = fact_stats(db.conn(), IDENT)
        self.assertEqual(s["facts"], 1)  # active 聚合不含软删行


if __name__ == "__main__":
    unittest.main()
