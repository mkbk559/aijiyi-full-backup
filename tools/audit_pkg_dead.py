"""只看 `aiji/` 包的模块级死代码：某模块没被包内任何其它模块 import 就是候选。

注意：CLI/服务入口（`aiji/__main__.py`、`cli.py`、`server.py`）是通过
`python -m aiji.cli` / 打包入口调用的，天然没有 import 方，要单独放行。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "aiji"
ENTRYPOINTS = {"__main__", "cli", "server", "console"}

texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in ROOT.glob("*.py")}
for p, txt in sorted(texts.items()):
    stem = p.stem
    if stem in ENTRYPOINTS:
        continue
    pat = re.compile(rf"\b{re.escape(stem)}\b")
    users = [q.stem for q, t in texts.items() if q != p and pat.search(t)]
    docs = []
    for d in (ROOT.parent / "docs").rglob("*.md"):
        if pat.search(d.read_text(encoding="utf-8", errors="replace")):
            docs.append(d.name)
    size = p.stat().st_size / 1024
    flag = "DEAD?" if not users else "ok"
    print(f"{flag:6} {stem:22} {size:6.1f}KB  importers={users[:5]}  docs={docs[:2]}")
