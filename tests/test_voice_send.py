import unittest
from unittest import mock
from aiji.heart import voice_plan, voice_send


class FakeAdapter:
    name = "qq"

    def __init__(self):
        self.sent = []

    def dm(self, peer, text, dry_run=None):
        self.sent.append(("dm", str(text)))
        return "text-ok"

    def group(self, peer, text, dry_run=None):
        self.sent.append(("group", str(text)))
        return "text-ok"

    def voice(self, peer, path, scope="dm", dry_run=None):
        self.sent.append(("voice", str(path)))
        return "voice-ok"


class NoVoiceAdapter:
    """模拟抖音/B站：只有文字通道，没有 voice()。"""

    name = "douyin"

    def __init__(self):
        self.sent = []

    def dm(self, peer, text, dry_run=None):
        self.sent.append(("dm", str(text)))
        return "text-ok"

    def group(self, peer, text, dry_run=None):
        self.sent.append(("group", str(text)))
        return "text-ok"


class VoiceSendTest(unittest.TestCase):
    def test_group_plain_chat_sends_text(self):
        a = FakeAdapter()
        out = voice_send.send(a, "1", "这道题我算出来是十二", scope="group",
                              ctx=voice_plan.Context(scope="group", text="这道题我算出来是十二"))
        self.assertEqual(out["kind"], "text")
        self.assertEqual(a.sent[0][0], "group")

    def test_peer_voice_triggers_voice(self):
        a = FakeAdapter()
        with mock.patch.object(voice_send, "render", return_value=__import__("pathlib").Path("x.wav")):
            out = voice_send.send(a, "1", "我到家啦",
                                  ctx=voice_plan.Context(scope="private", text="我到家啦", peer_is_voice=True))
        self.assertEqual(out["kind"], "voice")
        self.assertEqual(a.sent[0][0], "voice")

    def test_render_failure_falls_back_to_text(self):
        a = FakeAdapter()
        with mock.patch.object(voice_send, "render", return_value=None):
            out = voice_send.send(a, "1", "我到家啦",
                                  ctx=voice_plan.Context(scope="private", text="我到家啦", peer_is_voice=True))
        self.assertEqual(out["kind"], "text")
        self.assertIn("生成失败", out["why"])
        self.assertEqual(a.sent[0][0], "dm")

    def test_platform_without_voice_sends_text(self):
        a = NoVoiceAdapter()
        out = voice_send.send(a, "1", "我到家啦",
                              ctx=voice_plan.Context(scope="private", text="我到家啦", peer_is_voice=True))
        self.assertEqual(out["kind"], "text")
        self.assertIn("发不了语音", out["why"])


if __name__ == "__main__":
    unittest.main()
