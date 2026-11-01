"""只读：反思层产出率 + 涌现性格的实际内容。"""
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

print("## reflection 总行数 / 空 deltas 行数")
row = cur.execute(
    "SELECT COUNT(*) n, SUM(CASE WHEN deltas='[]' OR deltas='' THEN 1 ELSE 0 END) empty"
    " FROM reflection").fetchone()
print(f"  n={row['n']}  empty_deltas={row['empty']}  "
      f"产出率={0 if not row['n'] else round(1-row['empty']/row['n'],3)}")

print("\n## reflection 按天")
for row in cur.execute(
        "SELECT date(created_at,'unixepoch','localtime') d, COUNT(*) n,"
        " SUM(CASE WHEN deltas='[]' THEN 1 ELSE 0 END) empty"
        " FROM reflection GROUP BY d ORDER BY d DESC LIMIT 6"):
    print(f"  {row['d']}  n={row['n']:4d}  empty={row['empty']:4d}")

print("\n## reflection summary 空/无意义占比（含'没有/无可归纳/暂不'）")
row = cur.execute(
    "SELECT COUNT(*) n,"
    " SUM(CASE WHEN summary='' OR summary LIKE '%没有%' OR summary LIKE '%暂无%'"
    " OR summary LIKE '%无法%' OR summary LIKE '%不足以%' THEN 1 ELSE 0 END) hollow"
    " FROM reflection").fetchone()
print(f"  n={row['n']}  hollow={row['hollow']}  比例={round(row['hollow']/max(1,row['n']),3)}")

print("\n## trait 值 top（按出现身份数）")
for row in cur.execute(
        "SELECT value, COUNT(*) c, ROUND(AVG(confidence),2) conf, MAX(evidence) ev"
        " FROM trait GROUP BY value ORDER BY c DESC LIMIT 15"):
    print(f"  {row['c']:3d}人 conf={row['conf']} ev<={row['ev']}  {row['value']}")

print("\n## trait origin 分布")
for row in cur.execute("SELECT origin, COUNT(*) c FROM trait GROUP BY origin"):
    print(f"  {row['origin']:10s} {row['c']}")

print("\n## 同一身份 trait 数分布（前 5）")
for row in cur.execute(
        "SELECT platform_user_id, COUNT(*) c FROM trait GROUP BY platform,platform_user_id,scope"
        " ORDER BY c DESC LIMIT 5"):
    print(f"  {row['platform_user_id']:12s} {row['c']}")
con.close()
