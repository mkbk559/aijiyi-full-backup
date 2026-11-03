"""控制台"真浏览器"回归：页面在无头浏览器里必须跑得起来。

对应长期挂着的空白项（2026-11-03 之前：控制台只有"接口对得上"的静态检查，
没有任何浏览器级验证）。没装浏览器时跳过，别的机器上不该红。
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "archcheck" / "console_render.py"


class ConsoleRenderTest(unittest.TestCase):
    def test_console_renders_without_js_errors(self):
        sys.path.insert(0, str(ROOT / "tools" / "archcheck"))
        import console_render

        browser = console_render.find_browser()
        if not browser:
            self.skipTest("没装 Chrome/Edge，跳过浏览器级检查")
        html = console_render.PAGE.stat().st_size
        dom, err = console_render.render(browser)
        result = console_render.check(
            dom, err, console_render.spec_navs(), "entry", html)
        self.assertTrue(result["ok"], json.dumps(result, ensure_ascii=False))
        self.assertEqual(result["navs_found"], 11)      # 9 系统 + 配置 + 生命周期
        self.assertEqual(result["js_errors"], [])
        self.assertTrue(result["js_ran"])              # DOM 比静态文件大 = JS 注入过

    def test_tool_exits_zero_on_this_machine(self):
        proc = subprocess.run([sys.executable, str(TOOL)],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(payload.get("ok") or payload.get("skipped"), payload)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
