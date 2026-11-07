# -*- coding: utf-8 -*-
"""真实 OCR/ASR 引擎适配器（2026-10-29 接入）。

- OCR：RapidOCR（onnxruntime，PP-OCRv4 系，pip 即装，CPU 实测 ~1.1s/图）
- ASR：优先 FunASR paraformer-zh（中文 SOTA，实测 14/14 字符命中、0.9s），
  回退 faster-whisper（CTranslate2，tiny 模型 CPU 实测 ~17s 首载 / 秒级后续）
  注意：modelscope.cn 直连通；huggingface 直连被墙，whisper 模型下载走
  HF_ENDPOINT=https://hf-mirror.com（首次下载后缓存，之后离线可用）

两个引擎都是**懒加载单例**：进程内首次调用才加载模型（OCR 首载 ~1s，
whisper tiny 首载 ~15s），之后复用。超时由 Recognizers._timed 管。

设计约束（与 recognize.py 一致）：适配器只做"输入 → (text, confidence)"，
不碰库、不做抽取；异常如实抛给 Recognizers 转 status='error'。
"""
from __future__ import annotations

import io
import threading
import wave
from pathlib import Path

_LOCK = threading.Lock()
_OCR = None
_ASR = None


def _probe(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:  # noqa: BLE001
        return False


# 安装探测（import 成本低；真正的模型加载在首次调用）
HAS_OCR = _probe("rapidocr_onnxruntime")
HAS_ASR = _probe("faster_whisper")
HAS_FUNASR = _probe("funasr") and _probe("torch")   # funasr 还要 torch，缺一不可

_FUNASR = None
_ASR_MODEL = "tiny"


def set_asr_model(size: str) -> None:
    """设置 whisper 回退模型大小（tiny/base/small，由 Settings.asr_model 注入）。"""
    global _ASR_MODEL
    if size in ("tiny", "base", "small", "medium", "large", "large-v2", "large-v3"):
        _ASR_MODEL = size


def _get_funasr():
    """FunASR paraformer-zh 单例（含 VAD + 标点）。"""
    global _FUNASR
    if _FUNASR is None:
        from funasr import AutoModel
        _FUNASR = AutoModel(
            model="paraformer-zh",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            disable_update=True,
        )
    return _FUNASR


# ---------------- OCR ----------------

def _get_ocr():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    return _OCR


def ocr_image(data: bytes) -> tuple[str, float]:
    """图片字节 → (拼接文本, 平均置信度)。空图/无文字 → ("", 0.0)。"""
    import numpy as np
    from PIL import Image
    engine = _get_ocr()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    arr = np.array(img)
    result, _elapse = engine(arr)
    if not result:
        return "", 0.0
    texts, scores = [], []
    for item in result:
        try:
            text, score = item[1], float(item[2])
        except (ValueError, TypeError, IndexError):
            continue
        if text and text.strip():
            texts.append(text.strip())
            scores.append(score)
    return " ".join(texts), (sum(scores) / len(scores) if scores else 0.0)


def ocr_images(data_list: list[bytes]) -> tuple[str, float]:
    """多图批量 OCR：复用单例引擎（不重复加载），按行拼接、平均置信。"""
    texts, confs = [], []
    for data in data_list:
        t, c = ocr_image(data)
        if t:
            texts.append(t)
            confs.append(c)
    if not texts:
        return "", 0.0
    return "\n".join(texts), sum(confs) / len(confs)


# ---------------- ASR ----------------

def _resample16k(audio: "object", src_sr: int) -> tuple["object", int]:
    import numpy as np
    if src_sr == 16000:
        return audio, 16000
    n_out = int(len(audio) * 16000 / src_sr)
    idx = np.linspace(0, len(audio) - 1, n_out)
    return np.interp(idx, np.arange(len(audio)), audio).astype("float32"), 16000


def _get_asr(model_size: str | None = None):
    global _ASR
    if _ASR is None:
        from faster_whisper import WhisperModel
        _ASR = WhisperModel(model_size or _ASR_MODEL, device="cpu", compute_type="int8")
    return _ASR


def _load_wav_float32(data: bytes) -> tuple["object", int]:
    """wav 字节 → (float32 单声道 numpy, 原采样率)。Py3.13 无 PyAV 兼容轮，
    自读 PCM 绕开解码器。"""
    import numpy as np
    with wave.open(io.BytesIO(data), "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw == 2:
        audio = np.frombuffer(raw, dtype=np.int16).astype("float32") / 32768.0
    elif sw == 4:
        audio = np.frombuffer(raw, dtype=np.int32).astype("float32") / 2147483648.0
    else:
        raise ValueError(f"unsupported sample width: {sw}")
    if ch > 1:
        audio = audio.reshape(-1, ch).mean(axis=1)
    return audio, sr


def transcribe_wav_bytes(data: bytes) -> tuple[str, float]:
    """wav 字节 → (转写文本, 置信度估计)。优先 FunASR paraformer-zh
    （中文准确率实测优于 whisper tiny），无 funasr 时回退 faster-whisper。"""
    audio, sr = _load_wav_float32(data)
    audio, _ = _resample16k(audio, sr)

    if HAS_FUNASR:
        try:
            import torch

            res = _get_funasr().generate(input=torch.from_numpy(audio), batch_size_s=60)
            text = (res[0]["text"].strip() if res and res[0].get("text") else "")
            if text:
                return text, 0.9   # paraformer 无逐段 logprob，用非空给保守置信
        except Exception:  # noqa: BLE001 - FunASR 挂了就回退 whisper，别把链路带崩
            pass

    model = _get_asr()
    segments, info = model.transcribe(audio, language="zh")
    segs = list(segments)
    text = "".join(s.text for s in segs).strip()
    # whisper 的 avg_logprob → 经验映射到 0-1 置信：0.85 + lp*0.4
    # （lp=-0.2 → 0.77；lp=-1.0 → 0.45；再夹到 [0.3, 0.95]）
    if segs:
        lp = sum(s.avg_logprob for s in segs) / len(segs)
        conf = max(0.3, min(0.95, 0.85 + lp * 0.4))
    else:
        conf = 0.3
    return text, float(conf)


def transcribe_file(path: str | Path) -> tuple[str, float]:
    return transcribe_wav_bytes(Path(path).read_bytes())


def transcribe_wavs(data_list: list[bytes]) -> tuple[str, float]:
    """多音频批量 ASR：复用单例引擎，空格拼接、平均置信。"""
    texts, confs = [], []
    for data in data_list:
        t, c = transcribe_wav_bytes(data)
        if t:
            texts.append(t)
            confs.append(c)
    if not texts:
        return "", 0.0
    return " ".join(texts), sum(confs) / len(confs)
