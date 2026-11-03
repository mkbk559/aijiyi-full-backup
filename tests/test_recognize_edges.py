"""识别层的边界：超时、空结果、低置信、无引擎、SSRF 守卫。

这层是"任何输入 → 标准文本"的唯一入口，几条硬约束容易被悄悄破坏：
- 空结果不能记成低置信（否则"OCR 质量差"这种结论凭空成立）
- 没有引擎必须如实 `unsupported`，**不许编文本**
- 每个识别器都要受超时管；超时要能取消，别把 worker 线程挂住
- 链接必须先过 SSRF 守卫，解析器可注入（离线/测试不该因 DNS 抖动变 error）
"""
from __future__ import annotations

import pathlib
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aiji.hippocampus import recognize as rec  # noqa: E402
from aiji.hippocampus.recognize import Envelope, Item, Recognizers  # noqa: E402
from aiji.throat.net_guard import UrlGuardError  # noqa: E402


def public_resolver(_host: str) -> list[str]:
    return ["93.184.216.34"]


class CleanTest(unittest.TestCase):
    def test_zero_bytes_and_whitespace_are_normalized(self):
        self.assertEqual(rec._clean("a\x00b"), ("a b", False))
        self.assertEqual(rec._clean("  a \t\t b  "), ("a b", False))
        self.assertEqual(rec._clean("a\n\n\n\nb"), ("a\n\nb", False))
        self.assertEqual(rec._clean(None), ("", False))

    def test_overlong_text_is_truncated_and_flagged(self):
        text, truncated = rec._clean("字" * (rec.MAX_TEXT + 10))
        self.assertEqual(len(text), rec.MAX_TEXT)
        self.assertTrue(truncated)


class TimedTest(unittest.TestCase):
    def test_returns_value(self):
        self.assertEqual(rec._timed(lambda: 42, 2.0), 42)

    def test_timeout_cancels_future(self):
        def slow():
            time.sleep(0.4)
            return "太迟了"

        with self.assertRaises(rec.FutureTimeout):
            rec._timed(slow, 0.05)


class DefaultFetchTest(unittest.TestCase):
    class _Response:
        def __init__(self, raw, ctype):
            self._raw, self.headers = raw, {"Content-Type": ctype}

        def read(self, _n):
            return self._raw

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def test_charset_is_honoured(self):
        page = self._Response("标题".encode("gb18030"),
                              "text/html; charset=gb18030")
        with mock.patch.object(rec.urllib.request, "urlopen", return_value=page):
            self.assertEqual(rec._default_fetch("https://e.com"), "标题")

    def test_unknown_charset_falls_back_to_utf8(self):
        page = self._Response("标题".encode("utf-8"),
                              "text/html; charset=不存在的编码")
        with mock.patch.object(rec.urllib.request, "urlopen", return_value=page):
            self.assertEqual(rec._default_fetch("https://e.com"), "标题")

    def test_no_charset_uses_utf8(self):
        page = self._Response("标题".encode("utf-8"), "text/html")
        with mock.patch.object(rec.urllib.request, "urlopen", return_value=page):
            self.assertEqual(rec._default_fetch("https://e.com"), "标题")


class EnvelopeTest(unittest.TestCase):
    def test_usable_requires_text_and_ok_status(self):
        self.assertTrue(Envelope("text", "", "有内容", 1.0, "ok", "text").usable)
        self.assertFalse(Envelope("text", "", "   ", 1.0, "ok", "text").usable)
        self.assertFalse(Envelope("text", "", "有", 0.1, "low_confidence", "text").usable)
        self.assertTrue(Envelope("video", "", "x", 0.0, "metadata_only", "m").usable)

    def test_to_dict_rounds(self):
        out = Envelope("image", "r1", "t", 0.7361, "ok", "ocr",
                       elapsed_ms=1.23456).to_dict()
        self.assertEqual(out["confidence"], 0.736)
        self.assertEqual(out["elapsed_ms"], 1.235)
        self.assertEqual(out["kind"], "image")


