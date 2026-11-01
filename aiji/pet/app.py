"""大肥鱼桌宠 · 豆包式交互（精灵动画 + Alt+Space 小窗）。

形象与动画**照搬插件 @linxin666/dsh-pet 的鲸鱼娘**：
- 同一张 spritesheet.webp、同一份 pet.json 节奏、同一套帧推进语义（`sprite.py`）
- 拖动时播 running-left / running-right，说话 waving，听 waiting，开心 jumping

交互形态对齐豆包：
- 桌面上就是那只鲸鱼娘本体（不是圆形头像），可拖动、贴边、置顶
- 单击展开小窗；双击直接语音；右键菜单
- **Alt+Space** 任意应用里唤出/收起小窗
- 小窗外工具行：划词 / 截图 / 语音 / 翻译 / 打开 / 桌面

人格零硬编码：提示词全部来自 `selfhood.system_prefix()`。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import threading
import time
import tkinter as tk
import urllib.request
from pathlib import Path
from tkinter import scrolledtext

from . import actions, avatar, desk, growth, sprite, voice
from .brain import FishBrain
from .presence_client import PresenceClient

TRANSPARENT = "#010203"
PET_H = 128                      # 桌面上鲸鱼娘的高度（像素）
PANEL_W, PANEL_H = 460, 500
VK_MENU, VK_SPACE = 0x12, 0x20

# 全局热键（RegisterHotKey）常量
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
HK_ID = 0xB1F1
MODIFIER_NAMES = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
                  "shift": MOD_SHIFT, "win": MOD_WIN}
KEY_NAMES = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "`": 0xC0, "~": 0xC0, "-": 0xBD, "=": 0xBB, "": 0x00,
    **{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"},
    **{str(d): 0x30 + d for d in range(10)},
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'alt+space' → (MOD_ALT, VK_SPACE)。解析失败抛 ValueError。"""
    mods, vk = 0, 0
    for part in str(spec or "").lower().replace(" ", "").split("+"):
        if part in MODIFIER_NAMES:
            mods |= MODIFIER_NAMES[part]
        elif part in KEY_NAMES:
            vk = KEY_NAMES[part]
        else:
            raise ValueError(f"看不懂的按键：{part!r}（热键写法如 alt+space / ctrl+shift+j）")
    if not vk:
        raise ValueError(f"热键缺少主键：{spec!r}")
    return mods, vk


def _log(msg: str) -> None:
    """极简运行日志（写 data/pet.log）。GUI 无控制台，出问题只能靠它取证。"""
    try:
        p = Path("data/pet.log")
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:  # noqa: BLE001
        pass


class HotkeyThread(threading.Thread):
    """全局热键监听线程。

    为什么必须单独开线程：`RegisterHotKey(None, ...)` 把 WM_HOTKEY 投递到**注册它的那个线程**
    的消息队列，而 Tk/Tcl 自己的消息泵会先把线程消息取走并丢弃，所以主线程里
    PeekMessage 永远拿不到。这里用独立线程阻塞 `GetMessage` 收，主线程只读计数。
    """

    def __init__(self, spec: str):
        super().__init__(daemon=True)
        self.spec = spec
        self.registered = False
        self.hits = 0
        self.error = ""
        self._thread_id = 0
        self._stop = threading.Event()
        self._ready = threading.Event()

    def run(self) -> None:
        import ctypes.wintypes as wt

        u = ctypes.windll.user32
        try:
            mods, vk = parse_hotkey(self.spec)
            u.RegisterHotKey.argtypes = (wt.HWND, ctypes.c_int, wt.UINT, wt.UINT)
            u.RegisterHotKey.restype = wt.BOOL
            self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
            self.registered = bool(u.RegisterHotKey(None, HK_ID, mods, vk))
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self.registered = False
        finally:
            self._ready.set()

        if not self.registered:
            return

        msg = wt.MSG()
        try:
            while not self._stop.is_set():
                got = u.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if got in (0, -1):
                    break
                if msg.message == WM_HOTKEY and int(msg.wParam) == HK_ID:
                    self.hits += 1
        finally:
            try:
                u.UnregisterHotKey(None, HK_ID)
            except Exception:  # noqa: BLE001
                pass

    def wait_ready(self, timeout: float = 2.0) -> bool:
        self._ready.wait(timeout)
        return self.registered

    def stop(self) -> None:
        self._stop.set()
        try:
            import ctypes.wintypes as wt

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, wt.WM_QUIT, 0, 0)
        except Exception:  # noqa: BLE001
            pass


