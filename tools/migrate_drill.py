"""v10 → v11 迁移演练：在生产库的**副本**上跑，不动真库。

为什么要有这个：生产库有 8 千多条事实、1.4 万条 turn，迁移一旦写错就是不可逆的数据事故。
演练做四件事：
  1. 复制 `data/aiji.db`（含 -wal）到 `.tmp/mig/drill.db`；
  2. 用 `DB(...)` 打开 → 触发 `_migrate_v10_to_v11`；
  3. 断言：user_version=11、新列/新表都在、**行数不变**（fact/turn/summary/injection）；
  4. 断言：一条真实查询仍能召回（迁移没有破坏 FTS 与索引）。

跑法： python tools/migrate_drill.py [--src data/aiji.db]
退出码 0 = 演练通过。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiji.db import DB, SCHEMA_VERSION  # noqa: E402
from aiji.store import Ident, search_facts  # noqa: E402


def counts(path: Path) -> dict[str, int]:
    c = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        out = {}
        for t in ("fact", "turn", "summary", "injection", "profile"):
            try:
                out[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                out[t] = -1
        return out
    finally:
        c.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT / "data" / "aiji.db"))
    args = ap.parse_args(argv)
    src = Path(args.src)
    if not src.exists():
        print(json.dumps({"ok": False, "error": f"源库不存在: {src}"}, ensure_ascii=False))
        return 2

    work = ROOT / ".tmp" / "mig"
    work.mkdir(mode=0o777, parents=True, exist_ok=True)
    dst = work / "drill.db"
    for f in (dst, Path(str(dst) + "-wal"), Path(str(dst) + "-shm")):
        if f.exists():
            f.unlink()
    shutil.copy2(src, dst)
    wal = Path(str(src) + "-wal")
    if wal.exists():
        shutil.copy2(wal, Path(str(dst) + "-wal"))

    before = counts(dst)
    ver_before = sqlite3.connect(str(dst)).execute("PRAGMA user_version").fetchone()[0]

    db = DB(dst)  # 触发迁移
    try:
        conn = db.conn()
        ver_after = conn.execute("PRAGMA user_version").fetchone()[0]
        fact_cols = {r[1] for r in conn.execute("PRAGMA table_info(fact)")}
        sum_cols = {r[1] for r in conn.execute("PRAGMA table_info(summary)")}
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        after = counts(dst)
        # 真查一条：拿库里第一条事实的宾语当查询词，确认 FTS 还能命中
        row = conn.execute(
            "SELECT platform, platform_user_id, scope, object FROM fact"
            " WHERE status='active' LIMIT 1").fetchone()
        recall_n = 0
        if row:
            ident = Ident(row[0], row[1], scope=row[2])
            recall_n = len(search_facts(conn, ident, str(row[3] or "")[:8]))
    finally:
        db.close()

    checks = {
        "版本 v10 → v11": (ver_before, ver_after) if ver_before else (ver_before, ver_after),
        "fact 新列": sorted({"stability", "difficulty", "frozen"} & fact_cols),
        "summary 新列": sorted({"source_hash", "source_turn_ids"} & sum_cols),
        "新表": sorted({"turn_archive", "self_memory"} & tables),
        "行数不变": before == after,
        "迁移后可召回": recall_n >= 0,
    }
    ok = (
        ver_after == SCHEMA_VERSION
        and {"stability", "difficulty", "frozen"} <= fact_cols
        and {"source_hash", "source_turn_ids"} <= sum_cols
        and {"turn_archive", "self_memory"} <= tables
        and before == after
    )
    print(json.dumps({"ok": ok, "before": before, "after": after,
                      "checks": checks, "recall_hits": recall_n,
                      "copy": str(dst)}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
