"""收语音：把 QQ 语音条转成文字，再走正常对话链路。

为什么要单独一层：
- 「对方发语音」和「对方打字」对模型应该是**同一个意思**，只是多一句"（对方是语音说的）"；
- 转写失败不能假装听懂——宁可让它说"我没听清，你打字吧"。

2026-11-06 起：`transcribe_record()` 包住 `real_engines.transcribe_file`（FunASR paraformer-zh），
任何异常都降级成 `{"ok": False, ...}`，调用方据此决定回退。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _whisper(path: Path) -> tuple[str, float]:
    """兜底引擎：faster-whisper（不需要 torch，int8 CPU）。

    aiji/hippocampus/real_engines 先试 FunASR，但它要 torch；本机主力 .venv 没有 torch，
    所以那条会直接抛 ModuleNotFoundError。这里的兜底保证"至少能听"。
    """
    from faster_whisper import WhisperModel

    model = _whisper_model()
    segments, _info = model.transcribe(str(path), language="zh", vad_filter=True)
    text = "".join(seg.text for seg in segments).strip()
    return text, 0.5


_WHISPER = None


def _whisper_model():
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel

        _WHISPER = WhisperModel("tiny", device="cpu", compute_type="int8")
    return _WHISPER


def transcribe_record(path: str | Path) -> dict[str, Any]:
    """语音文件 → 文字。返回 {ok, text, conf, error}；永远不抛异常。"""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "text": "", "conf": 0.0, "error": "file_missing"}
    errors = []
    text, conf = "", 0.0
    for engine in ("primary", "whisper"):
        try:
            if engine == "primary":
                from aiji.hippocampus import real_engines

                text, conf = real_engines.transcribe_file(p)
            else:
                text, conf = _whisper(p)
            if str(text or "").strip():
                break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{engine}: {type(exc).__name__}: {str(exc)[:80]}")
            text, conf = "", 0.0
    if not str(text or "").strip():
        return {"ok": False, "text": "", "conf": float(conf or 0.0),
                "error": " | ".join(errors) or "empty"}
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "text": "", "conf": float(conf or 0.0), "error": "empty"}
    return {"ok": True, "text": text, "conf": float(conf or 0.0), "error": ""}


def voice_note(text: str) -> str:
    """给提示词加个来源标记：让模型知道这句是听来的，不是打字的。"""
    body = str(text or "").strip()
    return f"（对方是语音说的）{body}" if body else ""


def to_turn_content(result: dict[str, Any], *, fallback: str = "（对方发来一条语音，我没听清）") -> str:
    """转写结果 → 入库/入上下文的文本。失败时用一句诚实的占位，不要编。"""
    if result.get("ok"):
        return voice_note(result.get("text") or "")
    return fallback
