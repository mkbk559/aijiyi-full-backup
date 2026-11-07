"""把「该不该发语音」和「怎么发出去」接起来。

分工：
- `voice_plan.decide()`  决定发不发（纯规则，有单测）
- `make_voice`          文本 → 语音文件（TTS + RVC 变声，引擎不在就退回原声）
- 平台适配器的 `voice()` 只负责把文件送出去（QQ 有，抖音/B站没有）

任何一步失败（引擎不通、文件没生成、平台不支持）都**降级成文字**，
不允许"语音失败就哑了"。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from aiji.heart import voice_plan

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "temp" / "voice"


def render(text: str, *, no_rvc: bool = False, timeout_s: int = 240) -> Path | None:
    """调 tools/make_voice.py 生成语音文件；失败返回 None（调用方降级文字）。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"v{int(time.time() * 1000)}.wav"
    cmd = [sys.executable, str(ROOT / "tools" / "make_voice.py"), text, "-o", str(out)]
    if no_rvc:
        cmd.append("--no-rvc")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              timeout=timeout_s)
    except Exception:  # noqa: BLE001
        return None
    if proc.returncode != 0:
        return None
    produced = Path(out)
    if not produced.exists():
        alt = out.with_suffix(".mp3")
        produced = alt if alt.exists() else produced
    return produced if produced.exists() and produced.stat().st_size > 1024 else None


def send(adapter: Any, peer: str | int, text: str, *, ctx: voice_plan.Context,
         scope: str = "dm", dry_run: bool = False) -> dict[str, Any]:
    """按规则决定发语音还是发文字，返回 {"kind": "voice"|"text", "why": …}。"""
    ok, why = voice_plan.decide(ctx)
    can_voice = hasattr(adapter, "voice")
    if ok and not can_voice:
        ok, why = False, f"{getattr(adapter, 'name', '该平台')} 发不了语音"
    audio = None
    if ok and not dry_run:
        audio = render(text)
        if audio is None:
            ok, why = False, "语音生成失败，降级文字"

    if not ok:
        send_text = adapter.dm if scope == "dm" else adapter.group
        res = send_text(peer, text, dry_run=dry_run)
        return {"kind": "text", "why": why, "result": res}

    res = adapter.voice(peer, str(audio), scope=scope, dry_run=dry_run)
    return {"kind": "voice", "why": why, "path": str(audio), "result": res}
