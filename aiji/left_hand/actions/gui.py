"""Windows 桌面自动化：截屏、鼠标和键盘。调用前由 ActionExecutor 审批。"""

from __future__ import annotations

import ctypes
import json
import sys
import time
from typing import Any

_VK = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "space": 0x20,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "backspace": 0x08, "delete": 0x2E,
    **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"},
    **{str(i): 0x30 + i for i in range(10)},
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}


def parse_hotkey(keys: str) -> list[int]:
    out: list[int] = []
    for part in str(keys or "").lower().replace(" ", "").split("+"):
        if part not in _VK:
            raise ValueError(f"unsupported key: {part}")
        out.append(_VK[part])
    if not out:
        raise ValueError("empty hotkey")
    return out


def _windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("desktop automation is only enabled on Windows")


def _screenshot(include_ocr: bool) -> str:
    from aiji.endocrine import desktop_sense
    seen = desktop_sense.look(do_ocr=include_ocr)
    return json.dumps(seen, ensure_ascii=False, default=str)[:20_000]


def _mouse_click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    _windows()
    user32 = ctypes.windll.user32
    width = int(user32.GetSystemMetrics(0))
    height = int(user32.GetSystemMetrics(1))
    x, y = int(x), int(y)
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError("mouse coordinates outside screen")
    user32.SetCursorPos(x, y)
    down, up = ((0x0002, 0x0004) if button == "right" else (0x0002, 0x0004))
    if button == "right":
        down, up = 0x0008, 0x0010
    for _ in range(max(1, min(3, int(clicks or 1)))):
        user32.mouse_event(down, 0, 0, 0, 0)
        user32.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.05)
    return f"clicked {button} at {x},{y}"


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _INPUTUNION)]


def _send_unicode(text: str) -> str:
    _windows()
    clean = str(text or "")[:5000]
    if not clean:
        return "typed 0 chars"
    inputs = []
    for char in clean:
        for flags in (0x0004, 0x0004 | 0x0002):
            item = _INPUT(type=1)
            item.union.ki = _KEYBDINPUT(
                wVk=0, wScan=ord(char), dwFlags=flags, time=0, dwExtraInfo=None,
            )
            inputs.append(item)
    array = (_INPUT * len(inputs))(*inputs)
    sent = ctypes.windll.user32.SendInput(
        len(inputs), ctypes.byref(array), ctypes.sizeof(_INPUT),
    )
    if sent != len(inputs):
        raise RuntimeError(f"SendInput accepted {sent}/{len(inputs)} events")
    return f"typed {len(clean)} chars"


def _hotkey(keys: str) -> str:
    _windows()
    values = parse_hotkey(keys)
    user32 = ctypes.windll.user32
    for vk in values:
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.02)
    for vk in reversed(values):
        user32.keybd_event(vk, 0, 0x0002, 0)
        time.sleep(0.02)
    return f"pressed {keys}"


def execute_desktop(name: str, arguments: dict[str, Any]) -> str:
    if name == "screenshot":
        return _screenshot(bool(arguments.get("include_ocr", True)))
    if name == "mouse_click":
        return _mouse_click(
            int(arguments.get("x", 0)), int(arguments.get("y", 0)),
            str(arguments.get("button") or "left"),
            int(arguments.get("clicks", 1)),
        )
    if name == "type_text":
        return _send_unicode(str(arguments.get("text") or ""))
    if name == "hotkey":
        return _hotkey(str(arguments.get("keys") or ""))
    raise ValueError(f"unsupported desktop tool: {name}")


__all__ = ["execute_desktop", "parse_hotkey"]
