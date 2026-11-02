"""完整控制台 API：记忆、星图、人设、账号、调度、能力与 IM 网关入口。"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import unittest
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aiji.hippocampus import store
from aiji.right_hand.config import Settings
from aiji.hippocampus.memory import Memory
from aiji.throat.server import make_server
from aiji.hippocampus.store import FactDraft, Ident, add_fact

TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp"
TMP_ROOT.mkdir(mode=0o777, exist_ok=True)


def workdir() -> Path:
    path = TMP_ROOT / f"console-api-{uuid.uuid4().hex[:8]}"
    path.mkdir(mode=0o777)
    return path


class FakePool:
    _states = [object()]

    def __init__(self):
        self.calls: list[list[dict]] = []

    def chat(self, messages, *, timeout_s=None):
        self.calls.append(messages)
        return SimpleNamespace(text="网关回复", route="fake", latency_ms=1.0)

    def stats(self):
        return [{
            "name": "fake",
            "model": "fake-model",
            "calls": len(self.calls),
            "ok": len(self.calls),
            "failed": 0,
            "cooldown_s": 0,
        }]


class TestConsoleAPI(unittest.TestCase):
    def setUp(self):
        self.dir = workdir()
        self.memory = Memory(Settings(
            db_path=self.dir / "console.db",
            routes=(),
            reflection_interval_s=60,
            debounce_ms=0,
            humanize_min_ms=0,
            humanize_max_ms=0,
            typing_min_ms=0,
            typing_max_ms=0,
        ), engines="none")
        self.pool = FakePool()
        self.memory.pool = self.pool
        self.memory.reflective.pool = self.pool
        self.server = make_server(self.memory, "127.0.0.1", 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.wx = Ident("wechat", "u1", "personal")
        self.dy = Ident("douyin", "u1", "personal")
        self.env = mock.patch.dict(os.environ, {"AIJI_API_KEY": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.memory.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=10) as response:
            return json.loads(response.read())

    def post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read())

    def test_memory_library_and_graph_are_identity_scoped(self):
        with self.memory.db.tx() as conn:
            first, _ = add_fact(conn, self.wx, FactDraft(
                "user", "居住地", "杭州", "用户住在杭州", 0.95,
            ))
            second, _ = add_fact(conn, self.wx, FactDraft(
                "user", "职业", "后端工程师", "用户做后端开发", 0.9,
            ))
            add_fact(conn, self.dy, FactDraft(
                "user", "居住地", "成都", "用户住在成都", 0.95,
            ))
            conn.execute(
                "INSERT INTO fact_link(platform,platform_user_id,scope,src_id,dst_id,"
                "weight,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (self.wx.platform, self.wx.platform_user_id, self.wx.scope,
                 first, second, 0.8, "test", time.time()),
            )

        query = urllib.parse.urlencode({
            "platform": self.wx.platform,
            "platform_user_id": self.wx.platform_user_id,
            "scope": self.wx.scope,
        })
        memories = self.get("/api/memories?" + query)
        self.assertEqual({item["object"] for item in memories["items"]},
                         {"杭州", "后端工程师"})

        graph = self.get("/api/memory-graph?" + query)
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["edges"][0]["weight"], 0.8)

    def test_self_accounts_and_capabilities_are_real(self):
        with self.memory.db.tx() as conn:
            store.ensure_account(conn, self.wx, display_name="测试主人")
            store.upsert_trait(conn, self.wx, "语气", "说话简洁")

        query = urllib.parse.urlencode({
            "platform": self.wx.platform,
            "platform_user_id": self.wx.platform_user_id,
            "scope": self.wx.scope,
        })
        portrait = self.get("/api/self?" + query)
        self.assertIn("大肥鱼", portrait["floor"]["物种"])
        self.assertEqual(portrait["traits"][0]["value"], "说话简洁")

        accounts = self.get("/api/accounts")
        self.assertEqual(accounts["items"][0]["display_name"], "测试主人")

        capabilities = self.get("/api/capabilities")
        self.assertIn("ocr", capabilities)
        self.assertIn("asr", capabilities)
        self.assertIn("octop_gateway", capabilities)
        self.assertNotIn("api_key", capabilities["settings"]["routes"][0]
                         if capabilities["settings"]["routes"] else {})

    def test_scheduler_and_models_return_live_counts(self):
        with self.memory.db.tx() as conn:
            conn.execute(
                "INSERT INTO outbox(platform,platform_user_id,scope,kind,priority,payload,"
                "idem_key,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("qq", "u1", "group:1", "mention", 0, "{}", "console-api-1",
                 "pending", time.time(), time.time()),
            )
            conn.execute(
                "INSERT INTO send_queue(platform,platform_user_id,scope,kind,priority,payload,"
                "send_idem_key,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                ("qq", "u1", "group:1", "reply", 0, "{}", "send-console-api-1",
                 "pending", time.time()),
            )

        scheduler = self.get("/api/scheduler")
        self.assertEqual(scheduler["outbox"]["pending"], 1)
        self.assertEqual(scheduler["send_queue"]["pending"], 1)
        self.assertEqual(len(scheduler["recent_outbox"]), 1)

        models = self.get("/api/models")
        self.assertEqual(models["active"], 1)
        self.assertEqual(models["routes"][0]["route"], "fake")

    def test_unified_gateway_message_returns_reply(self):
        reply = self.post("/v1/gateway/messages", {
            "platform": "qq",
            "platform_user_id": "g1",
            "scope": "group:1",
            "session": "s1",
            "content": "去群里说句话",
            "debounce": False,
        })
        self.assertEqual(reply["text"], "网关回复")
        self.assertEqual(reply["platform"], "qq")
        self.assertEqual(self.memory.presence.snapshot()["platform"], "qq")

    def test_approval_api_releases_blocked_action(self):
        result = []
        thread = threading.Thread(target=lambda: result.append(
            self.memory.action_approvals.request(
                kind="write_file",
                summary="修改 demo.py",
                arguments={"path": "demo.py", "content": "print(1)"},
                timeout_s=3,
            )
        ))
        thread.start()
        deadline = time.monotonic() + 2
        pending = []
        while time.monotonic() < deadline:
            pending = self.get("/api/actions/pending")["items"]
            if pending:
                break
            time.sleep(0.01)
        self.assertTrue(pending)
        approved = self.post("/api/actions/approve", {"id": pending[0]["id"]})
        self.assertTrue(approved["approved"])
        thread.join(timeout=3)
        self.assertEqual(result, [True])

    def test_life_card_api_returns_hormones_and_context_budget(self):
        ident = {
            "platform": "desktop", "platform_user_id": "owner", "scope": "default",
        }
        data = self.get("/api/life?" + urllib.parse.urlencode(ident))
        self.assertEqual(set(data["snapshot"]["hormones"]),
                         {"sero", "dopa", "cort", "oxy"})
        self.assertGreaterEqual(data["context"]["weights"]["identity"], 0.15)
        self.assertEqual(len(data["context"]["budgets"]), 6)

    def test_onebot_webhook_enqueues_and_sends_reply(self):
        sent = []
        self.memory.onebot.configured = True
        self.memory.onebot.reply_fn = lambda event: "咕噜"
        self.memory.onebot.sender = lambda event, text: sent.append((event, text)) or True
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 999,
            "user_id": 42,
            "message_id": 901,
            "message": "在吗",
        }
        response = self.post("/v1/gateway/onebot", event)
        self.assertEqual(response["status"], "accepted")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not sent:
            time.sleep(0.01)
        self.assertEqual(sent[0][1], "咕噜")


if __name__ == "__main__":
    unittest.main(verbosity=2)
