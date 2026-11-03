"""控制台页面 ↔ 服务端路由的契约测试。

控制台是单页 `aiji/right_hand/web/operations.html`，按钮全靠 `fetch("/api/…")`。
路径写错不会报错——按钮点了没反应，页面上什么也不显示（第 11 轮 E2E 里
`/api/turns`、`/api/stats` 就是这么发现不存在的）。
所以这里把两边**机械地**对一遍：HTML 里出现的每个 API 路径，服务端必须认识。
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = ROOT / "aiji" / "right_hand" / "web" / "operations.html"
SERVER = ROOT / "aiji" / "throat" / "server.py"

CONSOLE_PATH = re.compile(r"""["'`](/api/[\w/.#{}:\-]*|/v1/[\w/.#{}:\-]*)""")
SERVER_LITERAL = re.compile(r"""["']((?:/api|/v1)/[\w/.\-]*)["']""")
SERVER_PREFIX = re.compile(
    r"""startswith\(\s*\(?\s*["']((?:/api|/v1)/[\w/.\-]*)["']""")


def console_paths() -> set[str]:
    return {m.group(1) for m in CONSOLE_PATH.finditer(HTML.read_text(encoding="utf-8"))}


def server_routes() -> tuple[set[str], set[str]]:
    text = SERVER.read_text(encoding="utf-8")
    literals = {m.group(1) for m in SERVER_LITERAL.finditer(text)}
    prefixes = {m.group(1) for m in SERVER_PREFIX.finditer(text)}
    return literals, prefixes


def unrouted(paths, literals: set[str], prefixes: set[str]) -> list[str]:
    """服务端不认识的路径（既有字面量、前缀式路由，也算"认识子路径"）。"""
    missing = []
    for path in sorted(paths):
        if path in literals:
            continue
        if any(path.startswith(prefix) for prefix in prefixes):
            continue
        if any(path.startswith(literal + "/") for literal in literals):
            continue
        missing.append(path)
    return missing


class ConsoleContractTest(unittest.TestCase):
    def test_console_file_exists(self):
        self.assertTrue(HTML.is_file(), "控制台页面不见了")
        self.assertGreater(HTML.stat().st_size, 5000)

    def test_every_console_path_is_a_server_route(self):
        literals, prefixes = server_routes()
        missing = unrouted(console_paths(), literals, prefixes)
        self.assertEqual(
            missing, [],
            "控制台调用了服务端没有的路径（按钮会静默失效）：" + ", ".join(missing))

    def test_checker_actually_catches_dead_paths(self):
        # 给检查器喂一个已知不存在的路径，它必须报出来（否则本测试没有牙）
        literals, prefixes = server_routes()
        self.assertEqual(
            unrouted({"/api/turns", "/api/stats"}, literals, prefixes),
            ["/api/stats", "/api/turns"])
        self.assertEqual(unrouted({"/api/fs/write"}, literals, prefixes), [],
                         "前缀式路由要认")

    def test_console_does_not_call_known_dead_paths(self):
        dead = {"/api/turns", "/api/stats", "/api/memory/library"}
        self.assertEqual(console_paths() & dead, set())

    def test_console_has_no_inline_secret(self):
        text = HTML.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"sk-[A-Za-z0-9]{16,}", "别把 key 写进页面")
        self.assertNotRegex(text, r"Bearer\s+[A-Za-z0-9\-_\.]{20,}",
                            "别把 token 硬编码进页面")


if __name__ == "__main__":
    unittest.main()
