"""只读：自我生活经济学量化（能量收支）+ 反思层产出。"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
DB = pathlib.Path(r"D:\AIJiYi\data\aiji.db")
con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro&immutable=1", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()
now = time.time()

print("## 近 24h：事件数与能量总和")
row = cur.execute(
    "SELECT COUNT(*) n, SUM(energy_delta) de, SUM(valence_delta) dv"
    " FROM being_event WHERE created_at>=?", (now - 86400,)).fetchone()
print(f"  事件 n={row['n']}  累计 energy_delta={row['de']:+.1f}  valence_delta={row['dv']:+.1f}")

print("\n## 近 24h 每小时：事件数 / 能量增减")
for row in cur.execute(
        "SELECT CAST(strftime('%H', created_at, 'unixepoch', 'localtime') AS INTEGER) h,"
        " COUNT(*) n, ROUND(SUM(energy_delta),1) de"
        " FROM being_event WHERE created_at>=? GROUP BY h ORDER BY h", (now - 86400,)):
    print(f"  {row['h']:02d}时  n={row['n']:3d}  energy={row['de']:+6.1f}")

print("\n## quiet_rest 选择次数 / 其他活动次数")
for row in cur.execute(
        "SELECT summary, COUNT(*) c FROM being_event WHERE kind='self_activity'"
        " GROUP BY summary ORDER BY c DESC LIMIT 5"):
    print(f"  {row['c']:3d}  {row['summary']}")

print("\n## 近 24h 用户消息数（turn）")
print("  ", cur.execute("SELECT COUNT(*) FROM turn WHERE ts>=? AND role='user'", (now - 86400,)).fetchone()[0])

print("\n## being_note 最近 5 条")
for row in cur.execute("SELECT text, visibility, created_at FROM being_note ORDER BY id DESC LIMIT 5"):
    print("  ", time.strftime("%m-%d %H:%M", time.localtime(row["created_at"])),
          row["visibility"], str(row["text"])[:80])

print("\n## reflection 最近 3 条")
for row in cur.execute(
        "SELECT platform_user_id, scope, turns_seen, summary, deltas, created_at"
        " FROM reflection ORDER BY id DESC LIMIT 3"):
    print("  ", time.strftime("%m-%d %H:%M", time.localtime(row["created_at"])),
          f"turns={row['turns_seen']}", str(row["summary"])[:100].replace("\n", " "))
    print("     deltas:", str(row["deltas"])[:160])

print("\n## reflection 覆盖：有多少身份被反思过 / 有 trait 的")
print("  reflection 去重身份:", cur.execute(
    "SELECT COUNT(*) FROM (SELECT DISTINCT platform,platform_user_id,scope FROM reflection)").fetchone()[0])
print("  trait 去重身份:", cur.execute(
    "SELECT COUNT(*) FROM (SELECT DISTINCT platform,platform_user_id,scope FROM trait)").fetchone()[0])

print("\n## trait 置信度分布")
for row in cur.execute(
        "SELECT CASE WHEN confidence>=0.9 THEN '0.9+' WHEN confidence>=0.7 THEN '0.7-0.9'"
        " WHEN confidence>=0.5 THEN '0.5-0.7' ELSE '<0.5' END b, COUNT(*) c"
        " FROM trait GROUP BY b ORDER BY b DESC"):
    print(f"  {row['b']:10s} {row['c']}")

print("\n## life_card: 激素偏离基线的行数")
base = (0.60, 0.40, 0.20, 0.50)
moved = 0
total = 0
for row in cur.execute("SELECT card FROM life_card"):
    card = json.loads(row["card"] or "{}")
    e = card.get("endo") or {}
    total += 1
    cur_v = tuple(round(float(e.get(k, 0)), 2) for k in ("sero", "dopa", "cort", "oxy"))
    if cur_v != base:
        moved += 1
print(f"  偏离基线 {moved}/{total}")
con.close()
