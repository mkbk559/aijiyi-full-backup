"""把"工作区开着的时候"也验一遍：越权读写必须被拦，正常读写必须能用。

默认 `AIJI_ENABLE_WORKSPACE=0`（探针实测 403 workspace_disabled），
所以路径限制逻辑平时根本没被走到——它的正确性只在测试里能证明。
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.right_hand.config import Settings  # noqa: E402
from aiji.hippocampus.memory import Memory  # noqa: E402
from aiji.throat import server as server_mod  # noqa: E402


class WorkspaceConfinementTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_tmp(self, "workspace-confine")
        self.memory = Memory(
            Settings(
                db_path=self.dir / "console.db",
                routes=(),
                actions_enabled=False,
                debounce_ms=0,
                sticker_update=False,
            ),
            engines="none",
        )
        self.old = os.environ.get("AIJI_ENABLE_WORKSPACE")
        os.environ["AIJI_ENABLE_WORKSPACE"] = "1"
        self.srv = server_mod.make_server(self.memory, port=0)
        import threading

        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.memory.close()
        if self.old is None:
            os.environ.pop("AIJI_ENABLE_WORKSPACE", None)
        else:
            os.environ["AIJI_ENABLE_WORKSPACE"] = self.old

    # ---- helpers ----
    def get(self, path: str) -> tuple[int, dict]:
        import urllib.error
        import urllib.request
        from urllib.parse import quote

        req = urllib.request.Request(self.base + path.replace("{q}", quote("")))
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def fs(self, rel: str) -> tuple[int, dict]:
        import urllib.error
        import urllib.request
        from urllib.parse import quote

        url = f"{self.base}/api/fs?path={quote(rel)}"
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    # ---- 越权 ----
    def test_dotdot_escaping_workspace_is_refused(self):
        for rel in ("../../../../Windows/win.ini", "..\\..\\..\\Windows\\win.ini",
                    "sub/../../..", "/etc/passwd", "C:/Windows/win.ini",
                    r"D:\AIJiYi\README.md"):
            status, payload = self.fs(rel)
            self.assertIn(status, (400, 403, 404),
                          f"{rel} 不该被读到：{status} {payload}")
            self.assertNotIn("workspace_disabled", str(payload))

    def test_normal_read_and_list_work(self):
        root = Path(self.memory.action_executor.root).resolve()
        (root / "hello.txt").write_text("你好", encoding="utf-8") if root.exists() else None
        status, payload = self.fs(".")
        self.assertEqual(status, 200, payload)
        self.assertIn("entries", payload)

    def test_shell_needs_three_gates(self):
        # ① 工作区开着 ② 没把 AIJI_CONSOLE_SHELL 关掉 ③ 显式 AIJI_UNSAFE_SHELL=1
        status, payload = self.post("/api/shell", {"cmd": "echo AIJI_OK"})
        self.assertEqual((status, payload.get("error")), (403, "unsafe_shell_disabled"),
                         "默认必须拒绝执行任意命令")
        os.environ["AIJI_UNSAFE_SHELL"] = "1"
        try:
            status, payload = self.post("/api/shell", {"cmd": "echo AIJI_OK"})
            self.assertEqual(status, 200, payload)
            self.assertIn("AIJI_OK", json.dumps(payload, ensure_ascii=False))
        finally:
            os.environ.pop("AIJI_UNSAFE_SHELL", None)

    def test_shell_env_kill_switch_wins(self):
        os.environ["AIJI_CONSOLE_SHELL"] = "0"
        os.environ["AIJI_UNSAFE_SHELL"] = "1"
        try:
            status, payload = self.post("/api/shell", {"cmd": "echo AIJI_OK"})
            self.assertEqual((status, payload.get("error")), (403, "shell_disabled"))
        finally:
            os.environ.pop("AIJI_CONSOLE_SHELL", None)
            os.environ.pop("AIJI_UNSAFE_SHELL", None)


if __name__ == "__main__":
    unittest.main()
