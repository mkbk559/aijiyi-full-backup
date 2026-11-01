"""NapCat / OneBot 11 个人 QQ 桥接测试。"""

from __future__ import annotations

import threading
import time
import unittest
import tempfile
from pathlib import Path
from unittest import mock

from aiji import Memory
from aiji.bridge.onebot import OneBotBridge, OneBotClient, parse_event
from aiji.config import Settings


class TestOneBotParse(unittest.TestCase):
    def test_group_at_message_is_normalized(self):
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 999,
            "group_id": 123,
            "user_id": 42,
            "message_id": 77,
            "sender": {"nickname": "小明"},
            "message": [
                {"type": "at", "data": {"qq": "999"}},
                {"type": "text", "data": {"text": " 大肥鱼你好"}},
            ],
        }
        parsed = parse_event(event, group_mode="mention")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["content"], "大肥鱼你好")
        self.assertEqual(parsed["platform_user_id"], "42")
        self.assertEqual(parsed["scope"], "group:123")
        self.assertEqual(parsed["session"], "group:123")
        self.assertEqual(parsed["sender_name"], "小明")
        self.assertTrue(parsed["mentioned"])

    def test_group_message_without_mention_is_ignored_by_default(self):
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 999,
            "group_id": 123,
            "user_id": 42,
            "message_id": 78,
            "message": [{"type": "text", "data": {"text": "普通聊天"}}],
        }
        self.assertIsNone(parse_event(event, group_mode="mention"))
        parsed = parse_event(event, group_mode="all")
        self.assertEqual(parsed["content"], "普通聊天")

    def test_group_message_without_mention_can_be_marked_for_ingest(self):
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 999,
            "group_id": 123,
            "user_id": 42,
            "message_id": 781,
            "message": [{"type": "text", "data": {"text": "只入库"}}],
        }
        parsed = parse_event(event, group_mode="mention", ingest_unmentioned=True)
        self.assertIsNotNone(parsed)
        self.assertFalse(parsed["mentioned"])
        self.assertFalse(parsed["should_reply"])
        self.assertEqual(parsed["content"], "只入库")

    def test_private_message_is_normalized(self):
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 999,
            "user_id": 42,
            "message_id": 79,
            "message": "私聊消息",
        }
        parsed = parse_event(event)
        self.assertEqual(parsed["scope"], "default")
        self.assertEqual(parsed["session"], "private:42")
        self.assertEqual(parsed["content"], "私聊消息")