class DispatchTest(unittest.TestCase):
    # 超时给足 2 秒：识别器跑在共享线程池里，整包 suite 满负载时
    # 一个"瞬时"的 lambda 也可能排队超过 0.2 秒（v48 就这么抖过一次：
    # status 从 empty 变 timeout）。要测超时的用例自己造 0.2 秒的实例。
    def make(self, **kwargs):
        return Recognizers(resolver=public_resolver, timeout_s=2.0, **kwargs)

    def test_unknown_kind_is_unsupported(self):
        env = self.make().recognize(Item(kind="hologram", ref="r"))
        self.assertEqual(env.status, "unsupported")
        self.assertEqual(env.meta["reason"], "unknown_kind:hologram")

    def test_recognizer_exception_becomes_error_not_crash(self):
        def boom(_item):
            raise RuntimeError("引擎挂了")

        env = self.make(ocr=boom).recognize(Item(kind="image", ref="r", url="u"))
        self.assertEqual(env.status, "error")
        self.assertIn("引擎挂了", env.meta["reason"])

    def test_timeout_is_reported_as_timeout(self):
        def slow(_item):
            time.sleep(0.5)
            return "迟到"

        recog = Recognizers(resolver=public_resolver, timeout_s=0.2, ocr=slow)
        env = recog.recognize(Item(kind="image", ref="r", url="u"))
        self.assertEqual(env.status, "timeout")
        self.assertEqual(env.meta["timeout_s"], 0.2)

    def test_empty_result_is_empty_not_low_confidence(self):
        env = self.make(ocr=lambda _i: ("", 0.9)).recognize(
            Item(kind="image", ref="r", url="u"))
        self.assertEqual(env.status, "empty")
        self.assertEqual(env.meta["reason"], "no_text")

    def test_low_confidence_is_flagged(self):
        env = self.make(ocr=lambda _i: ("模模糊糊", 0.2)).recognize(
            Item(kind="image", ref="r", url="u"))
        self.assertEqual(env.status, "low_confidence")

    def test_truncation_is_recorded(self):
        env = self.make().recognize(Item(kind="text", text="字" * (rec.MAX_TEXT + 5)))
        self.assertTrue(env.meta["truncated"])

    def test_stats_count_statuses(self):
        recog = self.make()
        recog.recognize(Item(kind="text", text="你好"))
        recog.recognize(Item(kind="text", text="   "))
        stats = recog.stats()
        self.assertEqual(stats["calls"], 2)
        self.assertEqual(stats["by_status"], {"ok": 1, "unsupported": 1})
        self.assertFalse(stats["ocr"])
        self.assertFalse(stats["asr"])
        self.assertEqual(stats["min_confidence"], 0.5)


