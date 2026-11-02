# -*- coding: utf-8 -*-
"""FunASR (paraformer-zh) 冒烟：同一句测试音频，对照 faster-whisper tiny 的结果。

跑法:  python tools/funasr_smoke.py <wav路径>
无参数时自动用 SAPI TTS 造一句（与 asr_smoke.py 同句），保证可比。
"""
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

TEXT = "我叫陈明，住在深圳，周末喜欢钓鱼。"


def make_wav(path: str) -> str:
    """SAPI TTS 造 16k 单声道 wav（与 faster-whisper 冒烟同源）。"""
    import win32com.client

    shutil_dir = os.path.dirname(path)
    os.makedirs(shutil_dir, exist_ok=True)
    tts = win32com.client.Dispatch("SAPI.SpVoice")
    # Huihui 中文女声（与 asr_smoke.py 同一音色）
    for v in tts.GetVoices():
        if "Huihui" in v.GetDescription():
            tts.Voice = v
            break
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 34  # 16kHz 16bit（实际写 44.1k，后面重采样）
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format = fmt
    stream.Open(path, 3, False)  # SSFMCreateForWrite=3
    tts.AudioOutputStream = stream
    tts.Speak(TEXT)
    stream.Close()
    return path


def load_wav_16k(path: str):
    """读 wav → float32 numpy @16k（复用 real_engines 的重采样逻辑）。"""
    from aiji.hippocampus.real_engines import _resample16k

    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported sample width {sw}")
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        data, _ = _resample16k(data, sr)
    return data


def main():
    from funasr import AutoModel

    wav_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(".tmp", "funasr_smoke.wav")
    if len(sys.argv) <= 1 and not os.path.exists(wav_path):
        make_wav(wav_path)
        print(f"[tts] generated: {wav_path}")

    print("[model] downloading/loading paraformer-zh (modelscope.cn)...")
    model = AutoModel(
        model="paraformer-zh",
        vad_model="fsmn-vad",
        punc_model="ct-punc",
        disable_update=True,
    )
    audio = load_wav_16k(wav_path)
    print(f"[audio] {len(audio)/16000:.2f}s @16k")

    import time
    import torch

    t0 = time.time()
    res = model.generate(input=torch.from_numpy(audio), batch_size_s=60)
    dt = time.time() - t0
    text = res[0]["text"] if res else ""
    print(f"[asr] ({dt:.1f}s) {text}")

    # 简单对照
    ref = TEXT.replace("，", "").replace("。", "")
    hyp = text.replace("，", "").replace("。", "").replace(" ", "")
    hit = sum(1 for c in ref if c in hyp)
    print(f"[score] 字符命中率 {hit}/{len(ref)}")
    expected = "陈明" in text and "深圳" in text and "钓鱼" in text
    print("[verdict]", "PASS" if expected else "CHECK-ACCURACY")


if __name__ == "__main__":
    main()
