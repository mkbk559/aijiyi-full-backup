"""按函数统计覆盖率（数据源 = `coverage json` 的输出，和 `coverage report` 口径一致）。

用法：
    python -m coverage json -o .tmp/cov.json
    python tools/coverage_by_func.py aiji/throat/server.py --cov .tmp/cov.json [--min 80]

为什么不用 `coverage.CoverageData` 直接读：它给的是"跟踪到的原始行"，
和报告里经过 exclude/tracing 处理的语句集合不完全一致（实测会漏掉已覆盖的行，
让人误以为某个函数没被测过）。JSON 报告的 `missing_lines` 才是报告口径。
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_missing(cov_path: pathlib.Path, target: pathlib.Path) -> set[int]:
    data = json.loads(cov_path.read_text(encoding="utf-8"))
    for name, entry in data["files"].items():
        resolved = (ROOT / name).resolve()
        if resolved == target.resolve():
            return set(entry.get("missing_lines") or [])
    raise SystemExit(f"{target} 不在覆盖率报告里（没被 import 过？）")


def functions(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inner = [n for n in ast.walk(node) if isinstance(n, ast.stmt)]
            inner = [n for n in inner
                     if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if inner:
                yield node.name, min(n.lineno for n in inner), max(n.lineno for n in inner)


def main() -> int:
    target = pathlib.Path(sys.argv[1])
    target = target if target.is_absolute() else ROOT / target
    cov_path = ROOT / ".tmp" / "cov.json"
    if "--cov" in sys.argv:
        cov_path = pathlib.Path(sys.argv[sys.argv.index("--cov") + 1])
    minimum = 101.0
    if "--min" in sys.argv:
        minimum = float(sys.argv[sys.argv.index("--min") + 1])

    missing = load_missing(cov_path, target)
    tree = ast.parse(target.read_text(encoding="utf-8"))
    rows = []
    for name, start, end in functions(tree):
        hit = [ln for ln in range(start, end + 1) if ln in missing]
        span = max(1, end - start + 1)
        rows.append((100.0 * (span - len(hit)) / span, name, start, end, len(hit), hit))
    rows.sort()
    print(f"{target.relative_to(ROOT)}：{len(rows)} 个函数，本文件缺失 {len(missing)} 行")
    print(f"{'行覆盖':>7} {'缺失':>5}  {'位置':<18} 函数（缺那些行）")
    for pct, name, start, end, miss, hit in rows:
        if pct > minimum:
            continue
        lines = ",".join(str(n) for n in hit[:8]) + ("…" if len(hit) > 8 else "")
        print(f"{pct:6.1f}% {miss:5d}  L{start}-{end}".ljust(42) + f"{name}  [{lines}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
