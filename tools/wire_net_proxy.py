"""把出站 urlopen 换成 `aiji.net.urlopen`（直连优先，代理兜底）。

只改真正的出站下载/调用点，不动测试里的本地请求与注入式 opener。
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SITES = [
    "aiji/heart/sticker_service.py",
    "aiji/heart/song_service.py",
    "aiji/heart/voice_service.py",
    "aiji/hippocampus/media_audio.py",
    "aiji/left_hand/mcp_research.py",
    "aiji/throat/bridge/onebot.py",
]
NEEDLE = "urllib.request.urlopen("
REPLACEMENT = "net.urlopen("


def ensure_import(text: str) -> str:
    if "\nfrom aiji import net\n" in text:
        return text
    lines = text.splitlines(keepends=True)
    last_import = 0
    for index, line in enumerate(lines[:80]):
        if line.startswith(("import ", "from ")) and "__future__" not in line:
            last_import = index
    lines.insert(last_import + 1, "from aiji import net\n")
    return "".join(lines)


def main() -> None:
    for rel in SITES:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        if NEEDLE not in text:
            print("SKIP (no urlopen)", rel)
            continue
        count = text.count(NEEDLE)
        text = text.replace(NEEDLE, REPLACEMENT)
        text = ensure_import(text)
        path.write_text(text, encoding="utf-8")
        print(f"{rel}: {count} 处")

    # 测试里按模块路径打桩的地方跟着改
    test = ROOT / "tests" / "test_onebot.py"
    t = test.read_text(encoding="utf-8")
    old = '"aiji.throat.bridge.onebot.urllib.request.urlopen"'
    new = '"aiji.throat.bridge.onebot.net.urlopen"'
    if old in t:
        test.write_text(t.replace(old, new), encoding="utf-8")
        print("test_onebot.py: 打桩路径已更新")


if __name__ == "__main__":
    main()
