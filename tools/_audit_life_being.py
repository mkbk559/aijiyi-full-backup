"""只读：自我生活层（being/life_card）在生产库里的真实形态。"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import time
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
DB = pathlib.Path(r"D:\AIJiYi\data\aiji.db")
con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro&immutable=1", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()
now = time.time()
print("now:", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)))

print("\n## being_event 近 24h 按 kind")
day = now - 86400
for row in cur.execute(
        "SELECT kind, COUNT(*) c FROM being_event WHERE created_at>=? GROUP BY kind ORDER BY c DESC",
        (day,)):
    print(f"  {row['kind']:14s} {row['c']}")
print("\n## being_event 全量按 kind（top）")
for row in cur.execute("SELECT kind, COUNT(*) c FROM being_event GROUP BY kind ORDER BY c DESC"):
    print(f"  {row['kind']:14s} {row['c']}")

print("\n## 生活活动（self_activity）最近 8 条")
for row in cur.execute(
        "SELECT summary, valence_delta, energy_delta, created_at FROM being_event"
        " WHERE kind='self_activity' ORDER BY id DESC LIMIT 8"):
    ts = time.strftime("%m-%d %H:%M", time.localtime(row["created_at"]))
    print(f"  {ts}  dv={row['valence_delta']:+.2f} de={row['energy_delta']:+.2f}  {row['summary']}")

print("\n## being_state 当前")
row = cur.execute("SELECT persona,state,updated_at FROM being_state LIMIT 1").fetchone()
if row:
    st = json.loads(row["state"])
    keep = {k: st.get(k) for k in (
        "energy", "valence", "social", "stress", "activity", "current_focus",
        "last_activity", "life_seq", "next_life_at", "asleep_until")}
    print("  persona:", row["persona"], " updated:",
          time.strftime("%m-%d %H:%M:%S", time.localtime(row["updated_at"])))
    print("  ", keep)

print("\n## life_card 数量 / 最近更新")
print("  rows:", cur.execute("SELECT COUNT(*) FROM life_card").fetchone()[0])
for row in cur.execute("SELECT platform,platform_user_id,scope,updated_at FROM life_card ORDER BY updated_at DESC LIMIT 3"):
    print("  ", dict(row), time.strftime("%m-%d %H:%M", time.localtime(row["updated_at"])))

print("\n## life_card 里激素/人格向量的真实分布（抽样 200）")
endo = Counter()
vec = Counter()
ticks = []
for row in cur.execute("SELECT card FROM life_card LIMIT 200"):
    card = json.loads(row["card"] or "{}")
    e = card.get("endo") or {}
    endo[(
        round(float(e.get("sero", 0)), 2), round(float(e.get("dopa", 0)), 2),
        round(float(e.get("cort", 0)), 2), round(float(e.get("oxy", 0)), 2),
    )] += 1
    p = card.get("personality") or {}
    vec[json.dumps(p.get("vector") or {}, sort_keys=True)] += 1
    if card.get("last_tick"):
        ticks.append(float(card["last_tick"]))
print("  personality.vector 取值分布:", dict(vec))
print("  激素组合 top5:")
for combo, c in endo.most_common(5):
    print(f"    sero/dopa/cort/oxy={combo}  n={c}")
print("  不同激素组合数:", len(endo))
print("  card 顶层的 key 集合:", endo and Counter(
    tuple(sorted(json.loads(r['card'] or '{}').keys()))
    for r in cur.execute("SELECT card FROM life_card LIMIT 50")))

print("\n## reflection 最近 3 条")
for row in cur.execute("SELECT platform_user_id,scope,text,created_at FROM reflection ORDER BY id DESC LIMIT 3"):
    print("  ", time.strftime("%m-%d %H:%M", time.localtime(row["created_at"])),
          str(row["text"])[:120].replace("\n", " "))
con.close()
