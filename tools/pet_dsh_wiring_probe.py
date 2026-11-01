"""验证「全功能」开关的接线：勾上后消息是否真的走 app.dsh_ask，答案是否回到聊天区。

不启动真 agent（省钱省时）：用 StubApp.dsh_ask 返回带 tools 的假结果，
断言聊天记录里出现回答和工具清单，并断言默认（未勾选）时走的是记忆 brain。
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiji.pet.app import Panel  # noqa: E402


class StubBrain:
    def reply(self, text: str) -> dict:
        return {"reply": f"记忆脑回答：{text}", "state": "talk", "source": "stub"}


class StubApp:
    def __init__(self, root):
        self.root = root
        self.brain = StubBrain()
        self.tmp_dir = ".tmp"
        self.state = "idle"
        self.status_text = "ALT+SPACE 唤出 / 收起"
        self.dsh_calls: list[str] = []
        self.turn_done = 0

    def set_state(self, state: str) -> None:
        self.state = state

    def cancel_hover_hide(self) -> None:
        pass

    def on_turn_done(self) -> None:
        self.turn_done += 1

    def dsh_ask(self, text, on_event=None):
        self.dsh_calls.append(text)
        if on_event:
            on_event("用工具：read_file")
        return {"ok": True, "text": "有 42 个 .py 文件。", "error": "",
                "session_id": "session-stub", "took_ms": 1.0,
                "tools": ["read_file", "bash"]}

    def toggle_panel(self) -> None:
        pass


def pump(root, ms: int = 1500) -> None:
    """跑事件循环 ms 毫秒，让后台线程的 after() 回调有机会执行。"""
    import time as _t

    end = _t.time() + ms / 1000
    while _t.time() < end:
        root.update()
        _t.sleep(0.02)


def main() -> int:
    root = tk.Tk()
    root.withdraw()
    app = StubApp(root)
    panel = Panel(app)
    ok = True

    # 1) 默认不勾「全功能」→ 走记忆 brain
    panel.entry.insert(0, "你好")
    panel._send()
    pump(root, 800)
    log1 = panel.log.get("1.0", "end")
    print("默认模式走 dsh 次数:", len(app.dsh_calls))
    if app.dsh_calls:
        print("FAIL: 未勾选却调了 dsh")
        ok = False
    if "记忆脑回答：你好" not in log1:
        print("FAIL: 默认模式没拿到记忆脑回答")
        ok = False

    # 2) 勾上「全功能」→ 走 dsh，答案带工具清单
    panel.use_dsh.set(True)
    panel.entry.insert(0, "数一下 py 文件")
    panel._send()
    pump(root, 1500)
    log2 = panel.log.get("1.0", "end")
    print("全功能模式走 dsh 次数:", len(app.dsh_calls), "内容:", app.dsh_calls)
    if app.dsh_calls != ["数一下 py 文件"]:
        print("FAIL: 勾选后没有把原话交给 dsh")
        ok = False
    if "有 42 个 .py 文件。" not in log2:
        print("FAIL: dsh 的回答没进聊天区")
        ok = False
    if "read_file" not in log2:
        print("FAIL: 工具清单没显示")
        ok = False
    if app.turn_done != 1:
        print(f"FAIL: 亲密度结算次数应为 1，实际 {app.turn_done}")
        ok = False

    print("---- 聊天区文本 ----")
    print(log2.strip()[-400:])
    print("RESULT:", "PASS" if ok else "FAIL")
    root.destroy()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
