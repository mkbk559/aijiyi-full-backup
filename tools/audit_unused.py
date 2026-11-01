"""全项目"有没有人用它"检测：对每个 .py 数一遍**外部引用**（代码 import + 文档/脚本里提到）。

判定规则（保守，宁可不删）：
- 被 `aiji/`、`tests/`、`tools/`、`bench/` 里的其它文件 import → 活
- 被 docs/ 或 README/pyproject 提到 → 活（可能是指令脚本/入口）
- 只在自身出现 → 候选死文件（再由人确认）

输出：.tmp/audit/unused.txt（全量）+ 控制台摘要。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".tmp" / "audit"
OUT.mkdir(parents=True, exist_ok=True)

SRC_DIRS = ["aiji", "tests", "tools", "bench"]
CODE_SUFFIX = {".py"}
DOC_GLOBS = ["*.md", "*.toml", "*.bat", "*.json", "*.txt"]

code_files = []
for d in SRC_DIRS:
    code_files += [p for p in (ROOT / d).rglob("*.py")]
doc_files = []
for pat in DOC_GLOBS:
    doc_files += [p for p in ROOT.rglob(pat)
                  if not any(part in {".tmp", ".venv", "voice-engine", "build", "dist", "data", "__pycache__"}
                             for part in p.parts)]


def haystack(files):
    out = {}
    for p in files:
        try:
            out[p] = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return out


code_text = haystack(code_files)
doc_text = haystack(doc_files)

rows = []
for p in sorted(code_files):
    stem = p.stem
    if stem == "__init__":
        continue
    pats = [re.compile(rf"\b{re.escape(stem)}\b")]
    hits_code = []
    for q, txt in code_text.items():
        if q == p:
            continue
        if any(r.search(txt) for r in pats):
            hits_code.append(str(q.relative_to(ROOT)))
    hits_doc = [str(q.relative_to(ROOT)) for q, txt in doc_text.items()
                if any(r.search(txt) for r in pats)]
    rows.append((str(p.relative_to(ROOT)), hits_code, hits_doc))

dead_code = [r for r in rows if not r[1]]
only_doc = [r for r in rows if not r[1] and r[2]]
truly_orphan = [r for r in rows if not r[1] and not r[2]]

lines = ["== 完全没有代码引用（文档提到 / 谁都没提）==", ""]
for path, hc, hd in rows:
    if hc:
        continue
    lines.append(f"{path}\n    文档提到: {len(hd)} 处 {hd[:4]}")
(OUT / "unused.txt").write_text("\n".join(lines), "utf-8")

print(f"扫描 {len(code_files)} 个 .py（含 tests/tools/bench），文档 {len(doc_files)} 个")
print(f"没有代码引用的: {len(dead_code)}（其中文档也没提的: {len(truly_orphan)}）")
print("\n-- 谁都没提（最可能是死文件）--")
for path, _hc, _hd in truly_orphan:
    print("  ", path, f"{Path(ROOT/path).stat().st_size/1024:.0f}KB")
print("\n-- 只有文档提到（脚本/入口，别删）--")
for path, _hc, hd in only_doc:
    print("  ", path, "|", hd[:3])