class RecognizerTest(unittest.TestCase):
    def make(self, **kwargs):
        return Recognizers(resolver=public_resolver, timeout_s=0.5, **kwargs)

    def test_text(self):
        env = self.make().recognize(Item(kind="text", text="  你好  "))
        self.assertEqual((env.text, env.status, env.confidence), ("你好", "ok", 1.0))
        empty = self.make().recognize(Item(kind="text", text="   "))
        self.assertEqual(empty.meta["reason"], "empty_text")

    def test_emoji_labels(self):
        env = self.make().recognize(Item(kind="emoji", text="今天真好😀🎉"))
        self.assertEqual(env.status, "ok")
        self.assertIn("表情：开心、庆祝", env.text)
        self.assertIn("开心", env.meta["labels"])

    def test_emoji_only_text(self):
        env = self.make().recognize(Item(kind="emoji", text="😭"))
        self.assertEqual(env.text, "用户发了表情：难过")

    def test_emoji_without_known_emoji_keeps_raw(self):
        env = self.make().recognize(Item(kind="emoji", text="就是普通的话"))
        self.assertEqual((env.text, env.status), ("就是普通的话", "ok"))
        self.assertEqual(env.meta["labels"], [])

    def test_emoji_empty(self):
        env = self.make().recognize(Item(kind="emoji", text="  "))
        self.assertEqual(env.meta["reason"], "empty_text")

    def test_image_without_engine_says_so(self):
        env = self.make().recognize(Item(kind="image", ref="r", url="https://e/i.png"))
        self.assertEqual(env.status, "unsupported")
        self.assertEqual(env.meta["reason"], "no_ocr_engine")
        self.assertEqual(env.text, "", "没有引擎时绝不能编文本")

    def test_image_scalar_result_gets_default_confidence(self):
        env = self.make(ocr=lambda _i: "发票金额 1280 元").recognize(
            Item(kind="image", ref="r", url="https://e/i.png", mime="image/png"))
        self.assertEqual(env.text, "发票金额 1280 元")
        self.assertEqual(env.confidence, 0.7)
        self.assertEqual(env.recognizer, "ocr")

    def test_voice_keeps_duration(self):
        env = self.make(asr=lambda _i: ("今天天气不错", 0.8)).recognize(
            Item(kind="voice", ref="r", url="https://e/v.amr",
                 extra={"duration_s": 3.2}))
        self.assertEqual(env.meta["duration_s"], 3.2)
        self.assertEqual(env.recognizer, "asr")
        self.assertEqual(env.confidence, 0.8)

    def test_voice_without_engine(self):
        env = self.make().recognize(Item(kind="voice", ref="r"))
        self.assertEqual(env.meta["reason"], "no_asr_engine")

    def test_file_prefers_caller_text(self):
        env = self.make().recognize(
            Item(kind="file", ref="r", text="文件里的内容", mime="text/plain"))
        self.assertEqual(env.recognizer, "caller-text")
        self.assertEqual(env.status, "ok")

    def test_textual_mime_without_text_asks_caller(self):
        env = self.make().recognize(Item(kind="file", ref="r", mime="text/markdown"))
        self.assertEqual(env.meta["reason"], "need_text_from_caller")

    def test_binary_mime_is_unsupported(self):
        env = self.make().recognize(Item(kind="file", ref="r", mime="application/pdf"))
        self.assertEqual(env.meta["reason"], "no_extractor_for_mime")

    def test_link_not_a_url(self):
        env = self.make().recognize(Item(kind="link", text="随便一段话"))
        self.assertEqual(env.meta["reason"], "not_a_url")

    def test_link_guard_blocks_private_host(self):
        def local_resolver(_host):
            return ["127.0.0.1"]

        env = Recognizers(resolver=local_resolver).recognize(
            Item(kind="link", url="http://localhost/api/self"))
        self.assertEqual(env.status, "error")
        self.assertEqual(env.meta["reason"], "private_url")
        # 端口级拦截是另一条规则（默认只放 80/443），单独钉一下
        port = Recognizers(resolver=local_resolver).recognize(
            Item(kind="link", url="http://example.com:8787/x"))
        self.assertEqual(port.meta["reason"], "port_not_allowed")

    def test_link_title(self):
        env = self.make(fetcher=lambda _u: "<html><title> 一个 标题 </title></html>").recognize(
            Item(kind="link", url="https://example.com/a"))
        self.assertEqual((env.text, env.status), ("一个 标题", "ok"))
        self.assertEqual(env.confidence, 0.6)

    def test_link_without_title(self):
        env = self.make(fetcher=lambda _u: "<html>没有标题</html>").recognize(
            Item(kind="link", url="https://example.com/a"))
        self.assertEqual(env.meta["reason"], "no_title")

    def test_link_unescapes_entities(self):
        env = self.make(fetcher=lambda _u: "<title>A &amp; B</title>").recognize(
            Item(kind="link", url="https://example.com/a"))
        self.assertEqual(env.text, "A & B")

    def test_video_is_metadata_only(self):
        env = self.make().recognize(
            Item(kind="video", ref="r", url="https://e/v.mp4", size_bytes=1024,
                 extra={"duration_s": 12.0}))
        self.assertEqual(env.status, "metadata_only")
        self.assertEqual(env.text, "", "视频不进抽取管线")
        self.assertEqual(env.meta["duration_s"], 12.0)
        self.assertFalse(env.usable)


class GuardErrorTest(unittest.TestCase):
    def test_url_guard_error_code_is_kept(self):
        recog = Recognizers(resolver=public_resolver)
        with mock.patch.object(rec, "validate_public_http_url",
                               side_effect=UrlGuardError("blocked_host", "拒绝")):
            env = recog.recognize(Item(kind="link", url="https://evil.example/x"))
        self.assertEqual(env.status, "error")
        self.assertEqual(env.meta["reason"], "blocked_host")


if __name__ == "__main__":
    unittest.main()
