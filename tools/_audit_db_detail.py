"""只读：媒体识别细节 + 注入/反馈使用情况。"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
DB = pathlib.Path(r"D:\AIJiYi\data\aiji.db")
con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro&immutable=1", uri=True, timeout=5)
con.row_factory = sqlite3.Row
cur = con.cursor()
print("## media by recognizer/status, avg conf, empty-text ratio")
for row in cur.execute("""
    SELECT kind, recognizer, status, COUNT(*) c,
           ROUND(AVG(COALESCE(confidence,0)),3) avg_conf,
           SUM(CASE WHEN COALESCE(text,'')='' THEN 1 ELSE 0 END) empty_text
    FROM media GROUP BY kind, recognizer, status ORDER BY c DESC LIMIT 25"""):
    print(f"  {row['kind']:7s} {str(row['recognizer']):18s} {row['status']:15s} n={row['c']:5d} "
          f"avg={row['avg_conf']} empty_text={row['empty_text']}")
print("\n## injection feedback filled vs null")
print("  ", dict(cur.execute(
    "SELECT SUM(CASE WHEN feedback IS NULL THEN 1 ELSE 0 END) null_fb,"
    " SUM(CASE WHEN feedback IS NOT NULL THEN 1 ELSE 0 END) has_fb FROM injection").fetchone()))
print("\n## fact source_kind")
for row in cur.execute("SELECT source_kind, COUNT(*) c FROM fact GROUP BY source_kind ORDER BY c DESC"):
    print(f"  {str(row['source_kind']):16s} {row['c']}")
print("\n## fact hit_count>0 (feedback loop actually used)")
print("  ", cur.execute("SELECT COUNT(*) FROM fact WHERE hit_count > 0").fetchone()[0])
print("\n## turn by platform")
for row in cur.execute("SELECT platform, COUNT(*) c FROM turn GROUP BY platform ORDER BY c DESC"):
    print(f"  {str(row['platform']):10s} {row['c']}")
print("\n## being_state")
for row in con.execute("SELECT * FROM being_state LIMIT 1"):
    print("  ", {k: (str(v)[:60] if v is not None else None) for k, v in dict(row).items()})
print("\n## latest life_card")
for row in con.execute("SELECT * FROM life_card ORDER BY rowid DESC LIMIT 1"):
    print("  ", {k: (str(v)[:70] if v is not None else None) for k, v in dict(row).items()})
print("\n## profile keys")
for row in cur.execute("SELECT key, COUNT(*) c FROM profile GROUP BY key ORDER BY c DESC LIMIT 20"):
    print(f"  {row['key']:18s} {row['c']}")
print("\n## trait samples")
for row in cur.execute("SELECT name, confidence, evidence FROM trait ORDER BY confidence DESC LIMIT 10"):
    print(f"  {row['name']:20s} conf={row['confidence']} ev={row['evidence']}")
con.close()
