"""只读：控制台 15 个视图是否都有 section + 加载器；打包 wheel 是否带 web/资产。"""
from __future__ import annotations

import pathlib
import re
import sys
import zipfile

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(r"D:\AIJiYi")
html = (ROOT / "aiji" / "web" / "console.html").read_text(encoding="utf-8")

views = sorted(set(re.findall(r'id="view-([a-z]+)"', html)))
nav = re.findall(r'data-view="([a-z]+)"', html)
print("## nav 按钮:", len(nav), nav)
print("## section:", len(views), views)
print("## nav 与 section 一一对应:", sorted(nav) == views)

# 每个视图的加载函数：navigate() 之后按 view 调 load*
loader_block = html.split("function navigate(view)", 1)[-1][:2500]
print("\n## navigate 之后的加载调用:")
for m in re.finditer(r"load[A-Za-z]+", loader_block):
    print("  ", m.group(0))
loaded = set(re.findall(r"load[A-Za-z]+", loader_block))
print("## 加载函数个数:", len(loaded))

# 每个 load 函数是否有定义
defined = set(re.findall(r"(?:async\s+)?function\s+(load[A-Za-z]+)", html))
print("## 定义但未调用:", sorted(defined - loaded))
print("## 调用但未定义:", sorted(loaded - defined))

# 视图里是否有内容（不是空壳）
for name in views:
    chunk = html.split(f'id="view-{name}"', 1)[-1][:400]
    empty = len(re.sub(r"<[^>]+>|\s", "", chunk)) < 10
    if empty:
        print(f"  ⚠ 视图 {name} 前 400 字几乎没有内容")

print("\n## wheel 内容检查")
for whl in sorted((ROOT / "dist").glob("aiji-0.8.3-*.whl")):
    with zipfile.ZipFile(whl) as z:
        names = z.namelist()
    want = ["aiji/web/console.html", "aiji/web/vendor/echarts.min.js",
            "aiji/pet/assets/whale_spritesheet.webp", "aiji/pet/assets/whale_pet.json"]
    for w in want:
        print(f"  {w:42s} {'OK' if w in names else 'MISSING'}")
    print(f"  wheel 文件数={len(names)}")
