"""A5：QQ 复读疲劳闸（OneBotBridge 层）。

真机背景：11-06 抖音对测试号循环文本回了 44 次（C1 修了抖音侧）；QQ 群里
同人同句刷屏此前无任何拦截。规则：群聊内同 (scope, sender, 归一化文本)
半小时窗口第 3+ 次 → 降级为只入记忆不回复；私聊不启用。
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiji.throat.bridge.onebot import _RepeatFatigue, OneBotBridge, parse_event


def _group_event(content: str, *, sender: str = "复读机", msg_id: int = 1):
    return {
        "post_type": "message",
        "message_type": "group",
        "message_id": msg_id,
        "self_id": "2684827447",
        "user_id": "10086",
        "group_id": 849663082,
        "sender": {"card": sender, "nickname": sender},
        "message": [{"type": "at", "data": {"qq": "2684827447"}},
                    {"type": "text", "data": {"text": content}}],
    }


class RepeatFatigueUnitTest(unittest.TestCase):
    def test_tired_after_cap(self):
        f = _RepeatFatigue()
        t = 1000.0
        self.assertFalse(f.tired("g", "a", "你好呀", now=t))
        self.assertFalse(f.tired("g", "a", "你好呀", now=t + 10))
        self.assertTrue(f.tired("g", "a", "你好呀", now=t + 20))   # 第 3 次

    def test_per_sender_independent(self):
        f = _RepeatFatigue()
        t = 1000.0
        f.tired("g", "a", "你好呀", now=t)
        f.tired("g", "a", "你好呀", now=t + 1)
        # 换个人说同一句：不疲劳（群里两个人一起复读不该连坐）
        self.assertFalse(f.tired("g", "b", "你好呀", now=t + 2))

    def test_punctuation_and_case_normalized(self):
        f = _RepeatFatigue()
        t = 1000.0
        f.tired("g", "a", "你好呀！", now=t)
        f.tired("g", "a", "你好 呀。", now=t + 1)
        # 标点/空白变了仍算同句
        self.assertTrue(f.tired("g", "a", "你好呀？", now=t + 2))

    def test_window_expiry_resets(self):
        f = _RepeatFatigue()
        t = 1000.0
        f.tired("g", "a", "你好呀", now=t)
        f.tired("g", "a", "你好呀", now=t + 100)
        # 窗口过后重新计数（30 分钟）
        self.assertFalse(f.tired("g", "a", "你好呀", now=t + 1900))

    def test_empty_text_never_tired(self):
        f = _RepeatFatigue()
        self.assertFalse(f.tired("g", "a", "。。。", now=1.0))


class BridgeFatigueTest(unittest.TestCase):
    """桥接层的端到端行为。注意：_loop 是后台线程，submit 完必须等队列清空
    再断言（否则竞态：事件还在队列里 stop 就 join 了）。"""

    def _drain(self, bridge, *, timeout_s: float = 5.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if bridge._queue.qsize() == 0 and bridge.stats["replied"] + \
                    bridge.stats["ingested"] + bridge.stats["failed"] >= \
                    bridge.stats["accepted"]:
                time.sleep(0.05)
                return
            time.sleep(0.02)

    def _bridge(self):
        replies, ingested, sent = [], [], []

        def reply_fn(ev):
            replies.append(ev)
            return "回你一句"

        def sender(ev, text):
            sent.append((ev, text))
            return True

        def ingest_fn(ev):
            ingested.append(ev)

        b = OneBotBridge(reply_fn=reply_fn, sender=sender, ingest_fn=ingest_fn,
                         group_mode="mention", configured=True).start()
        return b, replies, ingested, sent

    def test_looped_group_text_stops_replying_but_still_ingests(self):
        b, replies, ingested, sent = self._bridge()
        for i in range(4):
            b.submit(_group_event("你好呀", msg_id=100 + i))
        self._drain(b)
        b.stop(timeout=5.0)
        # 前 2 次正常回复，第 3、4 次疲劳：只入记忆
        self.assertEqual(b.stats["replied"], 2)
        self.assertEqual(b.stats["repeat_fatigued"], 2)
        self.assertEqual(len(ingested), 2)
        self.assertEqual(len(sent), 2)

    def test_two_senders_same_text_all_reply(self):
        b, replies, ingested, sent = self._bridge()
        for i, sender in enumerate(("张三", "李四")):
            b.submit(_group_event("你们看这个", sender=sender, msg_id=200 + i))
        self._drain(b)
        b.stop(timeout=5.0)
        self.assertEqual(b.stats["replied"], 2)
        self.assertEqual(b.stats["repeat_fatigued"], 0)

    def test_private_chat_not_gated(self):
        b, replies, ingested, sent = self._bridge()
        ev = {
            "post_type": "message", "message_type": "private",
            "message_id": 300, "self_id": "2684827447", "user_id": "10086",
            "sender": {"nickname": "私聊的人"},
            "message": "你好呀",
        }
        for i in range(4):
            b.submit({**ev, "message_id": 300 + i})
        self._drain(b)
        b.stop(timeout=5.0)
        # 私聊刷 4 遍同一句：疲劳闸不启用（各自都回）
        self.assertEqual(b.stats["replied"], 4)
        self.assertEqual(b.stats["repeat_fatigued"], 0)


if __name__ == "__main__":
    unittest.main()
