"""审计：吞异常、遗留标记、以及"只 return 不记录"的静默分支。

用法：python tools/audit_swallowed.py [--strict]
输出每一处的 文件:行 与源码片段，按类别分组。默认只报告（退出码 0），
`--strict` 时只要有"可疑吞异常"就退出 1（给 CI/自我迭代用）。
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "aiji"

# XXX 只在全大写时算标记（"group:xxx" 这种命名不是待办）
MARKERS = re.compile(r"\b(TODO|FIXME|XXX|HACK|WIP)\b|占位|待实现")
# 允许的"吞异常"：这些地方吞掉是对的，但必须留下可核对的理由（同一行/上一行有注释）
ALLOWED_HINTS = ("# noqa: BLE001", "best effort", "尽力", "可选", "忽略", "不重要")


def iter_files():
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _has_comment(path: pathlib.Path, node: ast.ExceptHandler) -> bool:
    """handler 之内（含 pass 那行、含 except 那行）有没有注释。

    本仓的约定是"吞掉就要写清为什么"，写在 handler 里的注释就是那条理由。
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    first = node.lineno
    last = max(getattr(item, "end_lineno", item.lineno) for item in node.body)
    for index in range(first - 1, min(last, len(lines))):
        line = lines[index]
        if "#" in line:
            head, _, tail = line.partition("#")
            if not head.count('"') % 2:          # 不是字符串里的 #
                return True
    return False


def _is_bare(node: ast.ExceptHandler) -> bool:
    if node.type is None:
        return True
    names = [item.id for item in ast.walk(node.type) if isinstance(item, ast.Name)]
    return "Exception" in names or "BaseException" in names


def swallowed(path: pathlib.Path, tree: ast.AST) -> list[tuple[int, str, str]]:
    """找 `except ...: pass` 与 `except ...: return/continue`（无日志、无 raise）。"""
    found = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        body = [item for item in node.body
                if not (isinstance(item, ast.Expr)
                        and isinstance(item.value, ast.Constant))]
        if len(body) != 1:
            continue
        only = body[0]
        silent = (
            (isinstance(only, ast.Pass))
            or (isinstance(only, ast.Continue))
            or (isinstance(only, ast.Return) and only.value is None)
            or (isinstance(only, ast.Return)
                and isinstance(only.value, ast.Constant) and only.value.value in (None, False, "", 0))
        )
        if not silent:
            continue
        line_no = node.lineno
        context = " ".join(lines[max(0, line_no - 4):line_no + 1])
        commented = _has_comment(path, node)
        reason = commented or any(hint.lower() in context.lower() for hint in ALLOWED_HINTS)
        flag = "" if reason else ("bare-no-reason" if _is_bare(node) else "typed-no-reason")
        found.append((line_no, flag,
                      (lines[line_no - 1] if line_no <= len(lines) else "").strip()))
    return found


def main() -> int:
    strict = "--strict" in sys.argv
    all_rows: list[tuple[int, str, str]] = []
    silent_total = 0
    unexplained = 0
    markers = 0
    print("== 1) 静默吞异常（except 里只有 pass/continue/return 空值） ==")
    for path in iter_files():
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        rows = swallowed(path, tree)
        if not rows:
            continue
        all_rows.extend(rows)
        rel = path.relative_to(ROOT)
        for line_no, flag, snippet in rows:
            silent_total += 1
            if flag:
                unexplained += 1
                print(f"  {rel}:{line_no}  [{flag}]  {snippet[:90]}")
    bare_unexplained = len([1 for row in all_rows if row[0] == "bare-no-reason"])
    print(f"  合计 {silent_total} 处；没写理由的 {unexplained} 处"
          f"（其中裸 except Exception 的 {bare_unexplained} 处）")

    print("== 2) TODO/FIXME/XXX/HACK/占位 标记 ==")
    for path in iter_files():
        rel = path.relative_to(ROOT)
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if MARKERS.search(line) and not line.lstrip().startswith("#!"):
                markers += 1
                print(f"  {rel}:{index}  {line.strip()[:100]}")
    print(f"  合计 {markers} 处")

    bare = [row for row in all_rows if row[1] == "bare-no-reason"]
    print(f"== 结论：静默吞异常 {silent_total}（无理由 {unexplained}，"
          f"裸 except 无理由 {len(bare)}），标记 {markers} ==")
    if strict and bare:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
