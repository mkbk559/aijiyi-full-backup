"""用 Playwright + 本机 Edge 登录百度，保存 Tieba 登录态。

首次运行会打开 Edge 登录页；用户扫码/登录后，脚本检测 BDUSS cookie，
保存 storage_state 到 data/browser/baidu_state.json，然后打开贴吧验证。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / ".tmp" / "tieba-profile"
STATE = ROOT / "data" / "browser" / "baidu_state.json"
SHOT = ROOT / ".tmp" / "baidu-login.png"


def has_login(context) -> bool:
    for cookie in context.cookies():
        if cookie.get("name") == "BDUSS" and cookie.get("value"):
            return True
    return False


def main() -> int:
    PROFILE.mkdir(parents=True, exist_ok=True)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE),
            channel="msedge",
            headless=False,
            viewport={"width": 1200, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://passport.baidu.com/v2/?login", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        page.screenshot(path=str(SHOT), full_page=False)
        print("LOGIN_PAGE", page.url, page.title(), flush=True)
        deadline = time.time() + 300
        while time.time() < deadline:
            if has_login(context):
                context.storage_state(path=str(STATE))
                print("LOGIN_OK", flush=True)
                page.goto("https://tieba.baidu.com/", wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                page.screenshot(path=str(ROOT / ".tmp" / "tieba-home.png"))
                print("TIEBA", page.url, page.title(), len(page.content()), flush=True)
                page.wait_for_timeout(8000)
                context.close()
                return 0
            page.wait_for_timeout(2000)
        print("LOGIN_TIMEOUT", flush=True)
        page.screenshot(path=str(ROOT / ".tmp" / "baidu-login-timeout.png"))
        context.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())
