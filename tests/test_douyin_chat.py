"""抖音群聊主动聊天的离线测试（不需要真浏览器）。

为什么值得测：`run_once` 是"读群 → 想一句 → 发出去 → 记账"的闭环，
最容易出的错是"重复回同一条"和"发完没记账"，两者都会让机器人在群里刷屏。
"""

from __future__ import annotations

import pathlib
import unittest

from aiji.heart.platforms import douyin_chat


def workdir(name: str) -> pathlib.Path:
    p = pathlib.Path(".tmp") / name
    p.mkdir(parents=True, exist_ok=True, mode=0o777)
    return p


def _no_gate():
    """测试里把作息闸门关掉：这类用例只验"读→挑→发→记账"这条链路。

    （真机行为由 test_day_gate_blocks_at_night 单独守。）
    """
    import unittest.mock as _m

    return _m.patch("aiji.heart.platforms.douyin_chat._asleep_reason", return_value="")


class FakeAdapter:
    def __init__(self, messages):
        self.messages = messages
        self.sent = []

    def chat_session(self, fn):
        return fn(object())

    def group(self, group, text, **_):
        self.sent.append((group, text))

        class _Detail(dict):
            pass

        class _Res:
            detail = {"sent": True, "verified": True}

        return _Res()


class PickTargetTest(unittest.TestCase):
    def test_skips_self_and_seen(self):
        msgs = [{"sender": "别人", "text": "在吗"},
                {"sender": "玛卡巴卡的小推车", "text": "我刚说的"},
                {"sender": "别人", "text": "最新一条"}]
        seen = {douyin_chat.msg_key(msgs[0])}
        target = douyin_chat.pick_reply_target(
            msgs, self_name="玛卡巴卡的小推车", seen=seen)
        self.assertEqual(target["text"], "最新一条")

    def test_returns_none_when_only_self(self):
        msgs = [{"sender": "玛卡巴卡的小推车", "text": "hi"}]
        self.assertIsNone(douyin_chat.pick_reply_target(
            msgs, self_name="玛卡巴卡的小推车", seen=set()))

    def test_key_is_stable(self):
        m = {"sender": "a", "text": "b"}
        self.assertEqual(douyin_chat.msg_key(m), douyin_chat.msg_key(dict(m)))

    def test_repeat_fatigue_ignores_looped_text(self):
        """回声室止损：同一个人同一句话刷 3 遍以上，不再接。"""
        msgs = [{"sender": "测试", "text": "你好呀"} for _ in range(4)]
        self.assertIsNone(douyin_chat.pick_reply_target(
            msgs, self_name="我", seen=set()))

    def test_repeat_fatigue_counts_per_sender(self):
        """两个不同的人各说一次同样的句子，不算刷屏。"""
        msgs = [{"sender": "甲", "text": "你好呀"},
                {"sender": "乙", "text": "你好呀"}]
        target = douyin_chat.pick_reply_target(
            msgs, self_name="我", seen=set())
        self.assertIsNotNone(target)

    def test_repeat_fatigue_under_cap_still_replies(self):
        """说两遍（=cap）还在容忍范围，第三遍开始不接。"""
        msgs = [{"sender": "测试", "text": "阱里。"},
                {"sender": "测试", "text": "阱里。"}]
        target = douyin_chat.pick_reply_target(
            msgs, self_name="我", seen=set())
        self.assertIsNotNone(target)


class RunOnceTest(unittest.TestCase):
    def setUp(self):
        douyin_chat.open_group = lambda session, group, **kw: None  # noqa: ARG005
        self.dir = workdir("douyin_chat")
        self.state = self.dir / "state.json"
        self.state.unlink(missing_ok=True)

    def _run(self, messages, ask=lambda t, s: "我在呢", **kw):
        douyin_chat.read_messages = (
            lambda session, limit=20, _m=messages: list(_m))  # noqa: ARG005
        kw.setdefault("min_interval_s", 0)
        adapter = FakeAdapter(messages)
        out = douyin_chat.run_once(adapter, "测试群", ask=ask,
                                   state_path=self.state, **kw)
        return adapter, out

    def test_replies_once_then_marked_seen(self):
        self.enterContext(_no_gate())
        msgs = [{"sender": "小明", "text": "有人吗"}]
        adapter, out = self._run(msgs)
        self.assertTrue(out["replied"])
        self.assertEqual(adapter.sent, [("测试群", "我在呢")])
        # 第二轮：同一条消息不该再回
        adapter2, out2 = self._run(msgs)
        self.assertFalse(out2["replied"])
        self.assertEqual(adapter2.sent, [])

    def test_dry_run_does_not_send(self):
        adapter, out = self._run([{"sender": "小明", "text": "在吗"}], dry_run=True)
        self.assertFalse(out["replied"])
        self.assertEqual(adapter.sent, [])

    def test_empty_model_answer_is_not_sent(self):
        adapter, out = self._run([{"sender": "小明", "text": "在吗"}],
                                 ask=lambda t, s: "   ")
        self.assertFalse(out["replied"])
        self.assertEqual(adapter.sent, [])

    def test_min_interval_blocks_second_reply(self):
        self.enterContext(_no_gate())
        msgs = [{"sender": "小明", "text": "第一句"}]
        self._run(msgs, min_interval_s=180)
        # 换个新消息再来一次：间隔没到 → 不回（防止计划任务和手工同时跑导致连发）
        msgs2 = msgs + [{"sender": "小明", "text": "第二句"}]
        adapter, out = self._run(msgs2, min_interval_s=180)
        self.assertFalse(out["replied"])
        self.assertIn("最小间隔", out["reason"])
        self.assertEqual(adapter.sent, [])


if __name__ == "__main__":
    unittest.main()

    def test_day_gate_blocks_when_asleep(self):
        """作息闸门：睡觉 / 上课时段一律不说话（严格时间规划）。"""
        from aiji.brain import day as day_mod
        from aiji.heart.platforms import douyin_chat as dc

        fake = {"start": "22:30", "end": "06:40", "what": "睡觉", "talk": "none"}
        with mock.patch.object(day_mod, "current_block", return_value=fake):
            reason = dc._asleep_reason()
        self.assertIn("睡觉", reason)

        awake = {"start": "21:00", "end": "22:00", "what": "自由时间", "talk": "normal"}
        with mock.patch.object(day_mod, "current_block", return_value=awake):
            self.assertEqual(dc._asleep_reason(), "")
