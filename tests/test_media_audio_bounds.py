"""媒体音频下载/转 WAV 的边界（补充 `test_media_audio.py`）。

全用注入的 fake（fetch / convert / resolver），不联网、不起 ffmpeg：
- 私有地址在**下载前**就被拦（不能等拉完再判断）
- 超限：Content-Length 提前拦 + 实际读到的字节再拦
- 已经是 WAV 就别再转；转出来不是 RIFF 要报错
- item 上的 bytes / bytes_list / url 三条取数路径 + 什么都没有 → ("", 0.0)

原先 `media_audio.py` 覆盖率 53%，漏的正是这些失败分支。
"""
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aiji.hippocampus import media_audio as ma  # noqa: E402
from aiji.throat.net_guard import UrlGuardError  # noqa: E402

PUBLIC_DNS = lambda host: ["93.184.216.34"]  # noqa: E731
RIFF = b"RIFF" + b"\x00" * 32


class FakeItem:
    def __init__(self, *, url="", extra=None):
        self.url = url
        self.extra = extra or {}


class MediaBytesTest(unittest.TestCase):
    def test_private_host_is_blocked_before_download(self):
        calls = []

        def fetch(url, limit):  # noqa: ARG001
            calls.append(url)
            return b"x"

        with self.assertRaises(UrlGuardError):
            ma.media_bytes_from_url(
                "http://127.0.0.1/a.wav", fetch=fetch, resolver=PUBLIC_DNS)
        self.assertEqual(calls, [], "私网地址不能先下载再判断")

    def test_non_bytes_fetcher_is_rejected(self):
        with self.assertRaises(ValueError):
            ma.media_bytes_from_url(
                "https://example.com/a.wav", fetch=lambda u, n: "文本",
                resolver=PUBLIC_DNS)

    def test_oversize_payload_is_rejected(self):
        with self.assertRaises(UrlGuardError) as ctx:
            ma.media_bytes_from_url(
                "https://example.com/a.wav",
                fetch=lambda u, n: b"x" * (n + 1), resolver=PUBLIC_DNS)
        self.assertEqual(ctx.exception.code, "media_too_large")

    def test_normal_download_returns_bytes(self):
        out = ma.media_bytes_from_url(
            "https://example.com/a.wav", fetch=lambda u, n: b"abc",
            resolver=PUBLIC_DNS)
        self.assertEqual(out, b"abc")


class AudioBytesTest(unittest.TestCase):
    def test_riff_payload_skips_conversion(self):
        converter = mock.Mock(side_effect=AssertionError("不该转"))
        out = ma.audio_bytes_from_url(
            "https://example.com/a.wav", fetch=lambda u, n: RIFF,
            convert=converter, resolver=PUBLIC_DNS)
        self.assertEqual(out, RIFF)
        self.assertEqual(converter.call_count, 0)

    def test_non_riff_payload_is_converted(self):
        out = ma.audio_bytes_from_url(
            "https://example.com/a.mp3", fetch=lambda u, n: b"ID3mp3",
            convert=lambda data: RIFF, resolver=PUBLIC_DNS)
        self.assertEqual(out, RIFF)

    def test_converter_returning_non_wav_is_rejected(self):
        with self.assertRaises(ValueError):
            ma.audio_bytes_from_url(
                "https://example.com/a.mp3", fetch=lambda u, n: b"ID3",
                convert=lambda data: b"nope", resolver=PUBLIC_DNS)


class TranscribeItemTest(unittest.TestCase):
    def test_bytes_on_item_is_transcribed(self):
        item = FakeItem(extra={"bytes": b"ID3"})
        text, conf = ma.transcribe_item_audio(
            item, transcribe=lambda raw: ("你好", 0.9),
            convert=lambda data: RIFF,
        )
        self.assertEqual((text, conf), ("你好", 0.9))

    def test_wav_bytes_skip_conversion(self):
        item = FakeItem(extra={"bytes": RIFF})
        seen = {}

        def transcribe(raw):
            seen["raw"] = raw
            return "好", 0.8

        ma.transcribe_item_audio(
            item, transcribe=transcribe,
            convert=mock.Mock(side_effect=AssertionError("不该转")))
        self.assertEqual(seen["raw"], RIFF)

    def test_bytes_list_uses_first_item(self):
        item = FakeItem(extra={"bytes_list": [RIFF, b"other"]})
        self.assertEqual(
            ma.transcribe_item_audio(item, transcribe=lambda raw: ("甲", 0.5)),
            ("甲", 0.5))

    def test_url_path_downloads_then_transcribes(self):
        item = FakeItem(url="https://example.com/a.wav")
        self.assertEqual(
            ma.transcribe_item_audio(
                item, transcribe=lambda raw: ("乙", 0.7),
                fetch=lambda u, n: RIFF, resolver=PUBLIC_DNS),
            ("乙", 0.7))

    def test_nothing_available_returns_empty(self):
        self.assertEqual(
            ma.transcribe_item_audio(FakeItem(), transcribe=lambda raw: ("不该", 1.0)),
            ("", 0.0))


class DefaultFetchTest(unittest.TestCase):
    def test_content_length_over_limit_is_rejected_early(self):
        class Response:
            headers = {"Content-Length": str(10 * 1024 * 1024)}

            def read(self, n):  # noqa: ARG002
                raise AssertionError("不该继续读")

            def __enter__(self):
                return self

            def __exit__(self, *a):  # noqa: ANN002
                return False

        with mock.patch.object(ma.net, "urlopen", return_value=Response()):
            with self.assertRaises(UrlGuardError) as ctx:
                ma._default_fetch("https://example.com/a.wav", 1024)
        self.assertEqual(ctx.exception.code, "audio_too_large")

    def test_network_error_is_wrapped(self):
        with mock.patch.object(ma.net, "urlopen", side_effect=OSError("断网")):
            with self.assertRaises(UrlGuardError) as ctx:
                ma._default_fetch("https://example.com/a.wav", 1024)
        self.assertEqual(ctx.exception.code, "audio_download_failed")

    def test_body_over_limit_is_rejected_after_read(self):
        class Response:
            headers = {}

            def read(self, n):
                return b"x" * n

            def __enter__(self):
                return self

            def __exit__(self, *a):  # noqa: ANN002
                return False

        with mock.patch.object(ma.net, "urlopen", return_value=Response()):
            with self.assertRaises(UrlGuardError) as ctx:
                ma._default_fetch("https://example.com/a.wav", 16)
        self.assertEqual(ctx.exception.code, "audio_too_large")


class FfmpegTest(unittest.TestCase):
    def test_missing_ffmpeg_is_explicit(self):
        with mock.patch.object(ma.shutil, "which", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                ma._ffmpeg_to_wav(b"ID3")
        self.assertIn("ffmpeg", str(ctx.exception))

    def test_ffmpeg_failure_surfaces_stderr(self):
        fake = mock.Mock(returncode=1, stdout=b"", stderr=b"Invalid data found")
        with mock.patch.object(ma.shutil, "which", return_value="ffmpeg"), \
             mock.patch.object(ma.subprocess, "run", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                ma._ffmpeg_to_wav(b"ID3")
        self.assertIn("Invalid data found", str(ctx.exception))

    def test_ffmpeg_success_returns_wav(self):
        fake = mock.Mock(returncode=0, stdout=RIFF, stderr=b"")
        with mock.patch.object(ma.shutil, "which", return_value="ffmpeg"), \
             mock.patch.object(ma.subprocess, "run", return_value=fake):
            self.assertEqual(ma._ffmpeg_to_wav(b"ID3"), RIFF)


if __name__ == "__main__":
    unittest.main()
