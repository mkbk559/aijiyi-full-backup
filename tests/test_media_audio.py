"""Voice URL ingestion and AMR-to-WAV conversion contracts."""

from __future__ import annotations

import unittest

from aiji.hippocampus.media_audio import audio_bytes_from_url, transcribe_item_audio
from aiji.throat.net_guard import UrlGuardError
from aiji.hippocampus.recognize import Item


class MediaAudioTest(unittest.TestCase):
    def test_voice_item_url_is_normalized_before_asr(self):
        seen = []

        def transcribe(data):
            seen.append(data)
            return "识别到了", 0.9

        text, confidence = transcribe_item_audio(
            Item(
                kind="voice",
                ref="voice-1",
                url="https://example.test/voice.amr",
            ),
            transcribe=transcribe,
            fetch=lambda _url, _max: b"amr",
            convert=lambda _data: b"RIFF-wav",
            resolver=lambda _host: ["93.184.216.34"],
        )
        self.assertEqual((text, confidence), ("识别到了", 0.9))
        self.assertEqual(seen, [b"RIFF-wav"])

    def test_voice_item_without_url_or_bytes_is_honest(self):
        text, confidence = transcribe_item_audio(
            Item(kind="voice", ref="missing", url=""),
            transcribe=lambda _data: ("不该调用", 1.0),
        )
        self.assertEqual((text, confidence), ("", 0.0))

    def test_public_amr_is_downloaded_and_converted_to_wav(self):
        seen = {}

        def fetch(url, max_bytes):
            seen["url"] = url
            seen["max_bytes"] = max_bytes
            return b"amr-bytes"

        def convert(data):
            seen["data"] = data
            return b"RIFF-converted"

        result = audio_bytes_from_url(
            "https://multimedia.nt.qq.com.cn/download?fileid=abc",
            fetch=fetch,
            convert=convert,
            resolver=lambda _host: ["93.184.216.34"],
            max_bytes=1024,
        )

        self.assertEqual(result, b"RIFF-converted")
        self.assertEqual(seen["url"], "https://multimedia.nt.qq.com.cn/download?fileid=abc")
        self.assertEqual(seen["max_bytes"], 1024)
        self.assertEqual(seen["data"], b"amr-bytes")

    def test_existing_wav_bytes_skip_conversion(self):
        called = []
        result = audio_bytes_from_url(
            "https://example.test/voice.wav",
            fetch=lambda _url, _max: b"RIFF" + b"\x00" * 20,
            convert=lambda data: called.append(data) or b"bad",
            resolver=lambda _host: ["93.184.216.34"],
        )
        self.assertTrue(result.startswith(b"RIFF"))
        self.assertEqual(called, [])

    def test_private_voice_url_is_rejected_before_fetch(self):
        called = []
        with self.assertRaises(UrlGuardError):
            audio_bytes_from_url(
                "http://127.0.0.1/voice.amr",
                fetch=lambda url, _max: called.append(url) or b"x",
            )
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
