"""桌宠的嘴和耳朵：SAPI 说话 + MCI 录音 + FunASR 听懂。

全部走 Windows 自带组件，**不装任何第三方音频包**：
- 说话：SAPI（`win32com.client` 的 SpVoice），中文音色优先选 Huihui
- 录音：winmm 的 MCI 命令（`open new type waveaudio`），16k/16bit/单声道
- 听懂：复用 `real_engines.transcribe_wav_bytes`（FunASR → whisper 回退）

三条约束：
- 说话**异步**，不阻塞界面；新话说出来先掐掉旧的（避免叠音）
- 录音**有时限**（默认 5s），到点自动停，不会无限占麦克风
- 任何一步失败都如实返回原因，不假装听懂了
"""

from __future__ import annotations

import asyncio
import ctypes
import importlib.util
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

SAMPLE_RATE = 16000
CHANNELS = 1
BITS = 16
DEFAULT_SECONDS = 5.0

# 语音引擎：edge（默认，微软在线、音色多）/ sapi（离线兜底）。
TTS_ENGINE = os.environ.get("AIJI_TTS", "edge").strip().lower()
DEFAULT_EDGE_VOICE = os.environ.get("AIJI_VOICE", "zh-CN-XiaoxiaoNeural")
TTS_DIR = Path(os.environ.get("AIJI_TTS_DIR", ".tmp/tts"))

EDGE_VOICES: tuple[str, ...] = (
    "zh-CN-XiaoxiaoNeural",   # 女，温暖
    "zh-CN-XiaoyiNeural",     # 女，活泼
    "zh-CN-YunxiNeural",      # 男，年轻
    "zh-CN-YunjianNeural",    # 男，浑厚
    "zh-CN-YunyangNeural",    # 男，播音
    "zh-CN-XiaohanNeural",    # 女，温柔
)


# ---------------- 说话（Edge / SAPI） ----------------

_SVSFlagsAsync = 1
_SVSFPurgeBeforeSpeak = 2

_sapi_lock = threading.Lock()
_sapi_voice = None
_play_lock = threading.Lock()


def _sapi():
    global _sapi_voice
    import win32com.client

    with _sapi_lock:
        if _sapi_voice is None:
            _sapi_voice = win32com.client.Dispatch("SAPI.SpVoice")
        return _sapi_voice


def pick_voice(voice=None, *, lang_hint=("huihui", "chinese", "zh-cn", "中文")):
    """挑一个中文音色。返回 (SpVoice, 音色描述)。找不到就用系统默认。"""
    v = _sapi()
    try:
        for item in v.GetVoices():
            desc = (item.GetDescription() or "").lower()
            if any(h in desc for h in lang_hint):
                v.Voice = item
                return v, item.GetDescription()
    except Exception:  # noqa: BLE001
        pass
    return v, "system-default"


def sapi_voices() -> list[str]:
    try:
        return [item.GetDescription() for item in _sapi().GetVoices()]
    except Exception:  # noqa: BLE001
        return []


def _edge_available() -> bool:
    return (importlib.util.find_spec("edge_tts") is not None
            and shutil.which("ffmpeg") is not None)


def _signed(value: int, unit: str) -> str:
    return f"{int(value):+d}{unit}"


def synthesize_edge(text: str, path: str | Path, *, voice: str | None = None,
                    rate: int = 0, pitch: int = 0, volume: int = 0) -> bool:
    """在线合成一段 mp3；失败返回 False（调用方回退 SAPI）。"""
    if not _edge_available():
        return False
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        import edge_tts

        async def run() -> None:
            communicate = edge_tts.Communicate(
                text, voice or DEFAULT_EDGE_VOICE,
                rate=_signed(rate, "%"), pitch=_signed(pitch, "Hz"),
                volume=_signed(volume, "%"),
            )
            await communicate.save(str(out))

        asyncio.run(asyncio.wait_for(run(), 60))
        return out.exists() and out.stat().st_size > 0
    except Exception:  # noqa: BLE001 - 网络/引擎失败都回退
        return False