class TestOneBotBridge(unittest.TestCase):
    def test_memory_onebot_reply_uses_durable_heart_queues(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = Memory(Settings(
                db_path=Path(tmp) / "onebot.db",
                routes=(),
                onebot_api="http://127.0.0.1:3000",
                onebot_group_mode="all",
                reflection_interval_s=60,
                debounce_ms=0,
                humanize_min_ms=0,
                humanize_max_ms=0,
                typing_min_ms=0,
                typing_max_ms=0,
            ), engines="none")
            sent = []

            class Pool:
                _states = [object()]

                @staticmethod
                def chat(messages, **kwargs):
                    return type("Result", (), {
                        "text": "在呢", "route": "fake", "latency_ms": 1.0,
                        "tool_calls": [],
                    })()

            memory.pool = Pool()
            memory.onebot_client._post = (
                lambda url, payload, headers:
                sent.append((url, payload)) or True
            )
            event = {
                "post_type": "message",
                "message_type": "private",
                "self_id": 999,
                "user_id": 42,
                "message_id": 8080,
                "message": "在吗",
            }
            try:
                self.assertEqual(memory.onebot.submit(event)["status"], "accepted")
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not sent:
                    time.sleep(0.02)
                self.assertTrue(sent)
                outbox = memory.db.read("SELECT status FROM outbox")
                send_queue = memory.db.read("SELECT status FROM send_queue")
                self.assertTrue(outbox, "reply bypassed durable outbox")
                self.assertTrue(send_queue, "reply bypassed durable send queue")
                self.assertEqual(outbox[0]["status"], "done")
                self.assertEqual(send_queue[0]["status"], "sent")
            finally:
                memory.close()

    def test_worker_replies_through_sender(self):
        sent = []
        bridge = OneBotBridge(
            reply_fn=lambda event: "咕噜",
            sender=lambda event, text: sent.append((event["message_id"], text)) or True,
            group_mode="all",
            dedupe_ttl_s=10,
        ).start()
        event = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 999,
            "user_id": 42,
            "message_id": 80,
            "message": "在吗",
        }
        try:
            status = bridge.submit(event)
            self.assertEqual(status["status"], "accepted")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not sent:
                time.sleep(0.01)
            self.assertEqual(sent, [(80, "咕噜")])
            self.assertEqual(bridge.submit(event)["status"], "duplicate")
        finally:
            bridge.stop()

    def test_client_builds_group_send_payload(self):
        seen = {}

        def fake_post(url, payload, headers):
            seen.update({"url": url, "payload": payload, "headers": headers})
            return True

        client = OneBotClient("http://127.0.0.1:3000", token="secret", post=fake_post)
        ok = client.send({
            "message_type": "group", "group_id": 123, "user_id": 42,
        }, "你好")
        self.assertTrue(ok)
        self.assertTrue(seen["url"].endswith("/send_group_msg"))
        self.assertEqual(seen["payload"]["group_id"], 123)
        self.assertEqual(seen["payload"]["message"][0]["data"]["text"], "你好")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer secret")

    def test_client_calls_extended_action_and_returns_data(self):
        seen = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return (
                    b'{"status":"ok","retcode":0,'
                    b'"data":{"tid":"post-123","time":1700000000}}'
                )

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            seen["method"] = request.get_method()
            seen["body"] = request.data
            seen["timeout"] = timeout
            return Response()

        client = OneBotClient(
            "http://127.0.0.1:3000",
            token="secret",
            timeout_s=9,
        )
        with mock.patch(
            "aiji.bridge.onebot.urllib.request.urlopen",
            fake_urlopen,
        ):
            data = client.call(
                "send_qzone_msg",
                {"content": "晚霞", "images": []},
            )

        self.assertEqual(
            seen["url"],
            "http://127.0.0.1:3000/send_qzone_msg",
        )
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["timeout"], 9.0)
        self.assertIn('"content": "晚霞"'.encode("utf-8"), seen["body"])
        self.assertEqual(data, {"tid": "post-123", "time": 1700000000})

    def test_unmentioned_group_message_is_ingested_without_reply(self):
        ingested = []
        sent = []
        bridge = OneBotBridge(
            reply_fn=lambda event: "不该回复",
            sender=lambda event, text: sent.append(text) or True,
            group_mode="mention",
            ingest_fn=lambda event: ingested.append(event),
            dedupe_ttl_s=10,
        ).start()
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 999,
            "group_id": 123,
            "user_id": 42,
            "message_id": 81,
            "message": [{"type": "text", "data": {"text": "群聊背景"}}],
        }
        try:
            self.assertEqual(bridge.submit(event)["status"], "accepted")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not ingested:
                time.sleep(0.01)
            self.assertEqual(len(ingested), 1)
            self.assertEqual(ingested[0]["content"], "群聊背景")
            self.assertEqual(sent, [])
        finally:
            bridge.stop()

    def test_queue_rejection_allows_same_message_to_retry(self):
        bridge = OneBotBridge(
            reply_fn=lambda event: "",
            sender=lambda event, text: True,
            group_mode="all",
            dedupe_ttl_s=10,
        )
        base = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 999,
            "user_id": 42,
            "message": "填队列",
        }
        for message_id in range(1, 1001):
            event = {**base, "message_id": message_id}
            self.assertEqual(bridge.submit(event)["status"], "accepted")

        rejected = {**base, "message_id": 2001}
        self.assertEqual(bridge.submit(rejected)["status"], "rejected")
        bridge._queue.get_nowait()
        self.assertEqual(bridge.submit(rejected)["status"], "accepted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
