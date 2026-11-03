"""文档引用漂移检查：**现行文档**里提到的文件/路由必须真的存在。

为什么只查"现行"：`PROJECT_STATE.md` 的决策日志在讲历史（"`aiji/pet/growth.py` 已删除"），
那里出现不存在的路径是**正确的**。真正会骗人的是"现状 / 排期 / 风险"这类当下描述，
以及没被标记为历史的 design 文档。

现行文档 = 
  - `README.md`、`PROJECT_STATE.md` 的**非决策日志部分**
  - `docs/agent/design/*.md` 里**没有** "设定已废弃/已作废" 横幅的
  - `docs/agent/rules/*.md`（规则必须当下有效）

用法：python tools/archcheck/doc_ref_check.py [--verbose]
产物：tools/archcheck/results/doc_ref.json
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "tools" / "archcheck" / "results" / "doc_ref.json"

FILE_REF = re.compile(
    r"`((?:tools|aiji|docs|tests|config|bench|deploy)/[\w./\-]+)`")
ROUTE_REF = re.compile(r"`(/api/[\w/\-]+|/v1/[\w/\-]+|/health|/stats)`")
HISTORY_MARK = ("设定已废弃", "已作废", "历史文档")
# 按需创建的文件：第一次运行时磁盘上还没有，但代码里就有这个路径
# （`aiji/right_hand/operations.py` 的 CONFIG_PATH 用 ROOT / "config" / "runtime.json"
# 拼出来，所以字面量扫描扫不到）。
LAZY_OK = {"config/runtime.json"}

LOG_HEAD = "## 决策日志"
NEXT_HEAD = "## 未决"


def code_path_literals() -> set[str]:
    """代码里写死的路径字面量（如 config/runtime.json 是按需创建，磁盘上可能没有）。"""
    literal = re.compile(r'["\']((?:[\w.\-]+/)+[\w.\-]+\.(?:json|db|log|yaml|yml|toml|html|md|txt|webp|png))["\']')
    found: set[str] = set()
    for path in (ROOT / "aiji").rglob("*.py"):
        found.update(literal.findall(path.read_text(encoding="utf-8", errors="ignore")))
    return found


def server_routes() -> set[str]:
    src = (ROOT / "aiji" / "throat" / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    routes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith(("/api/", "/v1/")) or node.value in ("/health", "/stats"):
                routes.add(node.value)
    return routes


def current_texts() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    readme = ROOT / "README.md"
    if readme.is_file():
        out.append((readme, readme.read_text(encoding="utf-8")))
    state = ROOT / "docs" / "agent" / "PROJECT_STATE.md"
    if state.is_file():
        text = state.read_text(encoding="utf-8")
        if LOG_HEAD in text:
            head, rest = text.split(LOG_HEAD, 1)
            tail = rest.split(NEXT_HEAD, 1)[1] if NEXT_HEAD in rest else ""
            text = head + "\n" + NEXT_HEAD + tail
        out.append((state, text))
    for pattern in ("docs/agent/design/*.md", "docs/agent/rules/*.md"):
        for path in sorted(ROOT.glob(pattern)):
            body = path.read_text(encoding="utf-8")
            if any(mark in body[:400] for mark in HISTORY_MARK):
                continue
            out.append((path, body))
    return out


def main() -> int:
    routes = server_routes()
    literals = code_path_literals()
    problems: list[dict] = []
    checked = 0
    for path, text in current_texts():
        for line_no, line in enumerate(text.splitlines(), 1):
            for ref in FILE_REF.findall(line):
                checked += 1
                if not (ROOT / ref).exists() and ref not in literals and ref not in LAZY_OK:
                    problems.append({"doc": str(path.relative_to(ROOT)), "line": line_no,
                                     "ref": ref, "kind": "file"})
            for route in ROUTE_REF.findall(line):
                checked += 1
                if route not in routes and not any(
                        r.startswith(route.rstrip("/") + "/") for r in routes):
                    problems.append({"doc": str(path.relative_to(ROOT)), "line": line_no,
                                     "ref": route, "kind": "route"})
    payload = {"ok": not problems, "checked": checked, "problems": problems[:40],
               "problem_count": len(problems)}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if "--verbose" in sys.argv or problems:
        for p in problems:
            print(f"  {p['doc']}:{p['line']}  {p['kind']}  {p['ref']}")
    print(json.dumps({k: payload[k] for k in ("ok", "checked", "problem_count")},
                     ensure_ascii=False))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
