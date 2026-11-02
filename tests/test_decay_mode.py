# -*- coding: utf-8 -*-
"""新近度口径（2026-11-03）：`RECALL_DECAY_MODE="stability"` 用事实自己存的 stability。

要钉死的性质：
  1. FSRS-5 曲线本身：R(t=0)=1、R(t=S)=0.9、随年龄单调降；
  2. **核心效应**：同龄两条旧事实，被引用过（stability 涨过）的那条新近度更高；
  3. 旧口径（half_life）对"用没用过"完全无感——这条是反证，证明改动确实补了缺口；
  4. 默认口径就是 stability（否则改了等于没改）。
"""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiji.hippocampus.recall import _recency_for_fact, score_fact
from aiji.hippocampus.store import Fact
from aiji.right_hand import params

NOW = time.time()


def mk(*, age_days: float, stability: float = 7.0, hits: int = 0) -> Fact:
    return Fact(
        id=1, platform="qq", platform_user_id="u", scope="default",
        kind="fact", subject="user", predicate="偏好", object="x", text="x",
        confidence=0.9, status="active",
        created_at=NOW - age_days * 86400, updated_at=NOW - age_days * 86400,
        source_kind="model", hit_count=hits, importance=0.5, mentions=1,
        stability=stability,
    )


class TestDecayCurve(unittest.TestCase):
    def test_curve_shape(self):
        f = mk(age_days=0.0, stability=30.0)
        self.assertAlmostEqual(_recency_for_fact(f, 0.0, "stability"), 1.0, places=4)
        # R(t=S)=0.9 是 FSRS 对"稳定度"的定义
        r_at_s = (1.0 + params.FSRS_F * 1.0) ** -0.5
        self.assertAlmostEqual(r_at_s, 0.9, places=3)
        f30 = mk(age_days=30.0, stability=30.0)
        self.assertAlmostEqual(_recency_for_fact(f30, 30 * 86400, "stability"), 0.9, places=3)

    def test_monotone_decreasing(self):
        f = mk(age_days=0.0, stability=10.0)
        vals = [_recency_for_fact(f, d * 86400, "stability") for d in (0, 5, 30, 365)]
        self.assertEqual(vals, sorted(vals, reverse=True))

    def test_used_old_fact_survives_better(self):
        old = mk(age_days=365.0, stability=7.0)
        old_used = mk(age_days=365.0, stability=28.0, hits=10)
        a = _recency_for_fact(old, 365 * 86400, "stability")
        b = _recency_for_fact(old_used, 365 * 86400, "stability")
        self.assertGreater(b, a, "被引用过（stability 涨过）的旧事实必须更抗遗忘")
        self.assertGreater(score_fact(old_used, 0, NOW, 0.6), score_fact(old, 0, NOW, 0.6))

    def test_old_mode_is_blind_to_usage(self):
        old = mk(age_days=365.0, stability=7.0)
        old_used = mk(age_days=365.0, stability=28.0, hits=10)
        self.assertEqual(
            _recency_for_fact(old, 365 * 86400, "half_life"),
            _recency_for_fact(old_used, 365 * 86400, "half_life"),
            "旧指数口径只看事实类型，看不出用没用过——这就是本轮补的缺口",
        )

    def test_default_mode_is_stability(self):
        self.assertEqual(params.RECALL_DECAY_MODE, "stability")
        old = mk(age_days=365.0, stability=7.0)
        old_used = mk(age_days=365.0, stability=28.0, hits=10)
        self.assertGreater(
            _recency_for_fact(old_used, 365 * 86400),
            _recency_for_fact(old, 365 * 86400),
            "不传 decay_mode 时应走 stability 口径",
        )

    def test_freshness_still_monotone_in_score(self):
        fresh = mk(age_days=1.0)
        stale = mk(age_days=400.0)
        self.assertGreater(score_fact(fresh, 0, NOW, 0.5), score_fact(stale, 0, NOW, 0.5))


if __name__ == "__main__":
    unittest.main()
