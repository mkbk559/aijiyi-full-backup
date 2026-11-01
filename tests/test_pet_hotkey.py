"""热键解析测试（纯函数，不碰系统热键，避免和运行中的桌宠抢注册）。"""

from __future__ import annotations

import unittest

from aiji.pet.app import KEY_NAMES, MOD_ALT, MOD_CONTROL, MOD_SHIFT, parse_hotkey


class TestParseHotkey(unittest.TestCase):
    def test_default_alt_space(self):
        self.assertEqual(parse_hotkey("alt+space"), (MOD_ALT, 0x20))

    def test_ctrl_shift_letter(self):
        mods, vk = parse_hotkey("ctrl+shift+j")
        self.assertEqual(mods, MOD_CONTROL | MOD_SHIFT)
        self.assertEqual(vk, ord("J"))

    def test_alt_q(self):
        self.assertEqual(parse_hotkey("alt+q"), (MOD_ALT, ord("Q")))

    def test_function_keys(self):
        self.assertEqual(parse_hotkey("ctrl+f1")[1], 0x70)
        self.assertEqual(parse_hotkey("ctrl+f12")[1], 0x7B)

    def test_backtick(self):
        self.assertEqual(parse_hotkey("ctrl+`")[1], 0xC0)

    def test_case_and_space_insensitive(self):
        self.assertEqual(parse_hotkey(" ALT + SPACE "), (MOD_ALT, 0x20))

    def test_missing_key_rejected(self):
        with self.assertRaises(ValueError):
            parse_hotkey("alt+")

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            parse_hotkey("alt+nope")

    def test_modifier_only_rejected(self):
        with self.assertRaises(ValueError):
            parse_hotkey("ctrl+shift")

    def test_key_table_covers_digits_and_space(self):
        for d in range(10):
            self.assertEqual(KEY_NAMES[str(d)], 0x30 + d)
        self.assertEqual(KEY_NAMES["space"], 0x20)


if __name__ == "__main__":
    unittest.main()
