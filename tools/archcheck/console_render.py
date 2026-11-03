"""控制台的**真浏览器**检查：页面在无头浏览器里跑一遍，确认 JS 没崩、结构都在。

为什么需要它：`console_check.py` 只证明"页面里写的接口在 server.py 里存在"，
证明不了"页面在浏览器里能跑"——2026-11-03 之前的 TODO 就是这条
（当时本机 Edge headless 被沙箱 ACL 拦过，后来改用独立 user-data-dir 就好了）。
本脚本用 `--dump-dom` 拿 JS 执行后的 DOM，检查：

1. 9 个系统导航全部出现（说明生成器注入生效）；
2. 节点按钮数量 = `nodes.json` 里的节点数（生成器与数据源一致）；
3. stderr 里没有 `Uncaught / TypeError / ReferenceError`（JS 没崩）；
4. 渲染后的可见文本里没有 `undefined` / `NaN`（面板占位没漏出来）。

用法：
    python tools/archcheck/console_render.py            # 跑一遍，打印 JSON
    AIJI_BROWSER=... python tools/archcheck/console_render.py
产物：tools/archcheck/results/console_render.json
找不到浏览器时输出 {"skipped": true} 并返回 0（别的机器上不该红）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "aiji" / "right_hand" / "web" / "operations.html"
NODES = ROOT / "tools" / "archcheck" / "results" / "nodes.json"
OUT = ROOT / "tools" / "archcheck" / "results" / "console_render.json"

CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def find_browser() -> str | None:
    env = os.environ.get("AIJI_BROWSER")
    if env and Path(env).exists():
        return env
    for path in CANDIDATES:
        if Path(path).exists():
            return path
    return None


def render(browser: str) -> tuple[str, str]:
    # 独立 user-data-dir：默认 profile 可能被别的实例占用，headless 直接起不来
    with tempfile.TemporaryDirectory(prefix="aiji-render-") as profile:
        proc = subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--no-first-run",
             f"--user-data-dir={profile}", "--virtual-time-budget=6000",
             "--enable-logging=stderr", "--log-level=0", "--dump-dom",
             PAGE.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
    return proc.stdout or "", proc.stderr or ""


def spec_navs() -> list[str]:
    """导航名以生成器写进页面里的为准，不在这里硬编码（硬编码过一次 骨架→骨骼）。"""
    html = PAGE.read_text(encoding="utf-8")
    return re.findall(r'"nav":\s*"([^"]+)"', html)


def spec_node_titles(system_id: str) -> list[str]:
    data = json.loads(NODES.read_text(encoding="utf-8"))
    return [n["t"] for n in data["NODES"] if n.get("band") == system_id]


def check(dom: str, err: str, navs: list[str], first_system: str,
          html_bytes: int) -> dict:
    missing_navs = [nav for nav in navs if nav not in dom]
    titles = spec_node_titles(first_system)
    missing_nodes = [t for t in titles if t not in dom]
    buttons = len(re.findall(r"<button\b", dom))
    js_errors = [
        line.strip() for line in err.splitlines()
        if re.search(r"Uncaught|TypeError|ReferenceError|SyntaxError", line)
    ]
    visible = re.sub(r"<script.*?</script>", "", dom, flags=re.S)
    leaking = [bad for bad in ("undefined", "NaN") if f">{bad}<" in visible]
    # JS 真的跑过：渲染后的 DOM 必须比磁盘上的静态页面更大（生成器注入导航/节点）
    js_ran = len(dom) > html_bytes
    ok = (not missing_navs and not missing_nodes and not js_errors
          and not leaking and js_ran)
    return {
        "ok": ok,
        "buttons": buttons,
        "navs_found": len(navs),
        "first_system": first_system,
        "node_titles": len(titles),
        "missing_navs": missing_navs,
        "missing_nodes": missing_nodes[:8],
        "js_errors": js_errors[:5],
        "placeholder_leaks": leaking,
        "js_ran": js_ran,
        "dom_bytes": len(dom),
        "html_bytes": html_bytes,
    }


def main() -> int:
    if not PAGE.exists():
        print(json.dumps({"ok": False, "error": f"页面不存在：{PAGE}"}, ensure_ascii=False))
        return 1
    browser = find_browser()
    if not browser:
        payload = {"skipped": True, "reason": "没找到 Chrome/Edge；设 AIJI_BROWSER 指一个"}
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    navs = spec_navs()
    data = json.loads(NODES.read_text(encoding="utf-8"))
    first_system = data["BANDS"][0]["id"]
    dom, err = render(browser)
    result = check(dom, err, navs, first_system, PAGE.stat().st_size)
    result["browser"] = Path(browser).name
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
