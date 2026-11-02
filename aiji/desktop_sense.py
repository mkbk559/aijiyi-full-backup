"""桌面截图、OCR 与活动窗口标题，供受控桌面工具使用。

设计约束：
- **不猜**：OCR 没认出字就说没认出，窗口标题拿不到就返回空列表。
- **不越权**：只读屏幕像素和窗口标题，不读文件、不读剪贴板、不监听输入。
- **可无头测试**：`look()` 返回纯 dict，逻辑可独立测试。
"""

from __future__ import annotations

import ctypes
import io
import time
from ctypes import wintypes
from typing import Any

MAX_OCR_TEXT = 1200       # 桌面文字上限（再长就不是"看桌面"而是"抄屏幕"）
MAX_WINDOWS = 12


def window_titles(limit: int = MAX_WINDOWS) -> list[str]:
    """前台可见窗口标题（Windows EnumWindows）。失败返回 []，绝不抛。"""
    try:
        user32 = ctypes.windll.user32
    except (AttributeError, OSError):
        return []
    titles: list[str] = []
    try:
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lparam):  # noqa: ANN001
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            t = (buf.value or "").strip()
            if t:
                titles.append(t)
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except (OSError, ValueError):
        return []
    # 去重保序
    seen: set[str] = set()
    out = []
    for t in titles:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:limit]


def grab_screen() -> Any:
    """截全屏，返回 PIL.Image。"""
    from PIL import ImageGrab

    return ImageGrab.grab()


def ocr_image_bytes(png: bytes) -> tuple[str, float]:
    """PNG 字节 → (文字, 平均置信)。引擎缺位返回 ("", 0.0)，不抛。"""
    try:
        from . import real_engines as re
        if not re.HAS_OCR:
            return "", 0.0
        return re.ocr_image(png)
    except Exception:  # noqa: BLE001 - OCR 失败不拖垮桌面工具
        return "", 0.0


def look(*, do_ocr: bool = True, max_text: int = MAX_OCR_TEXT) -> dict[str, Any]:
    """看一眼桌面：屏幕尺寸 + 窗口标题 + 屏上文字。"""
    t0 = time.time()
    out: dict[str, Any] = {
        "at": time.time(), "screen": None, "windows": [], "text": "",
        "ocr_conf": 0.0, "took_ms": 0.0, "note": "",
    }
    try:
        img = grab_screen()
    except Exception as e:  # noqa: BLE001
        out["note"] = f"截屏失败：{type(e).__name__}"
        out["took_ms"] = round((time.time() - t0) * 1000, 1)
        return out
    out["screen"] = list(img.size)
    out["windows"] = window_titles()

    if do_ocr:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        text, conf = ocr_image_bytes(buf.getvalue())
        out["text"] = (text or "")[:max_text]
        out["ocr_conf"] = round(float(conf), 3)
    out["took_ms"] = round((time.time() - t0) * 1000, 1)
    if not out["text"] and not out["windows"]:
        out["note"] = "屏幕上没读出字，也没拿到窗口标题"
    return out


def render(lk: dict[str, Any]) -> str:
    """把 look() 的结果压成给模型看的一小段描述。"""
    parts = []
    if lk.get("windows"):
        parts.append("开着的窗口：" + "；".join(lk["windows"][:MAX_WINDOWS]))
    if lk.get("text"):
        parts.append("屏幕上的字：" + " ".join(str(lk["text"]).split()))
    if not parts:
        return lk.get("note") or "屏幕上什么都没有。"
    return "\n".join(parts)


__all__ = ["grab_screen", "look", "ocr_image_bytes", "render", "window_titles"]
