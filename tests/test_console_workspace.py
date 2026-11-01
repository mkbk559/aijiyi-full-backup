"""控制台新增面：桌宠养成、工作区文件树/预览/写入、工作区终端。"""

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

from aiji.config import Settings
from aiji.memory import Memory
from aiji.pet.growth import PetStore
from aiji.server import make_server

TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp"
TMP_ROOT.mkdir(mode=0o777, exist_ok=True)


class TestConsoleNavigation(unittest.TestCase):
    def test_every_view_has_navigation_and_title(self):
        html = (Path(__file__).resolve().parent.parent / "aiji" / "web" / "console.html").read_text("utf-8")
        views = set(re.findall(r'<section id="view-([\w-]+)"', html))
        nav = set(re.findall(r'<button[^>]*data-view="([\w-]+)"', html))
        title_block = html.split("const titles = {", 1)[1].split("};", 1)[0]
        titles = set(re.findall(r"\b([a-z][\w-]*):\s*\[", title_block))
        self.assertEqual(views, nav)
        self.assertEqual(views, titles)


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

    # ---- 桌宠 ----

    def test_pet_snapshot_interactions_and_rename(self):
        pet = self.get("/api/pet")
        self.assertEqual(pet["name"], "大肥鱼")
        self.assertEqual(pet["treats"], 0)
        self.assertEqual(pet["affinity"]["points"], 0)
        self.assertEqual(pet["affinity"]["rank"], "幼鲸")

        status, first = self.post("/api/pet/pet", {})
        self.assertEqual(status, 200)
        self.assertTrue(first["ok"])
        self.assertEqual(first["delta"], 1)
        self.assertIn("摸摸", first["line"])

        # 10s 冷却内再摸：不加分，但要给冷却台词
        status, second = self.post("/api/pet/pet", {})
        self.assertEqual(status, 200)
        self.assertFalse(second["ok"])
        self.assertEqual(second["delta"], 0)

        # 没有小鱼干时喂食：明确拒绝且不扣粮
        status, hungry = self.post("/api/pet/feed", {})
        self.assertEqual(status, 200)
        self.assertFalse(hungry["ok"])
        self.assertEqual(hungry["snapshot"]["treats"], 0)

        # 直接给 3 条鱼干，再喂：消耗一条 +5
        store = PetStore(self.dir / "pet_state.json")
        store.treats = {**store.treats, "treats": 3}
        store.save()
        status, fed = self.post("/api/pet/feed", {})
        self.assertEqual(status, 200)
        self.assertTrue(fed["ok"])
        self.assertEqual(fed["delta"], 5)
        self.assertEqual(fed["snapshot"]["treats"], 2)

        status, renamed = self.post("/api/pet/rename", {"name": "小白鱼"})
        self.assertEqual(status, 200)
        self.assertEqual(renamed["name"], "小白鱼")
        self.assertEqual(self.get("/api/pet")["name"], "小白鱼")

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

    def test_console_page_and_assets(self):
        status, raw, ctype = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        html = raw.decode("utf-8")
        for marker in ("工作区", "pet-dock", "echarts.min.js", "whale_spritesheet.webp"):
            self.assertIn(marker, html)

        status, raw, ctype = self.request("/assets/echarts.min.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", ctype)
        self.assertGreater(len(raw), 500_000)

        status, raw, ctype = self.request("/assets/whale_spritesheet.webp")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "image/webp")
        self.assertGreater(len(raw), 100_000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
