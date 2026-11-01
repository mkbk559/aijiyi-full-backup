"""一次性审计脚本：导出每个模块的 docstring + 顶层函数/类签名（只读，不改代码）。"""
from __future__ import annotations

import ast
import io
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "aiji"


def first_line(doc: str | None, width: int = 110) -> str:
    if not doc:
        return ""
    for line in doc.strip().splitlines():
        line = line.strip()
        if line:
            return line[:width]
    return ""


def main() -> int:
    out = io.StringIO()
    files = sorted(PKG.rglob("*.py"))
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            out.write(f"\n### {rel}  [SYNTAX ERROR: {exc}]\n")
            continue
        lines = src.count("\n") + 1
        out.write(f"\n### {rel}  ({lines} lines)\n")
        mod_doc = first_line(ast.get_docstring(tree))
        if mod_doc:
            out.write(f"  MOD: {mod_doc}\n")
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.write(f"  def {node.name}()  {first_line(ast.get_docstring(node), 90)}\n")
            elif isinstance(node, ast.ClassDef):
                out.write(f"  class {node.name}  {first_line(ast.get_docstring(node), 90)}\n")
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if sub.name.startswith("__") and sub.name != "__init__":
                            continue
                        out.write(
                            f"      .{sub.name}()  {first_line(ast.get_docstring(sub), 80)}\n"
                        )
    text = out.getvalue()
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    print(f"\n[TOTAL] {len(files)} py files, {sum(1 for _ in (ROOT/'aiji').rglob('*.py'))} under aiji/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
