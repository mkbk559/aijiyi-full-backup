"""TTS/换声各条路径的实测延迟（2026-11-03）。

目的：回答"换什么引擎"。换引擎之前先量清楚**同一条句子上**各条路径要多久：
  1. sapi（本机离线，WASAPI/SAPI，零网络）
  2. edge（微软在线，需要出网）
  3. RVC 换声那一段（已由 rvc_bench/rvc_f0_bench 量过，这里只做对比引用）

输出 JSON，失败也照实记（例如本机没网就是 failed，不是 0 ms）。
用法：python tools/archcheck/tts_bench.py [text]
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from aiji.heart.voice_service import VoiceProfile, VoiceRegistry  # noqa: E402

TEXT = sys.argv[1] if len(sys.argv) > 1 else "今天下午三点在楼下咖啡店见，别忘了带上那份材料。"
OUT = ROOT / ".tmp" / "tts_bench"
OUT.mkdir(parents=True, exist_ok=True)


def run(reg: VoiceRegistry, profile: VoiceProfile, name: str) -> dict:
    target = OUT / f"{name}.wav"
    if target.exists():
        target.unlink()
    t0 = time.perf_counter()
    try:
        res = reg.synthesize(TEXT, target, profile_id=profile.id)
        ms = (time.perf_counter() - t0) * 1000.0
        return {
            "path": name,
            "ok": True,
            "ms": round(ms, 1),
            "bytes": target.stat().st_size if target.exists() else 0,
            "provider": getattr(res, "provider", None),
        }
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000.0
        return {
            "path": name,
            "ok": False,
            "ms": round(ms, 1),
            "error": f"{type(exc).__name__}: {exc}"[:300],
            "trace_tail": traceback.format_exc().splitlines()[-1][:200],
        }


def main() -> None:
    reg = VoiceRegistry(root=OUT / "voices")
    results = []

    # SAPI：本机 Windows 自带，完全离线
    sapi = VoiceProfile(id="bench-sapi", name="SAPI", provider="edge",
                        voice="zh-CN-XiaoxiaoNeural")
    t0 = time.perf_counter()
    try:
        raw = OUT / "sapi-raw.wav"
        reg._sapi(TEXT, raw, sapi)  # 直接打 provider，绕开 edge 的降级判断
        results.append({"path": "sapi", "ok": True,
                        "ms": round((time.perf_counter() - t0) * 1000.0, 1),
                        "bytes": raw.stat().st_size if raw.exists() else 0})
    except Exception as exc:  # noqa: BLE001
        results.append({"path": "sapi", "ok": False,
                        "ms": round((time.perf_counter() - t0) * 1000.0, 1),
                        "error": f"{type(exc).__name__}: {exc}"[:300]})

    # Edge：在线
    results.append(run(reg, reg.get("edge-default"), "edge"))

    out = {
        "text": TEXT,
        "text_len": len(TEXT),
        "results": results,
        "note": "synthesize 失败会自动降级到 sapi（voice_service 的既有行为），"
                "所以 edge 那行的 ok=True 不代表真的用了 edge，看 provider 字段",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    (ROOT / "tools" / "archcheck" / "results" / "tts_bench.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
