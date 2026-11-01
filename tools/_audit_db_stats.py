"""一次性审计脚本：只读统计实际数据库里的模块使用情况（immutable 打开，不写）。"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
DB = pathlib.Path(r"D:\AIJiYi\data\aiji.db")
if not DB.exists():
    print("no db")
    raise SystemExit(0)
uri = f"file:{DB.as_posix()}?mode=ro&immutable=1"
con = sqlite3.connect(uri, uri=True, timeout=5)
con.row_factory = sqlite3.Row
cur = con.cursor()
print("db bytes:", DB.stat().st_size)
print("user_version:", cur.execute("PRAGMA user_version").fetchone()[0])
tables = [r[0] for r in cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("\n## row counts")
for t in tables:
    try:
        n = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    except sqlite3.Error as exc:
        n = f"ERR {exc}"
    print(f"  {t:24s} {n}")
print("\n## fact by status/kind")
for row in cur.execute(
        "SELECT status, kind, COUNT(*) c FROM fact GROUP BY status, kind ORDER BY c DESC"):
    print(f"  {row['status']:12s} {row['kind']:10s} {row['c']}")
print("\n## accounts by platform")
for row in cur.execute("SELECT platform, COUNT(*) c FROM account GROUP BY platform ORDER BY c DESC"):
    print(f"  {row['platform']:12s} {row['c']}")
print("\n## media by kind/status")
for row in cur.execute("SELECT kind, status, COUNT(*) c FROM media GROUP BY kind, status ORDER BY c DESC LIMIT 20"):
    print(f"  {row['kind']:8s} {row['status']:14s} {row['c']}")
print("\n## style dims")
for row in cur.execute("SELECT dimension, COUNT(*) c FROM style GROUP BY dimension ORDER BY c DESC"):
    print(f"  {row['dimension']:10s} {row['c']}")
print("\n## turn role counts (last 30 days)")
for row in cur.execute("SELECT role, COUNT(*) c FROM turn GROUP BY role ORDER BY c DESC"):
    print(f"  {row['role']:10s} {row['c']}")
print("\n## outbox / send_queue / write_queue states")
for t in ("outbox", "send_queue", "write_queue"):
    try:
        for row in cur.execute(f"SELECT status, COUNT(*) c FROM {t} GROUP BY status"):
            print(f"  {t:12s} {row['status']:12s} {row['c']}")
    except sqlite3.Error as exc:
        print(f"  {t}: {exc}")
print("\n## summary rows")
print("  ", cur.execute("SELECT COUNT(*) FROM summary").fetchone()[0])
print("## injection rows")
print("  ", cur.execute("SELECT COUNT(*) FROM injection").fetchone()[0])
con.close()
