"""语音决策表逐行验收（对应 design/voice-plan-2026-11-05.md 第一节）。"""
import unittest

from aiji.heart.voice_plan import Context, decide


class VoicePlanTest(unittest.TestCase):
    def test_group_default_is_text(self):
        ok, why = decide(Context(scope="group", text="哈哈哈今天好热"))
        self.assertFalse(ok)
        self.assertIn("群里", why)

    def test_group_with_mention_can_voice(self):
        ok, _ = decide(Context(scope="group", text="给你唱一句", asked_by_at=True))
        self.assertTrue(ok)

    def test_asleep_blocks_everything(self):
        ok, why = decide(Context(scope="private", text="晚安", talk_allowed=False,
                                 peer_is_voice=True))
        self.assertFalse(ok)
        self.assertIn("睡觉", why)

    def test_peer_voice_gets_voice_reply(self):
        ok, why = decide(Context(scope="private", text="我到家啦", peer_is_voice=True))
        self.assertTrue(ok)
        self.assertIn("语音", why)

    def test_bedtime_short_sentence(self):
        ok, _ = decide(Context(scope="private", text="那我先睡啦", minute=22 * 60))
        self.assertTrue(ok)

    def test_long_text_or_link_is_text(self):
        self.assertFalse(decide(Context(scope="private", text="看这个 https://x.y"))[0])
        self.assertFalse(decide(Context(scope="private", text="字" * 90, peer_is_voice=True))[0])

    def test_hourly_and_daily_caps(self):
        self.assertFalse(decide(Context(scope="private", text="好", peer_is_voice=True,
                                        hour_sent=2))[0])
        self.assertFalse(decide(Context(scope="private", text="好", peer_is_voice=True,
                                        day_sent=10))[0])

    def test_same_peer_cooldown(self):
        ok, why = decide(Context(scope="private", text="好", peer_is_voice=True,
                                 last_voice_age_s=60))
        self.assertFalse(ok)
        self.assertIn("刚给", why)

    def test_emotion_short_sentence(self):
        self.assertTrue(decide(Context(scope="private", text="我今天好开心呀",
                                       emotion="happy"))[0])

    def test_plain_private_chat_stays_text(self):
        ok, why = decide(Context(scope="private", text="这道题我算出来是十二"))
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
