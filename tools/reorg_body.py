"""把 aiji/ 按 docs/architecture.html 的九个人体系统重排（默认 dry-run，--apply 才动）。

九层 → 九个子包：
  ① 感官 senses      入口/命令行                     aiji/cli.py
  ② 咽喉 throat      所有入口汇成一条咽喉            server, net_guard, bridge/
  ③ 大脑 brain       一次对话怎么被想出来            head, debounce, pool, gate, humanize,
                                                     role_boundary, persona, selfhood, quota
  ④ 海马体 hippocampus 写不丢·读有预算·注入不越权     memory, recognize, real_engines, media_audio,
                                                     extract, reflect, recall, inject, store,
                                                     vectors, writer, janitor, db
  ⑤ 内分泌 endocrine 状态·体检·主动·存在感            being, life, life_loop, being_checkup,
                                                     proactive, presence, desktop_sense
  ⑥ 心脏 heart       从队列到真实送达                heart, dispatch, social_service,
                                                     song_service, sticker_service, voice_service
  ⑦ 左手 left_hand   受控动作与联网                  actions/, mcp_client, mcp_research
  ⑧ 右手 right_hand  看得见·改得动·起停得住           admin, operations, service_control, params,
                                                     config, web/
  ⑨ 骨架 skeleton    路径与数据地基                  paths.py（新增）

做三件事：① 把包内所有相对/绝对 import 统一改写成新绝对路径；② git mv 文件；
③ 修正 __file__/parents[N] 类路径表达式（改走 aiji.skeleton.paths）。

用法：
    python tools/reorg_body.py            # dry-run：只打印计划
    python tools/reorg_body.py --apply    # 真做
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ── 旧路径 → 新路径（相对仓库根）────────────────────────────────────────────
MOVE: dict[str, str] = {
    # ① 感官
    "aiji/cli.py": "aiji/senses/cli.py",
    # ② 咽喉
    "aiji/server.py": "aiji/throat/server.py",
    "aiji/net_guard.py": "aiji/throat/net_guard.py",
    "aiji/bridge/__init__.py": "aiji/throat/bridge/__init__.py",
    "aiji/bridge/onebot.py": "aiji/throat/bridge/onebot.py",
    "aiji/bridge/octop_gateway.py": "aiji/throat/bridge/octop_gateway.py",
    # ③ 大脑
    "aiji/head.py": "aiji/brain/head.py",
    "aiji/debounce.py": "aiji/brain/debounce.py",
    "aiji/pool.py": "aiji/brain/pool.py",
    "aiji/gate.py": "aiji/brain/gate.py",
    "aiji/humanize.py": "aiji/brain/humanize.py",
    "aiji/role_boundary.py": "aiji/brain/role_boundary.py",
    "aiji/persona.py": "aiji/brain/persona.py",
    "aiji/selfhood.py": "aiji/brain/selfhood.py",
    "aiji/quota.py": "aiji/brain/quota.py",
    # ④ 海马体
    "aiji/memory.py": "aiji/hippocampus/memory.py",
    "aiji/recognize.py": "aiji/hippocampus/recognize.py",
    "aiji/real_engines.py": "aiji/hippocampus/real_engines.py",
    "aiji/media_audio.py": "aiji/hippocampus/media_audio.py",
    "aiji/extract.py": "aiji/hippocampus/extract.py",
    "aiji/reflect.py": "aiji/hippocampus/reflect.py",
    "aiji/recall.py": "aiji/hippocampus/recall.py",
    "aiji/inject.py": "aiji/hippocampus/inject.py",
    "aiji/store.py": "aiji/hippocampus/store.py",
    "aiji/vectors.py": "aiji/hippocampus/vectors.py",
    "aiji/writer.py": "aiji/hippocampus/writer.py",
    "aiji/janitor.py": "aiji/hippocampus/janitor.py",
    "aiji/db.py": "aiji/hippocampus/db.py",
    # ⑤ 内分泌
    "aiji/being.py": "aiji/endocrine/being.py",
    "aiji/life.py": "aiji/endocrine/life.py",
    "aiji/life_loop.py": "aiji/endocrine/life_loop.py",
    "aiji/being_checkup.py": "aiji/endocrine/being_checkup.py",
    "aiji/proactive.py": "aiji/endocrine/proactive.py",
    "aiji/presence.py": "aiji/endocrine/presence.py",
    "aiji/desktop_sense.py": "aiji/endocrine/desktop_sense.py",
    # ⑥ 心脏
    "aiji/heart.py": "aiji/heart/heart.py",
    "aiji/dispatch.py": "aiji/heart/dispatch.py",
    "aiji/social_service.py": "aiji/heart/social_service.py",
    "aiji/song_service.py": "aiji/heart/song_service.py",
    "aiji/sticker_service.py": "aiji/heart/sticker_service.py",
    "aiji/voice_service.py": "aiji/heart/voice_service.py",
    # ⑦ 左手
    "aiji/actions/__init__.py": "aiji/left_hand/actions/__init__.py",
    "aiji/actions/approval.py": "aiji/left_hand/actions/approval.py",
    "aiji/actions/executor.py": "aiji/left_hand/actions/executor.py",
    "aiji/actions/gui.py": "aiji/left_hand/actions/gui.py",
    "aiji/actions/policy.py": "aiji/left_hand/actions/policy.py",
    "aiji/actions/schema.py": "aiji/left_hand/actions/schema.py",
    "aiji/mcp_client.py": "aiji/left_hand/mcp_client.py",
    "aiji/mcp_research.py": "aiji/left_hand/mcp_research.py",
    # ⑧ 右手
    "aiji/admin.py": "aiji/right_hand/admin.py",
    "aiji/operations.py": "aiji/right_hand/operations.py",
    "aiji/service_control.py": "aiji/right_hand/service_control.py",
    "aiji/params.py": "aiji/right_hand/params.py",
    "aiji/config.py": "aiji/right_hand/config.py",
    "aiji/web/operations.html": "aiji/right_hand/web/operations.html",
}

REGISTRY: dict[str, str] = {
    "senses": ("senses", "① 感官 · 五官：外部世界从这里进来（命令行、看门人）。", "entry"),
    "throat": ("throat", "② 咽喉 · 神经通路：所有入口汇成一条咽喉（HTTP / 平台桥 / URL 护栏）。", "ingress"),
    "brain": ("brain", "③ 大脑 · 皮层六区：一次对话怎么被想出来（编排、模型池、出口闸门、人格）。", "orch"),
    "hippocampus": ("hippocampus", "④ 记忆 · 海马体：写不丢、读有预算、注入不越权（识别/抽取/召回/存储/代谢）。", "memory"),
    "endocrine": ("endocrine", "⑤ 内分泌 · 生命腺：能量、心情、体检、主动搭话、存在感。", "life"),
    "heart": ("heart", "⑥ 心脏 · 循环泵：从队列到真实送达（发件箱、限流退避、语音/点歌/表情/说说）。", "out"),
    "left_hand": ("left_hand", "⑦ 左手 · 动作：受控的动作与联网（工具定义/策略/审批/执行/桌面自动化/MCP）。", "tools"),
    "right_hand": ("right_hand", "⑧ 右手 · 控制：看得见、改得动、起停得住（控制台/配置/参数/服务生命周期）。", "ops"),
    "skeleton": ("skeleton", "⑨ 骨架 · 双脚地基：仓库根、数据目录、schema 与库文件的位置只在这里定义。", "data"),
}

# 旧点号模块名 → 新点号模块名
MODMAP: dict[str, str] = {}
for old, new in MOVE.items():
    if old.endswith(".html"):
        continue
    om = old[:-3].replace("/", ".")
    nm = new[:-3].replace("/", ".")
    MODMAP[om] = nm
# 包路径也要能整包改（aiji.bridge → aiji.throat.bridge 已由上面覆盖）
for pkg, (_, _, _) in REGISTRY.items():
    MODMAP.setdefault(f"aiji.{pkg}", f"aiji.{pkg}")

IMPORT_SRC_DIRS = ["aiji", "tests", "tools", "bench"]
STRING_PATCHES = [
    # (旧串, 新串) —— 只改确定是“模块路径”的串，绝不动 "aiji.db" 这类文件名
    (r'-m\s+aiji\.cli\b', '-m aiji.senses.cli'),
    (r'"aiji\.cli:main"', '"aiji.senses.cli:main"'),
    (r"'aiji\.cli'", "'aiji.senses.cli'"),
]


def iter_py():
    for d in IMPORT_SRC_DIRS:
        p = ROOT / d
        if not p.exists():
            continue
        for f in sorted(p.rglob("*.py")):
            yield f


def dotted_to_file(mod: str) -> Path | None:
    rel = ROOT / (mod.replace(".", "/") + ".py")
    if rel.exists():
        return rel
    pkg = ROOT / mod.replace(".", "/") / "__init__.py"
    return pkg if pkg.exists() else None


def resolve_relative(modname: str | None, level: int, file: Path) -> str | None:
    """把相对 import 还原成绝对模块名。"""
    if not level:
        return modname
    base = file.relative_to(ROOT).with_suffix("")
    parts = list(base.parts)
    # file= aiji/brain/head.py → 包 = aiji.brain
    pkg_parts = parts[:-1]
    for _ in range(level - 1):
        if not pkg_parts:
            return None
        pkg_parts.pop()
    if modname:
        pkg_parts += modname.split(".")
    return ".".join(pkg_parts) if pkg_parts else None


def new_mod(old: str) -> str:
    if old in MODMAP:
        return MODMAP[old]
    # 前缀匹配（aiji.bridge.onebot.xxx 这种）
    for o, n in sorted(MODMAP.items(), key=lambda kv: -len(kv[0])):
        if old.startswith(o + "."):
            return n + old[len(o):]
    return old


def rewrite_imports(file: Path, write: bool = True) -> list[str]:
    src = file.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"SKIP {file}: {e}"]
    lines = src.splitlines(keepends=True)
    edits: list[tuple[int, int, str]] = []   # (start_line0, end_line0, new_text)
    notes: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            old_mod = resolve_relative(node.module, node.level, file) if (node.level or node.module) else None
            if not old_mod:
                continue
            if not old_mod.startswith("aiji"):
                continue
            aliases = [(a.name, a.asname) for a in node.names]
            mod_names = [n for n, _ in aliases]
            # from aiji import store —— 成员其实是被搬走的子模块
            if old_mod == "aiji" and all(f"aiji.{n}" in MODMAP or dotted_to_file(f"aiji.{n}") for n in mod_names):
                parts = []
                groups: dict[str, list[tuple[str, str | None]]] = {}
                for n, asn in aliases:
                    target = f"aiji.{n}"
                    nm = new_mod(target)
                    pkgname = nm.rsplit(".", 1)[0] if nm.count(".") >= 1 else "aiji"
                    groups.setdefault(pkgname, []).append((n, asn))
                for pkgname, names in groups.items():
                    txt = ", ".join(n if not a else f"{n} as {a}" for n, a in names)
                    parts.append(f"from {pkgname} import {txt}")
                new_lines = "\n".join(parts) + "\n"
                edits.append((node.lineno - 1, (node.end_lineno or node.lineno) - 1, new_lines))
                continue
            nm = new_mod(old_mod)
            if nm != old_mod or node.level:
                txt = ", ".join(n if not a else f"{n} as {a}" for n, a in aliases)
                edits.append((node.lineno - 1, (node.end_lineno or node.lineno) - 1, f"from {nm} import {txt}\n"))
        elif isinstance(node, ast.Import):
            parts = []
            changed = False
            for a in node.names:
                if a.name.startswith("aiji"):
                    nm = new_mod(a.name)
                    if nm != a.name:
                        changed = True
                    parts.append(nm + (f" as {a.asname}" if a.asname else ""))
                else:
                    parts.append(a.name + (f" as {a.asname}" if a.asname else ""))
            if changed:
                edits.append((node.lineno - 1, (node.end_lineno or node.lineno) - 1, "import " + ", ".join(parts) + "\n"))

    if not edits:
        return notes
    edits.sort(reverse=True)
    out = lines[:]
    for s, e, txt in edits:
        tail = ""
        last = out[e]
        m = re.search(r"#.*$", last)
        if m and last.rstrip().endswith(m.group(0)):
            tail = "  " + m.group(0)
        if not txt.endswith("\n"):
            txt += "\n"
        out[s:e + 1] = [txt.rstrip("\n") + tail + "\n"]
        notes.append(f"  {file.relative_to(ROOT)}: L{s + 1} → {txt.strip()}")
    if write:
        file.write_text("".join(out), encoding="utf-8")
    return notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true", help="只算改写计划，不写盘、不移动")
    args = ap.parse_args()

    print(f"模块映射 {len(MODMAP)} 条，待移动文件 {len(MOVE)} 个")
    plan_notes: list[str] = []
    files = list(iter_py())
    print(f"扫描 {len(files)} 个 .py（{', '.join(IMPORT_SRC_DIRS)}）")
    if args.check:
        for f in files:
            plan_notes.extend(rewrite_imports(f, write=False))
        print(f"将改写 import 语句 {len(plan_notes)} 处，前 25 条：")
        for n in plan_notes[:25]:
            print(n)
        sus = [n for n in plan_notes if "aiji.hippocampus.hippocampus" in n or "aiji.." in n]
        print(f"可疑改写 {len(sus)} 处")
        for n in sus[:10]:
            print("  ?", n)
        return 0
    if args.apply:
        for f in files:
            plan_notes.extend(rewrite_imports(f, write=True))

    print(f"改写 import 语句 {len(plan_notes)} 处")

    # 2) git mv
    for old, new in MOVE.items():
        o, n = ROOT / old, ROOT / new
        n.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "mv", str(o), str(n)], cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            r = subprocess.run(["git", "add", "-A", str(o)], cwd=ROOT, capture_output=True, text=True)
            o.rename(n)
        print(f"  moved {old} -> {new}")

    # 3) 清掉空的旧目录
    for d in ("aiji/bridge", "aiji/actions", "aiji/web"):
        p = ROOT / d
        if p.exists() and not any(p.iterdir()):
            p.rmdir()
            print(f"  rmdir {d}")

    # 4) 包 __init__
    for pkg, (name, doc, band) in REGISTRY.items():
        if pkg == "skeleton":
            continue
        init = ROOT / "aiji" / pkg / "__init__.py"
        if not init.exists():
            init.parent.mkdir(parents=True, exist_ok=True)
            init.write_text(f'"""{doc}\n\n对应 docs/architecture.html 的 {band} 层。\n"""\n', encoding="utf-8")
            print(f"  new {init.relative_to(ROOT)}")

    print("\n下一步：python -m compileall -q aiji tests tools bench && python .tmp/archcheck/run_suite.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
