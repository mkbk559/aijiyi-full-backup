import unittest
from unittest import mock
from pathlib import Path
from aiji.heart import voice_inbox


class VoiceInboxTest(unittest.TestCase):
    def test_missing_file_is_soft_failure(self):
        r = voice_inbox.transcribe_record(".tmp/nope.wav")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "file_missing")

    def test_transcribe_ok_marks_voice_source(self):
        with mock.patch.object(voice_inbox, "transcribe_record",
                               return_value={"ok": True, "text": "我到家了", "conf": 0.9, "error": ""}):
            r = voice_inbox.transcribe_record("x.wav")
        self.assertIn("（对方是语音说的）", voice_inbox.to_turn_content(r))
        self.assertIn("我到家了", voice_inbox.to_turn_content(r))

    def test_engine_error_degrades(self):
        p = Path(".tmp/voice_inbox_probe.wav"); p.parent.mkdir(exist_ok=True); p.write_bytes(b"RIFF")
        with mock.patch("aiji.hippocampus.real_engines.transcribe_file", side_effect=RuntimeError("boom")), \
                mock.patch.object(voice_inbox, "_whisper", side_effect=RuntimeError("no whisper")):
            r = voice_inbox.transcribe_record(p)
        self.assertFalse(r["ok"])
        self.assertIn("boom", r["error"])
        self.assertIn("没听清", voice_inbox.to_turn_content(r))

    def test_empty_transcript_is_failure_not_fake(self):
        p = Path(".tmp/voice_inbox_probe2.wav"); p.parent.mkdir(exist_ok=True); p.write_bytes(b"RIFF")
        with mock.patch("aiji.hippocampus.real_engines.transcribe_file", return_value=("   ", 0.0)), \
                mock.patch.object(voice_inbox, "_whisper", return_value=("", 0.0)):
            r = voice_inbox.transcribe_record(p)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "empty")


if __name__ == "__main__":
    unittest.main()
