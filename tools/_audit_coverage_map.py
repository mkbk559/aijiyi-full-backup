"""一次性审计脚本：模块↔测试覆盖映射（静态引用，不是行覆盖）。"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

mods = []
for p in sorted((ROOT / "aiji").rglob("*.py")):
    rel = p.relative_to(ROOT / "aiji").with_suffix("")
    name = ".".join(rel.parts)
    if name.endswith(".__init__"):
        name = name[: -len(".__init__")]
    mods.append((name, p))

test_src = {}
for p in sorted((ROOT / "tests").glob("*.py")):
    test_src[p.name] = p.read_text(encoding="utf-8", errors="replace")

counts = {}
for name, p in test_src.items():
    try:
        tree = ast.parse(name and p)
    except SyntaxError:
        continue
    n = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")
    )
    counts[name] = n

print("## test methods per file")
for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
    print(f"  {n:3d}  {name}")
print(f"  TOTAL {sum(counts.values())} test methods in {len(counts)} files")

print("\n## module -> referencing test files")
unref = []
for name, p in mods:
    needle = "aiji." + name if name else "aiji"
    hits = sorted(f for f, src in test_src.items() if needle in src)
    if hits:
        print(f"  {name:32s} <- {', '.join(hits)}")
    else:
        unref.append(name)
print(f"\n## no direct test reference ({len(unref)})")
print("  " + "\n  ".join(unref))
