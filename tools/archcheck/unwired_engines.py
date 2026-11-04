"""死引擎检查：aiji/ 里的类，生产代码（aiji/**，除定义模块自身）零引用、
只有 tests/ 在用 → 标记为"建成但未接线"。

背景（2026-11-04 第 28 轮）：endocrine/proactive.py 的 Proactive 引擎（静默门/
额度/退避/outbox 全套，12 条测试）从建成就没在生产接过线——wiring_audit.py
只扫 life.py 的 LifeCard，发现不了它。本工具做通用版。

用法：python tools/archcheck/unwired_engines.py [--json]
退出码：发现未接线类 = 2（可见但有活干）；全接线 = 0。
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "aiji"
TESTS = ROOT / "tests"


def class_defs() -> list[tuple[str, Path]]:
    """aiji/ 里所有顶层 ClassDef：[(类名, 定义文件)]。"""
    out = []
    for p in sorted(PKG.rglob("*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                out.append((node.name, p))
    return out


def _file_refs(name: str, tree: ast.Module, skip_lineno_span: tuple[int, int] | None) -> int:
    """一个 AST 里对该类名的引用数；skip_lineno_span=(类起点,终点) 时跳过类体。"""
    n = 0
    for node in ast.walk(tree):
        if skip_lineno_span and isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            start, end = skip_lineno_span
            if node.lineno >= start and (node.end_lineno or node.lineno) <= end:
                continue
        if isinstance(node, ast.ImportFrom):
            if any(a.name == name for a in node.names):
                n += 1
        elif isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Name) and f.id == name) or (
                    isinstance(f, ast.Attribute) and f.attr == name):
                n += 1
        elif isinstance(node, ast.Attribute):
            if node.attr == name:
                n += 1
        elif isinstance(node, ast.Name):
            if node.id == name:
                n += 1
    return n


def refs(name: str, root: Path, skip: Path) -> int:
    """root 下对该类的引用：定义文件里"类体之外"的兄弟代码 + 其他文件全部。"""
    n = 0
    for p in root.rglob("*.py"):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        if p == skip:
            # 同文件兄弟代码算生产引用（wiring_audit 的教训：模块内 helper 用法要数）
            span = None
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name == name:
                    span = (node.lineno, node.end_lineno or node.lineno)
                    break
            n += _file_refs(name, tree, span)
        else:
            n += _file_refs(name, tree, None)
    return n


def main() -> int:
    as_json = "--json" in sys.argv
    findings = []
    for name, path in class_defs():
        prod = refs(name, PKG, path)
        test = refs(name, TESTS, path)
        if prod == 0 and test > 0:
            findings.append({
                "class": name,
                "file": str(path.relative_to(ROOT)),
                "test_refs": test,
            })
    if as_json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
    else:
        if not findings:
            print("全部引擎类都有生产引用，无建成但未接线的类。")
            return 0
        print("发现 {} 个建成但未接线的类（只有测试引用）：".format(len(findings)))
        for f in findings:
            print(f"  {f['class']:<24} {f['file']}  (tests 引用 {f['test_refs']} 次)")
        print("处置：接线是产品决策（会改变用户可见行为），删除前先查 excluded.md。")
    return 2 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
