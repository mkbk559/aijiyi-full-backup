"""Download user-supplied media URLs and normalize audio to WAV."""

from __future__ import annotations

import shutil
import subprocess
import urllib.request
from typing import Any, Callable

from aiji.throat.net_guard import UrlGuardError, validate_public_http_url
from aiji import net


def media_bytes_from_url(
    url: str,
    *,
    fetch: Callable[[str, int], bytes] | None = None,
    resolver: Callable[[str], list[str]] | None = None,
    max_bytes: int = 24 * 1024 * 1024,
) -> bytes:
    validate_public_http_url(url, resolver=resolver)
    data = (fetch or _default_fetch)(url, max_bytes)
    if not isinstance(data, bytes):
        raise ValueError("media downloader did not return bytes")
    if len(data) > max_bytes:
        raise UrlGuardError("media_too_large", "media exceeds size limit")
    return data


def audio_bytes_from_url(
    url: str,
    *,
    fetch: Callable[[str, int], bytes] | None = None,
    convert: Callable[[bytes], bytes] | None = None,
    resolver: Callable[[str], list[str]] | None = None,
    max_bytes: int = 24 * 1024 * 1024,
) -> bytes:
    data = media_bytes_from_url(
        url,
        fetch=fetch,
        resolver=resolver,
        max_bytes=max_bytes,
    )
    if data.startswith(b"RIFF"):
        return data
    converted = (convert or _ffmpeg_to_wav)(data)
    if not converted.startswith(b"RIFF"):
        raise ValueError("audio converter returned non-WAV data")
    return converted


def transcribe_item_audio(
    item: Any,
    *,
    transcribe: Callable[[bytes], tuple[str, float]],
    fetch: Callable[[str, int], bytes] | None = None,
    convert: Callable[[bytes], bytes] | None = None,
    resolver: Callable[[str], list[str]] | None = None,
) -> tuple[str, float]:
    extra = getattr(item, "extra", {}) or {}
    data = extra.get("bytes")
    if not data:
        values = extra.get("bytes_list")
        if isinstance(values, list) and values:
            data = values[0]
    if data:
        raw = bytes(data)
        if not raw.startswith(b"RIFF"):
            raw = (convert or _ffmpeg_to_wav)(raw)
        return transcribe(raw)
    url = str(getattr(item, "url", "") or "")
    if url:
        return transcribe(audio_bytes_from_url(
            url, fetch=fetch, convert=convert, resolver=resolver))
    return "", 0.0


def _default_fetch(url: str, max_bytes: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 AIJiYi-Audio/1.0",
            "Accept": "audio/*,*/*;q=0.5",
        },
    )
    try:
        with net.urlopen(request, timeout=25) as response:
            length = int(response.headers.get("Content-Length") or 0)
            if length > max_bytes:
                raise UrlGuardError("audio_too_large", "audio exceeds size limit")
            data = response.read(max_bytes + 1)
    except UrlGuardError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise UrlGuardError("audio_download_failed", str(exc)) from exc
    if len(data) > max_bytes:
        raise UrlGuardError("audio_too_large", "audio exceeds size limit")
    return data


def _ffmpeg_to_wav(data: bytes) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for audio conversion")
    result = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0", "-ar", "16000", "-ac", "1",
            "-f", "wav", "pipe:1",
        ],
        input=data,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0 or not result.stdout.startswith(b"RIFF"):
        detail = (result.stderr or b"ffmpeg conversion failed").decode(
            "utf-8", "replace")[-500:]
        raise RuntimeError(detail)
    return result.stdout


__all__ = ["audio_bytes_from_url", "media_bytes_from_url", "transcribe_item_audio"]
