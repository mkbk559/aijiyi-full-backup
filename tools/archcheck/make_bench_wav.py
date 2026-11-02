"""造用于 RVC 基准的样本 wav（带基频的谐波叠加，够 F0 提取用）。

用法：python tools/archcheck/make_bench_wav.py [out.wav] [seconds]
"""
from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".tmp/rvc_bench_in.wav")
OUT.parent.mkdir(parents=True, exist_ok=True)
SR = 40000
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
F0 = 220.0

frames = bytearray()
for i in range(int(SR * SECONDS)):
    t = i / SR
    # 基频 + 两个谐波，带一点包络，避免纯正弦被 F0 提取器当成静音
    v = 0.5 * math.sin(2 * math.pi * F0 * t)
    v += 0.25 * math.sin(2 * math.pi * 2 * F0 * t)
    v += 0.12 * math.sin(2 * math.pi * 3 * F0 * t)
    env = min(1.0, t / 0.05, (SECONDS - t) / 0.1)
    frames += struct.pack("<h", int(max(-1.0, min(1.0, v * max(0.0, env))) * 30000))

with wave.open(str(OUT), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(bytes(frames))
print(OUT, OUT.stat().st_size, "bytes")
