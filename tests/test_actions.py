"""执行层：工作区边界、危险命令、人工审批和工具循环。"""

from __future__ import annotations

import json
import shutil
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from aiji.left_hand.actions import ActionExecutor, ActionPolicy, ApprovalManager, ToolCall
from aiji.left_hand.actions.gui import parse_hotkey
from aiji.right_hand.config import Route, Settings
from aiji.brain.head import Head
from aiji.hippocampus.memory import Memory
from aiji.brain.pool import ModelPool
from aiji.hippocampus.store import Ident

TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp"
TMP_ROOT.mkdir(mode=0o777, exist_ok=True)


def workdir() -> Path:
    path = TMP_ROOT / f"actions-{uuid.uuid4().hex[:8]}"
    path.mkdir(mode=0o777)
    return path


class TestActionPolicy(unittest.TestCase):
    def setUp(self):
        self.root = workdir()
        (self.root / "note.txt").write_text("safe", encoding="utf-8")
        (self.root / ".env").write_text("SECRET=1", encoding="utf-8")
        self.executor = ActionExecutor(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_read_file_inside_workspace(self):
        result = self.executor.execute(ToolCall("read-1", "read_file", {
            "path": "note.txt",
        }))
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "safe")

    def test_path_escape_is_rejected(self):
        outside = self.root.parent / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        try:
            result = self.executor.execute(ToolCall("read-2", "read_file", {
                "path": "../outside.txt",
            }))
            self.assertFalse(result.ok)
            self.assertIn("outside workspace", result.error)
        finally:
            outside.unlink(missing_ok=True)

    def test_sensitive_file_is_rejected(self):
        result = self.executor.execute(ToolCall("read-3", "read_file", {
            "path": ".env",
        }))
        self.assertFalse(result.ok)
        self.assertIn("sensitive file", result.error)

    def test_dangerous_terminal_command_is_denied(self):
        policy = ActionPolicy(self.root)
        allowed, reason = policy.check_command("rm -rf /")
        self.assertFalse(allowed)
        self.assertIn("dangerous", reason)
        allowed, _ = policy.check_command("python -m pytest -q")
        self.assertTrue(allowed)

    def test_gui_hotkey_parser_rejects_unknown_keys(self):
        self.assertEqual(parse_hotkey("ctrl+shift+s"), [0x11, 0x10, 0x53])
        with self.assertRaises(ValueError):
            parse_hotkey("ctrl+launch_missiles")


class TestApprovalManager(unittest.TestCase):
    def test_request_blocks_until_approved(self):
        manager = ApprovalManager()
        result = []

        def worker():
            result.append(manager.request(
                kind="write_file",
                summary="write note.txt",
                arguments={"path": "note.txt"},
                timeout_s=3,
            ))

        thread = threading.Thread(target=worker)
        thread.start()
        deadline = time.monotonic() + 2
        pending = []
        while time.monotonic() < deadline:
            pending = manager.pending()
            if pending:
                break
            time.sleep(0.01)
        self.assertTrue(pending)
        self.assertTrue(manager.approve(pending[0]["id"]))
        thread.join(timeout=3)
        self.assertEqual(result, [True])


class SequencePool:
    _states = [object()]

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, *, tools=None, timeout_s=None):
        self.calls.append({"messages": [dict(item) for item in messages], "tools": tools})
        response = self.responses.pop(0)
        return SimpleNamespace(
            text=response.get("text", ""),
            tool_calls=response.get("tool_calls", []),
            route="fake",
            latency_ms=1.0,
        )

    def stats(self):
        return []


