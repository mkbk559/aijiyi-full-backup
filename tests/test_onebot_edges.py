"""OneBot 桥接的边角：消息解析各分支、HTTP 失败分类、`_loop` 的异常路径。

`tests/test_onebot.py` 覆盖了正常收发；这里补的是"出错时到底怎么算"——
个人 QQ 桥最常见的故障是 NapCat 没起来/反代隧道拿 4xx，
如果失败被当成成功，用户看到的是"机器人已读不回"却查不到原因。
"""
from __future__ import annotations

import json
import pathlib
import queue
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aiji.throat.bridge.onebot import (  # noqa: E402
    OneBotBridge, OneBotCallError, OneBotClient, SendOutcome, _parse_message,
    parse_event,
)


def body(obj: object) -> bytes:
    """把 dict 编成 UTF-8 JSON 字节（含中文，所以不能写 bytes 字面量）。"""
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


class Response:
    def __init__(self, payload: bytes = b"", code: int = 200):
        self._body = payload
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return self._body


def http_error(code: int, payload: bytes = b"nope") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://x/y", code, "err", {}, mock.Mock(read=lambda *_: payload))


class ParseMessageTest(unittest.TestCase):
    def test_cq_at_marks_mention_and_is_stripped(self):
        content, mentioned = _parse_message(
            "[CQ:at,qq=999] 你好呀 [CQ:face,id=1]", "999")
        self.assertTrue(mentioned)
        self.assertEqual(content, "你好呀")

    def test_other_mentions_become_plain_text(self):
        content, mentioned = _parse_message(
            [{"type": "at", "data": {"qq": "123"}},
             {"type": "text", "data": {"text": "看这个"}}], "999")
        self.assertFalse(mentioned, "被别人 @ 不算叫我")
        self.assertEqual(content, "@123 看这个")

    def test_media_kinds_become_placeholders(self):
        content, _ = _parse_message([
            {"type": "image", "data": {}},
            {"type": "record", "data": {}},
            {"type": "file", "data": {}},
            {"type": "face", "data": {}},
        ], "999")
        self.assertEqual(content, "[图片][语音][文件][表情]")

    def test_junk_items_are_skipped(self):
        content, _ = _parse_message(
            ["不是dict", {"type": "text", "data": {"text": "剩下这句"}}], "999")
        self.assertEqual(content, "剩下这句")

    def test_non_text_message_types_are_empty(self):
        self.assertEqual(_parse_message(12345, "999"), ("", False))
        self.assertEqual(_parse_message(None, "999"), ("", False))


class ParseEventEdgeTest(unittest.TestCase):
    base = {
        "post_type": "message",
        "message_type": "private",
        "self_id": 999,
        "user_id": 42,
        "message_id": 1,
        "message": "在吗",
    }

    def test_non_message_post_type_is_ignored(self):
        self.assertIsNone(parse_event({**self.base, "post_type": "notice"}))
        self.assertIsNone(parse_event({**self.base, "post_type": "meta_event"}))

    def test_unknown_message_type_is_ignored(self):
        self.assertIsNone(parse_event({**self.base, "message_type": "guild"}))

    def test_missing_user_or_self_message_is_ignored(self):
        self.assertIsNone(parse_event({**self.base, "user_id": 0}))
        self.assertIsNone(parse_event({**self.base, "user_id": 999}))

    def test_message_without_text_is_ignored(self):
        self.assertIsNone(parse_event({**self.base, "message": "[CQ:face,id=1]"}))

    def test_group_mode_off_ignores_groups(self):
        event = {**self.base, "message_type": "group", "group_id": 7,
                 "message": [{"type": "at", "data": {"qq": "999"}},
                             {"type": "text", "data": {"text": "喂"}}]}
        self.assertIsNone(parse_event(event, group_mode="off"))
        self.assertIsNotNone(parse_event(event, group_mode="mention"))

    def test_group_without_group_id_is_ignored(self):
        event = {**self.base, "message_type": "group",
                 "message": [{"type": "at", "data": {"qq": "999"}},
                             {"type": "text", "data": {"text": "喂"}}]}
        self.assertIsNone(parse_event(event, group_mode="mention"))

    def test_sender_falls_back_to_user_id(self):
        self.assertEqual(
            parse_event({**self.base, "sender": {"card": ""}})["sender_name"], "42")
        self.assertEqual(
            parse_event({**self.base, "sender": {"card": "卡片名"}})["sender_name"],
            "卡片名")

    def test_message_id_falls_back_to_time(self):
        event = {k: v for k, v in self.base.items() if k != "message_id"}
        event["time"] = 1700000000
        self.assertEqual(parse_event(event)["message_id"], 1700000000)


