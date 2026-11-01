"""小窗布局探针：证明「工具行 + 输入行真的可见、可点」，而不是被对话区挤没。

用法：python tools/pet_panel_probe.py
输出：每个控件的 mapped / 坐标 / 尺寸 + 是否落在窗口内，最后 PASS/FAIL。
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class StubBrain:
    def reply(self, text, **kw):
        return {"text": "stub", "ok": True, "reason": ""}


class StubApp:
    """Panel 只需要这几样东西。"""

    def __init__(self, root):
        self.root = root
        self.brain = StubBrain()
        self.tmp_dir = ".tmp"
        self.state = "idle"
        self.status_text = "ALT+SPACE 唤出 / 收起"

    def set_state(self, s):
        self.state = s


def main() -> int:
    from aiji.pet.app import PANEL_H, PANEL_W, Panel

    root = tk.Tk()
    root.withdraw()
    app = StubApp(root)
    panel = Panel(app)
    panel.top.geometry(f"{PANEL_W}x{PANEL_H}+0+0")
    panel.top.deiconify()
    panel.top.update_idletasks()
    panel.top.update()

    win_h = panel.top.winfo_height()
    win_w = panel.top.winfo_width()
    print(f"window: {win_w}x{win_h}")

    rows = []
    tools_frame = None
    for child in panel.top.winfo_children():
        for sub in child.winfo_children():
            if isinstance(sub, tk.Button) and sub.cget("text") in (
                    "划词", "截图", "语音", "翻译", "打开", "桌面"):
                tools_frame = child
                rows.append(("btn:" + sub.cget("text"), sub))

    entry_bar = panel.entry.master
    rows.append(("entry", panel.entry))
    for w in entry_bar.winfo_children():
        if isinstance(w, tk.Button):
            rows.append(("btn:" + w.cget("text"), w))

    ok = True
    for name, w in rows:
        mapped = w.winfo_ismapped()
        y = w.winfo_rooty() - panel.top.winfo_rooty()
        h = w.winfo_height()
        inside = mapped and h > 1 and 0 <= y < win_h and (y + h) <= win_h + 1
        ok = ok and inside
        print(f"{name:<10} mapped={mapped} y={y:>4} h={h:>3} bottom={y + h:>4} inside={inside}")

    # 输入行整体也要在窗口内
    bar_y = entry_bar.winfo_rooty() - panel.top.winfo_rooty()
    bar_ok = entry_bar.winfo_ismapped() and bar_y + entry_bar.winfo_height() <= win_h + 1
    print(f"{'entry_bar':<10} mapped={entry_bar.winfo_ismapped()} y={bar_y:>4} "
          f"h={entry_bar.winfo_height():>3} inside={bar_ok}")
    ok = ok and bar_ok

    # 对话区必须真的分到了空间（不是 0 高度）
    log_h = panel.log.winfo_height()
    log_ok = panel.log.winfo_ismapped() and log_h > 40
    print(f"{'log':<10} mapped={panel.log.winfo_ismapped()} h={log_h:>3} usable={log_ok}")
    ok = ok and log_ok

    print("RESULT:", "PASS" if ok else "FAIL")
    panel.top.destroy()
    root.destroy()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
