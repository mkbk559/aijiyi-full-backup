"""RegisterHotKey + WM_HOTKEY 冒烟：注册 Alt+Space，收到一次就算通过。

用法：后台运行本脚本，然后在别处按（或注入）Alt+Space。
    python tools/hotkey_smoketest.py [等待秒数]
退出码 0 = 收到热键；1 = 超时未收到。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import sys
import time


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    u = ctypes.windll.user32
    MOD_ALT, VK_SPACE, WM_HOTKEY, PM_REMOVE = 0x0001, 0x20, 0x0312, 0x0001
    HK_ID = 0xB1F1

    u.RegisterHotKey.argtypes = (w.HWND, ctypes.c_int, w.UINT, w.UINT)
    u.RegisterHotKey.restype = w.BOOL
    ok = u.RegisterHotKey(None, HK_ID, MOD_ALT, VK_SPACE)
    print("register Alt+Space:", bool(ok), flush=True)
    if not ok:
        print("RESULT: REGISTER_FAILED")
        return 1

    msg = w.MSG()
    hits = 0
    t0 = time.time()
    try:
        while time.time() - t0 < seconds:
            if u.PeekMessageW(ctypes.byref(msg), None, WM_HOTKEY, WM_HOTKEY, PM_REMOVE):
                if msg.message == WM_HOTKEY and int(msg.wParam) == HK_ID:
                    hits += 1
                    print(f"WM_HOTKEY received #{hits} at {round((time.time() - t0) * 1000)}ms",
                          flush=True)
                    break
            time.sleep(0.02)
    finally:
        u.UnregisterHotKey(None, HK_ID)
    print("RESULT:", "GOT_HOTKEY" if hits else "TIMEOUT")
    return 0 if hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