class DefaultPostTest(unittest.TestCase):
    def test_ok_response_is_true(self):
        client = OneBotClient("http://x")
        with mock.patch("aiji.throat.bridge.onebot.net.urlopen",
                        lambda *_a, **_k: Response(
                            body({"status": "ok", "retcode": 0, "data": {}}))):
            self.assertTrue(client._default_post("http://x/y", {}, {}))
        with mock.patch("aiji.throat.bridge.onebot.net.urlopen",
                        lambda *_a, **_k: Response(
                            body({"status": "failed", "retcode": 1}))):
            self.assertFalse(client._default_post("http://x/y", {}, {}))

    def test_network_and_parse_failures_are_false(self):
        client = OneBotClient("http://x")

        def raise_urlerror(*_a, **_k):
            raise urllib.error.URLError("连不上")

        with mock.patch("aiji.throat.bridge.onebot.net.urlopen", raise_urlerror):
            self.assertFalse(client._default_post("http://x/y", {}, {}))
        with mock.patch("aiji.throat.bridge.onebot.net.urlopen",
                        lambda *_a, **_k: Response("这不是 json".encode("utf-8"))):
            self.assertFalse(client._default_post("http://x/y", {}, {}))


class DefaultPostExTest(unittest.TestCase):
    def _probe(self, payload: bytes | Exception) -> SendOutcome:
        client = OneBotClient("http://x")

        def fake(*_a, **_k):
            if isinstance(payload, Exception):
                raise payload
            return Response(payload)

        with mock.patch("aiji.throat.bridge.onebot.net.urlopen", fake):
            return client._default_post_ex("http://x/y", {}, {})

    def test_success_carries_retcode_and_status(self):
        outcome = self._probe(body({"status": "ok", "retcode": 0, "message": "fine"}))
        self.assertTrue(outcome.ok)
        self.assertEqual((outcome.retcode, outcome.http_status), (0, 200))
        self.assertEqual(outcome.message, "fine")

    def test_business_failure_is_not_ok(self):
        outcome = self._probe(body({"status": "failed", "retcode": 1200,
                                      "wording": "限流"}))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.retcode, 1200)
        self.assertEqual(outcome.message, "限流")

    def test_http_error_carries_code(self):
        outcome = self._probe(http_error(403))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.http_status, 403)
        self.assertEqual(outcome.message, "HTTP 403")

    def test_network_error_has_no_status(self):
        outcome = self._probe(urllib.error.URLError("隧道断了"))
        self.assertFalse(outcome.ok)
        self.assertIsNone(outcome.http_status)
        self.assertEqual(outcome.message, "URLError")

    def test_garbage_response_is_reported(self):
        outcome = self._probe(b"<<<not json>>>")
        self.assertFalse(outcome.ok)
        self.assertIn("bad response", outcome.message)


