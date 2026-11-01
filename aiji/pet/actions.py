"""豆包式小窗动作：划词取词、打开文件/网址、翻译。

安全边界（对齐 selfhood 的物理底线）：
- 划词=模拟 Ctrl+C 读剪贴板，**读完立刻恢复原剪贴板**，不留痕
- 打开只允许 网址 / 已存在的文件路径，**不执行任意命令**，不静默装东西
- 翻译只把文字交给模型，不碰剪贴板以外的东西
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

_URL_RE = re.compile(r"^(https?://|www\.)", re.I)
_UNSAFE_OPEN_SUFFIXES = frozenset((
    ".bat", ".cmd", ".com", ".exe", ".hta", ".jse", ".js", ".lnk", ".msi",
    ".pif", ".ps1", ".reg", ".scr", ".url", ".vbs", ".wsf", ".wsh",
))


def auth_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    key = os.environ.get("AIJI_API_KEY", "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


# ---------------- 划词 ----------------

def _clip_get() -> str:
    try:
        import win32clipboard as cb

        cb.OpenClipboard()
        try:
            return str(cb.GetClipboardData(cb.CF_UNICODETEXT) or "")
        finally:
            cb.CloseClipboard()
    except Exception:  # noqa: BLE001
        return ""


def _clip_set(text: str) -> None:
    try:
        import win32clipboard as cb

        cb.OpenClipboard()
        try:
            cb.EmptyClipboard()
            cb.SetClipboardText(text, cb.CF_UNICODETEXT)
        finally:
            cb.CloseClipboard()
    except Exception:  # noqa: BLE001
        pass


def _send_ctrl_c() -> None:
    import ctypes

    VK_CONTROL, VK_C = 0x11, 0x43
    KEYUP = 0x0002
    u = ctypes.windll.user32
    u.keybd_event(VK_CONTROL, 0, 0, 0)
    u.keybd_event(VK_C, 0, 0, 0)
    u.keybd_event(VK_C, 0, KEYUP, 0)
    u.keybd_event(VK_CONTROL, 0, KEYUP, 0)


def selected_text(wait: float = 0.28) -> str:
    """读当前选中的文字：模拟 Ctrl+C → 读剪贴板 → 恢复原剪贴板。"""
    old = _clip_get()
    try:
        _clip_set("")
        _send_ctrl_c()
        time.sleep(wait)
        got = _clip_get().strip()
        return got
    finally:
        _clip_set(old)


# ---------------- 打开 ----------------

def open_target(text: str) -> tuple[bool, str]:
    """打开网址或本机文件。只允许这两类，绝不执行命令。"""
    t = (text or "").strip().strip('"').strip("'")
    if not t:
        return False, "没有给要打开的东西"
    if _URL_RE.match(t):
        url = t if t.lower().startswith("http") else "https://" + t
        try:
            os.startfile(url)  # noqa: S606 - 打开浏览器，不是执行
            return True, f"打开网址 {url}"
        except OSError as e:
            return False, f"打不开网址：{e}"
    p = Path(t).expanduser()
    if p.exists():
        target = p.resolve()
        suffixes = {p.suffix.lower(), target.suffix.lower()}
        if target.is_file() and suffixes & _UNSAFE_OPEN_SUFFIXES:
            return False, "出于安全考虑，不直接打开可执行文件或脚本"
        try:
            os.startfile(str(p))  # noqa: S606 - 打开文件，不是执行
            return True, f"打开 {p}"
        except OSError as e:
            return False, f"打不开：{e}"
    return False, f"找不到这个路径：{t}"


def looks_like_target(text: str) -> bool:
    """判断一句话是不是"要打开的东西"（供小窗自动识别）。"""
    t = (text or "").strip().strip('"').strip("'")
    if not t or len(t) > 260 or "\n" in t:
        return False
    if _URL_RE.match(t):
        return True
    return bool(re.match(r"^[A-Za-z]:\\", t)) or t.startswith(("~", "/", ".\\"))


# ---------------- 翻译 ----------------

def translate(brain: Any, text: str, *, to: str = "", timeout_s: float = 30.0) -> dict[str, Any]:
    """把一段文字译成中文/英文。走同一个记忆池，不改人格。"""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "text": "", "reason": "empty"}
    if not brain.has_brain():
        return {"ok": False, "text": "", "reason": "no_model"}
    target = to or ("英文" if re.search(r"[\u4e00-\u9fff]", text) else "中文")
    messages = [
        {"role": "system", "content":
            "你是翻译。只输出译文，不加解释、不加引号、不重复原文。保留原文换行与专有名词。"},
        {"role": "user", "content": f"把下面这段翻成{target}：\n\n{text}"},
    ]
    try:
        res = brain.memory.pool.chat(messages, timeout_s=timeout_s)
        out = (getattr(res, "text", "") or "").strip()
        return {"ok": bool(out), "text": out, "reason": "" if out else "empty_reply",
                "target": target}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "text": "", "reason": f"model_error:{type(e).__name__}"}


__all__ = ["auth_headers", "looks_like_target", "open_target", "selected_text", "translate"]
