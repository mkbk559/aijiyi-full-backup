"""热键能力探针：分别验证 GetAsyncKeyState 与 RegisterHotKey。

输出三项事实：
1. GetAsyncKeyState 能否读到 SendInput 注入的按键（读不到说明这条路在本环境废）
2. RegisterHotKey(MOD_ALT, VK_SPACE) 是否成功、失败错误码
3. 备选热键 MOD_ALT+VK_Q / MOD_CONTROL+VK_SPACE 是否成功
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import time

u = ctypes.windll.user32

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
VK_SPACE, VK_Q = 0x20, 0x51

u.RegisterHotKey.argtypes = (w.HWND, ctypes.c_int, w.UINT, w.UINT)
u.RegisterHotKey.restype = w.BOOL
u.UnregisterHotKey.argtypes = (w.HWND, ctypes.c_int)
u.GetLastError = ctypes.windll.kernel32.GetLastError  # 同一 DLL 的 GetLastError 即可
GetLastError = ctypes.windll.kernel32.GetLastError


def key_state_probe() -> None:
    print("== 1) GetAsyncKeyState 基线 ==")
    print("   当前 Space 状态:", bool(u.GetAsyncKeyState(VK_SPACE) & 0x8000))
    print("   当前 Alt   状态:", bool(u.GetAsyncKeyState(VK_MENU := 0x12) & 0x8000))
    print("   (这两个值反映此刻真实键盘状态，若一直 False 而你在按，说明读不到)")


def register_probe() -> None:
    print("== 2) RegisterHotKey 能力 ==")
    cases = [
        ("MOD_ALT + SPACE", MOD_ALT, VK_SPACE),
        ("MOD_ALT + Q", MOD_ALT, VK_Q),
        ("MOD_CONTROL + SPACE", MOD_CONTROL, VK_SPACE),
    ]
    registered = []
    for i, (name, mod, vk) in enumerate(cases, start=1):
        ok = u.RegisterHotKey(None, 0x9000 + i, mod, vk)
        err = GetLastError()
        print(f"   {name:<22} ok={bool(ok)} err={err}")
        if ok:
            registered.append(0x9000 + i)
    for hk in registered:
        u.UnregisterHotKey(None, hk)
    print("   （err=1409 表示已被别的程序占用；err=0 且 ok=0 少见）")


if __name__ == "__main__":
    key_state_probe()
    register_probe()
    time.sleep(0.1)
