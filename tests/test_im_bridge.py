"""IM 网关桥接：Octop InboundMessage → AIJiYi 统一消息入口。"""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass, field
from typing import Any

from aiji.throat.bridge.octop_gateway import AijiGatewayClient, inbound_payload, make_processor


@dataclass
class FakeSubject:
    subject_id: str
    chat_type: str = "group"


@dataclass
class FakeInbound:
    channel_type: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    channel_subject: FakeSubject | None = None
    channel_session_id: str | None = None


class TestInboundMapping(unittest.TestCase):
    def test_group_message_keeps_sender_and_group_scope(self):
        message = FakeInbound(
            channel_type="qq",
            text="大肥鱼帮我记一下",
            metadata={"sender_id": "user-openid", "sender_name": "小明"},
            channel_subject=FakeSubject("group-openid"),
            channel_session_id="session-1",
        )
        payload = inbound_payload(message)
        self.assertEqual(payload["platform"], "qq")
        self.assertEqual(payload["platform_user_id"], "user-openid")
        self.assertEqual(payload["scope"], "group:group-openid")
        self.assertEqual(payload["session"], "session-1")
        self.assertEqual(payload["content"], "大肥鱼帮我记一下")

    def test_gateway_client_posts_unified_payload(self):
        seen = {}

        async def fake_post(url, *, json, headers):
            seen.update({"url": url, "json": json, "headers": headers})
            return {"text": "收到", "degraded": False}

        client = AijiGatewayClient(
            "http://127.0.0.1:8787", api_key="downstream", post=fake_post,
        )
        result = asyncio.run(client.chat({
            "platform": "qq",
            "platform_user_id": "u1",
            "scope": "default",
            "session": "s1",
            "content": "你好",
        }))
        self.assertEqual(result["text"], "收到")
        self.assertEqual(seen["url"], "http://127.0.0.1:8787/v1/gateway/messages")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer downstream")

    def test_octop_processor_emits_real_message_events(self):
        class Client:
            async def chat(self, payload):
                return {"text": "从 AIJiYi 返回", "degraded": False}

        async def collect():
            processor = make_processor(Client())
            message = FakeInbound(
                channel_type="qq", text="你好",
                metadata={"sender_id": "u1"},
                channel_subject=FakeSubject("g1"),
            )
            return [event async for event in processor(message)]

        events = asyncio.run(collect())
        self.assertEqual(events[0].content[0].text, "从 AIJiYi 返回")
        self.assertEqual(events[1].type.value, "completed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
