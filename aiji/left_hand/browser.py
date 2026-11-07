"""浏览器会话（Playwright + 本机 Edge）。

为什么需要它：抖音没有公开写接口，B 站的一部分也只在网页上；
要"刷视频 / 发评论 / 私信"就只能走真实浏览器 + 真实登录态。

四条环境事实（2026-11-05 实测，别重走）：
1. **Python 用 `.venv` 的**（playwright 已装）；浏览器用 `channel="msedge"`（本机 Edge），
   不需要 `playwright install` 下载浏览器。
2. **必须把 TEMP/TMP 指到工作区**：`%TEMP%` 下 playwright 建 artifacts 目录会
   `EPERM mkdtemp`（本机沙箱 ACL）。所以 `session()` 自己设 `data/temp/playwright`。
3. **必须 `--no-proxy-server`**：机器上有 `HTTP_PROXY=127.0.0.1:7897`，Clash 没在跑，
   Chromium 继承它会全部 "目标计算机积极拒绝"。
4. **headless 能起**（`--headless=new` 直接跑 exe 的老路会挂，Playwright 驱动没事）；
   但抖音 headless 会掉进"验证中间页"，刷内容和发评论要用有头窗口。
"""

from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from aiji.skeleton.paths import DATA

PROFILE_ROOT = DATA / "browser"
TEMP_ROOT = DATA / "temp" / "playwright"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0")


def reap_profile(profile_dir: Path) -> int:
    """清掉占着这个 profile 的残留 Edge 进程（返回杀掉的个数）。

    为什么需要：2026-11-05 实测 `launch_persistent_context` 报
    `Target page, context or browser has been closed`（Edge 退出码 21），
    根因是**上一次有头会话留下的 msedge.exe 还握着 user-data-dir**，
    连 `lockfile` 都还在。表现就是用户看到的"双击 bat 一闪就没了 / 打不开"。
    清理顺序：先杀进程，再删 lockfile。
    """
    killed = 0
    if os.name == "nt":
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
            "Where-Object { $_.CommandLine -like '*" + str(profile_dir) + "*' } | "
            "ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force "
            "-ErrorAction SilentlyContinue; $_.ProcessId } catch {} }"
        )
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=25)
            killed = len([ln for ln in (out.stdout or "").splitlines()
                          if ln.strip().isdigit()])
        except Exception:  # noqa: BLE001 - 清理失败不该挡住主流程
            killed = 0
    for name in ("lockfile", "SingletonLock", "SingletonCookie"):
        try:
            (profile_dir / name).unlink()
        except Exception:  # noqa: BLE001
            pass
    return killed


def _visible_windows() -> dict[int, str]:
    """当前所有可见顶层窗口：{hwnd: title}。仅在 Windows 上工作，其它平台返回空。"""
    if os.name != "nt":
        return {}
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        out: dict[int, str] = {}
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            out[int(hwnd)] = buf.value
            return True

        user32.EnumWindows(EnumProc(cb), 0)
        return out
    except Exception:  # noqa: BLE001
        return {}


def _hide_windows(hwnds: "list[int]") -> int:
    """把指定窗口藏起来（SW_HIDE=0），并挪到屏幕外。返回成功数。"""
    if os.name != "nt" or not hwnds:
        return 0
    try:
        import ctypes

        user32 = ctypes.windll.user32
        done = 0
        for hwnd in hwnds:
            try:
                user32.MoveWindow(hwnd, -32000, -32000, 1280, 900, True)
                user32.ShowWindow(hwnd, 0)   # SW_HIDE
                done += 1
            except Exception:  # noqa: BLE001
                pass
        return done
    except Exception:  # noqa: BLE001
        return 0


