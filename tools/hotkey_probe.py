"""全局热键探针：轮询 GetAsyncKeyState，打印 Alt / Space 的按下与松开。

用法：
    python tools/hotkey_probe.py [秒数]
在另一个窗口/脚本里按 Alt+Space，看这里是否有 ALT_DOWN / SPACE_DOWN 记录。
"""

from __future__ import annotations

import ctypes
import sys
import time

VK_MENU, VK_SPACE = 0x12, 0x20


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    u = ctypes.windll.user32
    t0 = time.time()
    prev = (False, False)
    seen: list[str] = []
    while time.time() - t0 < seconds:
        alt = bool(u.GetAsyncKeyState(VK_MENU) & 0x8000)
        sp = bool(u.GetAsyncKeyState(VK_SPACE) & 0x8000)
        if (alt, sp) != prev:
            stamp = round((time.time() - t0) * 1000)
            seen.append(f"{stamp:>5}ms alt={int(alt)} space={int(sp)}")
            prev = (alt, sp)
        time.sleep(0.01)
    print("transitions:", len(seen))
    for line in seen:
        print(line)
    print("RESULT:", "SAW_BOTH" if any("alt=1" in s and "space=1" in s for s in seen)
          else "NO_SIMULTANEOUS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