class SendExTest(unittest.TestCase):
    def test_injected_post_success_and_failure(self):
        client = OneBotClient("http://x", post=lambda *_a: True)
        outcome = client.send_ex({"message_type": "group", "group_id": 1}, "hi")
        self.assertTrue(outcome.ok)
        self.assertEqual((outcome.retcode, outcome.http_status), (0, 200))

        client = OneBotClient("http://x", post=lambda *_a: False)
        outcome = client.send_ex({"message_type": "private", "user_id": 2}, "hi")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.retcode, -1)
        self.assertIsNone(outcome.http_status)

    def test_private_endpoint_and_token_header(self):
        seen = {}

        def post(url, payload, headers):
            seen.update(url=url, payload=payload, headers=headers)
            return True

        OneBotClient("http://x/", token="tk", post=post).send_ex(
            {"message_type": "private", "user_id": 2}, "hi")
        self.assertTrue(seen["url"].endswith("/send_private_msg"))
        self.assertEqual(seen["payload"]["user_id"], 2)
        self.assertEqual(seen["headers"]["Authorization"], "Bearer tk")


class CallErrorTest(unittest.TestCase):
    def test_invalid_action_name_is_rejected_before_network(self):
        client = OneBotClient("http://x")
        with self.assertRaises(OneBotCallError) as ctx:
            client.call("bad action; rm -rf /")
        self.assertIn("invalid OneBot action", str(ctx.exception))

    def test_http_error_includes_body_and_status(self):
        client = OneBotClient("http://x")
        with mock.patch("aiji.throat.bridge.onebot.net.urlopen",
                        side_effect=http_error(401, b"token invalid")):
            with self.assertRaises(OneBotCallError) as ctx:
                client.call("get_login_info")
        self.assertEqual(ctx.exception.status, 401)
        self.assertIn("token invalid", str(ctx.exception))

    def test_network_error_is_wrapped(self):
        client = OneBotClient("http://x")
        with mock.patch("aiji.throat.bridge.onebot.net.urlopen",
                        side_effect=urllib.error.URLError("没连上")):
            with self.assertRaises(OneBotCallError) as ctx:
                client.call("get_login_info")
        self.assertIn("没连上", str(ctx.exception))

    def test_non_object_response_is_rejected(self):
        client = OneBotClient("http://x")
        with mock.patch("aiji.throat.bridge.onebot.net.urlopen",
                        lambda *_a, **_k: Response(b"[1,2,3]")):
            with self.assertRaises(OneBotCallError) as ctx:
                client.call("get_login_info")
        self.assertIn("not an object", str(ctx.exception))

    def test_non_numeric_retcode_becomes_minus_one(self):
        client = OneBotClient("http://x")
        with mock.patch("aiji.throat.bridge.onebot.net.urlopen",
                        lambda *_a, **_k: Response(
                            body({"retcode": "abc", "status": "ok"}))):
            with self.assertRaises(OneBotCallError) as ctx:
                client.call("get_login_info")
        self.assertEqual(ctx.exception.retcode, -1)

    def test_failure_message_prefers_body_text(self):
        client = OneBotClient("http://x")
        cases = [
            (body({"retcode": 100, "status": "failed", "message": "消息被拒"}), "消息被拒"),
            (body({"retcode": 100, "status": "failed", "msg": "底层错误"}), "底层错误"),
            (body({"retcode": 100, "status": "failed"}),
             "OneBot action failed: failed"),
        ]
        for payload, expect in cases:
            with mock.patch("aiji.throat.bridge.onebot.net.urlopen",
                            lambda *_a, _p=payload, **_k: Response(_p)):
                with self.assertRaises(OneBotCallError) as ctx:
                    client.call("send_msg")
            self.assertEqual(str(ctx.exception), expect)


