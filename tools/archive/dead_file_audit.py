"""死文件审计：找出仓库里没人引用的模块/脚本/文档，输出候选清单（不删，只报）。"""
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"D:\AIJiYi")
PY = [p for p in ROOT.rglob("*.py")
      if not any(part in {".tmp", "build", "dist", "__pycache__", "voice-engine", ".venv", "node_modules"}
                 for part in p.parts)
      and ".egg-info" not in str(p)]

# 1) 代码里出现的模块名（import 语句 + 字符串）
refs = defaultdict(set)
strrefs = defaultdict(set)
for p in PY:
    try:
        src = p.read_text(encoding="utf-8")
    except Exception:
        continue
    for m in re.finditer(r"^\s*(?:from|import)\s+([\w\.]+)", src, re.M):
        refs[m.group(1)].add(p)
    for m in re.finditer(r"""["']([\w\.]+)["']""", src):
        strrefs[m.group(1)].add(p)

mods = {}
for p in PY:
    if not str(p).startswith(str(ROOT / "aiji")):
        continue
    rel = p.relative_to(ROOT / "aiji")
    if rel.name == "__init__.py":
        mod = "aiji." + ".".join(rel.parts[:-1]) if len(rel.parts) > 1 else "aiji"
    else:
        mod = "aiji." + ".".join(rel.with_suffix("").parts)
    mods[mod] = p

print(f"aiji 模块 {len(mods)} 个\n")
orphans = []
for mod, p in sorted(mods.items()):
    short = mod.rsplit(".", 1)[-1]
    hit = set()
    for name, files in refs.items():
        if name == mod or name.startswith(mod + "."):
            hit |= {f for f in files if f != p}
    if not hit:
        # 允许 "from aiji.hippocampus import store" 这种：看父包里是否出现该短名
        parent = mod.rsplit(".", 1)[0] if "." in mod else None
        for name, files in refs.items():
            if parent and name == parent and any(f != p for f in files):
                for f in files:
                    if f == p:
                        continue
                    try:
                        txt = f.read_text(encoding="utf-8")
                    except Exception:
                        continue
                    if re.search(rf"from {re.escape(parent)} import [^\n]*\b{re.escape(short)}\b", txt):
                        hit.add(f)
    if not hit:
        s = {f for name, files in strrefs.items() if name == mod or name.startswith(mod + ".") for f in files}
        if s:
            hit = s
    if not hit:
        orphans.append(mod)

print("零引用模块（候选死文件）:")
for m in orphans:
    print("  ", m, "->", mods[m].relative_to(ROOT))

# 2) 仓库根部/工具目录里没被任何东西引用的脚本
print("\n工具/脚本零引用:")
for p in PY:
    if p.parent.name not in {"tools", "bench"} and p.parent != ROOT:
        continue
    if p.parent.name == "probe":
        continue
    stem = p.stem
    hit = {f for name, files in refs.items() for f in files
           if stem in name.split(".") and f != p} | \
          {f for name, files in strrefs.items() if stem in name and f != p}
    if not hit:
        print("  ", p.relative_to(ROOT))
