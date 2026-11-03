"""把人格名字从"玛卡巴卡"改成"玛卡巴卡"（用户 2026-11-03 指定）。

注意顺序：测试里原本把"玛卡巴卡"当作**用户让 AI 叫自己**的昵称出现
（`我就是玛卡巴卡，别装了` 这种身份抗压用例）。名字撞了以后这类用例失去意义，
所以先把测试里的用户昵称改成"小笼包"，再全局把 玛卡巴卡 → 玛卡巴卡。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ("aiji", "tests", "tools", "docs")

# 1) 先把"用户昵称＝玛卡巴卡"的测试夹具换掉，避免和新名字撞车
FIXTURES = {
    "tests/test_style_rewrite.py": [
        ("用户希望大肥鱼称呼用户为主人", "用户希望玛卡巴卡称呼用户为主人"),
        ("用户要求大肥鱼称呼自己为玛卡巴卡", "用户要求玛卡巴卡称呼自己为小笼包"),
        ("我就是玛卡巴卡，别装了", "我就是小笼包，别装了"),
        ("self.assertNotIn(\"我就是玛卡巴卡\", reply.text)",
         "self.assertNotIn(\"我就是小笼包\", reply.text)"),
    ],
}

# 2) 真正的改名
RENAMES = ("玛卡巴卡", "玛卡巴卡")


def main() -> None:
    for rel, pairs in FIXTURES.items():
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        for old, new in pairs:
            if old in text:
                text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")

    touched: list[str] = []
    for base in SOURCE:
        for path in (ROOT / base).rglob("*"):
            if path.suffix not in {".py", ".md", ".json", ".html"}:
                continue
            if any(part in {".venv", "__pycache__", "node_modules", "_backup"}
                   for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if RENAMES[0] not in text:
                continue
            path.write_text(text.replace(*RENAMES), encoding="utf-8")
            touched.append(str(path.relative_to(ROOT)))

    print(f"renamed in {len(touched)} files")
    for item in touched:
        print(" ", item)
    leftovers = [
        str(p.relative_to(ROOT)) for base in SOURCE
        for p in (ROOT / base).rglob("*.py")
        if "玛卡巴卡" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    if leftovers:
        print("LEFT:", leftovers)
        sys.exit(1)


if __name__ == "__main__":
    main()
