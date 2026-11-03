"""服务端路由的行为覆盖：GET 各面板、POST 记忆入口、工作区/终端的开关门。

`tests/test_console_api.py` 只走了"读面板 + 网关回复"那几条主路；
`server.py` 里剩下的分支大多是**开关门**（工作区默认关、终端三重开关、
超大请求体、越界路径），这些分支写错了不会报错——只会悄悄变成"点了没反应"，
或者更糟：本该拒绝的放行了。这里逐条把开关的两个方向都试一遍。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.hippocampus.memory import Memory  # noqa: E402
from aiji.right_hand.config import Settings  # noqa: E402
from aiji.throat.server import make_server  # noqa: E402


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(make_tmp(self, "routes"))
        env = mock.patch.dict(os.environ, {"AIJI_MCP_RESEARCH_LOOP": "0"}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        (self.root / "workspace").mkdir(parents=True, exist_ok=True)
        self.memory = Memory(Settings(
            db_path=self.root / "routes.db",
            routes=(),
            # 工作区根必须落在临时目录里：默认是 cwd，跑测试会在仓库里
            # 真写出 notes/a.txt（本轮就这么误提交过一次）。
            action_root=str(self.root / "workspace"),
            debounce_ms=0,
            humanize_min_ms=0,
            humanize_max_ms=0,
            typing_min_ms=0,
            typing_max_ms=0,
        ), engines="none")
        self.server = make_server(self.memory, "127.0.0.1", 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.memory.close()

    def _request(self, method: str, path: str, payload: dict | None = None,
                 timeout: float = 30) -> tuple[int, bytes]:
        request = urllib.request.Request(
            self.base + path,
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            method=method,
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def get(self, path: str) -> tuple[int, dict]:
        status, raw = self._request("GET", path)
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, {"_raw": raw[:200]}

    def post(self, path: str, payload: dict) -> tuple[int, dict]:
        status, raw = self._request("POST", path, payload)
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, {"_raw": raw[:200]}


class GetPanelTest(ServerCase):
    """每个面板路由都要能出 JSON，且不能 500。"""

    CASES = {
        "/health": {"ok", "version", "queue_depth", "pool"},
        "/stats": {"writer", "store", "pool"},
        "/v1/voices": {"active", "profiles"},
        "/v1/songs": {"jobs"},
        "/api/social": {"configured", "transport"},
        "/api/stickers": {"service"},
        "/api/memories": {"identity", "items", "query"},
        "/api/memory-graph": {"nodes", "edges"},
        "/api/self": {"floor", "global_traits", "prefix", "traits"},
        "/api/scheduler": {"outbox", "send_queue"},
        "/api/models": {"routes"},
        "/api/capabilities": {"ocr", "asr", "onebot", "openai_api", "settings",
                              "platforms", "websocket", "octop_gateway",
                              "gateway_webhook"},
        "/api/operations/features": {"items"},
        "/api/operations/config": {"values"},
        "/api/operations/services": {"services", "ready", "desired", "total"},
        "/api/growth": {"stage", "stages"},
        "/api/recent-turns": {"items"},
        "/api/injections": {"items"},
        "/api/proactive": {"items"},
        "/api/actions/pending": {"items", "stats"},
        "/api/actions/stats": {"stats", "root"},
        "/api/integrations/onebot": {"configured", "webhook"},
        "/api/being/checkup": {"overall_score", "verdict", "layers",
                               "verified_layers", "is_human_like_certified"},
        "/media": {"items"},
        "/style": {"style", "summary"},
        "/being": {"snapshot", "events", "notes"},
        "/accounts": {"items"},
        "/v1/models": {"data"},
    }

    def test_panels_answer_with_expected_keys(self):
        for path, keys in self.CASES.items():
            with self.subTest(path=path):
                status, body = self.get(path)
                self.assertEqual(status, 200, f"{path} -> {status} {body}")
                self.assertFalse(
                    set(keys) - set(body),
                    f"{path} 少了字段：{sorted(set(keys) - set(body))}")

    def test_root_serves_the_console_html(self):
        status, raw = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("<html", raw[:400].decode("utf-8", "replace").lower())

    def test_unknown_path_is_404_json(self):
        status, body = self.get("/api/definitely-not-here")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")

    def test_bad_query_number_is_400(self):
        status, body = self.get("/api/memories?limit=abc")
        self.assertEqual(status, 400)
        self.assertIn("must be an integer", body["error"])


class MemoryEndpointTest(ServerCase):
    """写入口：空的要拒绝，有内容的要 202/201 入队。"""

    def test_turn_endpoint(self):
        status, body = self.post("/turns", {"content": "   "})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "content_required")
        status, body = self.post("/turns", {"content": "我在测试", "session": "s1"})
        self.assertEqual(status, 202)
        self.assertTrue(body["queued"])

    def test_remember_endpoint(self):
        status, body = self.post("/remember", {})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "text_required")
        status, body = self.post("/remember", {"text": "记住这条"})
        self.assertEqual(status, 202)

    def test_media_endpoint_requires_ref(self):
        status, body = self.post("/media", {})
        self.assertEqual((status, body["error"]), (400, "ref_required"))
        status, body = self.post("/media", {"ref": "a.jpg", "kind": "image"})
        self.assertEqual(status, 201)
        self.assertEqual(body["stored"], "reference_only")

    def test_ingest_endpoint(self):
        status, body = self.post("/ingest", {"kind": "image"})
        self.assertEqual((status, body["error"]), (400, "ref_or_url_required"))
        status, body = self.post("/ingest", {"kind": "text", "text": "一段话"})
        self.assertEqual(status, 202)
        self.assertEqual(body["kind"], "text")

    def test_recall_and_inject_answer(self):
        self.post("/turns", {"content": "我住在杭州", "session": "s1"})
        self.post("/flush", {"timeout": 5})
        status, body = self.post("/recall", {"query": "杭州"})
        self.assertEqual(status, 200)
        self.assertIn("hits", body)
        status, body = self.post("/inject", {"query": "杭州", "log": False})
        self.assertEqual(status, 200)
        self.assertIn("text", body)

    def test_feedback_and_flush(self):
        status, body = self.post("/feedback", {"used_ids": []})
        self.assertEqual(status, 200)
        self.assertIn("updated", body)
        status, body = self.post("/flush", {"timeout": 1})
        self.assertEqual(status, 200)
        self.assertTrue(body["flushed"])

    def test_being_note_and_event(self):
        status, body = self.post("/being/note", {"text": ""})
        self.assertEqual((status, body["error"]), (400, "text_required"))
        status, body = self.post("/being/note", {"text": "今天很开心"})
        self.assertEqual(status, 201)
        status, body = self.post("/being/event", {"summary": ""})
        self.assertEqual((status, body["error"]), (400, "summary_required"))
        status, body = self.post("/being/event", {
            "kind": "life", "summary": "写完一轮自检", "valence_delta": 0.2})
        self.assertEqual(status, 201)

    def test_unknown_approval_is_404(self):
        status, body = self.post("/api/actions/approve", {"id": "不存在"})
        self.assertEqual(status, 404)
        self.assertFalse(body["resolved"])

    def test_embeddings_endpoint(self):
        status, body = self.post("/v1/embeddings", {"input": "一句话"})
        self.assertEqual(status, 200)
        self.assertEqual(len(body["data"]), 1)
        self.assertEqual(body["data"][0]["index"], 0)
        self.assertTrue(body["data"][0]["embedding"])

    def test_embeddings_rejects_too_many_inputs(self):
        status, body = self.post("/v1/embeddings", {"input": ["x"] * 257})
        self.assertEqual((status, body["error"]), (400, "too_many_inputs"))


class WorkspaceGateTest(ServerCase):
    """工作区默认关；打开后路径必须锁在根内。"""

    def test_everything_is_403_while_disabled(self):
        for method, path, payload in (
            ("GET", "/api/fs", None),
            ("GET", "/api/fs/read?path=a.txt", None),
            ("GET", "/api/fs/raw?path=a.txt", None),
            ("POST", "/api/fs/write", {"path": "a.txt", "text": "x"}),
            ("POST", "/api/shell", {"cmd": "echo hi"}),
        ):
            with self.subTest(path=path):
                status, raw = self._request(method, path, payload)
                self.assertEqual(status, 403, f"{path} -> {status}")
                self.assertIn(b"workspace_disabled", raw)

    def test_write_read_list_roundtrip_when_enabled(self):
        with mock.patch.dict(os.environ, {"AIJI_ENABLE_WORKSPACE": "1"}):
            status, body = self.post("/api/fs/write",
                                     {"path": "notes/a.txt", "text": "你好"})
            self.assertEqual(status, 200, body)
            self.assertEqual(body["size"], len("你好".encode("utf-8")))
            status, body = self.get("/api/fs?path=notes")
            self.assertEqual(status, 200)
            self.assertIn("a.txt", [e["name"] for e in body["entries"]])
            status, body = self.get("/api/fs/read?path=notes/a.txt")
            self.assertEqual((status, body["text"]), (200, "你好"))
            status, raw = self._request("GET", "/api/fs/raw?path=notes/a.txt")
            self.assertEqual(status, 200)
            self.assertEqual(raw.decode("utf-8"), "你好")

    def test_escape_attempts_are_rejected(self):
        with mock.patch.dict(os.environ, {"AIJI_ENABLE_WORKSPACE": "1"}):
            for path in ("../outside.txt", "notes/../../outside.txt", ".."):
                status, body = self.post("/api/fs/write",
                                         {"path": path, "text": "x"})
                self.assertEqual(status, 403, path)
                self.assertEqual(body["error"], "path_outside_root")

    def test_write_validation(self):
        with mock.patch.dict(os.environ, {"AIJI_ENABLE_WORKSPACE": "1"}):
            status, body = self.post("/api/fs/write", {"path": "", "text": "x"})
            self.assertEqual((status, body["error"]), (400, "path_and_text_required"))
            status, body = self.post("/api/fs/write", {"path": "a.txt"})
            self.assertEqual((status, body["error"]), (400, "path_and_text_required"))
            status, body = self.post("/api/fs/write", {"path": ".", "text": "x"})
            self.assertEqual((status, body["error"]), (400, "is_directory"))

    def test_read_missing_file_and_directory_listing(self):
        with mock.patch.dict(os.environ, {"AIJI_ENABLE_WORKSPACE": "1"}):
            status, body = self.get("/api/fs/read?path=nope.txt")
            self.assertEqual((status, body["error"]), (404, "not_found"))
            status, body = self.get("/api/fs?path=.")
            self.assertEqual(status, 200)
            self.assertTrue(body["path"] == "" or body["path"] == ".")


class ShellGateTest(ServerCase):
    """终端三重开关：工作区 → 控制台 shell → unsafe 开关。"""

    def test_shell_disabled_by_console_switch(self):
        env = {"AIJI_ENABLE_WORKSPACE": "1", "AIJI_CONSOLE_SHELL": "0"}
        with mock.patch.dict(os.environ, env):
            status, body = self.post("/api/shell", {"cmd": "echo hi"})
        self.assertEqual((status, body["error"]), (403, "shell_disabled"))

    def test_shell_disabled_by_unsafe_switch(self):
        env = {"AIJI_ENABLE_WORKSPACE": "1", "AIJI_CONSOLE_SHELL": "1"}
        env["AIJI_UNSAFE_SHELL"] = "0"
        with mock.patch.dict(os.environ, env):
            status, body = self.post("/api/shell", {"cmd": "echo hi"})
        self.assertEqual((status, body["error"]), (403, "unsafe_shell_disabled"))

    def test_empty_command_is_400(self):
        env = {"AIJI_ENABLE_WORKSPACE": "1", "AIJI_CONSOLE_SHELL": "1",
               "AIJI_UNSAFE_SHELL": "1"}
        with mock.patch.dict(os.environ, env):
            status, body = self.post("/api/shell", {"cmd": "  "})
        self.assertEqual((status, body["error"]), (400, "cmd_required"))

    def test_command_runs_and_reports_exit_code(self):
        env = {"AIJI_ENABLE_WORKSPACE": "1", "AIJI_CONSOLE_SHELL": "1",
               "AIJI_UNSAFE_SHELL": "1"}
        with mock.patch.dict(os.environ, env):
            status, body = self.post("/api/shell", {"cmd": "echo aiji-shell"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["code"], 0)
        self.assertIn("aiji-shell", body["stdout"])

    def test_cwd_outside_root_is_rejected(self):
        env = {"AIJI_ENABLE_WORKSPACE": "1", "AIJI_CONSOLE_SHELL": "1",
               "AIJI_UNSAFE_SHELL": "1"}
        with mock.patch.dict(os.environ, env):
            status, body = self.post("/api/shell", {"cmd": "echo x", "cwd": "../.."})
        self.assertEqual((status, body["error"]), (403, "path_outside_root"))


class UnconfiguredServiceTest(ServerCase):
    """没配外部服务时必须**明确拒绝**，不能假装成功。"""

    def test_social_publish_without_napcat(self):
        status, body = self.post("/api/social/qzone/publish", {"content": "你好"})
        self.assertEqual(status, 503, body)
        self.assertEqual(body["error"]["code"], "social_not_configured")

    def test_social_publish_validates_before_dispatch(self):
        status, body = self.post("/api/social/qzone/publish", {"content": ""})
        self.assertEqual(status, 400, body)

    def test_onebot_webhook_without_napcat(self):
        status, body = self.post("/v1/gateway/onebot", {"post_type": "message"})
        self.assertEqual(status, 503)
        self.assertEqual(body["status"], "not_configured")

    def test_gateway_message_requires_content(self):
        status, body = self.post("/v1/gateway/messages", {"content": "  "})
        self.assertEqual((status, body["error"]), (400, "content_required"))

    def test_songs_submit_without_source(self):
        status, body = self.post("/v1/songs", {})
        self.assertIn(status, (400, 503), body)

    def test_song_job_lookup_unknown(self):
        status, body = self.get("/v1/songs/does-not-exist")
        self.assertIn(status, (404, 400), body)

    def test_voice_use_requires_profile(self):
        status, body = self.post("/v1/voices/use", {})
        self.assertEqual((status, body["code"]), (400, "profile_required"))

    def test_speech_with_empty_text_is_rejected(self):
        status, body = self.post("/v1/audio/speech", {"input": ""})
        self.assertIn(status, (400, 503), body)
        self.assertEqual(body["error"]["code"], "text_required")


if __name__ == "__main__":
    unittest.main()
