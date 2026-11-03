"""桌面感知（`endocrine/desktop_sense.py`）——原先 0% 覆盖。

它不读文件、不读剪贴板，只读屏幕像素与窗口标题；`look()` 返回纯 dict，
所以可以完全不碰真屏幕地测：把 `grab_screen` / `window_titles` / OCR 换掉。
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiji.endocrine import desktop_sense as ds  # noqa: E402


class FakeImage:
    def __init__(self, size=(1024, 768)):
        self.size = size
        self.saved = None

    def convert(self, mode):  # noqa: ARG002
        return self

    def save(self, buf, format="PNG"):  # noqa: A002
        self.saved = format
        buf.write(b"\x89PNG-fake")


class LookTest(unittest.TestCase):
    def test_normal_desktop(self):
        with mock.patch.object(ds, "grab_screen", lambda: FakeImage((1920, 1080))), \
             mock.patch.object(ds, "window_titles", lambda: ["记事本", "浏览器"]), \
             mock.patch.object(ds, "ocr_image_bytes", lambda png: ("待办：买菜", 0.91)):
            out = ds.look()
        self.assertEqual(out["screen"], [1920, 1080])
        self.assertEqual(out["windows"], ["记事本", "浏览器"])
        self.assertEqual(out["text"], "待办：买菜")
        self.assertAlmostEqual(out["ocr_conf"], 0.91)
        self.assertEqual(out["note"], "")
        self.assertGreaterEqual(out["took_ms"], 0.0)

    def test_screenshot_failure_is_reported_not_raised(self):
        def boom():
            raise OSError("no display")

        with mock.patch.object(ds, "grab_screen", boom):
            out = ds.look()
        self.assertEqual(out["note"], "截屏失败：OSError")
        self.assertIsNone(out["screen"])
        self.assertEqual(out["windows"], [])

    def test_ocr_can_be_skipped(self):
        with mock.patch.object(ds, "grab_screen", lambda: FakeImage()), \
             mock.patch.object(ds, "window_titles", lambda: ["终端"]), \
             mock.patch.object(ds, "ocr_image_bytes",
                               side_effect=AssertionError("不该被调用")):
            out = ds.look(do_ocr=False)
        self.assertEqual(out["text"], "")
        self.assertEqual(out["windows"], ["终端"])

    def test_long_text_is_clipped(self):
        with mock.patch.object(ds, "grab_screen", lambda: FakeImage()), \
             mock.patch.object(ds, "window_titles", lambda: []), \
             mock.patch.object(ds, "ocr_image_bytes", lambda png: ("字" * 5000, 0.8)):
            out = ds.look(max_text=100)
        self.assertEqual(len(out["text"]), 100)

    def test_nothing_found_says_so(self):
        with mock.patch.object(ds, "grab_screen", lambda: FakeImage()), \
             mock.patch.object(ds, "window_titles", lambda: []), \
             mock.patch.object(ds, "ocr_image_bytes", lambda png: ("", 0.0)):
            out = ds.look()
        self.assertEqual(out["note"], "屏幕上没读出字，也没拿到窗口标题")

    def test_ocr_engine_missing_returns_empty(self):
        with mock.patch("aiji.hippocampus.real_engines.HAS_OCR", False):
            self.assertEqual(ds.ocr_image_bytes(b"x"), ("", 0.0))

    def test_ocr_exception_returns_empty(self):
        with mock.patch("aiji.hippocampus.real_engines.HAS_OCR", True), \
             mock.patch("aiji.hippocampus.real_engines.ocr_image",
                        side_effect=RuntimeError("engine died")):
            self.assertEqual(ds.ocr_image_bytes(b"x"), ("", 0.0))

    def test_window_titles_never_raises(self):
        titles = ds.window_titles()
        self.assertIsInstance(titles, list)
        self.assertLessEqual(len(titles), ds.MAX_WINDOWS)


class RenderTest(unittest.TestCase):
    def test_windows_and_text(self):
        text = ds.render({"windows": ["记事本"], "text": "第一行\n第二行"})
        self.assertIn("开着的窗口：记事本", text)
        self.assertIn("屏幕上的字：第一行 第二行", text)

    def test_empty_uses_note(self):
        self.assertEqual(ds.render({"windows": [], "text": "", "note": "没东西"}),
                         "没东西")

    def test_blank_has_default(self):
        self.assertEqual(ds.render({}), "屏幕上什么都没有。")


if __name__ == "__main__":
    unittest.main()
