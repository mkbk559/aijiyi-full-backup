"""决定性探测：dafeiyu 的 Seed-VC 后处理到底有没有真的执行。

隔离变量：手工生成 dafeiyu 用的 edge 原始音频，再直接调 _postprocess_http，
对比"转换前 vs 转换后"的频谱。若链路正常，两者必须明显不同且耗时 >0.3s；
若后处理失败，客户端会抛 VoiceError 或返回近似原始音频。
"""

from __future__ import annotations

import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402

from aiji.voice_service import VoiceError, VoiceRegistry  # noqa: E402

ROOT = Path(r"D:\AIJiYi")
TEXT = "你好呀，我是大肥鱼，今天也在深海里待着。"


def stats(path: Path) -> dict:
    with wave.open(str(path), "rb") as w:
        rate, frames = w.getframerate(), w.getnframes()
        raw = w.readframes(frames)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    n = min(len(audio), rate * 4)
    spectrum = np.abs(np.fft.rfft(audio[:n]))
    freqs = np.fft.rfftfreq(n, 1.0 / rate)
    return {
        "seconds": round(frames / rate, 2),
        "rms": round(float(np.sqrt((audio ** 2).mean())), 4),
        "centroid_hz": round(float((spectrum * freqs).sum() / max(spectrum.sum(), 1e-9)), 1),
    }


registry = VoiceRegistry(ROOT / "data" / "voices")
profile = registry.get("dafeiyu")
endpoint = str(profile.postprocess.get("url") or "")
print("endpoint:", endpoint)
print("reference:", profile.postprocess.get("reference"))

work = ROOT / ".tmp" / "voice-direct"
work.mkdir(mode=0o777, parents=True, exist_ok=True)
raw = work / "raw-edge.wav"

# 1) 用 dafeiyu profile 的 edge 参数直接生成原始音频（不经过后处理）
mp3 = work / "raw-edge.mp3"
registry._edge(TEXT, mp3, profile)
registry._to_wav(mp3, raw)
print("\n原始 edge 音频:", raw.stat().st_size, "bytes", stats(raw))

# 2) 直接调后处理（字节协议），单独计时
started = time.time()
try:
    converted = registry._postprocess_http(raw, work, profile, endpoint)
    took = time.time() - started
    print(f"\n后处理返回: {converted}  {converted.stat().st_size} bytes  took={took:.2f}s")
    print("转换后:", stats(converted))
    a, b = stats(raw), stats(converted)
    ratio = b["centroid_hz"] / max(a["centroid_hz"], 1e-9)
    print(f"\n质心比={ratio:.3f}  时长差={b['seconds'] - a['seconds']:+.2f}s  RMS比={b['rms'] / max(a['rms'], 1e-9):.3f}")
    changed = abs(ratio - 1.0) > 0.03 or abs(b["rms"] / max(a["rms"], 1e-9) - 1.0) > 0.05
    print("判定:", "Seed-VC 转换已执行" if changed else "输出与原始几乎相同 → 转换未生效")
except VoiceError as exc:
    print(f"\n后处理失败: {exc.code} :: {exc.message}")
except Exception as exc:  # noqa: BLE001
    print(f"\n后处理异常: {type(exc).__name__}: {exc}")
