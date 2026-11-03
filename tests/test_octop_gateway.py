"""Octop Gateway 桥接：归一化、aiohttp 调用、processor 的兜底文案、CLI 入口。

这条链路是所有非 OneBot 平台（QQ 官方机器人/飞书/微信）唯一的入口，
`octop_gateway` 这个第三方包在本机没装，所以这里用假模块注入 sys.modules，
把"包装层自己"的行为全部测到（真协议由 Octop 负责，不属于本项目）。
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import pathlib
import runpy
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aiji.throat.bridge.octop_gateway import (  # noqa: E402
    AijiGatewayClient, inbound_payload, main, make_processor, run_qq,
)


class Subject:
    def __init__(self, subject_id="", chat_type=""):
        self.subject_id = subject_id
        self.chat_type = chat_type


class Message:
    def __init__(self, **kwargs):
        self.metadata = kwargs.get("metadata", {})
        self.channel_subject = kwargs.get("channel_subject")
        self.channel_session_id = kwargs.get("channel_session_id", "")
        self.channel_type = kwargs.get("channel_type", "qq")
        self.text = kwargs.get("text", "")
        self.has_media = kwargs.get("has_media", False)


# ---- 假的 octop_gateway 包 ----
class MessageEventFake:
    emitted: list[tuple[str, str]] = []

    @classmethod
    def text(cls, value):
        cls.emitted.append(("text", value))
        return ("text", value)

    @classmethod
    def completed(cls):
        cls.emitted.append(("completed", ""))
        return ("completed", "")


class FakeChannelManager:
    instances: list["FakeChannelManager"] = []

    def __init__(self, *, processor, workers_per_channel, media_backend):
        self.processor = processor
        self.workers = workers_per_channel
        self.media = media_backend
        self.started = False
        self.stopped = False
        self.added: list[object] = []
        FakeChannelManager.instances.append(self)

    async def start(self):
        self.started = True

    async def add_qq_channel(self, config):
        self.added.append(config)
        return "channel-1"

    async def stop(self):
        self.stopped = True


def fake_octop_modules() -> dict[str, types.ModuleType]:
    """把 octop_gateway 造成**包**（`from octop_gateway.channels.qq import …` 才行）。"""
    module = types.ModuleType("octop_gateway")
    module.__path__ = []                     # 声明成 package
    module.MessageEvent = MessageEventFake
    module.ChannelManager = FakeChannelManager
    module.FileSystemMediaBackend = lambda path: ("media", path)
    channels = types.ModuleType("octop_gateway.channels")
    channels.__path__ = []
    qq = types.ModuleType("octop_gateway.channels.qq")
    qq.QQConfig = lambda **kwargs: ("qqconfig", kwargs)
    module.channels = channels
    channels.qq = qq
    return {"octop_gateway": module,
            "octop_gateway.channels": channels,
            "octop_gateway.channels.qq": qq}


class InboundPayloadTest(unittest.TestCase):
    def test_group_scopes_by_subject(self):
        payload = inbound_payload(Message(
            channel_subject=Subject("g-9", "group"),
            metadata={"sender_id": "u1"}, text="  你好  "))
        self.assertEqual(payload["scope"], "group:g-9")
        self.assertEqual(payload["content"], "你好")
        self.assertTrue(payload["debounce"])
        self.assertEqual((payload["platform"], payload["platform_user_id"]),
                         ("qq", "u1"))

    def test_guild_and_channel_count_as_group(self):
        for chat_type in ("group", "guild", "channel"):
            payload = inbound_payload(Message(
                channel_subject=Subject("s1", chat_type), text="hi"))
            self.assertEqual(payload["scope"], "group:s1", chat_type)

    def test_private_scope_is_default(self):
        payload = inbound_payload(Message(
            channel_subject=Subject("s1", "private"), text="hi"))
        self.assertEqual(payload["scope"], "default")

    def test_sender_fallbacks(self):
        self.assertEqual(
            inbound_payload(Message(metadata={"user_openid": "o1"}, text="x"))
            ["platform_user_id"], "o1")
        self.assertEqual(
            inbound_payload(Message(channel_subject=Subject("sid"), text="x"))
            ["platform_user_id"], "sid")
        self.assertEqual(inbound_payload(Message(text="x"))["platform_user_id"],
                         "unknown")

    def test_session_fallbacks(self):
        self.assertEqual(
            inbound_payload(Message(channel_session_id="cs1", text="x"))["session"],
            "cs1")
        self.assertEqual(
            inbound_payload(Message(channel_subject=Subject("sid"), text="x"))
            ["session"], "sid")
        self.assertEqual(
            inbound_payload(Message(metadata={"sender_id": "u1"}, text="x"))["session"],
            "u1")

    def test_media_only_message_gets_placeholder(self):
        payload = inbound_payload(Message(has_media=True))
        self.assertEqual(payload["content"], "[用户发送了媒体消息]")
        self.assertEqual(inbound_payload(Message()).get("content"), "")


class ChatTest(unittest.TestCase):
    def test_calls_gateway_endpoint_with_bearer(self):
        seen = {}

        async def post(url, *, json, headers):
            seen.update(url=url, payload=json, headers=headers)
            return {"text": "在的"}

        client = AijiGatewayClient("http://127.0.0.1:8787/", api_key="k",
                                   post=post)
        result = asyncio.run(client.chat({"content": "在吗"}))
        self.assertEqual(result["text"], "在的")
        self.assertEqual(seen["url"], "http://127.0.0.1:8787/v1/gateway/messages")
        self.assertEqual(seen["headers"]["Authorization"], "Bearer k")

    def test_no_api_key_means_no_authorization_header(self):
        seen = {}

        async def post(url, *, json, headers):
            seen.update(headers=headers)
            return {}

        asyncio.run(AijiGatewayClient(post=post).chat({"content": "x"}))
        self.assertNotIn("Authorization", seen["headers"])

    def test_default_post_uses_aiohttp(self):
        calls = []

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            def raise_for_status(self):
                calls.append("raise_for_status")

            async def json(self):
                return self._payload

        class FakeSession:
            def __init__(self, *, timeout=None, **kwargs):
                calls.append(("session", timeout))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            def post(self, url, *, json=None, headers=None):
                calls.append(("post", url, json, headers))
                return FakeResponse({"text": "回来了"})

        module = types.ModuleType("aiohttp")
        module.ClientTimeout = lambda total: ("timeout", total)
        module.ClientSession = FakeSession
        with mock.patch.dict(sys.modules, {"aiohttp": module}):
            client = AijiGatewayClient("http://h", timeout_s=7)
            result = asyncio.run(client._default_post(
                "http://h/x", json={"a": 1}, headers={"H": "v"}))
        self.assertEqual(result, {"text": "回来了"})
        self.assertIn(("session", ("timeout", 7.0)), calls)
        self.assertIn("raise_for_status", calls)

    def test_default_post_returns_empty_for_non_object_json(self):
        class FakeResponse:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            def raise_for_status(self):
                return None

            async def json(self):
                return [1, 2, 3]

        class FakeSession:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            def post(self, *_a, **_k):
                return FakeResponse()

        module = types.ModuleType("aiohttp")
        module.ClientTimeout = lambda total: total
        module.ClientSession = FakeSession
        with mock.patch.dict(sys.modules, {"aiohttp": module}):
            result = asyncio.run(AijiGatewayClient()._default_post(
                "http://h/x", json={}, headers={}))
        self.assertEqual(result, {})


class ProcessorTest(unittest.TestCase):
    def setUp(self):
        MessageEventFake.emitted = []
        patcher = mock.patch.dict(sys.modules, fake_octop_modules())
        patcher.start()
        self.addCleanup(patcher.stop)

    def collect(self, client, message) -> list[tuple[str, str]]:
        async def run():
            return [item async for item in make_processor(client)(message)]
        return asyncio.run(run())

    def test_replies_with_gateway_text(self):
        async def post(url, *, json, headers):
            return {"text": "  玛卡巴卡！  "}

        events = self.collect(AijiGatewayClient(post=post), Message(text="你好"))
        self.assertEqual(events, [("text", "玛卡巴卡！"), ("completed", "")])

    def test_empty_text_gets_soft_fallback(self):
        async def post(url, *, json, headers):
            return {"text": "   "}

        events = self.collect(AijiGatewayClient(post=post), Message(text="你好"))
        self.assertIn("没接上话", events[0][1])

    def test_exception_becomes_friendly_error(self):
        async def post(url, *, json, headers):
            raise RuntimeError("核心没起")

        events = self.collect(AijiGatewayClient(post=post), Message(text="你好"))
        self.assertIn("本地 AIJiYi 暂时没连上：RuntimeError", events[0][1])
        self.assertEqual(events[-1][0], "completed")

    def test_message_without_text_short_circuits(self):
        called = []

        async def post(url, *, json, headers):
            called.append(True)
            return {}

        events = self.collect(AijiGatewayClient(post=post), Message(text=""))
        self.assertEqual(called, [], "没文字就别打网关")
        self.assertIn("没有可识别的文字", events[0][1])


class RunQqTest(unittest.TestCase):
    def setUp(self):
        FakeChannelManager.instances = []
        patcher = mock.patch.dict(sys.modules, fake_octop_modules())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_requires_app_id_and_secret(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SystemExit):
                asyncio.run(run_qq(AijiGatewayClient()))

    def test_starts_channel_then_stops_manager(self):
        class FakeEvent:
            async def wait(self):
                raise asyncio.CancelledError()

        async def fake_run():
            with mock.patch.object(asyncio, "Event", FakeEvent):
                try:
                    await run_qq(AijiGatewayClient(), sandbox=True)
                except asyncio.CancelledError:
                    pass

        env = {"QQ_APP_ID": "app", "QQ_TOKEN": "tok", "QQ_SECRET": "sec"}
        buffer = io.StringIO()
        with mock.patch.dict("os.environ", env, clear=True), \
                contextlib.redirect_stdout(buffer):
            asyncio.run(fake_run())
            manager = FakeChannelManager.instances[-1]
        self.assertTrue(manager.started)
        self.assertTrue(manager.stopped, "finally 里必须 stop")
        self.assertEqual(manager.workers, 4)
        config = manager.added[0]
        self.assertEqual(config[0], "qqconfig")
        self.assertEqual(config[1]["app_id"], "app")
        self.assertTrue(config[1]["sandbox"])


class MainTest(unittest.TestCase):
    def test_returns_zero_and_builds_client_from_args(self):
        seen = {}

        def fake_run(client, *, sandbox):
            seen["client"] = client
            seen["sandbox"] = sandbox

        with mock.patch("aiji.throat.bridge.octop_gateway.asyncio.run",
                        lambda coro: (coro.close(), fake_run(
                            AijiGatewayClient("http://x"), sandbox=True))):
            with mock.patch.dict("os.environ", {"QQ_SANDBOX": "1"}, clear=True):
                self.assertEqual(
                    main(["--core-url", "http://core:1", "--api-key", "k"]), 0)

    def test_env_defaults_fill_arguments(self):
        parser_seen = {}

        def fake_parse(argv):
            parser_seen["argv"] = argv
            return types.SimpleNamespace(core_url="http://env:1", api_key="",
                                         sandbox=True)

        with mock.patch("argparse.ArgumentParser.parse_args",
                        lambda self, argv=None: fake_parse(argv)), \
                mock.patch("aiji.throat.bridge.octop_gateway.asyncio.run",
                           lambda coro: coro.close()):
            self.assertEqual(main([]), 0)

    def test_keyboard_interrupt_returns_zero(self):
        def boom(coro):
            coro.close()                     # 否则 RuntimeWarning: never awaited
            raise KeyboardInterrupt

        with mock.patch("aiji.throat.bridge.octop_gateway.asyncio.run", boom):
            self.assertEqual(main(["--core-url", "http://x"]), 0)

    def test_module_entrypoint_exits_with_zero(self):
        with mock.patch("aiji.throat.bridge.octop_gateway.asyncio.run",
                        lambda coro: coro.close()), \
                mock.patch.object(sys, "argv", ["octop_gateway.py"]):
            with self.assertRaises(SystemExit) as ctx:
                runpy.run_path(
                    str(pathlib.Path(__file__).resolve().parent.parent
                        / "aiji" / "throat" / "bridge" / "octop_gateway.py"),
                    run_name="__main__")
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
