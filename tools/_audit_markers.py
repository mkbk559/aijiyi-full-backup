"""一次性审计脚本：扫描未实现/占位/延期标记（只读）。"""
from __future__ import annotations

import ast
import io
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

MARKERS = re.compile(
    r"(TODO|FIXME|XXX|HACK|NotImplementedError|not implemented|尚未实现|未实现|待实现|占位|placeholder|\bstub\b|shortcut:)"
)


def scan_markers() -> list[str]:
    hits: list[str] = []
    for path in sorted(ROOT.joinpath("aiji").rglob("*.py")) + sorted(ROOT.joinpath("tools").glob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if path.name.startswith("_"):
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if MARKERS.search(line):
                hits.append(f"{rel}:{i}: {line.strip()[:160]}")
    return hits


def empty_bodies() -> list[str]:
    hits: list[str] = []
    for path in sorted(ROOT.joinpath("aiji").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = [n for n in node.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))]
                if len(body) == 1 and isinstance(body[0], ast.Pass):
                    hits.append(f"{rel}:{node.lineno}: def {node.name}() -> pass")
                elif len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and body[0].value.value is Ellipsis:
                    hits.append(f"{rel}:{node.lineno}: def {node.name}() -> ...")
                elif len(body) == 1 and isinstance(body[0], ast.Raise) and "NotImplemented" in ast.dump(body[0]):
                    hits.append(f"{rel}:{node.lineno}: def {node.name}() -> raise NotImplementedError")
    return hits


def bare_except() -> list[str]:
    hits: list[str] = []
    for path in sorted(ROOT.joinpath("aiji").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                hits.append(f"{rel}:{node.lineno}: bare except")
    return hits


out = io.StringIO()
marks = scan_markers()
out.write(f"## markers ({len(marks)})\n")
out.write("\n".join(marks) + "\n")
empties = empty_bodies()
out.write(f"\n## empty bodies ({len(empties)})\n")
out.write("\n".join(empties) + "\n")
be = bare_except()
out.write(f"\n## bare except ({len(be)})\n")
out.write("\n".join(be) + "\n")
print(out.getvalue())