class BrowserError(RuntimeError):
    def __init__(self, code: str, message: str = "", *, hint: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        out = {"ok": False, "code": self.code, "error": self.message}
        if self.hint:
            out["hint"] = self.hint
        return out


def _prepare_temp() -> None:
    """把 playwright 的临时目录钉在工作区（沙箱里 %TEMP% 不可用）。"""
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(TEMP_ROOT, 0o777)
    except OSError:
        pass
    for name in ("TEMP", "TMP", "TMPDIR"):
        os.environ[name] = str(TEMP_ROOT)


class BrowserSession:
    """一条持久登录态的浏览器会话。`with` 进出，退出即关，不常驻。"""

    def __init__(self, profile: str = "default", *, headless: bool = True,
                 channel: str = "msedge", timeout_ms: int = 30_000,
                 proxy_bypass: bool = True, locale: str = "zh-CN",
                 viewport: tuple[int, int] = (1280, 800),
                 offscreen: bool = False) -> None:
        self.profile = profile
        self.headless = headless
        self.offscreen = offscreen
        self.channel = channel
        self.timeout_ms = int(timeout_ms)
        self.proxy_bypass = proxy_bypass
        self.locale = locale
        self.viewport = viewport
        self._pw: Any = None
        self._ctx: Any = None
        self.page: Any = None
        self.profile_dir = PROFILE_ROOT / profile

    # ---- 生命周期 ---------------------------------------------------------
    def start(self) -> "BrowserSession":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的明确报错
            raise BrowserError(
                "playwright_missing",
                f"没装 playwright（{exc}）",
                hint="uv pip install --python .venv\\Scripts\\python.exe playwright",
            ) from exc
        _prepare_temp()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.reaped = reap_profile(self.profile_dir)
        args = ["--disable-blink-features=AutomationControlled"]
        if self.proxy_bypass:
            args.append("--no-proxy-server")
        if self.offscreen:
            # "背后跑"：有些页面（抖音 /chat）headless 会崩，但用户又不想看见窗口。
            # 把窗口挪到屏幕外，渲染完整性等同有头，人眼看不见。
            args += ["--window-position=-32000,-32000", "--window-size=1280,900"]
        last_exc: Exception | None = None
        before_windows = _visible_windows() if self.offscreen else {}
        self._baseline_windows = set(before_windows)
        try:
            self._pw = sync_playwright().start()
            # 有头会话偶尔把 profile 锁在半死状态：第一次 launch 会一直等到超时。
            # 所以给一次"清残留 + 重试"，别让用户看到"打不开"。
            for attempt in range(2):
                try:
                    self._ctx = self._pw.chromium.launch_persistent_context(
                        user_data_dir=str(self.profile_dir),
                        channel=self.channel,
                        headless=self.headless,
                        args=args,
                        locale=self.locale,
                        viewport={"width": self.viewport[0],
                                  "height": self.viewport[1]},
                        user_agent=UA,
                        timeout=60_000,
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if attempt == 0:
                        reap_profile(self.profile_dir)
                        time.sleep(2.0)
                        continue
                    raise
        except Exception as exc:  # noqa: BLE001 - 统一成机器可判的 code
            self.stop()
            raise BrowserError(
                "browser_launch_failed",
                str(last_exc or exc)[:400]) from (last_exc or exc)
        pages = self._ctx.pages
        self.page = pages[0] if pages else self._ctx.new_page()
        self.page.set_default_timeout(self.timeout_ms)
        if self.offscreen:
            # 窗口是异步创建/可能开第二个（渲染进程），所以连续藏几轮，
            # 只藏"launch 之后才出现的"，不碰用户自己的 Edge。
            hidden = 0
            for _ in range(6):
                try:
                    fresh = [h for h in _visible_windows() if h not in before_windows]
                except Exception:  # noqa: BLE001
                    fresh = []
                hidden += _hide_windows(fresh)
                time.sleep(0.35)
            self.hidden_windows = hidden
        return self

    def stop(self) -> None:
        ctx, pw = self._ctx, self._pw
        self._ctx = self._pw = None
        try:
            if ctx is not None:
                ctx.close()
        except Exception:  # noqa: BLE001 - 关不掉也不该让调用方炸
            pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:  # noqa: BLE001
            pass
        # 有头会话的 Edge 有时不随 context.close() 退出，会锁住 profile 让下一次直接
        # launch 失败（Edge 退出码 21）。关完再收一遍尸，别把坑留给下一个进程。
        reap_profile(self.profile_dir)

    def __enter__(self) -> "BrowserSession":
        return self.start()

    _TITLE_HINTS = ("抖音", "哔哩哔哩", "bilibili", "QQ")

    def hide(self) -> int:
        """把这次会话的浏览器窗口藏起来。

        Edge 在导航/拿到焦点后会把窗口重新 Show 出来，所以除了"launch 之后新出现的窗口"，
        再按平台标题兜底藏一遍（只在 offscreen 模式下做）。
        """
        if not getattr(self, "offscreen", False):
            return 0
        base = getattr(self, "_baseline_windows", set())
        try:
            wins = _visible_windows()
        except Exception:  # noqa: BLE001
            return 0
        targets = []
        for hwnd, title in wins.items():
            if hwnd in base:
                continue
            if hwnd not in base or any(k in title for k in self._TITLE_HINTS):
                targets.append(hwnd)
        return _hide_windows(targets)

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # ---- 常用动作 ---------------------------------------------------------
    def goto(self, url: str, *, wait_until: str = "domcontentloaded",
             timeout_ms: int | None = None) -> Any:
        try:
            return self.page.goto(url, wait_until=wait_until,
                                  timeout=timeout_ms or self.timeout_ms)
        except Exception as exc:  # noqa: BLE001
            raise BrowserError("goto_failed", f"{url}：{str(exc)[:300]}") from exc

    def wait(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)
        self.hide()

    def screenshot(self, path: str | Path) -> str:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=False)
        return str(path)

    def cookie_names(self) -> set[str]:
        try:
            return {c.get("name", "") for c in self._ctx.cookies()}
        except Exception:  # noqa: BLE001
            return set()

    def url(self) -> str:
        try:
            return str(self.page.url)
        except Exception:  # noqa: BLE001
            return ""

    def title(self) -> str:
        try:
            return str(self.page.title())
        except Exception:  # noqa: BLE001
            return ""


@contextmanager
def session(profile: str = "default", **kwargs: Any) -> Iterator[BrowserSession]:
    s = BrowserSession(profile, **kwargs)
    try:
        yield s.start()
    finally:
        s.stop()


def cookie_jar(profile: str) -> dict[str, str]:
    """读某条 profile 的 cookie（用于"登录态在不在"的判断，不打印值）。"""
    with session(profile, headless=True) as s:
        try:
            jar = {c.get("name", ""): c.get("value", "") for c in s._ctx.cookies()}
        except Exception:  # noqa: BLE001
            jar = {}
    return jar


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")
