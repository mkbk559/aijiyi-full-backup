"""只读：看图片 media 行的 extra（诊断 OCR 全空的根因）。"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
DB = pathlib.Path(r"D:\AIJiYi\data\aiji.db")
con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro&immutable=1", uri=True)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT id,ref,url,mime,size_bytes,confidence,status,recognizer,elapsed_ms,extra,created_at "
    "FROM media WHERE kind='image' ORDER BY id DESC LIMIT 6").fetchall()
for r in rows:
    d = dict(r)
    d["url"] = (d["url"] or "")[:90]
    d["extra"] = (d["extra"] or "")[:300]
    print(d)
print("\n## distinct image extra keys")
vals = [r["extra"] or "" for r in con.execute("SELECT extra FROM media WHERE kind='image'")]
import json
keys = {}
for v in vals:
    try:
        obj = json.loads(v or "{}")
    except Exception:
        keys["<unparsable>"] = keys.get("<unparsable>", 0) + 1
        continue
    for k in obj:
        keys[k] = keys.get(k, 0) + 1
print(keys)
print("\n## image size_bytes distribution")
for r in con.execute(
        "SELECT CASE WHEN size_bytes IS NULL THEN 'null' "
        "WHEN size_bytes=0 THEN '0' ELSE '>0' END b, COUNT(*) c, "
        "MIN(size_bytes) mn, MAX(size_bytes) mx FROM media WHERE kind='image' GROUP BY b"):
    print(dict(r))
con.close()
