# -*- coding: utf-8 -*-
"""Windows SAPI TTS 生成中文 wav → faster-whisper 真跑 ASR。"""
import io
import sys
import time
import wave
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
WORK = Path(r"D:\AIJiYi\.tmp")
WORK.mkdir(mode=0o777, exist_ok=True)

TEXT = "我叫陈明，住在深圳，周末喜欢钓鱼。"
wav_path = WORK / "asr_test.wav"

# ---- SAPI TTS ----
import win32com.client  # noqa: E402  (pywin32；没有就用 PowerShell)

try:
    speaker = win32com.client.Dispatch("SAPI.SpVoice")
    # 中文声音
    for v in speaker.GetVoices():
        if "Huihui" in v.GetDescription() or "Chinese" in v.GetDescription():
            speaker.Voice = v
            break
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format.Type = 34  # SAFT16kHz16BitMono  = 34
    stream.Open(str(wav_path), 3)  # SSFMCreateForWrite
    speaker.AudioOutputStream = stream
    speaker.Speak(TEXT)
    stream.Close()
    print("TTS OK:", wav_path)
except Exception as e:
    print("win32com TTS FAIL:", e)
    sys.exit(2)

# ---- faster-whisper ----
import numpy as np  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402

# av（PyAV）在 Py3.13 无兼容轮，decode_audio 会炸——自己读 wav（16kHz 16bit mono）为
# float32 [-1,1]，直接喂 transcribe，绕开 av。
with wave.open(str(wav_path), "rb") as w:
    sr = w.getframerate()
    ch = w.getnchannels()
    assert ch == 1, ch
    raw = w.readframes(w.getnframes())
audio = (np.frombuffer(raw, dtype=np.int16).astype(np.float32)) / 32768.0
if sr != 16000:  # 线性重采样到 16k（whisper 要求）
    n_out = int(len(audio) * 16000 / sr)
    idx = np.linspace(0, len(audio) - 1, n_out)
    audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)
print(f"wav: {len(audio)/16000:.1f}s (src {sr}Hz → 16k)")

t0 = time.time()
model = WhisperModel("tiny", device="cpu", compute_type="int8")
segments, info = model.transcribe(audio, language="zh")
segs = list(segments)
dt = round(time.time() - t0, 1)
text = "".join(s.text for s in segs).strip()
print(f"ASR {dt}s（含模型加载） lang={info.language}")
print("识别:", text)
print("原文:", TEXT)