class RemainingPathTest(unittest.TestCase):
    def test_send_ex_without_injected_post_hits_network_path(self):
        client = OneBotClient("http://x")
        with mock.patch("aiji.throat.bridge.onebot.net.urlopen",
                        lambda *_a, **_k: Response(
                            body({"status": "ok", "retcode": 0}))):
            outcome = client.send_ex({"message_type": "group", "group_id": 1}, "hi")
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.http_status, 200)

    def test_submit_reports_ignored_for_unparsable_event(self):
        bridge = OneBotBridge(reply_fn=lambda e: "x", sender=lambda e, t: True)
        result = bridge.submit({"post_type": "notice", "message_type": "group"})
        self.assertEqual(result, {"status": "ignored"})
        self.assertEqual(bridge.stats["ignored"], 1)


class LoopEdgeTest(unittest.TestCase):
    def _drain(self, bridge, event) -> None:
        bridge._queue.put(event)
        bridge._queue.put(None)
        bridge._loop()          # 本线程跑，确定性

    def test_unmentioned_without_ingest_is_dropped_silently(self):
        sent = []
        bridge = OneBotBridge(reply_fn=lambda e: "回复",
                              sender=lambda e, t: sent.append(t) or True,
                              group_mode="all")
        self._drain(bridge, {"should_reply": False, "message_id": 1})
        self.assertEqual(sent, [])
        self.assertEqual(bridge.stats["ingested"], 0)

    def test_send_failure_is_counted(self):
        bridge = OneBotBridge(reply_fn=lambda e: "回复",
                              sender=lambda e, t: False, group_mode="all")
        self._drain(bridge, {"should_reply": True, "message_id": 1})
        self.assertEqual(bridge.stats["failed"], 1)
        self.assertEqual(bridge.snapshot()["last_error"], "send_failed")

    def test_reply_exception_is_recorded_not_raised(self):
        def boom(_event):
            raise RuntimeError("模型挂了")

        bridge = OneBotBridge(reply_fn=boom, sender=lambda e, t: True,
                              group_mode="all")
        self._drain(bridge, {"should_reply": True, "message_id": 1})
        self.assertEqual(bridge.stats["failed"], 1)
        self.assertIn("RuntimeError: 模型挂了", bridge.snapshot()["last_error"])

    def test_empty_reply_is_not_sent(self):
        sent = []
        bridge = OneBotBridge(reply_fn=lambda e: "   ",
                              sender=lambda e, t: sent.append(t) or True,
                              group_mode="all")
        self._drain(bridge, {"should_reply": True, "message_id": 1})
        self.assertEqual(sent, [])
        self.assertEqual(bridge.stats["replied"], 0)

    def test_stop_with_full_queue_does_not_raise(self):
        bridge = OneBotBridge(reply_fn=lambda e: "", sender=lambda e, t: True)
        bridge._queue = queue.Queue(maxsize=1)
        bridge._queue.put_nowait({"should_reply": False, "message_id": 1})
        bridge.stop(timeout=0.1)          # 塞不进哨兵，只能走 except 分支
        self.assertTrue(bridge._stop.is_set())

    def test_start_is_idempotent_and_snapshot_reports_state(self):
        bridge = OneBotBridge(reply_fn=lambda e: "x", sender=lambda e, t: True,
                              configured=True, group_mode="mention")
        bridge.start().start()
        try:
            snap = bridge.snapshot()
            self.assertTrue(snap["configured"])
            self.assertEqual(snap["group_mode"], "mention")
            for key in ("accepted", "ignored", "duplicate", "replied",
                        "ingested", "failed", "rejected", "queue_depth"):
                self.assertIn(key, snap)
        finally:
            bridge.stop()

    def test_expired_dedupe_entries_are_pruned(self):
        bridge = OneBotBridge(reply_fn=lambda e: "x", sender=lambda e, t: True,
                              group_mode="all", dedupe_ttl_s=1)
        bridge._seen["老消息"] = 0.0
        event = {"post_type": "message", "message_type": "private",
                 "self_id": 999, "user_id": 42, "message_id": 5, "message": "在吗"}
        bridge.submit(event)
        self.assertNotIn("老消息", bridge._seen, "过期去重键必须清掉")


if __name__ == "__main__":
    unittest.main()