def _speak_sapi(text: str, *, rate: int = 0, volume: int = 100, wait: bool = False) -> bool:
    try:
        v, _desc = pick_voice()
        try:
            v.Rate = int(rate)
            v.Volume = int(volume)
        except Exception:  # noqa: BLE001
            pass
        v.Speak(text, _SVSFlagsAsync | _SVSFPurgeBeforeSpeak)
        if wait:
            while True:
                try:
                    if not v.Status.RunningState:
                        break
                except Exception:  # noqa: BLE001
                    break
                time.sleep(0.1)
        return True
    except Exception:  # noqa: BLE001
        return False


def _play_edge(text: str, *, voice: str | None, rate: int, volume: int,
               wait: bool) -> bool:
    """合成 mp3 → ffmpeg 转 wav → winsound 播放；失败回退 SAPI。"""
    mp3 = TTS_DIR / f"edge-{uuid.uuid4().hex}.mp3"
    wav = mp3.with_suffix(".wav")
    try:
        if not synthesize_edge(text, mp3, voice=voice, rate=rate,
                               volume=volume - 100 if volume < 100 else 0):
            return _speak_sapi(text, rate=rate, volume=volume, wait=wait)
        try:
            import winsound
        except ImportError:
            return _speak_sapi(text, rate=rate, volume=volume, wait=wait)
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return _speak_sapi(text, rate=rate, volume=volume, wait=wait)
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(mp3),
             "-ar", "24000", "-ac", "1", str(wav)],
            timeout=30, check=True,
        )
        flags = winsound.SND_FILENAME | (winsound.SND_SYNC if wait else winsound.SND_ASYNC)
        winsound.PlaySound(str(wav), flags)
        return True
    except Exception:  # noqa: BLE001
        return _speak_sapi(text, rate=rate, volume=volume, wait=wait)
    finally:
        try:
            mp3.unlink()
        except OSError:
            pass
        # 异步播放时 wav 不能立刻删；下次调用前清理 1 小时前的旧文件。
        try:
            cutoff = time.time() - 3600
            for old in TTS_DIR.glob("edge-*.wav"):
                if old.stat().st_mtime < cutoff:
                    old.unlink()
        except OSError:
            pass


def speak(text: str, *, rate: int = 0, volume: int = 100, wait: bool = False,
          voice: str | None = None, engine: str | None = None) -> bool:
    """念一句话。默认 Edge（在线、音色好），失败回退 SAPI。

    `wait=False` 时在后台线程合成+播放，立即返回；`wait=True` 时等播完。
    """
    text = (text or "").strip()
    if not text:
        return False
    stop_speaking()
    engine = (engine or TTS_ENGINE).strip().lower()
    if engine == "edge" and _edge_available():
        result: list[bool] = []

        def job() -> None:
            with _play_lock:
                result.append(_play_edge(
                    text, voice=voice, rate=rate, volume=volume, wait=wait))

        thread = threading.Thread(target=job, name="aiji-tts", daemon=True)
        thread.start()
        if wait:
            thread.join()
            return bool(result and result[0])
        return True
    return _speak_sapi(text, rate=rate, volume=volume, wait=wait)


def stop_speaking() -> None:
    """停掉 Edge（winsound）和 SAPI 两路；SAPI 必须用同一个 SpVoice 实例 Purge。"""
    try:
        import winsound
        winsound.PlaySound(None, 0)
    except Exception:  # noqa: BLE001
        pass
    try:
        v, _ = pick_voice()
        v.Speak("", _SVSFlagsAsync | _SVSFPurgeBeforeSpeak)
    except Exception:  # noqa: BLE001
        pass


