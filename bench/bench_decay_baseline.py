"""阶段 0-①：代谢基线（只读生产库，不写任何东西）。

回答三个问题（borrow-plan §6）：
  1. 有多少比例的事实"90 天没被提起"？→ 决定冻结线定在哪，而不是拍 0.5；
  2. 有多少条会被当前判据冻结？→ 冻结是"温和"还是"砍掉半个记忆"；
  3. `mark_used` 到底有没有被调用？（本脚本给现状，probe_mark_used.py 给闭环验证）

跑法：
    python bench/bench_decay_baseline.py                 # 默认 data/aiji.db
    python bench/bench_decay_baseline.py --db <path> --json out.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiji import params  # noqa: E402

DAY = 86400.0


def retrievability(days: float, stability: float) -> float:
    s = max(0.5, float(stability or params.STABILITY_INIT_DAYS))
    return float((1.0 + params.FSRS_F * max(0.0, days) / s) ** -0.5)


def open_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def baseline(db_path: Path, *, now: float | None = None) -> dict:
    t = time.time() if now is None else now
    out: dict = {"db": str(db_path), "now": t, "exists": db_path.exists()}
    if not db_path.exists():
        out["error"] = "数据库不存在（先跑一次 aiji 或指向真实库）"
        return out
    c = open_ro(db_path)
    try:
        have = {r[1] for r in c.execute("PRAGMA table_info(fact)")}
        cols = [x for x in ("stability", "difficulty", "frozen", "last_used_at", "hit_count",
                            "kind", "confidence", "updated_at") if x in have]
        rows = c.execute(
            f"SELECT id,{','.join(cols)} FROM fact WHERE status='active'").fetchall()
        total = len(rows)
        by_kind: dict[str, int] = {}
        stale90 = stale180 = never = 0
        would_freeze = 0
        hit_used = 0
        for r in rows:
            kind = (r["kind"] if "kind" in r.keys() else "fact") or "fact"
            by_kind[kind] = by_kind.get(kind, 0) + 1
            age_d = (t - float(r["updated_at"] or t)) / DAY
            if age_d >= 90:
                stale90 += 1
            if age_d >= 180:
                stale180 += 1
            hc = int(r["hit_count"] or 0) if "hit_count" in r.keys() else 0
            last = r["last_used_at"] if "last_used_at" in r.keys() else None
            if hc == 0 and last is None:
                never += 1
            if hc > 0 or last is not None:
                hit_used += 1
            if int(r["frozen"] or 0) if "frozen" in r.keys() else 0:
                continue
            conf = float(r["confidence"] or 0)
            stab = float(r["stability"] or params.STABILITY_INIT_DAYS) if "stability" in r.keys() \
                else params.STABILITY_INIT_DAYS
            ref = float(last) if last else float(r["updated_at"] or t)
            r_val = retrievability((t - ref) / DAY, stab)
            if (conf < params.STALE_CONFIDENCE and hc == 0 and last is None
                    and age_d >= params.STALE_DAYS and r_val < params.FREEZE_BELOW):
                would_freeze += 1
        out["facts"] = {
            "active_total": total,
            "by_kind": by_kind,
            "stale_90d": stale90,
            "stale_90d_pct": round(100.0 * stale90 / total, 2) if total else 0.0,
            "stale_180d": stale180,
            "never_used": never,
            "never_used_pct": round(100.0 * never / total, 2) if total else 0.0,
            "ever_used": hit_used,
            "would_freeze_now": would_freeze,
            "would_freeze_pct": round(100.0 * would_freeze / total, 2) if total else 0.0,
        }
        try:
            turns = c.execute("SELECT COUNT(*) FROM turn").fetchone()[0]
            out["turns"] = {"count": turns}
        except sqlite3.Error:
            out["turns"] = {"count": 0}
        try:
            arch = c.execute("SELECT COUNT(*) FROM turn_archive").fetchone()[0]
            out["turn_archive"] = {"count": arch}
        except sqlite3.Error:
            out["turn_archive"] = {"count": 0, "note": "表不存在（v10 及更早）"}
        out["injections"] = {}
        try:
            n = c.execute("SELECT COUNT(*) FROM injection").fetchone()[0]
            out["injections"]["count"] = n
        except sqlite3.Error:
            pass
    finally:
        c.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "aiji.db"))
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    res = baseline(Path(args.db))
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=2), "utf-8")
    return 0 if res.get("exists") else 2


if __name__ == "__main__":
    raise SystemExit(main())