def _wav_from_pcm(pcm_float32, sr: int = 16000) -> bytes:
    """float32 单声道 PCM → wav 字节（预热 ASR 用）。"""
    import io
    import wave

    import numpy as np

    data = (np.clip(pcm_float32, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data)
    return buf.getvalue()


# ---- 悬停面板（养成：名字 / 亲密度 / 小鱼干 / 点数 + 喂食/改名/隐藏）----


class GrowthPanel:
    """照搬插件 PetSprite 的 hover 面板：显示亲密度与小鱼干，提供喂食/改名/隐藏。"""

    def __init__(self, app: "PetApp"):
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.configure(bg="#FFFFFF")
        frame = tk.Frame(self.top, bg="#FFFFFF", bd=1, relief="solid",
                         highlightbackground="#C9D9F2")
        frame.pack(fill="both", expand=True)

        self.name_lbl = tk.Label(frame, text="大肥鱼", font=("Microsoft YaHei UI", 11, "bold"),
                                 bg="#FFFFFF", fg="#26406E")
        self.name_lbl.grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        self.rank_lbl = tk.Label(frame, text="亲密度 幼鲸", font=("Microsoft YaHei UI", 9),
                                 bg="#FFFFFF", fg="#5B7BA8")
        self.rank_lbl.grid(row=0, column=1, sticky="e", padx=8, pady=(6, 0))
        self.treat_lbl = tk.Label(frame, text="小鱼干 ×0", font=("Microsoft YaHei UI", 9),
                                  bg="#FFFFFF", fg="#5B7BA8")
        self.treat_lbl.grid(row=1, column=0, sticky="w", padx=8)
        self.point_lbl = tk.Label(frame, text="0 点", font=("Microsoft YaHei UI", 9),
                                  bg="#FFFFFF", fg="#5B7BA8")
        self.point_lbl.grid(row=1, column=1, sticky="e", padx=8)

        btns = tk.Frame(frame, bg="#FFFFFF")
        btns.grid(row=2, column=0, columnspan=2, pady=(6, 6), padx=6, sticky="we")
        for label, fn in (("喂食", app.feed_pet), ("改名", app.rename_pet),
                          ("隐藏", app.hide_pet)):
            tk.Button(btns, text=label, relief="flat", bg="#E3EDFF", fg="#26406E",
                      activebackground="#D0E2FF", font=("Microsoft YaHei UI", 9),
                      command=fn).pack(side="left", padx=3)

        # 鼠标移到面板上时不要把它收起来（插件的"桥接"行为）
        for w in (self.top, frame, self.name_lbl, self.rank_lbl, self.treat_lbl,
                  self.point_lbl, btns):
            w.bind("<Enter>", lambda _e: app.cancel_hover_hide())

    def refresh(self) -> None:
        snap = self.app.store.snapshot(time.time() * 1000)
        aff = snap["affinity"]
        self.name_lbl.config(text=snap["name"])
        self.rank_lbl.config(text=f"亲密度 {aff['rank']}")
        self.treat_lbl.config(text=f"小鱼干 ×{snap['treats']}")
        self.point_lbl.config(text=f"{aff['points']} 点")

    def place_near(self, x: int, y: int, w: int, h: int) -> None:
        self.top.update_idletasks()
        pw, ph = self.top.winfo_width(), self.top.winfo_height()
        self.top.geometry(f"+{max(0, x + (w - pw) // 2)}+{max(0, y - ph - 6)}")

    def show(self) -> None:
        self.refresh()
        self.app.cancel_hover_hide()
        self.top.deiconify()
        self.top.lift()
        self.place_near(self.app.root.winfo_x(), self.app.root.winfo_y(),
                        self.app.w_px, self.app.h_px)

    def hide(self) -> None:
        self.top.withdraw()


# ---- 豆包式小窗 ----


class Panel:
    """对话小窗。四段式：头部 / 记录 / 工具行 / 输入行。"""

    TOOLS = (
        ("划词", "_sel"),
        ("截图", "_shot"),
        ("语音", "_voice"),
        ("翻译", "_translate"),
        ("打开", "_open"),
        ("桌面", "_look"),
    )

    def __init__(self, app: "PetApp"):
        self.app = app
        self.brain = app.brain
        self.top = tk.Toplevel(app.root)
        self.top.title("大肥鱼")
        self.top.attributes("-topmost", True)
        self.top.configure(bg="#F5F8FF")
        self.top.geometry(f"{PANEL_W}x{PANEL_H}"
                          f"+{max(0, app.root.winfo_x() - PANEL_W - 12)}+{app.root.winfo_y()}")
        self.top.protocol("WM_DELETE_WINDOW", self.hide)

        # ── 头部：立绘头像 + 名字 + 状态 ──
        head = tk.Frame(self.top, bg="#E8F2FF")
        head.pack(side="top", fill="x")
        from PIL import ImageTk

        self._face = ImageTk.PhotoImage(avatar.head_crop(56, "idle"))
        self.face_label = tk.Label(head, image=self._face, bg="#E8F2FF")
        self.face_label.pack(side="left", padx=(10, 6), pady=6)
        info = tk.Frame(head, bg="#E8F2FF")
        info.pack(side="left", fill="both", expand=True)
        tk.Label(info, text="大肥鱼", font=("Microsoft YaHei UI", 12, "bold"),
                 bg="#E8F2FF", fg="#26406E").pack(anchor="w", pady=(8, 0))
        self.status = tk.Label(info, text=app.status_text, font=("Microsoft YaHei UI", 9),
                               bg="#E8F2FF", fg="#5B7BA8")
        self.status.pack(anchor="w")

        # 全功能模式开关：勾上 = 走 dsh（能读文件/跑命令/用插件与 MCP），不勾 = 走记忆+网关
        self.use_dsh = tk.BooleanVar(value=False)
        tk.Checkbutton(
            head, text="全功能", variable=self.use_dsh,
            bg="#E8F2FF", fg="#26406E", activebackground="#E8F2FF",
            selectcolor="#FFFFFF", font=("Microsoft YaHei UI", 9), bd=0,
        ).pack(side="right", padx=(0, 10))

        # ── 对话记录（最后 pack，避免被底部控件挤掉）──
        self.log = scrolledtext.ScrolledText(self.top, wrap="word", state="disabled",
                                             font=("Microsoft YaHei UI", 10),
                                             bg="#FFFFFF", relief="flat")
        self.log.tag_config("user", foreground="#26406E", font=("Microsoft YaHei UI", 10, "bold"))
        self.log.tag_config("fish", foreground="#3A6BC4", font=("Microsoft YaHei UI", 10, "bold"))

        # ── 输入行（先 pack 到底部，永远可见）──
        bar = tk.Frame(self.top, bg="#F5F8FF")
        bar.pack(side="bottom", fill="x", padx=10, pady=(2, 10))
        self.entry = tk.Entry(bar, font=("Microsoft YaHei UI", 11), relief="solid", bd=1)
        self.entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.entry.bind("<Return>", lambda _e: self._send())
        tk.Button(bar, text="发送", width=6, relief="flat", bg="#4A90D9", fg="#FFFFFF",
                  activebackground="#3A7BC9", font=("Microsoft YaHei UI", 10, "bold"),
                  command=self._send).pack(side="left", padx=(6, 0))

        # ── 工具行（同样先 pack 到底部）──
        tools = tk.Frame(self.top, bg="#F5F8FF")
        tools.pack(side="bottom", fill="x", padx=10)
        for label, attr in self.TOOLS:
            tk.Button(tools, text=label, width=5, relief="flat", bg="#E3EDFF", fg="#26406E",
                      activebackground="#D0E2FF", font=("Microsoft YaHei UI", 9),
                      command=getattr(self, attr)).pack(side="left", padx=2, pady=4)

        self.log.pack(fill="both", expand=True, padx=10, pady=(8, 4))

    # ---- 显示 ----

    def show(self) -> None:
        """显示小窗。

        注意：RegisterHotKey 只是"通知我"，并不会拦住原按键——Alt+Space 仍会被
        前台窗口当成系统菜单键，刚拿焦点的面板可能被系统收成最小化。
        所以这里不 focus_force，而是在事件循环里再"扶正"一次。
        """
        self.top.deiconify()
        self.top.lift()
        self.top.attributes("-topmost", True)
        # Alt+Space 的按键本身会落到刚显示出来的窗口上（系统菜单键），可能把它收成最小化，
        # 所以短时间内多次"扶正"，等按键松开后再把焦点给输入框。
        self.top.after(60, self._normalize)
        self.top.after(250, self._normalize)
        self.top.after(450, self._focus_entry)

    def _normalize(self) -> None:
        try:
            if self.top.state() != "normal":
                self.top.deiconify()
                self.top.state("normal")
            self.top.lift()
        except tk.TclError:
            pass

    def _focus_entry(self) -> None:
        try:
            self._normalize()
            self.entry.focus_set()
        except tk.TclError:
            pass

    def hide(self) -> None:
        self.top.withdraw()

    def toggle(self) -> None:
        # 用"当前是否可见"判断，而不是 state()——新建的 Toplevel state 就是 normal，
        # 按 state 判断会把刚建好的小窗立刻藏掉（本轮踩过）。
        try:
            visible = self.top.winfo_viewable()
        except tk.TclError:
            visible = False
        if visible:
            self.hide()
        else:
            self.show()

    # ---- 渲染 ----

    def say(self, who: str, text: str) -> None:
        tag = "fish" if who == "大肥鱼" else "user"
        self.log.config(state="normal")
        self.log.insert("end", f"{who}：", tag)
        self.log.insert("end", f"{text}\n\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _state(self, txt: str) -> None:
        self.status.config(text=txt)

    def _busy(self, msg: str) -> None:
        self.entry.config(state="disabled")
        self._state(msg)
        self.app.set_state("listen")

    def _done(self) -> None:
        self.entry.config(state="normal")
        self.entry.focus_set()
        self.app.set_state("idle")
        self._state(self.app.status_text)

    def _run(self, label: str, fn, show):
        """统一的后台执行：置忙 → 后台跑 → 回主线程渲染。"""
        self._busy(label)

        def work():
            try:
                out = show(fn())
            except Exception as e:  # noqa: BLE001
                out = f"出错了：{type(e).__name__}: {e}"
            self.top.after(0, lambda: (self.say("大肥鱼", out), self._done()))

        threading.Thread(target=work, daemon=True).start()

    # ---- 动作 ----

    def _send(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.say("你", text)
        target = text[2:].strip() if text.startswith("打开") else ""
        if target and actions.looks_like_target(target):
            self._run("打开中…", lambda: actions.open_target(target), lambda r: r[1])
            return
        if self.use_dsh.get():
            self._run_dsh(text)
            return
        self.app.set_state("think")
        self._run("在想…", lambda: self.brain.reply(text), self._after_reply)

    def _run_dsh(self, text: str) -> None:
        """走 dsh：完整 agent（能读文件、跑命令、用插件/MCP）。慢，但什么都能干。"""
        self._busy("DSH 干活中…（首次约 10-30 秒）")

        def on_ev(msg: str) -> None:
            try:
                self.top.after(0, lambda: self._state(f"DSH：{msg}"))
            except tk.TclError:
                pass

        def work():
            r = self.app.dsh_ask(text, on_event=on_ev)
            if r.get("ok"):
                out = r["text"]
                if r.get("tools"):
                    used = "、".join(dict.fromkeys(r["tools"]))
                    out += f"\n（本次用了工具：{used}）"
            else:
                out = f"DSH 没答上来：{r.get('error') or '未知错误'}"
            self.top.after(0, lambda: (self.say("大肥鱼", out), self._done()))

        threading.Thread(target=work, daemon=True).start()

    def _after_reply(self, r: dict) -> str:
        """对话成功 → 给鱼记一轮（亲密度 +1、参与小鱼干结算）。"""
        if r.get("ok"):
            self.app.on_turn_done()
        return r["text"]

    def _shot(self) -> None:
        self._run("看屏幕…", lambda: self.brain.comment_on(self.brain.look()),
                  lambda r: r["text"])

    def _look(self) -> None:
        self._run("看桌面…", lambda: desk.render(self.brain.look()), lambda s: s[:600])

    def _sel(self) -> None:
        def job():
            got = actions.selected_text()
            if not got:
                return "（没选中任何文字——先在别的窗口里选中一段再点我）"
            r = self.brain.reply("解释一下这段：" + got)
            return f"[选中] {got[:200]}\n[回答] {r['text']}"

        self._run("读选中文字…", job, lambda s: s)

    def _translate(self) -> None:
        def job():
            got = actions.selected_text() or self.entry.get().strip()
            if not got:
                return "（没有选中文字，也没在输入框里写东西）"
            r = actions.translate(self.brain, got)
            return f"[原文] {got[:200]}\n[译文] {r['text']}" if r["ok"] else f"翻不了：{r['reason']}"

        self._run("翻译中…", job, lambda s: s)

    def _open(self) -> None:
        text = self.entry.get().strip() or actions.selected_text()
        self._run("打开中…", lambda: actions.open_target(text), lambda r: r[1])

    def _voice(self) -> None:
        def show(r):
            if not r.get("heard"):
                return f"（没听清：{r.get('reason')}）"
            return f"[听到] {r['heard']}\n[回话] {r['reply']}" + (
                "\n（已念出来）" if r.get("spoken") else "")

        self._run("在听（5 秒）…",
                  lambda: self.brain.voice_reply(5.0, tmp_dir=self.app.tmp_dir), show)


# ---- 桌面精灵宿主 ----


class PetApp:
    """桌面上那只鲸鱼娘：动画 + 拖动 + 小窗。"""

    def __init__(self, brain: FishBrain, tmp_dir: str = ".tmp",
                 store: growth.PetStore | None = None, hotkey: str = "alt+space",
                 presence_url: str = "", core_url: str = "http://127.0.0.1:8787"):
        self.brain = brain
        self.tmp_dir = tmp_dir
        self.store = store or growth.PetStore()
        self.picker = growth.RemarkPicker()
        self._hover_after: str | None = None
        self._hidden = False
        self._panel: GrowthPanel | None = None
        self.panel: Panel | None = None
        self.dsh = None  # 懒加载：只有用「全功能」时才起桥接
        self.presence_client: PresenceClient | None = None
        self.core_url = core_url.rstrip("/")
        self._approval_dialog: tk.Toplevel | None = None
        self._bubble: tk.Toplevel | None = None
        self._hot_down = False
        self._moved = False
        self._state = "idle"
        self._anim = sprite.Animator("idle")
        self._last_tick = time.time()
        self._base_state = "idle"

        scale = PET_H / sprite.CELL_H
        self.w_px = max(1, round(sprite.CELL_W * scale))
        self.h_px = PET_H

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.config(bg=TRANSPARENT)
        try:
            self.root.attributes("-transparentcolor", TRANSPARENT)
        except tk.TclError:
            pass
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{self.w_px}x{self.h_px}+{sw - self.w_px - 40}+{sh // 3}")

        from PIL import ImageTk

        self._img = ImageTk.PhotoImage(avatar.frame("idle", 0, self.h_px))
        self.label = tk.Label(self.root, image=self._img, bg=TRANSPARENT, bd=0,
                              highlightthickness=0)
        self.label.pack()

        self._dx = self._dy = 0
        self.label.bind("<Button-1>", self._press)
        self.label.bind("<B1-Motion>", self._drag)
        self.label.bind("<ButtonRelease-1>", self._release)
        self.label.bind("<Double-Button-1>", lambda _e: self._quick_voice())
        self.label.bind("<Button-3>", self._menu)
        self.label.bind("<Enter>", lambda _e: self._schedule_hover_show())
        self.label.bind("<Leave>", lambda _e: self._schedule_hover_hide())

        self._menu_obj = tk.Menu(self.root, tearoff=0)
        self._menu_obj.add_command(label="打开小窗", command=self.toggle_panel)
        self._menu_obj.add_command(label="喂食", command=self.feed_pet)
        self._menu_obj.add_command(label="改名", command=self.rename_pet)
        self._menu_obj.add_command(label="隐藏", command=self.hide_pet)
        self._menu_obj.add_separator()
        self._menu_obj.add_command(label="语音说话", command=self._quick_voice)
        self._menu_obj.add_command(label="看桌面", command=self._quick_look)
        self._menu_obj.add_separator()
        self._menu_obj.add_command(label="退出", command=self._quit)

        self._register_hotkey(hotkey)
        self._poll_hotkey()
        self._warm_asr()
        self._tick()
        self._show_bubble(f"点我，或按 {hotkey} 说话。")
        if presence_url:
            self.presence_client = PresenceClient(presence_url, self._on_presence).start()

    # ---- 动画 ----

    def _tick(self) -> None:
        """帧循环：按 pet.json 的逐帧时长推进，和插件的 rAF 循环同语义。"""
        from PIL import ImageTk

        now = time.time()
        dt = (now - self._last_tick) * 1000.0
        self._last_tick = now
        track, idx = self._anim.advance(dt)
        try:
            self._img = ImageTk.PhotoImage(avatar.frame(track, idx, self.h_px))
            self.label.config(image=self._img)
        except tk.TclError:
            return
        if self._anim.done() and track in ("jumping", "failed"):
            self._set_track(self._state)
        self.root.after(self._anim.delay_ms(), self._tick)

    def _set_track(self, state: str) -> None:
        self._state = state
        self._anim.play(avatar.track_for(state))

    def set_state(self, state: str) -> None:
        self._set_track(state if state in avatar.STATE_TRACK else "idle")
        if self.panel is not None and self.panel.top.winfo_exists():
            try:
                from PIL import ImageTk

                self.panel._face = ImageTk.PhotoImage(avatar.head_crop(56, self._state))
                self.panel.face_label.config(image=self.panel._face)
            except tk.TclError:
                pass

    def _on_presence(self, event: dict) -> None:
        """核心 WS 在后台线程到达，切回 Tk 主线程更新动画。"""
        try:
            self.root.after(0, lambda: self._apply_presence(event))
        except (RuntimeError, tk.TclError):
            pass

    def _apply_presence(self, event: dict) -> None:
        if event.get("state") == "approval_required" and event.get("approval"):
            self._show_approval(event["approval"])
        mapping = {
            "idle": "idle",
            "thinking": "think",
            "acting": "think",
            "approval_required": "think",
            "speaking": "talk",
            "degraded": "fail",
        }
        state = mapping.get(str(event.get("state") or ""), "idle")
        self.set_state(state)
        if state == "talk" and not self._hidden:
            platform = str(event.get("platform") or "远方").upper()
            preview = str(event.get("preview") or "").strip()
            if preview:
                self._show_bubble(f"在 {platform}：{preview[:80]}", 5000)

    def _show_approval(self, request: dict) -> None:
        if self._approval_dialog is not None:
            try:
                self._approval_dialog.destroy()
            except tk.TclError:
                pass
        top = tk.Toplevel(self.root)
        self._approval_dialog = top
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg="#0B2630")
        frame = tk.Frame(top, bg="#0B2630", padx=14, pady=12,
                         highlightbackground="#FF7478", highlightthickness=1)
        frame.pack()
        tk.Label(frame, text="需要你的批准", bg="#0B2630", fg="#FF7478",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        tk.Message(frame, text=str(request.get("summary") or request.get("kind") or ""),
                   width=330, bg="#0B2630", fg="#E9F7F4",
                   font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(7, 9))
        buttons = tk.Frame(frame, bg="#0B2630")
        buttons.pack(fill="x")
        tk.Button(buttons, text="允许", bg="#62D8C3", fg="#031015", relief="flat",
                  command=lambda: self._resolve_approval(request["id"], True)).pack(
                      side="left", expand=True, fill="x", padx=(0, 4))
        tk.Button(buttons, text="拒绝", bg="#10242B", fg="#FF7478", relief="solid",
                  command=lambda: self._resolve_approval(request["id"], False)).pack(
                      side="left", expand=True, fill="x", padx=(4, 0))
        top.update_idletasks()
        x = max(0, self.root.winfo_x() - top.winfo_width() - 10)
        y = max(0, self.root.winfo_y() - top.winfo_height() // 3)
        top.geometry(f"+{x}+{y}")

    def _resolve_approval(self, approval_id: str, approved: bool) -> None:
        try:
            endpoint = "approve" if approved else "reject"
            body = json.dumps({"id": approval_id}).encode("utf-8")
            request = urllib.request.Request(
                f"{self.core_url}/api/actions/{endpoint}", data=body, method="POST",
                headers=actions.auth_headers({"Content-Type": "application/json"}),
            )
            with urllib.request.urlopen(request, timeout=5):
                pass
            self._show_bubble("已允许执行。" if approved else "已拒绝执行。", 3000)
        except Exception as exc:  # noqa: BLE001
            self._show_bubble(f"审批提交失败：{type(exc).__name__}", 5000)
        finally:
            if self._approval_dialog is not None:
                try:
                    self._approval_dialog.destroy()
                except tk.TclError:
                    pass
                self._approval_dialog = None

    # ---- 拖动 / 菜单 ----

    def _press(self, e) -> None:
        self._dx, self._dy = e.x, e.y
        self._moved = False

    def _drag(self, e) -> None:
        self._moved = True
        old_x = self.root.winfo_x()
        self.root.geometry(f"+{self.root.winfo_pointerx() - self._dx}"
                           f"+{self.root.winfo_pointery() - self._dy}")
        # 拖哪边朝哪边跑（与插件 running-left / running-right 一致）
        dx = self.root.winfo_x() - old_x
        if abs(dx) > 1:
            want = "running-right" if dx > 0 else "running-left"
            if self._anim.track != want:
                self._anim.play(want, restart=False)

    def _release(self, _e) -> None:
        if self._moved:
            self._set_track(self._state)
        else:
            self.pet_pet()

    # ---- 养成（摸头 / 喂食 / 改名 / 隐藏），规则见 growth.py ----

    def pet_pet(self) -> None:
        """单击 = 摸头（插件行为）：冷却 10 秒，+1 亲密度，说原版台词。"""
        delta, line, ok = self.store.pet(time.time() * 1000, self.picker)
        self._set_track("happy")
        self._show_bubble(line, 3000)
        if self._panel is not None and self._panel.top.winfo_exists():
            self._panel.refresh()

    def feed_pet(self) -> None:
        """喂食：冷却 30 秒、消耗 1 条小鱼干、+5 亲密度。"""
        delta, line, ok = self.store.feed(time.time() * 1000, self.picker)
        self._set_track("happy" if ok else "idle")
        self._show_bubble(line, 3500)
        if self._panel is not None and self._panel.top.winfo_exists():
            self._panel.refresh()

    def rename_pet(self) -> None:
        from tkinter import simpledialog

        name = simpledialog.askstring("改名", "给它起个新名字（最多 20 字）：",
                                      initialvalue=self.store.name, parent=self.root)
        if name:
            self.store.rename(name)
            self._show_bubble(f"我现在叫{self.store.name}啦～", 3000)
            if self._panel is not None and self._panel.top.winfo_exists():
                self._panel.refresh()

    def hide_pet(self) -> None:
        self._hidden = True
        self.root.withdraw()
        if self._panel is not None and self._panel.top.winfo_exists():
            self._panel.hide()
        self._show_bubble("我先潜下去了，按 Alt+Space 或右键菜单可以叫我回来～", 5000)

    def summon_pet(self) -> None:
        self._hidden = False
        self.root.deiconify()
        self._set_track("happy")
        self._show_bubble("浮上来了！", 2500)

    def on_turn_done(self) -> None:
        """一轮对话完成：亲密度 +1，并顺带结算小鱼干。"""
        self.store.reward_turn()
        self.store.settle(time.time() * 1000)
        if self._panel is not None and self._panel.top.winfo_exists():
            self._panel.refresh()

    # ---- DSH 全功能模式 ----

    def dsh_ask(self, text: str, on_event=None) -> dict:
        """把一句话交给 dsh（完整 agent）。首次调用才创建桥接，会话自动续接。"""
        if self.dsh is None:
            from .dsh_bridge import DshBridge

            self.dsh = DshBridge(cwd=Path.cwd(), timeout_s=300.0,
                                 stderr_dir=self.tmp_dir)
            _log("dsh bridge created")
        r = self.dsh.ask(text, on_event=on_event)
        _log(f"dsh ask ok={r.get('ok')} err={(r.get('error') or '')[:120]} "
             f"ms={r.get('took_ms')} session={r.get('session_id')}")
        if r.get("ok"):
            self.on_turn_done()
        return r

    # ---- 悬停面板 ----

    def _schedule_hover_show(self) -> None:
        self.cancel_hover_hide()
        if self._hidden:
            return
        self.root.after(120, self._show_growth_panel)

    def _show_growth_panel(self) -> None:
        if self._hidden:
            return
        if self._panel is None or not self._panel.top.winfo_exists():
            self._panel = GrowthPanel(self)
        self._panel.show()

    def _schedule_hover_hide(self) -> None:
        self.cancel_hover_hide()
        # 300ms 宽限期：鼠标从鱼身上移到面板上时不会闪没（插件同款）
        self._hover_after = self.root.after(300, self._hide_growth_panel)

    def _hide_growth_panel(self) -> None:
        self._hover_after = None
        if self._panel is not None and self._panel.top.winfo_exists():
            self._panel.hide()

    def cancel_hover_hide(self) -> None:
        if self._hover_after is not None:
            try:
                self.root.after_cancel(self._hover_after)
            except tk.TclError:
                pass
            self._hover_after = None

    def _menu(self, e) -> None:
        try:
            self._menu_obj.tk_popup(e.x_root, e.y_root)
        finally:
            self._menu_obj.grab_release()

    # ---- 小窗 ----

    def toggle_panel(self) -> None:
        if self._hidden:
            self.summon_pet()
            return
        fresh = self.panel is None or not self.panel.top.winfo_exists()
        if fresh:
            self.panel = Panel(self)
            self.panel.say("大肥鱼", "嗝。浮上来了。")
            self.panel.show()
            return
        self.panel.toggle()

    def _register_hotkey(self, spec: str) -> None:
        """起一个独立线程注册系统级热键（理由见 HotkeyThread 注释）。"""
        self._hotkey_spec = spec
        self._hotkey_mods = self._hotkey_vk = 0
        self._hotkey = HotkeyThread(spec)
        self._hotkey.start()
        self._hotkey_ok = self._hotkey.wait_ready(2.0)
        self._hotkey_seen = 0
        _log(f"hotkey {spec} registered={self._hotkey_ok} err={self._hotkey.error or '-'}")
        if self._hotkey_ok:
            self.status_text = f"{spec.upper()} 唤出 / 收起"
        else:
            self.status_text = f"热键 {spec} 注册失败（右键菜单/AI 助手仍可用）"

    def _poll_hotkey(self) -> None:
        """主线程只读热键线程的计数（Tcl 会吞掉线程消息，不能自己 PeekMessage）。"""
        fired = False
        if self._hotkey_ok:
            hits = self._hotkey.hits
            if hits != self._hotkey_seen:
                self._hotkey_seen = hits
                fired = True
        else:
            # 兜底：注册失败才退回按键轮询
            try:
                u = ctypes.windll.user32
                down = bool(u.GetAsyncKeyState(VK_MENU) & 0x8000) and bool(
                    u.GetAsyncKeyState(VK_SPACE) & 0x8000)
                if down and not self._hot_down:
                    self._hot_down = True
                    fired = True
                elif not down:
                    self._hot_down = False
            except Exception:  # noqa: BLE001
                pass
        if fired:
            _log("hotkey fired -> toggle_panel")
            self.toggle_panel()
        self.root.after(60, self._poll_hotkey)

    # ---- 气泡 / 快捷动作 ----

    def _show_bubble(self, text: str, seconds: int = 6000) -> None:
        if self._bubble is not None:
            try:
                self._bubble.destroy()
            except tk.TclError:
                pass
        top = tk.Toplevel(self.root)
        self._bubble = top
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        tk.Message(top, text=text, width=280, bg="#FFFDF5", fg="#2B3A55",
                   font=("Microsoft YaHei UI", 10), relief="solid", bd=1,
                   padx=10, pady=8).pack()
        top.update_idletasks()
        bx = max(0, self.root.winfo_x() - top.winfo_width() - 8)
        top.geometry(f"+{bx}+{self.root.winfo_y()}")
        top.after(seconds, lambda: top.destroy() if top.winfo_exists() else None)

    def _quick_voice(self) -> None:
        self.set_state("listen")
        self._show_bubble("在听…（5 秒）", 9000)

        def work():
            r = self.brain.voice_reply(5.0, tmp_dir=self.tmp_dir)
            txt = (f"{r['heard']} → {r['reply']}" if r.get("heard")
                   else f"没听清（{r.get('reason') or '没有声音'}）")
            self.root.after(0, lambda: (self._show_bubble(txt, 9000),
                                        self.set_state("happy")))

        threading.Thread(target=work, daemon=True).start()

    def _quick_look(self) -> None:
        self.set_state("think")
        self._show_bubble("睁眼看屏幕…", 8000)

        def work():
            lk = desk.look()
            self.root.after(0, lambda: (self._show_bubble(desk.render(lk)[:280], 12000),
                                        self.set_state("idle")))

        threading.Thread(target=work, daemon=True).start()

    def _warm_asr(self) -> None:
        """后台预热 ASR：FunASR 首载 40s+，不预热第一句语音会像卡死。"""
        def work():
            try:
                from .. import real_engines as re

                if re.HAS_ASR:
                    import numpy as np

                    re.transcribe_wav_bytes(_wav_from_pcm(np.zeros(16000, dtype=np.float32)))
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=work, daemon=True).start()

    def _quit(self) -> None:
        try:
            if self.presence_client is not None:
                self.presence_client.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            if getattr(self, "_hotkey", None) is not None:
                self._hotkey.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            voice.stop_speaking()
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="大肥鱼桌宠（鲸鱼娘动画 + 豆包式小窗）")
    ap.add_argument("--db", default=None, help="库路径（默认 data/aiji.db）")
    ap.add_argument("--ident", default="desktop:me", help="身份 platform:user_id")
    ap.add_argument("--session", default="pet", help="会话名")
    ap.add_argument("--tmp", default=".tmp", help="录音临时目录")
    ap.add_argument("--pet-state", default="data/pet_state.json",
                    help="养成状态（名字/亲密度/小鱼干）落盘路径")
    ap.add_argument("--hotkey", default="alt+space",
                    help="全局热键，如 alt+space / ctrl+shift+j / alt+q")
    ap.add_argument("--presence-url", default="ws://127.0.0.1:8789",
                    help="存在感 WebSocket；空字符串则关闭")
    ap.add_argument("--core-url", default="http://127.0.0.1:8787",
                    help="本地 AIJiYi HTTP 服务")
    args = ap.parse_args(argv)

    from ..memory import Memory

    platform, _, uid = args.ident.partition(":")
    ident = (platform or "desktop", uid or "me")
    Path(args.tmp).mkdir(mode=0o777, exist_ok=True)
    m = Memory.open(path=args.db)
    store = growth.PetStore(path=args.pet_state)
    app = PetApp(FishBrain(m, ident, args.session), tmp_dir=args.tmp, store=store,
                 hotkey=args.hotkey, presence_url=args.presence_url,
                 core_url=args.core_url)
    try:
        app.run()
    finally:
        m.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
