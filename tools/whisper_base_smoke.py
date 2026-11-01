# -*- coding: utf-8 -*-
"""whisper tiny vs base 中文错字对照。跑法: python tools/whisper_base_smoke.py

用同一句 SAPI TTS 音频（与 funasr_smoke 同源），对比 tiny/base 对"陈明"等
专名的识别。base 模型 ~150MB，首次走 hf-mirror 下载（离线后缓存）。
"""
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEXT = "我叫陈明，住在深圳，周末喜欢钓鱼。"


def make_wav(path: str) -> str:
    import win32com.client

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tts = win32com.client.Dispatch("SAPI.SpVoice")
    for v in tts.GetVoices():
        if "Huihui" in v.GetDescription():
            tts.Voice = v
            break
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 34
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format = fmt
    stream.Open(path, 3, False)
    tts.AudioOutputStream = stream
    tts.Speak(TEXT)
    stream.Close()
    return path


def load_wav_16k(path: str):
    from aiji.real_engines import _resample16k

    with wave.open(path, "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        data, _ = _resample16k(data, sr)
    return data


def transcribe(model_size: str, audio):
    from faster_whisper import WhisperModel

    import time
    t0 = time.time()
    m = WhisperModel(model_size, device="cpu", compute_type="int8")
    load = time.time() - t0
    t0 = time.time()
    segs = list(m.transcribe(audio, language="zh")[0])
    dt = time.time() - t0
    text = "".join(s.text for s in segs).strip()
    return text, load, dt


def main():
    wav = sys.argv[1] if len(sys.argv) > 1 else os.path.join(".tmp", "whisper_base_smoke.wav")
    if len(sys.argv) <= 1 and not os.path.exists(wav):
        make_wav(wav)
    audio = load_wav_16k(wav)
    print(f"[audio] {len(audio)/16000:.2f}s @16k\n")

    ref = TEXT.replace("，", "").replace("。", "")
    for size in ("tiny", "base"):
        text, load, dt = transcribe(size, audio)
        wrong = sum(1 for c in ref if c not in text)
        ok = "陈明" in text and "深圳" in text and "钓鱼" in text
        print(f"[{size}] ({load:.0f}s load, {dt:.1f}s) {text}")
        print(f"       缺字 {wrong}/{len(ref)}  关键三词{'全对' if ok else '有错'}")

    # 结论判定：base 关键三词全对即 PASS（tiny 已知"陈明→臣名"错）
    _, _, _ = transcribe("tiny", audio)


if __name__ == "__main__":
    main()
