"""Octop 桥接（`throat/bridge/octop_gateway.py`）——原先 53% 覆盖。

桥接层最容易出的错是**身份/范围串号**（群聊混进私聊）和"平台消息没字时直接崩"。
这里不装 octop_gateway 包：给 `make_processor` 注入一个假 MessageEvent 模块，
再把 `client.chat` 换成假实现，验：
- 私聊/群聊 scope 与 platform_user_id 的取法
- 只有媒体没有文字时的兜底话术
- 网关返回空/异常时的兜底话术（不能把异常抛给平台）
- Authorization 头只在配了 api_key 时出现
"""
import asyncio
import pathlib
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aiji.throat.bridge import octop_gateway as og  # noqa: E402


class Subject:
    def __init__(self, subject_id="", chat_type=""):
        self.subject_id = subject_id
        self.chat_type = chat_type


class Message:
    def __init__(self, text="", *, metadata=None, subject=None,
                 session="", channel="qq", has_media=False):
        self.text = text
        self.metadata = metadata or {}
        self.channel_subject = subject
        self.channel_session_id = session
        self.channel_type = channel
        self.has_media = has_media


class InboundPayloadTest(unittest.TestCase):
    def test_private_chat_scope_is_default(self):
        payload = og.inbound_payload(Message("你好", metadata={"sender_id": "u1"}))
        self.assertEqual(payload["scope"], "default")
        self.assertEqual(payload["platform_user_id"], "u1")
        self.assertEqual(payload["content"], "你好")
        self.assertTrue(payload["debounce"])

    def test_group_chat_scope_carries_subject(self):
        payload = og.inbound_payload(Message(
            "在吗", metadata={"sender_id": "u2"},
            subject=Subject("g-42", "group")))
        self.assertEqual(payload["scope"], "group:g-42", "群聊必须带 subject，否则串号")
        self.assertEqual(payload["platform_user_id"], "u2")
        self.assertEqual(payload["session"], "g-42")

    def test_chat_type_can_come_from_metadata(self):
        payload = og.inbound_payload(Message(
            "hi", metadata={"sender_id": "u3", "chat_type": "Guild"},
            subject=Subject("c-7")))
        self.assertEqual(payload["scope"], "group:c-7", "chat_type 大小写不敏感")

    def test_sender_falls_back_to_openid_then_subject(self):
        payload = og.inbound_payload(Message(
            "hi", metadata={"user_openid": "open-1"}, subject=Subject("s-1")))
        self.assertEqual(payload["platform_user_id"], "open-1")
        payload = og.inbound_payload(Message("hi", subject=Subject("s-2")))
        self.assertEqual(payload["platform_user_id"], "s-2")
        payload = og.inbound_payload(Message("hi"))
        self.assertEqual(payload["platform_user_id"], "unknown")

    def test_media_only_message_gets_placeholder_text(self):
        payload = og.inbound_payload(Message("", has_media=True))
        self.assertEqual(payload["content"], "[用户发送了媒体消息]")

    def test_whitespace_text_is_stripped(self):
        self.assertEqual(og.inbound_payload(Message("   你好  "))["content"], "你好")


class FakeMessageEvent:
    def __init__(self, kind, text=""):
        self.kind = kind
        self.text_value = text

    @classmethod
    def text(cls, value):
        return cls("text", value)

    @classmethod
    def completed(cls):
        return cls("completed")


def fake_octop_module():
    return types.SimpleNamespace(MessageEvent=FakeMessageEvent)


async def collect(gen):
    return [item async for item in gen]


class ProcessorTest(unittest.TestCase):
    def _run(self, message, chat_result=None, raise_exc=None):
        client = og.AijiGatewayClient()

        async def chat(payload):  # noqa: ARG001
            if raise_exc:
                raise raise_exc
            return chat_result or {}

        client.chat = chat
        processor = og.make_processor(client)
        with mock.patch.dict(sys.modules,
                             {"octop_gateway": fake_octop_module()}):
            return asyncio.run(collect(processor(message)))

    def test_normal_reply_is_forwarded(self):
        events = self._run(Message("你好"), chat_result={"text": "在呀"})
        self.assertEqual([e.kind for e in events], ["text", "completed"])
        self.assertEqual(events[0].text_value, "在呀")

    def test_empty_reply_uses_placeholder(self):
        events = self._run(Message("你好"), chat_result={"text": "   "})
        self.assertIn("没接上话", events[0].text_value)

    def test_gateway_error_is_soft(self):
        events = self._run(Message("你好"), raise_exc=OSError("连不上"))
        self.assertIn("没连上", events[0].text_value)
        self.assertIn("OSError", events[0].text_value)
        self.assertEqual(events[-1].kind, "completed")

    def test_message_without_text_never_calls_gateway(self):
        client = og.AijiGatewayClient()
        called = []

        async def chat(payload):
            called.append(payload)
            return {"text": "不该到这里"}

        client.chat = chat
        processor = og.make_processor(client)
        with mock.patch.dict(sys.modules,
                             {"octop_gateway": fake_octop_module()}):
            events = asyncio.run(collect(processor(Message(""))))
        self.assertEqual(called, [])
        self.assertIn("没有可识别的文字", events[0].text_value)


class ChatClientTest(unittest.TestCase):
    def test_authorization_header_only_when_key_present(self):
        seen = {}

        async def post(url, *, json, headers):  # noqa: A002
            seen["url"] = url
            seen["headers"] = headers
            seen["json"] = json
            return {"text": "ok"}

        client = og.AijiGatewayClient("http://127.0.0.1:8787/", post=post)
        asyncio.run(client.chat({"content": "hi"}))
        self.assertEqual(seen["url"], "http://127.0.0.1:8787/v1/gateway/messages")
        self.assertNotIn("Authorization", seen["headers"])

        client = og.AijiGatewayClient("http://x", api_key="k1", post=post)
        asyncio.run(client.chat({"content": "hi"}))
        self.assertEqual(seen["headers"]["Authorization"], "Bearer k1")

    def test_base_url_trailing_slash_is_normalised(self):
        self.assertEqual(og.AijiGatewayClient("http://x///").base_url, "http://x")


class RunQqGuardTest(unittest.TestCase):
    def test_missing_credentials_exits_with_message(self):
        with mock.patch.dict("os.environ", {"QQ_APP_ID": "", "QQ_SECRET": ""},
                             clear=False):
            with self.assertRaises(SystemExit) as ctx:
                asyncio.run(og.run_qq(og.AijiGatewayClient()))
        self.assertIn("QQ_APP_ID", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
