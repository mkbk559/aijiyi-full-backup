"""新近度口径 A/B（2026-11-03）：只换衰减算法，看排序会不会变、往哪变。

背景：项目里 FSRS-5 的 **stability 状态**早就有了（`store.retrievability` 管门禁/冻结，
`bump_usage` 每次被引用就涨 stability），但 `recall.score_fact` 的"排序新近度"一直用**按类型定死的
半衰期**——等于排序完全无视"这条被想起过多少次"。本脚本量这个差别。

三段：
  1. 合成对照：同龄旧事实，一个被引用 10 次、一个从没被引用 → 两种口径下的分差。
  2. 真库抽样：从 data/aiji.db 抽 N 条 active 事实，按它们真实的 stability 算两种口径的新近度。
  3. 结论行：哪些不变量成立（单调、R(t=S)=0.9 之类）。

用法：python tools/archcheck/fsrs_ab.py [db_path]
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from aiji.hippocampus.recall import _recency_for_fact, half_life_days, score_fact  # noqa: E402
from aiji.hippocampus.store import Fact, retrievability  # noqa: E402
from aiji.right_hand import params  # noqa: E402

NOW = time.time()


def mk(*, age_days: float, hits: int = 0, stability: float = 7.0, kind: str = "fact",
       predicate: str = "偏好") -> Fact:
    return Fact(
        id=1, platform="qq", platform_user_id="u", scope="default",
        kind=kind, subject="user", predicate=predicate, object="x", text="x",
        confidence=0.9, status="active",
        created_at=NOW - age_days * 86400, updated_at=NOW - age_days * 86400,
        source_kind="model", hit_count=hits, importance=0.5, mentions=1,
        stability=stability,
    )


def synthetic() -> dict:
    rows = []
    for age in (7, 90, 365):
        for hits, stab in ((0, 7.0), (10, 28.0)):
            f = mk(age_days=age, hits=hits, stability=stab)
            rows.append({
                "age_days": age, "hits": hits, "stability_days": stab,
                "half_life": round(_recency_for_fact(f, NOW - f.updated_at, "half_life"), 4),
                "stability_rank": round(_recency_for_fact(f, NOW - f.updated_at, "stability"), 4),
                "score_half_life": round(score_fact(f, 0, NOW, 0.6, "half_life"), 4),
                "score_stability": round(score_fact(f, 0, NOW, 0.6, "stability"), 4),
            })
    used = mk(age_days=365, hits=10, stability=28.0)
    plain = mk(age_days=365, hits=0, stability=7.0)
    return {
        "rows": rows,
        "head_to_head_365d": {
            "half_life_gap": round(score_fact(used, 0, NOW, 0.6, "half_life")
                                   - score_fact(plain, 0, NOW, 0.6, "half_life"), 4),
            "stability_gap": round(score_fact(used, 0, NOW, 0.6, "stability")
                                   - score_fact(plain, 0, NOW, 0.6, "stability"), 4),
        },
    }


def real_db(path: Path, limit: int = 400) -> dict:
    if not path.exists():
        return {"skipped": f"{path} not found"}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM fact WHERE status='active' ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    deltas, used, plain, stabs = [], [], [], []
    for r in rows:
        keys = r.keys()
        f = Fact(
            id=r["id"], platform=r["platform"], platform_user_id=r["platform_user_id"],
            scope=r["scope"], kind=r["kind"], subject=r["subject"], predicate=r["predicate"],
            object=r["object"], text=r["text"], confidence=r["confidence"],
            status=r["status"], created_at=r["created_at"], updated_at=r["updated_at"],
            source_kind=r["source_kind"] if "source_kind" in keys else "model",
            hit_count=r["hit_count"], mentions=r["mentions"],
            importance=r["importance"] if "importance" in keys else 0.5,
            stability=float(r["stability"]) if "stability" in keys else 7.0,
        )
        age = NOW - f.updated_at
        a = _recency_for_fact(f, age, "half_life")
        b = _recency_for_fact(f, age, "stability")
        deltas.append(b - a)
        (used if f.hit_count else plain).append(b - a)
        stabs.append(float(f.stability))
    return {
        "sample": len(rows),
        "stability_days": {
            "min": round(min(stabs), 2) if stabs else None,
            "median": round(statistics.median(stabs), 2) if stabs else None,
            "max": round(max(stabs), 2) if stabs else None,
        },
        "n_used": len(used),
        "n_never_used": len(plain),
        "median_delta_all": round(statistics.median(deltas), 4) if deltas else None,
        "median_delta_used": round(statistics.median(used), 4) if used else None,
        "median_delta_never_used": round(statistics.median(plain), 4) if plain else None,
        "changed_gt_0.01": sum(1 for d in deltas if abs(d) > 0.01),
    }


def invariants() -> dict:
    f_fresh = mk(age_days=1.0, stability=7.0)
    f_old = mk(age_days=365.0, stability=7.0)
    f_used_old = mk(age_days=365.0, stability=28.0)
    return {
        "recall_high_at_t0": round(_recency_for_fact(f_fresh, 0.0, "stability"), 4),
        "R_at_t_equals_S": round(float((1.0 + params.FSRS_F * 1.0) ** -0.5), 4),
        "fresh_beats_old": (_recency_for_fact(f_fresh, 0, "stability")
                            > _recency_for_fact(f_old, 365 * 86400, "stability")),
        "used_old_beats_never_used_old": (
            _recency_for_fact(f_used_old, 365 * 86400, "stability")
            > _recency_for_fact(f_old, 365 * 86400, "stability")),
        "half_life_ignores_usage": (
            _recency_for_fact(f_used_old, 365 * 86400, "half_life")
            == _recency_for_fact(f_old, 365 * 86400, "half_life")),
        "store_retrievability_matches": round(
            retrievability(mk(age_days=30.0, stability=30.0), now=NOW), 4),
        "half_life_days_of_pref": half_life_days(mk(age_days=0)),
    }


def main() -> None:
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "aiji.db"
    print(json.dumps({
        "params": {"RECALL_DECAY_MODE": params.RECALL_DECAY_MODE,
                   "FSRS_F": round(params.FSRS_F, 6),
                   "STABILITY_INIT_DAYS": params.STABILITY_INIT_DAYS,
                   "STABILITY_MAX_DAYS": params.STABILITY_MAX_DAYS},
        "invariants": invariants(),
        "synthetic": synthetic(),
        "real_db": real_db(db),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