def save_wav(text: str, path: str | Path, *, rate: int = 0) -> Path | None:
    """把一句话录成 wav 文件（调试/留证用）。"""
    try:
        import win32com.client

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # 独立实例：避免把共享 SpVoice 的 AudioOutputStream 永久改成文件流。
        v = win32com.client.Dispatch("SAPI.SpVoice")
        try:
            v.Voice = _sapi().Voice
        except Exception:  # noqa: BLE001
            pass
        v.Rate = int(rate)
        fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = 34  # 22kHz 16bit 单声道
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Format = fmt
        stream.Open(str(p), 3, False)
        v.AudioOutputStream = stream
        v.Speak(text)
        stream.Close()
        return p
    except Exception:  # noqa: BLE001
        return None


# ---------------- 录音（MCI） ----------------

def _mci(cmd: str) -> int:
    """发一条 MCI 命令。返回 0 = 成功。"""
    buf = ctypes.create_unicode_buffer(255)
    return ctypes.windll.winmm.mciSendStringW(cmd, buf, 254, 0)


def record_wav(path: str | Path, seconds: float = DEFAULT_SECONDS,
               *, device_open_timeout: float = 2.0) -> Path | None:
    """录一段 wav（16k/16bit/单声道）。成功返回路径，失败返回 None。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.unlink()
    alias = "aijirec"
    secs = max(0.5, float(seconds))
    try:
        _mci(f"open new type waveaudio alias {alias}")
    except Exception:  # noqa: BLE001
        return None
    try:
        # 先设格式再录，否则默认 8bit 音质没法识
        _mci(f"set {alias} time format ms bitspersample {BITS} "
             f"channels {CHANNELS} samplespersec {SAMPLE_RATE}")
        if _mci(f"record {alias}") != 0:
            return None
        time.sleep(secs)
        _mci(f"stop {alias}")
        if _mci(f'save {alias} "{p}"') != 0:
            return None
        return p if p.exists() and p.stat().st_size > 44 else None
    finally:
        _mci(f"close {alias}")


def record_bytes(seconds: float = DEFAULT_SECONDS, tmp_dir: str | Path = ".tmp") -> bytes:
    """录一段，返回 wav 字节（读不出来返回 b""）。"""
    p = record_wav(Path(tmp_dir) / f"rec_{int(time.time() * 1000)}.wav", seconds)
    if p is None:
        return b""
    try:
        return p.read_bytes()
    finally:
        try:
            p.unlink()
        except OSError:
            pass


# ---------------- 听懂（ASR） ----------------

def transcribe(wav: bytes) -> tuple[str, float]:
    """wav 字节 → (文字, 置信)。引擎缺位返回 ("", 0.0)。"""
    if not wav:
        return "", 0.0
    try:
        from .. import real_engines as re
        if not re.HAS_ASR:
            return "", 0.0
        return re.transcribe_wav_bytes(wav)
    except Exception:  # noqa: BLE001
        return "", 0.0


def listen(seconds: float = DEFAULT_SECONDS, *, tmp_dir: str | Path = ".tmp") -> dict:
    """录一句 + 听懂。返回 {text, confidence, ok, reason, took_ms}。"""
    t0 = time.time()
    wav = record_bytes(seconds, tmp_dir=tmp_dir)
    if not wav:
        return {"text": "", "confidence": 0.0, "ok": False, "reason": "record_failed",
                "took_ms": round((time.time() - t0) * 1000, 1)}
    text, conf = transcribe(wav)
    return {
        "text": text, "confidence": round(float(conf), 3),
        "ok": bool(text.strip()),
        "reason": "" if text.strip() else "no_speech",
        "took_ms": round((time.time() - t0) * 1000, 1),
    }


__all__ = [
    "DEFAULT_SECONDS", "EDGE_VOICES", "listen", "pick_voice", "record_bytes",
    "record_wav", "sapi_voices", "save_wav", "speak", "stop_speaking",
    "synthesize_edge", "transcribe",
]
