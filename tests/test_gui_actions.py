"""桌面自动化（`left_hand/actions/gui.py`）——原先 33% 覆盖。

真发鼠标/键盘事件不行（会动到用户桌面），所以把 `ctypes.windll` 换成假对象，
只验"参数校验 + 事件序列 + 返回话术"。
顺带修掉一个真 bug：`button` 只认 "right"，其它值（含拼错的 "middle"）静默当左键。
"""
import json
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aiji.left_hand.actions import gui  # noqa: E402


class FakeUser32:
    def __init__(self, width=1920, height=1080, send_input=None):
        self.width = width
        self.height = height
        self.events = []
        self.keys = []
        self.send_input = send_input
        self.sent_counts = []
        self.cursor = None

    def GetSystemMetrics(self, index):  # noqa: N802
        return self.width if index == 0 else self.height

    def SetCursorPos(self, x, y):  # noqa: N802
        self.cursor = (x, y)
        return 1

    def mouse_event(self, flags, dx, dy, data, extra):  # noqa: N802, ARG002
        self.events.append(flags)
        return 1

    def keybd_event(self, vk, scan, flags, extra):  # noqa: N802, ARG002
        self.keys.append((vk, flags))
        return 1

    def SendInput(self, count, ptr, size):  # noqa: N802, ARG002
        self.sent_counts.append(count)
        if self.send_input is None:
            return count
        return self.send_input


def fake_windll(user32):
    return mock.patch.object(gui.ctypes, "windll", mock.Mock(user32=user32))


class ParseHotkeyTest(unittest.TestCase):
    def test_modifiers_and_letters(self):
        self.assertEqual(gui.parse_hotkey("ctrl+s"), [0x11, ord("S")])
        self.assertEqual(gui.parse_hotkey("CTRL + ALT + delete"), [0x11, 0x12, 0x2E])

    def test_function_digit_and_named_keys(self):
        self.assertEqual(gui.parse_hotkey("f5"), [0x74])
        self.assertEqual(gui.parse_hotkey("0"), [0x30])
        self.assertEqual(gui.parse_hotkey("pageup"), [0x21])

    def test_unknown_key_is_rejected(self):
        with self.assertRaises(ValueError):
            gui.parse_hotkey("ctrl+不存在的键")

    def test_empty_hotkey_is_rejected(self):
        with self.assertRaises(ValueError):
            gui.parse_hotkey("")


class WindowsGuardTest(unittest.TestCase):
    def test_non_windows_platform_refuses(self):
        with mock.patch.object(gui.sys, "platform", "linux"):
            with self.assertRaises(RuntimeError):
                gui._mouse_click(1, 1)

    def test_non_windows_platform_refuses_typing(self):
        with mock.patch.object(gui.sys, "platform", "darwin"):
            with self.assertRaises(RuntimeError):
                gui._send_unicode("你好")


class ScreenshotTest(unittest.TestCase):
    def test_delegates_to_desktop_sense(self):
        seen = {"screen": [1920, 1080], "windows": ["记事本"], "text": "你好"}
        with mock.patch("aiji.endocrine.desktop_sense.look", return_value=seen) as look:
            out = gui.execute_desktop("screenshot", {"include_ocr": False})
        look.assert_called_once_with(do_ocr=False)
        self.assertEqual(json.loads(out)["windows"], ["记事本"])

    def test_output_is_truncated(self):
        seen = {"text": "字" * 40000}
        with mock.patch("aiji.endocrine.desktop_sense.look", return_value=seen):
            out = gui.execute_desktop("screenshot", {})
        self.assertEqual(len(out), 20_000)


class MouseClickTest(unittest.TestCase):
    def test_left_click_sends_down_up_once(self):
        user32 = FakeUser32()
        with fake_windll(user32):
            out = gui._mouse_click(100, 200)
        self.assertEqual(user32.cursor, (100, 200))
        self.assertEqual(user32.events, [0x0002, 0x0004])
        self.assertEqual(out, "clicked left at 100,200")

    def test_right_click_uses_right_flags(self):
        user32 = FakeUser32()
        with fake_windll(user32):
            gui._mouse_click(10, 10, button="right")
        self.assertEqual(user32.events, [0x0008, 0x0010])

    def test_clicks_are_clamped_to_three(self):
        user32 = FakeUser32()
        with fake_windll(user32), mock.patch.object(gui.time, "sleep"):
            gui._mouse_click(10, 10, clicks=99)
        self.assertEqual(len(user32.events), 6, "3 次 × (down+up)")

    def test_unknown_button_is_rejected(self):
        user32 = FakeUser32()
        with fake_windll(user32):
            with self.assertRaises(ValueError):
                gui._mouse_click(10, 10, button="middle")
        self.assertEqual(user32.events, [], "不能静默当左键点下去")

    def test_out_of_screen_coordinates_are_rejected(self):
        user32 = FakeUser32(width=100, height=100)
        with fake_windll(user32):
            with self.assertRaises(ValueError):
                gui._mouse_click(150, 50)


class TypeTextTest(unittest.TestCase):
    def test_unicode_text_sends_two_events_per_char(self):
        user32 = FakeUser32()
        with fake_windll(user32):
            out = gui._send_unicode("你好a")
        self.assertEqual(out, "typed 3 chars")
        # 每个字符两条事件：0x0004（keydown）+ 0x0004|0x0002（keyup）
        self.assertEqual(user32.sent_counts, [6])

    def test_empty_text_short_circuits(self):
        self.assertEqual(gui._send_unicode(""), "typed 0 chars")

    def test_partial_send_raises(self):
        user32 = FakeUser32(send_input=0)
        with fake_windll(user32):
            with self.assertRaises(RuntimeError) as ctx:
                gui._send_unicode("ab")
        self.assertIn("SendInput accepted 0/4", str(ctx.exception))


class HotkeyTest(unittest.TestCase):
    def test_presses_then_releases_in_reverse(self):
        user32 = FakeUser32()
        with fake_windll(user32), mock.patch.object(gui.time, "sleep"):
            out = gui._hotkey("ctrl+s")
        self.assertEqual(out, "pressed ctrl+s")
        self.assertEqual(user32.keys, [(0x11, 0), (ord("S"), 0),
                                       (ord("S"), 0x0002), (0x11, 0x0002)])


class DispatchTest(unittest.TestCase):
    def test_unknown_tool_is_rejected(self):
        with self.assertRaises(ValueError):
            gui.execute_desktop("format_disk", {})

    def test_type_text_route(self):
        user32 = FakeUser32()
        with fake_windll(user32):
            self.assertEqual(gui.execute_desktop("type_text", {"text": "嗨"}),
                             "typed 1 chars")

    def test_hotkey_route_uses_argument(self):
        user32 = FakeUser32()
        with fake_windll(user32), mock.patch.object(gui.time, "sleep"):
            self.assertEqual(gui.execute_desktop("hotkey", {"keys": "esc"}),
                             "pressed esc")


if __name__ == "__main__":
    unittest.main()
