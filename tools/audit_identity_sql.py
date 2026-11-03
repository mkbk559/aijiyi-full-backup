"""串号风险审计：找出"碰身份表但 WHERE 里没有身份条件"的 SQL 字符串。

本项目的头号已知风险是跨平台串号（一次漏写 `platform` 条件就是事故），
而身份条件散落在几十个 SQL 字符串里，靠人眼 review 必然漏。
这里用 AST 把 `aiji/**/*.py` 里所有 SQL 字面量取出来，做**静态**分类：

- INSERT：看是否写了身份列
- SELECT / UPDATE / DELETE：看 WHERE 里有没有身份列

输出四类（**这是 review 清单，不是 bug 清单**）：
  A. no_where      碰身份表、整条语句没有 WHERE（通常是全局维护/计数，需人眼确认）
  B. filtered_other WHERE 存在但没有身份列（按 status/时间维护，同样需确认）
  C. by_id          只按 `id = ?` 定位（通常安全；但要确认 id 来自本身份）
  D. ok             WHERE 里有 platform/platform_user_id/scope

注意：静态分析看到的只是 SQL **字面量**。很多查询用
`f"SELECT ... WHERE {w}"` + `ident.where()` 在运行时拼条件，
这里会把它们归到 A/B；真正验证隔离要靠
`tests/test_identity_isolation.py`（行为级、双身份对撞）。

用法：python tools/audit_identity_sql.py [--verbose] [--strict]
      --strict：只要 A/B 非空就返回 1（CI 用；本地 review 别开）
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "aiji"

IDENTITY_TABLES = {
    "fact", "fact_link", "fact_log", "turn", "turn_archive", "profile", "trait",
    "injection", "reflection", "summary", "outbox", "send_queue", "media",
    "being_state", "being_event", "being_note", "heart_state", "life_card",
    "self_memory", "style", "account", "instance", "event_log", "write_queue",
}
IDENTITY_COLUMNS = ("platform", "platform_user_id", "scope")
SQL_START = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", re.I)
TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)


def sql_strings(path: pathlib.Path):
    """产出 (行号, SQL 文本)——把相邻字符串字面量拼起来，跟 Python 的语义一致。"""
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if SQL_START.match(text):
                yield node.lineno, text
        elif isinstance(node, ast.JoinedStr):
            continue
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            joined = _concat(node)
            if joined and SQL_START.match(joined):
                yield node.lineno, joined


def _concat(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _concat(node.left)
        right = _concat(node.right)
        if left is None or right is None:
            return None
        return left + right
    return None


def has_where(sql: str) -> bool:
    return " where " in " " + " ".join(sql.lower().split()) + " "


def classify(sql: str) -> tuple[str, list[str]]:
    tables = {t.lower() for t in TABLE_REF.findall(sql)}
    identity_tables = sorted(tables & IDENTITY_TABLES)
    if not identity_tables:
        return "skip", identity_tables
    lowered = sql.lower()
    has_identity = any(col in lowered for col in IDENTITY_COLUMNS)
    if sql.lstrip()[:6].upper() == "INSERT":
        return ("ok" if has_identity else "insert_missing"), identity_tables
    if not has_identity:
        if re.search(r"\bid\s*=\s*\?", lowered):
            return "by_id", identity_tables
        return ("filtered_other" if has_where(sql) else "no_where"), identity_tables
    return "ok", identity_tables


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    buckets: dict[str, list[tuple[str, int, str, list[str]]]] = {
        "no_where": [], "filtered_other": [], "insert_missing": [],
        "by_id": [], "ok": []}
    for path in sorted(PKG.rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        for lineno, sql in sql_strings(path):
            kind, tables = classify(sql)
            if kind == "skip":
                continue
            buckets[kind].append((rel, lineno, " ".join(sql.split())[:220], tables))
    for kind in ("no_where", "filtered_other", "insert_missing", "by_id", "ok"):
        rows = buckets[kind]
        print(f"\n=== {kind} ({len(rows)}) ===")
        for rel, lineno, sql, tables in rows:
            if kind == "ok" and not args.verbose:
                continue
            print(f"{rel}:{lineno} [{','.join(tables)}] {sql}")
    print("\n总计：" + "、".join(
        f"{kind} {len(rows)}" for kind, rows in buckets.items()))
    print("提示：A/B 大多是有意的全局维护（janitor/growth/stats）；"
          "运行时的隔离保证见 tests/test_identity_isolation.py")
    review = len(buckets["no_where"]) + len(buckets["filtered_other"]) + \
        len(buckets["insert_missing"])
    return 1 if (args.strict and review) else 0


if __name__ == "__main__":
    raise SystemExit(main())
