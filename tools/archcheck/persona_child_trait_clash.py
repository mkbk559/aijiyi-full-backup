"""实测：人格切成"六七岁小孩"后，历史涌现性格还会不会漏进提示词。

背景（2026-11-03 审计）：`head.py` 组装提示词时无条件把
`memory.traits(ident)` 交给 `selfhood.system_prefix()`，而 `persona=child`
只影响 `persona.style_block()`。库里 1521 条 trait 中有 235 条是
"喜欢调侃/损对方/自嘲"这类成年语感（置信 0.95、evidence 最高 90）。

本脚本用**假模型**只做提示词取证（不打真模型，避免随机性）：
1. 建临时库，写入一条成年语感 trait + 一条全局 trait；
2. 以 persona=child 组装一轮对话，捕获 model_messages；
3. 检查 trait 文本是否出现在系统提示里。

产物：tools/archcheck/results/persona_child_trait_clash.json
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from aiji.brain.head import Head  # noqa: E402
from aiji.hippocampus import store  # noqa: E402
from aiji.hippocampus.memory import Memory  # noqa: E402
from aiji.hippocampus.store import Ident  # noqa: E402
from aiji.right_hand.config import Settings  # noqa: E402

ADULT_TRAITS = (
    ("调侃", "常带调侃语气损对方，如『你那是守锅还是守夜』", 90),
    ("自嘲", "对话中喜欢调侃对方或自嘲，营造轻松的交流氛围", 40),
)
PROMPT = "在吗"


class CapturePool:
    """只记录喂给模型的消息，返回一句占位回复。"""

    _states: ClassVar[list] = [object()]

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def chat(self, messages, *, timeout_s=None, **kwargs):
        self.calls.append(messages)
        return type("R", (), {
            "text": "在呀。",
            "route": "capture",
            "latency_ms": 1.0,
            "tool_calls": [],
        })()


def workdir(name: str) -> Path:
    path = ROOT / ".tmp" / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o777)
    return path


def main() -> int:
    tmp = workdir("persona-trait-clash")
    settings = Settings(
        db_path=tmp / "clash.db",
        routes=(),
        persona="child",
        debounce_ms=0,
        humanize_min_ms=0,
        humanize_max_ms=0,
        typing_min_ms=0,
        typing_max_ms=0,
    )
    memory = Memory(settings, engines="none")
    ident = Ident("qq", "u-audit", "private")
    try:
        with memory.db.tx() as conn:
            for name, value, weight in ADULT_TRAITS:
                store.upsert_trait(conn, ident, name, value, weight=weight)

        pool = CapturePool()
        memory.pool = pool
        Head(memory).reply(
            [{"role": "user", "content": PROMPT}],
            ident,
            session="clash",
            remember=False,
        )
        if not pool.calls:
            print("模型没被调用，检查 Settings/pool 接线")
            return 1
        messages = pool.calls[0]
        system_text = "\n".join(
            str(m.get("content", "")) for m in messages if m.get("role") == "system")

        probes = {
            "trait_调侃": "调侃" in system_text,
            "trait_自嘲": "自嘲" in system_text,
            "trait_损对方": "损对方" in system_text,
            "persona_child": "六七岁" in system_text,
            "few_shot_present": "不是你们真说过的话" in system_text,
        }
        verdict = {
            "leak": probes["trait_调侃"] or probes["trait_自嘲"],
            "probes": probes,
            "system_chars": len(system_text),
            "adult_traits_in_db": [v for _, v, _ in ADULT_TRAITS],
        }
        out = {"prompt": PROMPT, "verdict": verdict, "system_head": system_text[:1200]}
        target = ROOT / "tools" / "archcheck" / "results" / "persona_child_trait_clash.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(verdict, ensure_ascii=False))
        return 0 if not verdict["leak"] else 2
    finally:
        memory.close()


if __name__ == "__main__":
    raise SystemExit(main())
