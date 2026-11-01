"""只读：孤儿行定位（fact_log 用 ts 列）。"""
from __future__ import annotations

import pathlib
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
DB = pathlib.Path(r"D:\AIJiYi\data\aiji.db")
con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro&immutable=1", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()
cols = [r[1] for r in cur.execute("PRAGMA table_info(fact_log)")]
print("fact_log 列:", cols)
tcol = "ts" if "ts" in cols else ("created_at" if "created_at" in cols else None)
print("\n## fact_log 孤儿（按 op / 身份）")
sql = (
    "SELECT l.op, l.platform, l.platform_user_id, COUNT(*) c"
    + (f", MIN(l.{tcol}) t0, MAX(l.{tcol}) t1" if tcol else "")
    + " FROM fact_log l LEFT JOIN fact f ON f.id=l.fact_id"
    " WHERE l.fact_id<>0 AND f.id IS NULL GROUP BY l.op, l.platform, l.platform_user_id"
)
for row in cur.execute(sql):
    d = dict(row)
    if tcol:
        d["t0"] = time.strftime("%m-%d %H:%M", time.localtime(d["t0"]))
        d["t1"] = time.strftime("%m-%d %H:%M", time.localtime(d["t1"]))
    print("  ", d)
print("\n## 这些身份现在还有多少 fact")
for row in cur.execute(
        "SELECT l.platform_user_id uid, (SELECT COUNT(*) FROM fact f2"
        "  WHERE f2.platform=l.platform AND f2.platform_user_id=l.platform_user_id) alive"
        " FROM fact_log l LEFT JOIN fact f ON f.id=l.fact_id"
        " WHERE l.fact_id<>0 AND f.id IS NULL GROUP BY 1"):
    print("  ", dict(row))
con.close()
