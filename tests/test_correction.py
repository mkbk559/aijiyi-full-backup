"""纠错提取 + 负向下调 + event_log 回放 单测。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiji.hippocampus.db import DB
from aiji.hippocampus.extract import detect_correction
from aiji.heart.heart import Heart, OutboxEvent
from aiji.hippocampus.store import FactDraft, Ident, add_fact, apply_feedback


def workdir(name: str) -> Path:
    d = ROOT / ".tmp" / f"test_corr_{name}"
    d.mkdir(mode=0o777, parents=True, exist_ok=True)
    return d


def mkdb(name: str) -> DB:
    p = workdir(name) / "c.db"
    if p.exists():
        p.unlink()
    return DB(p)


IDENT = Ident("qq", "u1")


class TestDetectCorrection(unittest.TestCase):
    def test_positive(self):
        c = detect_correction("你错了，我住的是苏州不是杭州")
        self.assertIsNotNone(c)
        self.assertEqual(c.predicate_hint, "居住地")

    def test_not_correction(self):
        self.assertIsNone(detect_correction("今天天气不错"))
        self.assertIsNone(detect_correction("你叫什么名字"))

    def test_new_value(self):
        c = detect_correction("不对，其实是上海")
        self.assertIsNotNone(c)
        self.assertEqual(c.new_value, "上海")

    def test_name_hint(self):
        c = detect_correction("记错了，我姓王不姓李")
        self.assertIsNotNone(c)
        self.assertEqual(c.predicate_hint, "名字")


class TestNegativeFeedback(unittest.TestCase):
    def test_downgrade_with_floor(self):
        from aiji.right_hand import params
        db = mkdb("down")
        with db.tx() as c:
            fid, _ = add_fact(c, IDENT, FactDraft("user", "居住地", "杭州", "用户住在杭州", 0.9))
        # 连续 3 次负反馈：0.9 → ×NEG_FACTOR^n（0.7: 0.63 → 0.441 → 0.3087）
        for _ in range(3):
            with db.tx() as c:
                apply_feedback(c, IDENT, None, [fid], helpful=False)
        row = db.read("SELECT confidence FROM fact WHERE id=?", (fid,))[0]
        self.assertAlmostEqual(row["confidence"], 0.9 * params.NEG_FACTOR ** 3, places=3)
        # 持续负反馈：地板不穿透
        for _ in range(10):
            with db.tx() as c:
                apply_feedback(c, IDENT, None, [fid], helpful=False)
        row = db.read("SELECT confidence FROM fact WHERE id=?", (fid,))[0]
        self.assertGreaterEqual(row["confidence"], 0.15)
        # 软删状态不变（负反馈下调，不删行）
        self.assertEqual(db.read("SELECT status FROM fact WHERE id=?", (fid,))[0]["status"],
                         "active")
        # 可回升（不对称升降）：正向确认 ×POS_FACTOR 拉回
        for _ in range(4):
            with db.tx() as c:
                apply_feedback(c, IDENT, None, [fid], helpful=True)
        row = db.read("SELECT confidence FROM fact WHERE id=?", (fid,))[0]
        self.assertGreater(row["confidence"], 0.19, "正向确认必须能拉离地板")

    def test_positive_feedback_still_counts(self):
        db = mkdb("pos")
        with db.tx() as c:
            fid, _ = add_fact(c, IDENT, FactDraft("user", "爱好", "钓鱼", "爱钓鱼", 0.7))
        with db.tx() as c:
            apply_feedback(c, IDENT, None, [fid], helpful=True)
        row = db.read("SELECT hit_count, confidence FROM fact WHERE id=?", (fid,))[0]
        self.assertEqual(row["hit_count"], 1)
        self.assertAlmostEqual(row["confidence"], 0.7)  # 未被下调过：正反馈只计数不动置信


class TestEventLogReplay(unittest.TestCase):
    def test_replay_recent_isolated(self):
        db = mkdb("replay")
        h = Heart(db)
        a, b = Ident("qq", "u_a"), Ident("qq", "u_b")
        for i in range(5):
            h.enqueue(a, OutboxEvent(kind="mention", text=f"a{i}"))
        h.enqueue(b, OutboxEvent(kind="mention", text="b0"))
        ra = h.replay_recent(a, limit=100)
        rb = h.replay_recent(b, limit=100)
        self.assertEqual(len(ra), 5)
        self.assertEqual(len(rb), 1)
        self.assertTrue(all(p["payload"]["text"].startswith("a") for p in ra))
        self.assertEqual(rb[0]["payload"]["text"], "b0")
        # 正序（时间递增）
        ts = [p["ts"] for p in ra]
        self.assertEqual(ts, sorted(ts))
        # limit 生效
        self.assertEqual(len(h.replay_recent(a, limit=2)), 2)


if __name__ == "__main__":
    unittest.main()
