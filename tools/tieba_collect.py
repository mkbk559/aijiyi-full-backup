"""用已登录的百度态抓孙笑川吧帖子（Playwright，只读，自动轮换页面）。

每次运行会：
- 从没抓过的页里随机选 N 页（pn=0/50/100…）
- 抓页面正文行 + 最多 M 个没看过的帖子正文/评论
- 记录 seen_pages / seen_threads，避免每轮都是同一批
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "browser" / "baidu_state.json"
STATE_FILE = ROOT / "data" / "persona" / "learn_state.json"
OUT = ROOT / ".tmp" / "tieba_forum.json"
THREAD_RE = re.compile(r"/p/(\d+)")
NOISE = (
    "首页", "我的", "发贴", "关注", "回复", "分享", "赞", "全部回复",
    "只看楼主", "热门", "正序", "倒序", "参与讨论", "百度贴吧", "贴吧协议",
    "隐私政策", "投诉反馈", "下载", "打开APP", "登录", "注册",
)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text("utf-8"))
        return {
            "seen_pages": [int(x) for x in data.get("seen_pages", [])],
            "seen_threads": [str(x) for x in data.get("seen_threads", [])],
        }
    except (OSError, ValueError, TypeError):
        return {"seen_pages": [], "seen_threads": []}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["seen_pages"] = state["seen_pages"][-200:]
    state["seen_threads"] = state["seen_threads"][-5000:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), "utf-8")


def useful(line: str) -> bool:
    if not (5 <= len(line) <= 120):
        return False
    if line in NOISE or any(word in line for word in NOISE):
        return False
    if line.isdigit() or re.fullmatch(r"[\d\W_]+", line):
        return False
    if line.startswith("http") or "tieba.baidu.com" in line:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=4)
    parser.add_argument("--threads", type=int, default=3)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    state = load_state()
    page_pool = [pn for pn in range(0, 500, 50) if pn not in state["seen_pages"]]
    if len(page_pool) < args.pages:
        state["seen_pages"] = []
        page_pool = list(range(0, 500, 50))
    pages = random.sample(page_pool, min(args.pages, len(page_pool)))
    lines: list[str] = []
    threads: list[dict] = []
    seen_threads = set(state["seen_threads"])

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=not args.headed)
        context = browser.new_context(storage_state=str(STATE),
                                      viewport={"width": 1280, "height": 900})
        page = context.new_page()
        for pn in pages:
            url = f"https://tieba.baidu.com/f?kw=%E5%AD%99%E7%AC%91%E5%B7%9D&pn={pn}"
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            body = page.locator("body").inner_text()
            for raw in body.splitlines():
                line = clean(raw)
                if useful(line):
                    lines.append(line)
            anchors = page.locator('a[href*="/p/"]').evaluate_all(
                "els => els.map(e => ({href: e.href, text: e.innerText}))")
            for item in anchors:
                match = THREAD_RE.search(item.get("href") or "")
                text = clean(item.get("text") or "")
                if not match:
                    continue
                tid = match.group(1)
                if tid in seen_threads or any(t["thread_id"] == tid for t in threads):
                    continue
                threads.append({"thread_id": tid, "title": text,
                                "url": f"https://tieba.baidu.com/p/{tid}"})
            print(f"page pn={pn} lines={len(lines)} candidates={len(threads)}",
                  flush=True)
            state["seen_pages"].append(pn)

        random.shuffle(threads)
        for thread in threads[: args.threads]:
            try:
                page.goto(thread["url"], wait_until="domcontentloaded")
                page.wait_for_timeout(2200)
                body = page.locator("body").inner_text()
                thread["body"] = clean(body)[:7000]
                for raw in body.splitlines():
                    line = clean(raw)
                    if useful(line):
                        lines.append(line)
                seen_threads.add(thread["thread_id"])
                print("thread", thread["thread_id"], len(thread["body"]), flush=True)
            except Exception as exc:  # noqa: BLE001
                print("thread error", thread["thread_id"], type(exc).__name__, flush=True)
        context.close()
        browser.close()

    state["seen_threads"] = list(seen_threads)
    save_state(state)
    seen = set()
    uniq: list[str] = []
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        uniq.append(line)
    uniq = uniq[:400]
    OUT.write_text(json.dumps({
        "lines": uniq,
        "threads": threads[: args.threads],
        "pages": pages,
        "collected_at": time.time(),
    }, ensure_ascii=False, indent=2), "utf-8")
    print("saved", len(uniq), "lines /", len(threads[: args.threads]),
          "threads ->", OUT, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
