"""只读探测：定制声线是否真的走了 Seed-VC 转换（字节协议版）。

对照：edge-default（纯 TTS） vs dafeiyu（TTS + Seed-VC 换音色）。
若转换生效，两者的频谱质心/带宽应有可测差异，且引擎日志不出现 PermissionError。
"""

from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aiji.voice_service import VoiceRegistry  # noqa: E402

ROOT = Path(r"D:\AIJiYi")
OUT = ROOT / ".tmp"
OUT.mkdir(mode=0o777, exist_ok=True)
TEXT = "你好呀，我是大肥鱼，今天也在深海里待着。"


def stats(path: Path) -> dict:
    import numpy as np

    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        frames = w.getnframes()
        raw = w.readframes(frames)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size == 0:
        return {"seconds": 0.0}
    spectrum = np.abs(np.fft.rfft(audio[: rate * 4]))
    freqs = np.fft.rfftfreq(min(len(audio), rate * 4), 1.0 / rate)
    centroid = float((spectrum * freqs).sum() / max(spectrum.sum(), 1e-9))
    return {
        "seconds": round(frames / rate, 2),
        "rate": rate,
        "rms": round(float(np.sqrt((audio ** 2).mean())), 4),
        "centroid_hz": round(centroid, 1),
    }


registry = VoiceRegistry(ROOT / "data" / "voices")
print("active:", registry.active_id())
profile = registry.get("dafeiyu")
print("profile:", profile.id, "has_rvc=", bool(profile.postprocess.get("command") or profile.postprocess.get("url")))
print("ready:", registry._ready(profile))

results = {}
for pid in ("edge-default", "dafeiyu"):
    target = OUT / f"probe-{pid}.wav"
    started = time.time()
    result = registry.synthesize(TEXT, target, profile_id=pid)
    took = time.time() - started
    info = stats(target) if target.exists() else {}
    results[pid] = info
    print(f"\n[{pid}] status ok  provider={result.provider} profile={result.profile_id} "
          f"took={took:.1f}s size={target.stat().st_size if target.exists() else 0}")
    print("   audio:", info)

print("\nlast_fallback:", registry.last_fallback)

base = results.get("edge-default", {})
conv = results.get("dafeiyu", {})
if base.get("centroid_hz") and conv.get("centroid_hz"):
    delta = conv["centroid_hz"] - base["centroid_hz"]
    ratio = conv["centroid_hz"] / base["centroid_hz"]
    print(f"\n频谱质心: edge={base['centroid_hz']}Hz  dafeiyu={conv['centroid_hz']}Hz  "
          f"delta={delta:+.1f}Hz ratio={ratio:.2f}")
    print("判定:", "转换生效（音色已改变）" if abs(ratio - 1.0) > 0.05 else "几乎没变，可能仍走原音色")
