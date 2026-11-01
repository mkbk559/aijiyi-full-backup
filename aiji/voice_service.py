"""Voice profile registry and local TTS adapters.

Profiles are small JSON files under ``data/voices/<profile-id>/profile.json``.
The service deliberately keeps provider-specific details behind one interface:
callers only ask for text, a profile id and an output path.
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


class VoiceError(RuntimeError):
    """A stable, user-facing voice failure."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _safe_id(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip(".-")
    return clean[:80] or "default"


# 参考音每次合成都一样，别反复读 2MB 文件（带 mtime 失效）。
_REFERENCE_CACHE: dict[tuple[str, float], bytes] = {}


def _reference_bytes(path: Path) -> bytes:
    try:
        stamp = path.stat().st_mtime
    except OSError:
        stamp = 0.0
    key = (str(path), float(stamp))
    cached = _REFERENCE_CACHE.get(key)
    if cached is None:
        cached = path.read_bytes()
        if len(_REFERENCE_CACHE) > 8:
            _REFERENCE_CACHE.clear()
        _REFERENCE_CACHE[key] = cached
    return cached


def _clamp(value: Any, low: float, high: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


@dataclass(slots=True)
class VoiceProfile:
    id: str
    name: str
    provider: str = "edge"
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: int = 0
    pitch: int = 0
    volume: int = 0
    endpoint: str = ""
    model: str = ""
    prompt_text: str = ""
    prompt_lang: str = "zh"
    text_lang: str = "zh"
    reference_audio: str = ""
    api_key: str = ""
    mode: str = "sft"
    speaker: str = "中文女"
    instruct: str = ""
    timeout_s: int = 120
    postprocess: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    source: str = "builtin"

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, source: str = "") -> VoiceProfile:
        provider = str(payload.get("provider") or "edge").strip().lower()
        if provider not in ("edge", "gptsovits", "cosyvoice", "openai", "http"):
            raise VoiceError("unsupported_provider", f"unsupported provider: {provider}")
        raw_extra = payload.get("extra")
        raw_post = payload.get("postprocess")
        profile = cls(
            id=_safe_id(str(payload.get("id") or "")),
            name=str(payload.get("name") or payload.get("display_name") or "").strip()
            or "未命名声线",
            provider=provider,
            voice=str(payload.get("voice") or "zh-CN-XiaoxiaoNeural").strip(),
            rate=int(_clamp(payload.get("rate"), -100, 100, 0)),
            pitch=int(_clamp(payload.get("pitch"), -24, 24, 0)),
            volume=int(_clamp(payload.get("volume"), -100, 100, 0)),
            endpoint=str(payload.get("endpoint") or "").strip().rstrip("/"),
            model=str(payload.get("model") or "").strip(),
            prompt_text=str(payload.get("prompt_text") or "").strip()[:1000],
            prompt_lang=str(payload.get("prompt_lang") or "zh").strip()[:16],
            text_lang=str(payload.get("text_lang") or "zh").strip()[:16],
            reference_audio=str(payload.get("reference_audio") or "").strip(),
            api_key=str(payload.get("api_key") or "").strip(),
            mode=str(payload.get("mode") or "sft").strip().lower(),
            speaker=str(payload.get("speaker") or "中文女").strip()[:80],
            instruct=str(payload.get("instruct") or "").strip()[:1000],
            timeout_s=max(5, min(900, int(_clamp(
                payload.get("timeout_s"), 5, 900, 120)))),
            postprocess=raw_post if isinstance(raw_post, dict) else {},
            extra=raw_extra if isinstance(raw_extra, dict) else {},
            source=source,
        )
        if profile.prompt_lang not in ("zh", "en", "ja", "ko", "auto"):
            profile.prompt_lang = "zh"
        if profile.text_lang not in ("zh", "en", "ja", "ko", "auto"):
            profile.text_lang = "zh"
        return profile

    def public(self, *, active: bool = False) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "voice": self.voice,
            "rate": self.rate,
            "pitch": self.pitch,
            "volume": self.volume,
            "mode": self.mode,
            "speaker": self.speaker,
            "has_reference": bool(
                self.reference_audio or self.postprocess.get("reference")),
            "has_rvc": bool(
                self.postprocess.get("command") or self.postprocess.get("url")),
            "source": self.source,
            "active": active,
        }


@dataclass(slots=True)
class SynthesisResult:
    path: Path
    profile_id: str
    provider: str
    took_ms: float


