"""接线审计：`life.py` 里的引擎，到底谁在运行时真的被调用（2026-11-03）。

三条机器判据（AST 级，不数注释/字符串）：
  1. **组合**：LifeCard 的 dataclass 字段类型里是否出现该类；
  2. **LifeCard 内部真调用**：LifeCard 的方法里是否出现 `self.<字段>.<method>(...)`（字段→类型映射来自第 1 步）；
  3. **外部调用点**：`aiji/` 里除 life.py 外，是否 import 该类、或以 `Name(` / `x.Name(` / `模块级 helper(` 形式出现。

第 3 条要连 helper 一起数：`LifeCard` 自己是靠模块级 `save_life_card/load_life_card` 被内存层调用的，
只匹配类名会得出"未接线"的**假阴性**——这正是本脚本第一版踩过的坑。
用法：python tools/archcheck/wiring_audit.py [--json]
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "aiji"
TARGET = PKG / "endocrine" / "life.py"


def module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def class_names(tree: ast.Module) -> list[str]:
    return [n.name for n in tree.body if isinstance(n, ast.ClassDef)]


def lifecard_field_types(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "LifeCard":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    ann = stmt.annotation
                    if isinstance(ann, ast.Name):
                        out[stmt.target.id] = ann.id
                    elif isinstance(ann, ast.Constant):
                        out[stmt.target.id] = str(ann.value)
                    elif isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name):
                        out[stmt.target.id] = ann.value.id
    return out


def lifecard_internal_calls(tree: ast.Module, field_types: dict[str, str]) -> dict[str, set[str]]:
    """LifeCard 方法里 `self.<字段>.<method>(...)` → {类名: {方法名}}。"""
    used: dict[str, set[str]] = {}
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == "LifeCard"):
            continue
        for fn in node.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            for call in ast.walk(fn):
                if not isinstance(call, ast.Call):
                    continue
                f = call.func
                if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Attribute)
                        and isinstance(f.value.value, ast.Name)
                        and f.value.value.id == "self"):
                    cls = field_types.get(f.value.attr)
                    if cls:
                        used.setdefault(cls, set()).add(f.attr)
                elif isinstance(f, ast.Name):
                    # 内部直接构造/调用（如 context_plan 里 `ContextBudget().allocate(...)`）
                    used.setdefault(f.id, set()).add(f"{fn.name}()")
    return used


def helper_functions(tree: ast.Module, names: set[str]) -> dict[str, list[str]]:
    """模块级函数里，参数/返回值标注提到某个类的 → {类名: [helper 名]}。"""
    out: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        anns = []
        for a in list(node.args.args) + list(node.args.kwonlyargs):
            if a.annotation is not None:
                anns.append(ast.unparse(a.annotation))
        if node.returns is not None:
            anns.append(ast.unparse(node.returns))
        blob = " ".join(anns)
        for n in names:
            if n in blob:
                out.setdefault(n, []).append(node.name)
    return out


def external_usage(names: set[str], helpers: dict[str, list[str]],
                   skip: Path) -> dict[str, list[str]]:
    """aiji/ 里除 life.py 外的引用点（import / 调用 / helper 调用）。"""
    hits: dict[str, list[str]] = {n: [] for n in names}
    helper_owner = {h: cls for cls, hs in helpers.items() for h in hs}
    for p in PKG.rglob("*.py"):
        if p == skip:
            continue
        try:
            tree = module_tree(p)
        except SyntaxError:
            continue
        rel = str(p.relative_to(ROOT))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                ident = None
                if isinstance(fn, ast.Name):
                    ident = fn.id
                elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
                    ident = fn.value.id
                if ident in names:
                    hits[ident].append(f"{rel}:{node.lineno}")
                elif ident in helper_owner:
                    hits[helper_owner[ident]].append(f"{rel}:{node.lineno} (via {ident})")
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id in names:
                    hits[node.value.id].append(f"{rel}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name in names:
                        hits[a.name].append(f"{rel}:{node.lineno} (import)")
                    elif a.name in helper_owner:
                        hits[helper_owner[a.name]].append(f"{rel}:{node.lineno} (import {a.name})")
    return hits


def main() -> None:
    tree = module_tree(TARGET)
    names = class_names(tree)
    field_types = lifecard_field_types(tree)
    composed = set(field_types.values()) & set(names)
    internal = lifecard_internal_calls(tree, field_types)
    helpers = helper_functions(tree, set(names))
    hits = external_usage(set(names), helpers, skip=TARGET)

    rows = []
    for n in names:
        sites = sorted(set(hits.get(n, [])))
        methods = sorted(internal.get(n, []))
        if sites:
            verdict = "在链路上"
        elif methods:
            verdict = f"仅在 LifeCard 内部（{','.join(methods[:3])}）"
        elif n in composed:
            verdict = "只被组合，从未被调用"
        else:
            verdict = "未接线"
        rows.append({
            "engine": n,
            "composed_in_lifecard": n in composed,
            "lifecard_calls": methods,
            "external_sites": len(sites),
            "where": sites[:5],
            "helpers": helpers.get(n, []),
            "verdict": verdict,
        })

    summary = {
        "total_classes": len(names),
        "on_live_path": [r["engine"] for r in rows if r["external_sites"]],
        "only_inside_lifecard": [r["engine"] for r in rows if not r["external_sites"] and r["lifecard_calls"]],
        "composed_but_never_used": [
            r["engine"] for r in rows
            if r["composed_in_lifecard"] and not r["external_sites"] and not r["lifecard_calls"]],
        "unwired": [
            r["engine"] for r in rows
            if not r["composed_in_lifecard"] and not r["external_sites"] and not r["lifecard_calls"]],
    }
    if "--json" in sys.argv:
        print(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2))
        return
    print(f"{'engine':22s} {'composed':9s} {'ext':4s} {'lifecard-calls':22s} verdict")
    for r in rows:
        print(f"{r['engine']:22s} {str(r['composed_in_lifecard']):9s} {r['external_sites']:<4d} "
              f"{','.join(r['lifecard_calls'])[:22]:22s} {r['verdict']}")
        for w in r["where"][:3]:
            print(f"{'':22s} -> {w}")
    print()
    print("summary:", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
