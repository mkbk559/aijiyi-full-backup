"""只读：wheel 里的关键文件是否与当前源码一致（打包新鲜度）。"""
from __future__ import annotations

import hashlib
import pathlib
import sys
import zipfile

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(r"D:\AIJiYi")
wheel = sorted((ROOT / "dist").glob("aiji-0.8.3-*.whl"))[-1]
print("wheel:", wheel.name, wheel.stat().st_mtime)
targets = [
    "aiji/reflect.py", "aiji/memory.py", "aiji/heart.py", "aiji/dispatch.py",
    "aiji/web/console.html", "aiji/server.py", "aiji/pool.py", "aiji/params.py",
]
stale = []
with zipfile.ZipFile(wheel) as z:
    names = set(z.namelist())
    for rel in targets:
        src = ROOT / rel
        src_hash = hashlib.sha256(src.read_bytes()).hexdigest()[:12]
        if rel not in names:
            print(f"  {rel:28s} MISSING")
            stale.append(rel)
            continue
        whl_hash = hashlib.sha256(z.read(rel)).hexdigest()[:12]
        same = src_hash == whl_hash
        print(f"  {rel:28s} {'same' if same else 'STALE'}  src={src_hash} whl={whl_hash}")
        if not same:
            stale.append(rel)
print("\nSTALE:", stale)
