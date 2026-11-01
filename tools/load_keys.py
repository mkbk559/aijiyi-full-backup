# -*- coding: utf-8 -*-
"""AIJI 启动前的 key 桥：把 ~/.dsh/.credentials.yaml 的 refs 注入环境变量。

AIJiYi 的惯例是 key 只从环境变量读（config._load_routes 的 api_key_env 路径），
本工具在进程内把 DSH credentials 的 refs 桥接过来——密钥不落任何仓库文件。

用法（Python 进程内）：
    from tools.load_keys import bridge
    bridge()          # 读 refs → os.environ（已存在的环境变量不覆盖）

用法（PowerShell 子进程，给 CLI / 测试用）：
    python tools/load_keys.py --shell > keys.ps1; . keys.ps1; python ...
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_KNOWN = (
    "WORKBUDDY_HUB_KEY", "AGENT2API_KEY",
    "POLLINATIONS_API_KEY", "ZHIPU_API_KEY", "SILICONFLOW_API_KEY",
    "OPENROUTER_API_KEY",
)
_REF_LINE = re.compile(r"^  ([A-Za-z0-9_]+):\s*(.+?)\s*$")


def _refs_path() -> Path:
    return Path.home() / ".dsh" / ".credentials.yaml"


def read_refs() -> dict[str, str]:
    p = _refs_path()
    if not p.exists():
        return {}
    refs: dict[str, str] = {}
    in_refs = False
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if re.match(r"^refs:\s*$", line):
            in_refs = True
            continue
        if in_refs:
            if line and not line[0].isspace():
                in_refs = False
                continue
            m = _REF_LINE.match(line)
            if m:
                refs[m.group(1)] = m.group(2).strip("'\"")
    return refs


def bridge(overwrite: bool = False) -> dict[str, str]:
    """refs → os.environ。返回实际注入的（名字 → 是否新设）。"""
    injected: dict[str, str] = {}
    for name, val in read_refs().items():
        if name in _KNOWN and (overwrite or name not in os.environ) and val:
            os.environ[name] = val
            injected[name] = "set"
    return injected


def _shell() -> int:
    """输出 PowerShell 片段（不回显密钥值本身，只写赋值语句到 stdout——
    调用方重定向到临时文件再 dot-source；文件用完即删）。"""
    refs = read_refs()
    lines = []
    for name in _KNOWN:
        val = refs.get(name, "")
        if val:
            safe = val.replace("'", "''")
            lines.append(f"$env:{name} = '{safe}'")
    if lines:
        print("\n".join(lines))
    else:
        print("# no refs found")
    return 0


if __name__ == "__main__":
    if "--shell" in sys.argv:
        sys.exit(_shell())
    got = bridge()
    for k, v in got.items():
        print(f"{k}: {v}")