class TestFunctionCalling(unittest.TestCase):
    def setUp(self):
        self.root = workdir()
        (self.root / "note.txt").write_text("inside", encoding="utf-8")
        self.memory = Memory(Settings(
            db_path=self.root / "actions.db",
            routes=(), reflection_interval_s=60,
            debounce_ms=0, humanize_min_ms=0, humanize_max_ms=0,
            typing_min_ms=0, typing_max_ms=0,
        ), engines="none")

    def tearDown(self):
        self.memory.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_pool_parses_openai_tool_calls(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n))
                assert payload["tools"][0]["function"]["name"] == "read_file"
                data = json.dumps({
                    "choices": [{
                        "message": {
                            "content": None,
                            "tool_calls": [{
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": "{\"path\":\"note.txt\"}",
                                },
                            }],
                        },
                    }],
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            pool = ModelPool((Route(
                name="fake", base_url=f"http://127.0.0.1:{server.server_address[1]}",
                model="fake", api_key="k", timeout_s=3,
            ),))
            result = pool.chat(
                [{"role": "user", "content": "read it"}],
                tools=[{"type": "function", "function": {"name": "read_file"}}],
            )
            self.assertEqual(result.tool_calls[0].name, "read_file")
            self.assertEqual(result.tool_calls[0].arguments, {"path": "note.txt"})
        finally:
            server.shutdown()
            server.server_close()

    def test_upstream_without_tools_falls_back_to_plain_chat(self):
        class Handler(BaseHTTPRequestHandler):
            calls = 0

            def log_message(self, *args):
                return

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n))
                type(self).calls += 1
                if "tools" in payload:
                    self.send_response(400)
                    self.end_headers()
                    return
                data = json.dumps({
                    "choices": [{"message": {"content": "不支持工具也能聊天"}}],
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            pool = ModelPool((Route(
                name="fake", base_url=f"http://127.0.0.1:{server.server_address[1]}",
                model="fake", api_key="k", timeout_s=3,
            ),))
            result = pool.chat(
                [{"role": "user", "content": "hello"}],
                tools=[{"type": "function", "function": {"name": "read_file"}}],
            )
            self.assertEqual(result.text, "不支持工具也能聊天")
            self.assertEqual(Handler.calls, 2)
        finally:
            server.shutdown()
            server.server_close()

    def test_head_executes_read_tool_and_continues(self):
        call = ToolCall("call-read", "read_file", {"path": "note.txt"})
        self.memory.pool = SequencePool([
            {"tool_calls": [call]},
            {"text": "文件里写的是 inside", "tool_calls": []},
        ])
        self.memory.reflective.pool = self.memory.pool
        self.memory.action_executor = ActionExecutor(self.root)
        reply = Head(self.memory).reply(
            [{"role": "user", "content": "读 note.txt"}],
            Ident("desktop", "me", "default"),
            session="s1",
        )
        self.assertEqual(reply.text, "文件里写的是 inside")
        self.assertEqual(len(self.memory.pool.calls), 2)
        second = json.dumps(self.memory.pool.calls[1]["messages"], ensure_ascii=False)
        self.assertIn("inside", second)
        self.assertIn("call-read", second)

    def test_head_write_waits_for_approval_then_continues(self):
        call = ToolCall("call-write", "write_file", {
            "path": "created.txt", "content": "approved",
        })
        approvals = ApprovalManager()
        self.memory.pool = SequencePool([
            {"tool_calls": [call]},
            {"text": "文件已创建", "tool_calls": []},
        ])
        self.memory.reflective.pool = self.memory.pool
        self.memory.action_executor = ActionExecutor(
            self.root, approvals=approvals, approval_timeout_s=3,
        )
        result = []
        thread = threading.Thread(target=lambda: result.append(Head(self.memory).reply(
            [{"role": "user", "content": "创建 created.txt"}],
            Ident("desktop", "me", "default"),
            session="s1",
        )))
        thread.start()
        deadline = time.monotonic() + 2
        pending = []
        while time.monotonic() < deadline:
            pending = approvals.pending()
            if pending:
                break
            time.sleep(0.01)
        self.assertTrue(pending)
        self.assertEqual((self.root / "created.txt").exists(), False)
        approvals.approve(pending[0]["id"])
        thread.join(timeout=4)
        self.assertTrue(result)
        self.assertEqual(result[0].text, "文件已创建")
        self.assertEqual((self.root / "created.txt").read_text("utf-8"), "approved")


if __name__ == "__main__":
    unittest.main(verbosity=2)
