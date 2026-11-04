"""HTTP 管道层：CORS 预检、静态/原始字节发送、401 收尾、错误码映射。

这些是**每个响应都要走一遍**的代码（`end_headers` / `_send_bytes` / `_unauthorized`），
覆盖率低不是因为难，而是因为"正常路径看起来对就不会有人去看"。
但它们的错法都很阴：
- CORS 头漏了 → 控制台从 file:// 打开时每个请求都失败，浏览器只给一句 console 报错
- `_send_bytes` 忘了 Content-Length → 客户端挂到超时
- 401 不读空请求体就关连接 → 客户端拿到 RST（WinError 10053）而不是 401
- `/api/fs/raw` 是**按字节**返回工作区文件的路由，越界拦截写错就等于把整机文件读出去
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.hippocampus.memory import Memory  # noqa: E402
from aiji.right_hand.config import Settings  # noqa: E402
from aiji.throat import server as server_mod  # noqa: E402
from aiji.throat.server import make_server  # noqa: E402


class PlumbingCase(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(make_tmp(self, "plumbing"))
        env = mock.patch.dict(os.environ, {"AIJI_MCP_RESEARCH_LOOP": "0",
                                           "AIJI_LIFE_LOOP": "0"}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.memory = Memory(Settings(
            db_path=self.root / "plumbing.db",
            routes=(),
            action_root=str(self.workspace),
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

    def call(self, method: str, path: str, body: dict | None = None,
             headers: dict | None = None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=headers or {})
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()


class CorsTest(PlumbingCase):
    ALLOWED = "http://127.0.0.1:8787"

    def test_preflight_from_allowed_origin(self):
        status, headers, body = self.call("OPTIONS", "/api/memories",
                                          headers={"Origin": self.ALLOWED})
        self.assertEqual(status, 204)
        self.assertEqual(body, b"")
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), self.ALLOWED)
        self.assertEqual(headers.get("Vary"), "Origin")
        self.assertIn("POST", headers.get("Access-Control-Allow-Methods", ""))
        self.assertIn("x-aiji-api-key", headers.get("Access-Control-Allow-Headers", ""))

    def test_preflight_from_null_origin_means_file_url(self):
        status, headers, _ = self.call("OPTIONS", "/api/memories",
                                       headers={"Origin": "null"})
        self.assertEqual(status, 204, "控制台从 file:// 打开时 Origin 是字符串 null")
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "null")

    def test_preflight_from_foreign_origin_is_rejected(self):
        status, headers, _ = self.call("OPTIONS", "/api/memories",
                                       headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"),
                          "外来源一个 CORS 头都不能放")

    def test_plain_get_without_origin_has_no_cors_headers(self):
        status, headers, _ = self.call("GET", "/health")
        self.assertEqual(status, 200)
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_plain_get_with_allowed_origin_gets_cors_headers(self):
        status, headers, _ = self.call("GET", "/health", headers={"Origin": self.ALLOWED})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), self.ALLOWED)
        self.assertEqual(headers.get("Vary"), "Origin")


class RawFileTest(PlumbingCase):
    def setUp(self):
        super().setUp()
        (self.workspace / "hello.txt").write_text("工作区文本", encoding="utf-8")
        (self.workspace / "pixel.bin").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    def test_raw_needs_workspace_enabled(self):
        status, _, body = self.call("GET", "/api/fs/raw?path=hello.txt")
        self.assertEqual(status, 403)
        self.assertIn("workspace_disabled", body.decode("utf-8"))

    def test_raw_returns_bytes_with_type_and_no_store(self):
        with mock.patch.dict(os.environ, {"AIJI_ENABLE_WORKSPACE": "1"}):
            status, headers, body = self.call("GET", "/api/fs/raw?path=hello.txt")
            self.assertEqual(status, 200)
            self.assertEqual(body, "工作区文本".encode("utf-8"))
            self.assertTrue(headers.get("Content-Type", "").startswith("text/plain"))
            self.assertEqual(headers.get("Cache-Control"), "no-store",
                             "工作区文件会被改，不许让浏览器缓存")
            self.assertEqual(int(headers["Content-Length"]), len(body))

    def test_raw_binary_file(self):
        with mock.patch.dict(os.environ, {"AIJI_ENABLE_WORKSPACE": "1"}):
            status, headers, body = self.call("GET", "/api/fs/raw?path=pixel.bin")
        self.assertEqual(status, 200)
        self.assertTrue(headers.get("Content-Type", "").startswith("application/octet-stream"))
        self.assertTrue(body.startswith(b"\x89PNG"))

    def test_raw_missing_file_is_404(self):
        with mock.patch.dict(os.environ, {"AIJI_ENABLE_WORKSPACE": "1"}):
            status, _, body = self.call("GET", "/api/fs/raw?path=nope.txt")
        self.assertEqual(status, 404)
        self.assertIn("not_found", body.decode("utf-8"))

    def test_raw_outside_root_is_403(self):
        with mock.patch.dict(os.environ, {"AIJI_ENABLE_WORKSPACE": "1"}):
            for evil in ("../plumbing.db", "..\\..\\Windows\\win.ini", "C:/Windows/win.ini"):
                with self.subTest(path=evil):
                    status, _, body = self.call("GET", f"/api/fs/raw?path={evil}")
                    self.assertEqual(status, 403, body[:120])
                    self.assertIn("path_outside_root", body.decode("utf-8"))

    def test_read_reports_binary_and_truncation(self):
        big = self.workspace / "big.txt"
        big.write_text("x" * (2 * 1024 * 1024 + 10), encoding="utf-8")
        with mock.patch.dict(os.environ, {"AIJI_ENABLE_WORKSPACE": "1"}):
            status, _, body = self.call("GET", "/api/fs/read?path=big.txt")
            payload = json.loads(body)
            self.assertEqual(status, 200)
            self.assertTrue(payload["truncated"], "超过 2MB 要标 truncated")
            self.assertEqual(len(payload["text"]), 2 * 1024 * 1024)

            status, _, body = self.call("GET", "/api/fs/read?path=pixel.bin")
            payload = json.loads(body)
            self.assertTrue(payload["binary"], "含 NUL 的当二进制，不回文本")

    def test_directory_listing_marks_kind_and_skips_broken_stat(self):
        (self.workspace / "sub").mkdir()
        broken = self.workspace / "ghost.txt"
        broken.write_text("x", encoding="utf-8")
        with mock.patch.dict(os.environ, {"AIJI_ENABLE_WORKSPACE": "1"}):
            broken.unlink()  # 列目录前消失 → stat 抛 OSError 的那一支
            status, _, body = self.call("GET", "/api/fs")
        payload = json.loads(body)
        names = {e["name"]: e["kind"] for e in payload["entries"]}
        self.assertEqual(status, 200)
        self.assertEqual(names.get("sub"), "dir")
        self.assertEqual(names.get("hello.txt"), "file")
        self.assertNotIn("ghost.txt", names)


class UnauthorizedTest(PlumbingCase):
    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(os.environ, {"AIJI_API_KEY": "secret"}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_wrong_token_gets_full_401_not_a_reset(self):
        status, headers, body = self.call("GET", "/api/memories",
                                         headers={"Authorization": "Bearer nope"})
        payload = json.loads(body)
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "invalid_api_key")
        self.assertEqual(headers.get("WWW-Authenticate"), "Bearer")
        self.assertEqual(headers.get("Connection"), "close")
        self.assertEqual(int(headers["Content-Length"]), len(body))

    def test_post_with_body_is_drained_before_401(self):
        # 带 body 的请求如果不等 body 读完就关，客户端会拿到 RST 而不是 401
        status, _, body = self.call("POST", "/turns",
                                    body={"content": "x" * 2000},
                                    headers={"Authorization": "Bearer nope"})
        self.assertEqual(status, 401)
        self.assertIn("invalid_api_key", body.decode("utf-8"))

    def test_x_aiji_api_key_header_also_works(self):
        status, _, _ = self.call("GET", "/api/memories",
                                 headers={"X-Aiji-Api-Key": "secret"})
        self.assertEqual(status, 200)


class StickerErrorTest(PlumbingCase):
    class Boom(Exception):
        def __init__(self, code="sticker_unavailable", status=503):
            super().__init__("下载失败")
            self.code = code
            self.status = status

    def test_import_error_maps_code_and_status(self):
        with mock.patch.object(self.memory.stickers, "import_image",
                               side_effect=self.Boom()):
            status, _, body = self.call("POST", "/api/stickers/import",
                                        body={"url": "https://example.invalid/a.png"})
        payload = json.loads(body)
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "sticker_unavailable")
        self.assertIn("下载失败", payload["error"]["message"])

    def test_import_error_without_code_defaults_to_400(self):
        with mock.patch.object(self.memory.stickers, "import_image",
                               side_effect=RuntimeError("坏图")):
            status, _, body = self.call("POST", "/api/stickers/import",
                                        body={"url": "https://example.invalid/a.png"})
        payload = json.loads(body)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "sticker_error")
        self.assertEqual(payload["error"]["message"], "坏图")

    def test_sync_update_error_is_mapped_too(self):
        with mock.patch.object(self.memory.stickers, "update",
                               side_effect=self.Boom(code="feed_unreachable", status=502)):
            status, _, body = self.call("POST", "/api/stickers/update?wait=1")
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(body)["error"]["code"], "feed_unreachable")


class StreamTest(PlumbingCase):
    def _chat(self, stream: bool):
        payload = {"model": "aiji", "stream": stream,
                   "messages": [{"role": "user", "content": "你好"}]}
        return self.call("POST", "/v1/chat/completions", body=payload)

    def test_stream_emits_role_content_finish_and_done(self):
        status, headers, body = self._chat(True)
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", headers.get("Content-Type", ""))
        self.assertEqual(headers.get("Cache-Control"), "no-cache")
        events = [chunk for chunk in body.decode("utf-8").split("\n\n") if chunk.strip()]
        self.assertTrue(all(e.startswith("data: ") for e in events))
        self.assertEqual(events[-1].strip(), "data: [DONE]")
        deltas = [json.loads(e[len("data: "):]) for e in events[:-1]]
        self.assertEqual(deltas[0]["choices"][0]["delta"]["role"], "assistant")
        self.assertEqual(deltas[-1]["choices"][0]["finish_reason"], "stop")
        self.assertIn("aiji", deltas[-1], "最后一帧要带 aiji 观测字段")
        self.assertEqual({d["object"] for d in deltas}, {"chat.completion.chunk"})
        self.assertEqual(len({d["id"] for d in deltas}), 1, "同一个 completion id")

    def test_non_stream_reply_has_usage_and_choices(self):
        status, _, body = self._chat(False)
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["object"], "chat.completion")
        self.assertIn("usage", payload)
        self.assertEqual(payload["choices"][0]["message"]["role"], "assistant")


class ShellTest(PlumbingCase):
    """工作区终端的三重开关之后：超时、坏参数、cwd 回退、超大输出截断。

    这段代码最容易"看起来对但其实没兜住"：命令卡住不返回、timeout 传了个字符串、
    cwd 指向一个已经不是目录的路径——任何一条没兜住，控制台就是"点了没反应"。
    """

    ENV = {"AIJI_ENABLE_WORKSPACE": "1", "AIJI_CONSOLE_SHELL": "1",
           "AIJI_UNSAFE_SHELL": "1"}

    def shell(self, **body):
        with mock.patch.dict(os.environ, self.ENV):
            return self.call("POST", "/api/shell", body=body)

    def test_disabled_layers(self):
        status, _, body = self.call("POST", "/api/shell", body={"cmd": "echo x"})
        self.assertEqual(status, 403)
        self.assertIn("workspace_disabled", body.decode("utf-8"))
        with mock.patch.dict(os.environ, {"AIJI_ENABLE_WORKSPACE": "1",
                                          "AIJI_CONSOLE_SHELL": "0"}, clear=False):
            status, _, body = self.call("POST", "/api/shell", body={"cmd": "echo x"})
            self.assertEqual(status, 403)
            self.assertIn("shell_disabled", body.decode("utf-8"))
        with mock.patch.dict(os.environ, {"AIJI_ENABLE_WORKSPACE": "1",
                                          "AIJI_CONSOLE_SHELL": "1",
                                          "AIJI_UNSAFE_SHELL": "0"}, clear=False):
            status, _, body = self.call("POST", "/api/shell", body={"cmd": "echo x"})
            self.assertEqual(status, 403)
            self.assertIn("unsafe_shell_disabled", body.decode("utf-8"))

    def test_empty_command_is_400(self):
        status, _, body = self.shell(cmd="   ")
        self.assertEqual(status, 400)
        self.assertIn("cmd_required", body.decode("utf-8"))

    def test_cwd_outside_root_is_403(self):
        status, _, body = self.shell(cmd="echo x", cwd="../../..")
        self.assertEqual(status, 403)
        self.assertIn("path_outside_root", body.decode("utf-8"))

    def test_cwd_that_is_not_a_directory_falls_back_to_root(self):
        status, _, body = self.shell(cmd="echo fallback-ok", cwd="hello.txt")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn("fallback-ok", payload["stdout"])
        self.assertEqual(payload["code"], 0)

    def test_bad_timeout_value_falls_back(self):
        status, _, body = self.shell(cmd="echo ok", timeout="立刻")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn("ok", payload["stdout"])

    def test_command_timeout_is_reported_not_raised(self):
        status, _, body = self.shell(cmd="ping -n 5 127.0.0.1", timeout=1)
        payload = json.loads(body)
        self.assertEqual(status, 200, "超时也要回 200 + 结构化结果")
        self.assertTrue(payload["timed_out"])
        self.assertEqual(payload["stderr"], "timeout")
        self.assertEqual(payload["code"], -1)

    def test_large_output_is_truncated(self):
        status, _, body = self.shell(
            cmd="powershell -NoProfile -Command \"1..40000 | %{ 'x' * 10 }\"",
            timeout=30)
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertLessEqual(len(payload["stdout"]), 200_000)

    def test_failing_command_reports_exit_code(self):
        status, _, body = self.shell(cmd="exit 3")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["code"], 3)


class HelperTest(unittest.TestCase):
    def test_decode_console_prefers_utf8_then_gb18030(self):
        from aiji.throat.server import _decode_console

        self.assertEqual(_decode_console(b""), "")
        self.assertEqual(_decode_console("中文".encode("utf-8")), "中文")
        self.assertEqual(_decode_console("中文".encode("gb18030")), "中文")

    def test_decode_console_never_raises(self):
        from aiji.throat.server import _decode_console

        self.assertIsInstance(_decode_console(b"\xff\xfe\x80"), str)

    def test_query_int_validates(self):
        from aiji.throat.server import _query_int

        self.assertEqual(_query_int({"n": "7"}, "n", 3), 7)
        self.assertEqual(_query_int({}, "n", 3), 3)
        self.assertEqual(_query_int({"n": "999"}, "n", 3, high=50), 50)
        self.assertEqual(_query_int({"n": "-9"}, "n", 3, low=0), 0)
        with self.assertRaises(ValueError) as ctx:
            _query_int({"n": "abc"}, "n", 3)
        self.assertIn("must be an integer", str(ctx.exception))


class StickerBackgroundJobTest(PlumbingCase):
    def test_async_update_records_error_instead_of_swallowing_it(self):
        boom = RuntimeError("网络挂了")
        with mock.patch.object(self.memory.stickers, "update", side_effect=boom):
            status, _, body = self.call("POST", "/api/stickers/update")
            payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["started"], "默认后台跑，立刻返回")
        for _ in range(100):
            job = getattr(self.server, "sticker_job", None) or {}
            if not job.get("running"):
                break
            time.sleep(0.05)
        job = getattr(self.server, "sticker_job", None) or {}
        self.assertFalse(job.get("running"))
        self.assertIn("RuntimeError", str(job.get("error")),
                      "后台任务失败要记进 sticker_job，不能只写日志")

    def test_second_call_while_running_reports_running(self):
        started = threading.Event()

        def slow_update():
            started.set()
            time.sleep(0.5)
            return {"count": 0}

        with mock.patch.object(self.memory.stickers, "update", side_effect=slow_update):
            status, _, body = self.call("POST", "/api/stickers/update")
            self.assertTrue(json.loads(body)["started"])
            started.wait(2)
            status, _, body = self.call("POST", "/api/stickers/update")
            payload = json.loads(body)
        self.assertFalse(payload["started"], "正在跑的时候不许再开一个")
        self.assertTrue(payload["running"])


class DeadCodeTest(unittest.TestCase):
    def test_send_asset_helper_is_not_needed(self):
        # `_send_asset` 曾经没有任何调用方（静态资产早就改成按路由现读），
        # 它连同 cache=86400 的语义一起被删掉了。这里守着"别又长回来"。
        self.assertFalse(hasattr(server_mod._Handler, "_send_asset"))


if __name__ == "__main__":
    unittest.main()