class VoiceRegistry:
    """Discover profiles, persist the active choice and synthesize speech."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        ffmpeg: str | None = None,
        ffprobe: str | None = None,
    ) -> None:
        base = root or os.environ.get(
            "AIJI_VOICE_DIR",
            str(Path(os.environ.get("AIJI_DATA_DIR", "data")) / "voices"),
        )
        self.root = Path(base).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe = ffprobe or shutil.which("ffprobe") or "ffprobe"
        self._active_path = self.root / "active_voice.txt"
        # 最近一次合成是否因为"声线不可用"降级（诊断用，进 /v1/voices）
        self.last_fallback: dict[str, Any] | None = None
        self._builtin = VoiceProfile(
            id="edge-default",
            name="微软晓晓",
            provider="edge",
            voice=os.environ.get("AIJI_VOICE", "zh-CN-XiaoxiaoNeural"),
            source="builtin",
        )

    @staticmethod
    def _limit_peak(path: Path, ceiling: float = 0.95) -> None:
        """把 16-bit wav 的峰值压到 ceiling 以下（只在超过 0.98 时动手）。

        换声器（Seed-VC/RVC）的 float 输出常超过 ±1.0，直接写 PCM16 会削波：
        历史 seed-vc 产物 peak=1.000、2-10% 采样贴顶。放在客户端做，
        不依赖具体引擎是否实现，CLI/HTTP 两种接法都受益。
        """
        try:
            import array
            import wave

            with wave.open(str(path), "rb") as reader:
                params = reader.getparams()
                if params.sampwidth != 2 or params.nchannels < 1:
                    return
                raw = reader.readframes(params.nframes)
            samples = array.array("h")
            samples.frombytes(raw)
            if not samples:
                return
            peak = max(max(samples), -min(samples))
            if peak <= int(0.98 * 32767):
                return
            scale = (ceiling * 32767.0) / float(peak)
            scaled = array.array("h", (int(value * scale) for value in samples))
            with wave.open(str(path), "wb") as writer:
                writer.setparams(params)
                writer.writeframes(scaled.tobytes())
        except (OSError, EOFError, wave.Error, ValueError):
            # 不是标准 PCM wav / 文件被占用：保持原样，绝不让后处理拖垮合成
            return


    def profiles(self) -> list[VoiceProfile]:
        items = [self._builtin]
        for manifest in sorted(self.root.glob("*/profile.json")):
            try:
                raw = json.loads(manifest.read_text("utf-8"))
                if not isinstance(raw, dict):
                    continue
                raw.setdefault("id", manifest.parent.name)
                profile = VoiceProfile.from_dict(raw, source=str(manifest))
                profile.id = _safe_id(profile.id)
                items.append(profile)
            except (OSError, ValueError, TypeError, VoiceError):
                continue
        unique: dict[str, VoiceProfile] = {}
        for item in items:
            unique[item.id] = item
        return sorted(unique.values(), key=lambda item: (item.id != self.active_id(), item.id))

    def get(self, profile_id: str = "") -> VoiceProfile:
        wanted = _safe_id(profile_id or self.active_id())
        for profile in self.profiles():
            if profile.id == wanted:
                return profile
        raise VoiceError("profile_not_found", f"voice profile not found: {wanted}")

    def active_id(self) -> str:
        try:
            value = self._active_path.read_text("utf-8").strip()
        except OSError:
            value = ""
        value = _safe_id(value) if value else _safe_id(
            os.environ.get("AIJI_VOICE_PROFILE", "edge-default"))
        return value or "edge-default"

    def set_active(self, profile_id: str) -> VoiceProfile:
        profile = self.get(profile_id)
        self._active_path.write_text(profile.id, encoding="utf-8")
        return profile

    def snapshot(self) -> dict[str, Any]:
        active = self.active_id()
        items = []
        for profile in self.profiles():
            item = profile.public(active=profile.id == active)
            item["ready"] = self._ready(profile)
            items.append(item)
        return {
            "active": active,
            "root": str(self.root),
            "profiles": items,
            "ffmpeg": bool(shutil.which(self.ffmpeg) or Path(self.ffmpeg).is_file()),
            "last_fallback": self.last_fallback,
        }

    def _workdir(self) -> Path:
        """显式 0o777 工作目录。

        `tempfile.mkdtemp()`/`TemporaryDirectory()` 建的是 0o700，在本机沙箱下
        连它自己的清理（`_resetperms`）都会 PermissionError，所以不用它们。
        """
        base = self.root / ".work"
        base.mkdir(mode=0o777, parents=True, exist_ok=True)
        path = base / f"voice-{uuid.uuid4().hex[:10]}"
        path.mkdir(mode=0o777)
        return path

    def _ready(self, profile: VoiceProfile) -> bool:
        ready = self._provider_ready(profile)
        if ready and profile.postprocess.get("url"):
            parsed = urllib.parse.urlparse(str(profile.postprocess["url"]))
            host = parsed.hostname
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if host:
                try:
                    with socket.create_connection((host, port), timeout=0.25):
                        pass
                except OSError:
                    return False
        return ready

    def _provider_ready(self, profile: VoiceProfile) -> bool:
        if profile.provider == "edge":
            try:
                import edge_tts  # noqa: F401
            except ImportError:
                return False
            return True
        if profile.provider == "http":
            return "{text}" in str(profile.extra.get("url_template") or "")
        if profile.provider == "openai":
            return bool(profile.endpoint)
        if profile.provider == "gptsovits":
            return bool(profile.endpoint) and self._has_local_reference(
                profile.reference_audio)
        if profile.provider == "cosyvoice":
            if not profile.endpoint:
                return False
            if profile.mode in ("zero_shot", "cross_lingual"):
                return self._has_local_reference(profile.reference_audio)
            return True
        return False

    def synthesize(
        self,
        text: str,
        output: str | Path,
        *,
        profile_id: str = "",
        rate: int | None = None,
        pitch: int | None = None,
        volume: int | None = None,
        instruct: str = "",
    ) -> SynthesisResult:
        clean = self._clean_text(text)
        profile = self.get(profile_id)
        requested = profile
        if not self._ready(profile):
            fallback = self.get("edge-default")
            if fallback.id != profile.id and self._ready(fallback):
                # 定制声线不可用时静默退回会让人以为"语音坏了"，这里留痕进 /v1/voices。
                self.last_fallback = {
                    "requested": requested.id,
                    "used": fallback.id,
                    "reason": "profile_not_ready",
                    "at": time.time(),
                }
                profile = fallback
        else:
            self.last_fallback = None
        if rate is not None:
            profile.rate = int(_clamp(rate, -100, 100, profile.rate))
        if pitch is not None:
            profile.pitch = int(_clamp(pitch, -24, 24, profile.pitch))
        if volume is not None:
            profile.volume = int(_clamp(volume, -100, 100, profile.volume))
        if instruct:
            profile.instruct = str(instruct).strip()[:1000]
        target = Path(output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        tmp = self._workdir()
        try:
            raw = tmp / "provider-audio"
            used_provider = profile.provider
            try:
                if profile.provider == "edge":
                    self._edge(clean, raw, profile)
                elif profile.provider == "gptsovits":
                    self._gptsovits(clean, raw, profile)
                elif profile.provider == "cosyvoice":
                    self._cosyvoice(clean, raw, profile)
                elif profile.provider == "openai":
                    self._openai(clean, raw, profile)
                elif profile.provider == "http":
                    self._http(clean, raw, profile)
                else:  # guarded by from_dict
                    raise VoiceError(
                        "unsupported_provider", profile.provider)
            except VoiceError as primary:
                if profile.provider != "edge":
                    raise
                try:
                    if profile.voice == "zh-CN-XiaoxiaoNeural":
                        raise primary
                    source_profile = replace(
                        profile, voice="zh-CN-XiaoxiaoNeural")
                    self._edge(clean, raw, source_profile)
                except Exception:
                    try:
                        sapi_raw = raw.with_suffix(".wav")
                        self._sapi(clean, sapi_raw, profile)
                        raw = sapi_raw
                        used_provider = "sapi"
                    except Exception:
                        raise primary
            if not raw.exists() or raw.stat().st_size < 100:
                raise VoiceError("empty_audio", "TTS provider returned no audio")
            converted = tmp / "converted.wav"
            self._to_wav(raw, converted)
            if (
                profile.postprocess.get("command")
                or profile.postprocess.get("url")
            ):
                try:
                    converted = self._postprocess(
                        converted, tmp, profile, mode="speech")
                except VoiceError:
                    # Keep the provider voice when the optional converter is down.
                    pass
            self._limit_peak(converted)
            shutil.copy2(converted, target)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return SynthesisResult(
            path=target,
            profile_id=profile.id,
            provider=used_provider,
            took_ms=round((time.perf_counter() - started) * 1000, 1),
        )

    def convert_audio(
        self,
        source: str | Path,
        output: str | Path,
        *,
        profile_id: str = "",
        pitch: int | None = None,
        mode: str = "speech",
    ) -> Path:
        """Apply a profile's RVC/voice-conversion command to any audio file."""
        profile = self.get(profile_id)
        if pitch is not None:
            profile.pitch = int(_clamp(pitch, -24, 24, profile.pitch))
        if not (
            profile.postprocess.get("command")
            or profile.postprocess.get("url")
        ):
            raise VoiceError("converter_not_configured", "profile has no converter command")
        input_path = Path(source).expanduser().resolve()
        target = Path(output).expanduser().resolve()
        if not input_path.is_file():
            raise VoiceError("input_missing", f"audio not found: {input_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        converted = self._postprocess(
            input_path, target.parent, profile, mode=mode)
        shutil.copy2(converted, target)
        return target

    @staticmethod
    def _clean_text(text: str) -> str:
        clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", "", str(text or ""))
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            raise VoiceError("text_required", "text is required")
        if len(clean) > 4000:
            raise VoiceError("text_too_long", "text exceeds 4000 characters")
        return clean

    def _edge(self, text: str, output: Path, profile: VoiceProfile) -> None:
        try:
            import edge_tts
        except ImportError as exc:
            raise VoiceError("provider_unavailable", "edge-tts is not installed") from exc
        voice = profile.voice
        rate = profile.rate
        pitch = profile.pitch
        volume = profile.volume
        mp3 = output.with_suffix(".mp3")

        async def run() -> None:
            communicate = edge_tts.Communicate(
                text,
                voice,
                rate=f"{rate:+d}%",
                pitch=f"{pitch:+d}Hz",
                volume=f"{volume:+d}%",
            )
            await communicate.save(str(mp3))

        try:
            asyncio.run(asyncio.wait_for(run(), profile.timeout_s))
        except Exception as exc:
            raise VoiceError("provider_failed", f"edge-tts: {exc}") from exc
        if not mp3.exists():
            raise VoiceError("empty_audio", "edge-tts returned no audio")
        mp3.replace(output)

    def _sapi(
        self, text: str, output: Path, profile: VoiceProfile,
    ) -> None:
        script = Path(__file__).resolve().parents[1] / "tools" / "sapi_tts.ps1"
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy", "Bypass",
                    "-File", str(script),
                    "-Text", text,
                    "-Output", str(output),
                ],
                capture_output=True,
                timeout=60,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise VoiceError("provider_failed", f"SAPI: {exc}") from exc
        if result.returncode != 0 or not output.is_file():
            detail = (result.stderr or result.stdout or b"").decode(
                "utf-8", "replace")[-500:]
            raise VoiceError(
                "provider_failed", f"SAPI: {detail or result.returncode}")

    def _gptsovits(self, text: str, output: Path, profile: VoiceProfile) -> None:
        if not profile.endpoint:
            raise VoiceError("not_configured", "GPT-SoVITS endpoint is empty")
        reference = str(
            self._local_reference(profile.reference_audio)
            if profile.reference_audio else ""
        )
        params = {
            "text": text,
            "text_lang": profile.text_lang,
            "ref_audio_path": reference,
            "prompt_text": profile.prompt_text,
            "prompt_lang": profile.prompt_lang,
            "media_type": "wav",
        }
        api_style = str(profile.extra.get("api_style") or "legacy").strip().lower()
        if api_style in ("v2", "post", "json"):
            params["speed_factor"] = round(
                1.0 + _clamp(profile.rate, -100, 100, 0) / 200.0, 4)
            data = self._fetch(
                f"{profile.endpoint}/tts",
                method="POST",
                timeout_s=profile.timeout_s,
                headers={
                    "Content-Type": "application/json",
                    **(
                        {"Authorization": f"Bearer {profile.api_key}"}
                        if profile.api_key else {}
                    ),
                },
                data=json.dumps(params, ensure_ascii=False).encode("utf-8"),
                raw_response=True,
            )
            output.write_bytes(data)
            return
        query = urllib.parse.urlencode(params)
        data = self._fetch(
            f"{profile.endpoint}/tts?{query}",
            method="GET",
            timeout_s=profile.timeout_s,
            headers={"Authorization": f"Bearer {profile.api_key}"} if profile.api_key else {},
        )
        output.write_bytes(data)

    def _cosyvoice(self, text: str, output: Path, profile: VoiceProfile) -> None:
        if not profile.endpoint:
            raise VoiceError("not_configured", "CosyVoice endpoint is empty")
        if profile.mode == "zero_shot":
            ref = self._local_reference(profile.reference_audio)
            fields = {
                "tts_text": text,
                "prompt_text": profile.prompt_text,
            }
            data, content_type = self._multipart(
                fields,
                file_field="prompt_wav",
                file_path=ref,
            )
            endpoint = f"{profile.endpoint}/inference_zero_shot"
        elif profile.mode == "cross_lingual":
            ref = self._local_reference(profile.reference_audio)
            data, content_type = self._multipart(
                {"tts_text": text},
                file_field="prompt_wav",
                file_path=ref,
            )
            endpoint = f"{profile.endpoint}/inference_cross_lingual"
        else:
            fields = {"tts_text": text, "spk_id": profile.speaker}
            if profile.instruct:
                fields["instruct_text"] = profile.instruct
            data = urllib.parse.urlencode(fields).encode("utf-8")
            content_type = "application/x-www-form-urlencoded"
            endpoint = (
                f"{profile.endpoint}/inference_instruct"
                if profile.instruct else f"{profile.endpoint}/inference_sft"
            )
        body = self._fetch(
            endpoint,
            method="POST",
            timeout_s=profile.timeout_s,
            headers={"Content-Type": content_type},
            data=data,
            raw_response=True,
        )
        output.write_bytes(body)

    def _openai(self, text: str, output: Path, profile: VoiceProfile) -> None:
        if not profile.endpoint:
            raise VoiceError("not_configured", "OpenAI-compatible TTS endpoint is empty")
        endpoint = profile.endpoint
        if endpoint.endswith("/audio/speech"):
            pass
        elif endpoint.endswith("/v1"):
            endpoint = f"{endpoint}/audio/speech"
        else:
            endpoint = f"{endpoint}/v1/audio/speech"
        payload = json.dumps({
            "model": profile.model or "tts-1",
            "voice": profile.voice or "alloy",
            "input": text,
            "response_format": "wav",
            "speed": 1.0 + _clamp(profile.rate, -100, 100, 0) / 200.0,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if profile.api_key:
            headers["Authorization"] = f"Bearer {profile.api_key}"
        output.write_bytes(self._fetch(
            endpoint, method="POST", timeout_s=profile.timeout_s,
            headers=headers, data=payload, raw_response=True,
        ))

    def _http(self, text: str, output: Path, profile: VoiceProfile) -> None:
        template = str(profile.extra.get("url_template") or "")
        if "{text}" not in template:
            raise VoiceError("not_configured", "http TTS template needs {text}")
        url = template.replace("{text}", urllib.parse.quote(text, safe=""))
        output.write_bytes(self._fetch(
            url, method="GET", timeout_s=profile.timeout_s,
            headers={"Authorization": f"Bearer {profile.api_key}"} if profile.api_key else {},
            raw_response=True,
        ))

    def _fetch(
        self,
        url: str,
        *,
        method: str,
        timeout_s: int,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        raw_response: bool = False,
    ) -> bytes:
        request = urllib.request.Request(
            url, method=method, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                body = response.read(24 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            detail = exc.read(500).decode("utf-8", "replace")
            raise VoiceError(
                "provider_http_error", f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise VoiceError("provider_unreachable", str(exc)) from exc
        if len(body) > 24 * 1024 * 1024:
            raise VoiceError("audio_too_large", "TTS response exceeds 24 MB")
        return body

    def _local_reference(self, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (self.root / path).resolve()
        if not path.is_file():
            raise VoiceError("reference_missing", f"reference audio not found: {path}")
        return path

    def _has_local_reference(self, value: str) -> bool:
        if not str(value or "").strip():
            return False
        try:
            self._local_reference(value)
        except VoiceError:
            return False
        return True

    @staticmethod
    def _multipart(
        fields: dict[str, str], *, file_field: str, file_path: Path
    ) -> tuple[bytes, str]:
        boundary = f"----aiji{uuid.uuid4().hex}"
        chunks: list[bytes] = []
        for name, value in fields.items():
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ])
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{file_path.name}"\r\nContent-Type: {mime}\r\n\r\n'
            ).encode(),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"

    def _to_wav(self, source: Path, output: Path) -> None:
        ffmpeg = shutil.which(self.ffmpeg) or self.ffmpeg
        if not Path(ffmpeg).is_file() and not shutil.which(ffmpeg):
            raise VoiceError("ffmpeg_missing", "ffmpeg is required")
        result = subprocess.run(
            [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(source), "-ar", "24000", "-ac", "1",
                "-c:a", "pcm_s16le", str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0 or not output.exists():
            raise VoiceError(
                "audio_convert_failed",
                (result.stderr or "ffmpeg failed").strip()[-500:],
            )

    def _postprocess(
        self,
        source: Path,
        tmp: Path,
        profile: VoiceProfile,
        *,
        mode: str = "speech",
    ) -> Path:
        endpoint = str(profile.postprocess.get("url") or "").strip()
        if endpoint:
            return self._postprocess_http(
                source, tmp, profile, endpoint, mode=mode)
        raw = profile.postprocess.get("command")
        if not isinstance(raw, list) or not raw:
            return source
        output = tmp / "postprocessed.wav"
        values = {
            "input": str(source),
            "output": str(output),
            "pitch": str(profile.pitch),
            "profile": profile.id,
        }
        command: list[str] = []
        for part in raw:
            value = str(part)
            for key, replacement in values.items():
                value = value.replace("{" + key + "}", replacement)
            command.append(value)
        try:
            result = subprocess.run(
                command, check=False, capture_output=True, text=True,
                timeout=profile.timeout_s)
        except Exception as exc:
            raise VoiceError("postprocess_failed", str(exc)) from exc
        if result.returncode != 0 or not output.exists():
            raise VoiceError(
                "postprocess_failed",
                (result.stderr or result.stdout or "postprocess failed").strip()[-500:],
            )
        return output

    def _postprocess_http(
        self,
        source: Path,
        tmp: Path,
        profile: VoiceProfile,
        endpoint: str,
        *,
        mode: str = "speech",
    ) -> Path:
        reference = str(profile.postprocess.get("reference") or "").strip()
        if not reference:
            raise VoiceError(
                "reference_missing", "postprocess.reference is required")
        ref_path = self._local_reference(reference)
        output = tmp / "postprocessed-http.wav"
        token = str(
            profile.postprocess.get("token")
            or os.environ.get("AIJI_SEED_VC_TOKEN", "")
        ).strip()
        # 纯字节协议：source/target 都由本进程读好再传，不要求引擎进程能访问
        # 我们的临时目录（跨完整性级别/跨用户时 stat 会被 WinError 5 拒绝，
        # 那样会静默退回 edge 音色，表现就是"定制语音不起作用"）。
        try:
            source_bytes = source.read_bytes()
        except OSError as exc:
            raise VoiceError(
                "postprocess_failed", f"cannot read source audio: {exc}") from exc
        try:
            reference_bytes = _reference_bytes(ref_path)
        except OSError as exc:
            raise VoiceError(
                "reference_missing", f"cannot read reference audio: {exc}") from exc
        payload = json.dumps({
            "source_b64": base64.b64encode(source_bytes).decode("ascii"),
            "target_b64": base64.b64encode(reference_bytes).decode("ascii"),
            "output": str(output),
            "diffusion_steps": int(_clamp(
                profile.postprocess.get(
                    "song_diffusion_steps"
                    if mode == "song" else "diffusion_steps"
                ),
                1, 100, 30 if mode == "song" else 10,
            )),
            "inference_cfg_rate": _clamp(
                profile.postprocess.get("inference_cfg_rate"), 0.0, 1.0, 0.7),
            "length_adjust": _clamp(
                profile.postprocess.get("length_adjust"), 0.5, 2.0, 1.0),
            "f0_condition": _bool(
                profile.postprocess.get("f0_condition"), False),
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = self._fetch(
            endpoint,
            method="POST",
            timeout_s=max(profile.timeout_s, 600),
            headers=headers,
            data=payload,
            raw_response=True,
        )
        if len(data) < 100:
            raise VoiceError("postprocess_failed", "converter returned no audio")
        output.write_bytes(data)
        return output


__all__ = [
    "SynthesisResult",
    "VoiceError",
    "VoiceProfile",
    "VoiceRegistry",
]
