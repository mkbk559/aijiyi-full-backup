"""控制台工作区文件树/预览/写入、工作区终端。"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from unittest import mock

from aiji.right_hand.config import Settings
from aiji.right_hand import operations
from aiji.hippocampus.memory import Memory
from aiji.throat.server import make_server

TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp"
TMP_ROOT.mkdir(mode=0o777, exist_ok=True)


class TestConsoleNavigation(unittest.TestCase):
    def test_operations_views_have_navigation(self):
        """控制台导航必须和架构图的九大系统一一对应（2026-11-03 重构）。

        页面由 tools/console_build.py 从 architecture 的 nodes.json 生成，
        导航在浏览器里运行时创建，所以这里校验生成进去的数据块。
        """
        html = (Path(__file__).resolve().parent.parent / "aiji" / "right_hand" / "web" / "operations.html").read_text("utf-8")
        systems = re.findall(r'\{"id": "(\w+)", "nav": "([^"]+)"', html)
        ids = [sid for sid, _nav in systems]
        # 前 9 个是身体系统，后面是运维视图（配置 / 生命周期）
        self.assertEqual(ids[:9], ["entry", "ingress", "orch", "memory", "life",
                                   "out", "tools", "ops", "data"])
        self.assertEqual(ids[9:], ["settings", "lifecycle"])
        # 每个系统都带节点和面板，不能有空壳页
        for entry in re.findall(r'\{"id": "(\w+)", "nav"[^}]*"panels": \[', html)[:9]:
            self.assertIn(entry, ids[:9])


def workdir() -> Path:
    path = TMP_ROOT / f"console-ws-{uuid.uuid4().hex[:8]}"
    path.mkdir(mode=0o777)
    return path


class TestConsoleWorkspace(unittest.TestCase):
    def setUp(self):
        self.dir = workdir()
        self.memory = Memory(Settings(
            db_path=self.dir / "console.db",
            action_root=str(self.dir),
            routes=(),
            reflection_interval_s=60,
            debounce_ms=0,
            humanize_min_ms=0,
            humanize_max_ms=0,
            typing_min_ms=0,
            typing_max_ms=0,
        ), engines="none")
        self.server = make_server(self.memory, "127.0.0.1", 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.env = mock.patch.dict(os.environ, {
            "AIJI_API_KEY": "", "AIJI_CONSOLE_SHELL": "1",
            "AIJI_ENABLE_WORKSPACE": "1",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.memory.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    # ---- HTTP helpers ----

    def request(self, path: str, body: dict | None = None) -> tuple[int, bytes, str]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base + path, data=data,
            method="GET" if body is None else "POST",
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.status, response.read(), response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers.get_content_type()

    def get(self, path: str) -> dict:
        status, raw, _ = self.request(path)
        self.assertEqual(status, 200, raw.decode("utf-8", "replace"))
        return json.loads(raw)

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        status, raw, _ = self.request(path, body)
        return status, json.loads(raw) if raw else {}

    def test_removed_pet_routes_do_not_create_state(self):
        for route in ("/api/pet", "/assets/whale_spritesheet.webp"):
            status, _, _ = self.request(route)
            self.assertEqual(status, 404)
        for route in ("/api/pet/pet", "/api/pet/feed", "/api/pet/rename"):
            status, _ = self.post(route, {})
            self.assertEqual(status, 404)
        self.assertFalse((self.dir / "pet_state.json").exists())

    def test_operations_api_lists_features_and_services(self):
        features = self.get("/api/operations/features")
        self.assertGreaterEqual(len(features["items"]), 18)
        services = self.get("/api/operations/services")
        self.assertIn("aiji", {item["id"] for item in services["services"]})

    def test_operations_config_saves_validated_values(self):
        with mock.patch.object(operations, "CONFIG_PATH", self.dir / "runtime.json"):
            status, payload = self.post("/api/operations/config", {
                "changes": {"budget_ms": 150, "rank_mode": "rrf_shadow"}})
            self.assertEqual(status, 200)
            self.assertEqual(payload["values"]["budget_ms"], 150)
            self.assertEqual(self.get("/api/operations/config")["values"]["rank_mode"], "rrf_shadow")
            status, _ = self.post("/api/operations/config", {
                "changes": {"onebot_token": "must-not-save"}})
            self.assertEqual(status, 400)
            self.assertNotIn("onebot_token", (self.dir / "runtime.json").read_text("utf-8"))

    # ---- 工作区文件 ----

    def test_workspace_list_read_write_and_traversal(self):
        (self.dir / "hello.txt").write_text("深海控制台", encoding="utf-8")
        (self.dir / "sub").mkdir()
        (self.dir / "sub" / "a.md").write_text("# 标题", encoding="utf-8")

        listing = self.get("/api/fs?path=")
        names = {item["name"]: item for item in listing["entries"]}
        self.assertIn("hello.txt", names)
        self.assertEqual(names["sub"]["kind"], "dir")
        self.assertEqual(listing["root"], str(self.dir))

        sub = self.get("/api/fs?path=sub")
        self.assertEqual(sub["path"], "sub")
        self.assertEqual({item["name"] for item in sub["entries"]}, {"a.md"})

        read = self.get("/api/fs/read?path=hello.txt")
        self.assertFalse(read["binary"])
        self.assertEqual(read["text"], "深海控制台")

        status, written = self.post("/api/fs/write", {"path": "sub/new.txt", "text": "新文件"})
        self.assertEqual(status, 200)
        self.assertTrue(written["ok"])
        self.assertEqual((self.dir / "sub" / "new.txt").read_text("utf-8"), "新文件")

        # 越权路径一律 403
        status, _, _ = self.request("/api/fs?path=" + urllib.parse.quote("../.."))
        self.assertEqual(status, 403)
        status, _, _ = self.request("/api/fs/read?path=" + urllib.parse.quote("../console.db"))
        self.assertEqual(status, 403)
        status, _ = self.post("/api/fs/write", {"path": "../escape.txt", "text": "x"})
        self.assertEqual(status, 403)
        self.assertFalse((self.dir.parent / "escape.txt").exists())

        status, raw, ctype = self.request("/api/fs/raw?path=hello.txt")
        self.assertEqual(status, 200)
        self.assertIn("text", ctype)

    def test_workspace_is_disabled_by_default(self):
        with mock.patch.dict(os.environ, {"AIJI_ENABLE_WORKSPACE": "0"}):
            status, _, _ = self.request("/api/fs?path=")
            self.assertEqual(status, 403)
            status, _ = self.post("/api/fs/write", {"path": "x.txt", "text": "x"})
            self.assertEqual(status, 403)
            status, _ = self.post("/api/shell", {"cmd": "echo x"})
            self.assertEqual(status, 403)
        self.assertFalse((self.dir / "x.txt").exists())

    # ---- 工作区终端 ----

    def test_shell_runs_in_workspace_and_can_be_disabled(self):
        status, disabled = self.post("/api/shell", {"cmd": "echo console-ok"})
        self.assertEqual(status, 403)
        self.assertEqual(disabled["error"], "unsafe_shell_disabled")

        with mock.patch.dict(os.environ, {"AIJI_UNSAFE_SHELL": "1"}):
            status, result = self.post("/api/shell", {"cmd": "echo console-ok"})
            self.assertEqual(status, 200)
            self.assertEqual(result["code"], 0)
            self.assertIn("console-ok", result["stdout"])
            self.assertFalse(result["timed_out"])

        with mock.patch.dict(os.environ, {"AIJI_UNSAFE_SHELL": "1"}):
            status, blocked = self.post("/api/shell", {"cmd": "echo x", "cwd": "../.."})
        self.assertEqual(status, 403)
        self.assertEqual(blocked["error"], "path_outside_root")

        with mock.patch.dict(os.environ, {"AIJI_CONSOLE_SHELL": "0"}):
            status, disabled = self.post("/api/shell", {"cmd": "echo x"})
        self.assertEqual(status, 403)
        self.assertEqual(disabled["error"], "shell_disabled")

    # ---- 静态资源 ----

    def test_operations_page_replaces_console(self):
        status, raw, ctype = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        html = raw.decode("utf-8")
        # 重构后的页面：九大系统 + 运维视图，且不引用任何外部 CDN
        for marker in ("九大系统", "海马体", "骨架", "功能配置", "从启动到结束"):
            self.assertIn(marker, html)
        self.assertNotIn("pet-dock", html)
        self.assertNotIn("http://cdn", html)
        self.assertNotIn("https://cdn", html)
        status, _, _ = self.request("/console")
        self.assertEqual(status, 404)



if __name__ == "__main__":
    unittest.main(verbosity=2)
