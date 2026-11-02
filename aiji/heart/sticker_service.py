"""Local sticker pack with explicit, validated online updates."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable


DEFAULT_STICKER_FEED = (
    "https://cdn.jsdelivr.net/gh/cheems135/dsh-qq-bridge@main/"
    "assets/preset/manifest.json"
)


class StickerError(RuntimeError):
    def __init__(self, code: str, message: str = "", *, status: int = 400) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.status = int(status)


_MOODS = {
    "开心": "happy",
    "高兴": "happy",
    "笑": "happy",
    "生气": "angry",
    "愤怒": "angry",
    "得意": "proud",
    "骄傲": "proud",
    "累": "tired",
    "困": "tired",
    "无语": "speechless",
    "尴尬": "speechless",
    "疑惑": "question",
    "问号": "question",
    "吃": "eating",
    "饿": "eating",
    "好奇": "curious",
    "害羞": "shy",
    "互动": "playful",
    "难过": "sad",
    "惊讶": "surprised",
}
_IMAGE_SUFFIXES = frozenset((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))


def _mood(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "misc"
    for key, mood in _MOODS.items():
        if key in raw:
            return mood
    return re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-")[:40] or "misc"


def _safe_filename(value: str) -> str:
    name = Path(str(value or "")).name.strip()
    name = re.sub(r"[^\w.-]+", "-", name, flags=re.UNICODE).strip(".-")
    if not name or Path(name).suffix.lower() not in _IMAGE_SUFFIXES:
        raise StickerError("invalid_sticker_filename", f"invalid sticker file: {value}")
    return name[:160]


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_bytes(data)
    os.replace(temp, path)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_bytes(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
    )


def _image_suffix(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"\xff\xd8\xff",)):
        return ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith(b"BM"):
        return ".bmp"
    raise StickerError("invalid_image", "remote file is not a supported image")


class StickerService:
    def __init__(
        self,
        directory: str | Path,
        *,
        feed_url: str = DEFAULT_STICKER_FEED,
        fetch: Callable[[str, int], bytes] | None = None,
        resolver: Callable[[str], list[str]] | None = None,
        timeout_s: float = 30.0,
        max_bytes: int = 8 * 1024 * 1024,
        max_items: int = 20,
    ) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.feed_url = str(feed_url or "").strip()
        self._fetch = fetch
        self._resolver = resolver
        self.timeout_s = max(1.0, float(timeout_s))
        self.max_bytes = max(64 * 1024, min(32 * 1024 * 1024, int(max_bytes)))
        self.max_items = max(1, min(500, int(max_items)))
        self.state_path = self.directory / ".update_state.json"
        self._lock = threading.RLock()

    def snapshot(self) -> dict[str, Any]:
        manifest = self._read_manifest()
        stickers = manifest.get("stickers") if isinstance(manifest, dict) else []
        items = self._valid_entries(stickers if isinstance(stickers, list) else [])
        return {
            "directory": str(self.directory),
            "manifest": str(self.directory / "manifest.json"),
            "source": "online_feed+local_manifest",
            "remote_fetch": True,
            "auto_update_capable": bool(self.feed_url),
            "feed_url": self.feed_url,
            "count": len(items),
            "name": str(manifest.get("name") or "") if isinstance(manifest, dict) else "",
            "moods": sorted({item["mood"] for item in items if item.get("mood")}),
            "last_update": self._read_state(),
        }

    def update(self) -> dict[str, Any]:
        with self._lock:
            return self._update_locked()

    def _update_locked(self) -> dict[str, Any]:
        if not self.feed_url:
            raise StickerError(
                "sticker_feed_not_configured",
                "set AIJI_STICKER_FEED_URL",
                status=503,
            )
        started = time.time()
        try:
            feed_raw = self._get(self.feed_url)
            feed = json.loads(feed_raw.decode("utf-8-sig"))
            raw_items = feed if isinstance(feed, list) else (
                feed.get("stickers") if isinstance(feed, dict) else None
            )
            if not isinstance(raw_items, list):
                raise StickerError("invalid_feed", "sticker feed has no stickers list")
            current = self._read_manifest()
            local_items = current.get("stickers") if isinstance(current, dict) else []
            local_items = local_items if isinstance(local_items, list) else []
            local_by_file = {
                str(item.get("file") or ""): item
                for item in local_items
                if isinstance(item, dict) and item.get("file")
            }
            remote: list[dict[str, Any]] = []
            added = updated = 0
            failed = 0
            failures: list[dict[str, str]] = []
            base_url = self.feed_url
            for raw in raw_items[:self.max_items]:
                if not isinstance(raw, dict):
                    continue
                filename = _safe_filename(str(raw.get("file") or ""))
                image_url = str(raw.get("url") or "").strip()
                if not image_url:
                    image_url = urllib.parse.urljoin(
                        base_url, urllib.parse.quote(filename))
                try:
                    data = self._get(
                        image_url,
                        timeout_s=min(self.timeout_s, 10.0),
                    )
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    failures.append({
                        "file": filename,
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    })
                    if failed >= 8:
                        break
                    continue
                detected = _image_suffix(data)
                if Path(filename).suffix.lower() not in _IMAGE_SUFFIXES:
                    filename = f"{Path(filename).stem}{detected}"
                digest = hashlib.sha256(data).hexdigest()
                target = self.directory / filename
                changed = not target.is_file() or hashlib.sha256(
                    target.read_bytes()).hexdigest() != digest
                if changed:
                    _atomic_bytes(target, data)
                    if filename in local_by_file:
                        updated += 1
                    else:
                        added += 1
                remote.append({
                    "file": filename,
                    "mood": _mood(raw.get("group") or raw.get("mood")),
                    "description": str(
                        raw.get("name") or raw.get("description") or ""
                    ).strip()[:160],
                    "source": self.feed_url,
                    "url": image_url,
                    "sha256": digest,
                })
            remote_files = {str(item["file"]) for item in remote}
            preserved = [
                item for item in local_items
                if isinstance(item, dict)
                and str(item.get("file") or "") not in remote_files
                and (self.directory / str(item.get("file") or "")).is_file()
            ]
            merged = remote + preserved
            manifest_name = (
                str(feed.get("name") or "").strip()
                if isinstance(feed, dict) else ""
            ) or "aiji-online"
            _atomic_json(self.directory / "manifest.json", {
                "version": 2,
                "name": manifest_name,
                "updated_at": time.time(),
                "feed": self.feed_url,
                "stickers": merged,
            })
            result = {
                "ok": True,
                "status": "ok",
                "added": added,
                "updated": updated,
                "failed": failed,
                "partial": bool(failed),
                "failures": failures[:8],
                "preserved": len(preserved),
                "total": len(merged),
                "feed_url": self.feed_url,
                "took_ms": round((time.time() - started) * 1000, 1),
            }
            _atomic_json(self.state_path, {**result, "finished_at": time.time()})
            return result
        except StickerError as exc:
            self._write_failed_state(exc)
            raise
        except Exception as exc:  # noqa: BLE001
            error = StickerError("sticker_update_failed", str(exc), status=502)
            self._write_failed_state(error)
            raise error from exc

    def import_image(
        self,
        url: str,
        *,
        mood: str = "",
        description: str = "",
        source: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            return self._import_image_locked(
                url, mood=mood, description=description, source=source)

    def _import_image_locked(
        self,
        url: str,
        *,
        mood: str = "",
        description: str = "",
        source: str = "",
    ) -> dict[str, Any]:
        image_url = str(url or "").strip()
        data = self._get(image_url)
        detected = _image_suffix(data)
        raw_name = Path(urllib.parse.urlparse(image_url).path).name
        stem = Path(raw_name).stem
        if not stem or stem in (".", ".."):
            stem = hashlib.sha256(data).hexdigest()[:16]
        digest = hashlib.sha256(data).hexdigest()
        filename = _safe_filename(f"{stem}{detected}")
        target = self.directory / filename
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            filename = _safe_filename(f"{stem[:100]}-{digest[:16]}{detected}")
            target = self.directory / filename
        _atomic_bytes(target, data)
        current = self._read_manifest()
        stickers = current.get("stickers") if isinstance(current, dict) else []
        stickers = stickers if isinstance(stickers, list) else []
        entry = {
            "file": filename,
            "mood": _mood(mood),
            "description": str(description or "").strip()[:160],
            "source": (
                "group_chat"
                if str(source or "").strip().lower() == "group_chat"
                else "manual_import"
            ),
            "url": image_url,
            "sha256": digest,
        }
        merged = [entry] + [
            item for item in stickers
            if isinstance(item, dict) and item.get("file") != filename
        ]
        _atomic_json(self.directory / "manifest.json", {
            "version": 2,
            "name": str(current.get("name") or "aiji-local"),
            "updated_at": time.time(),
            "stickers": merged,
        })
        return {
            "ok": True,
            "file": filename,
            "mood": entry["mood"],
            "sha256": digest,
            "total": len(merged),
        }

    def _write_failed_state(self, exc: StickerError) -> None:
        try:
            _atomic_json(self.state_path, {
                "ok": False,
                "status": "failed",
                "error_code": exc.code,
                "error": exc.message,
                "feed_url": self.feed_url,
                "finished_at": time.time(),
            })
        except OSError:
            pass

    def _get(self, url: str, *, timeout_s: float | None = None) -> bytes:
        self._validate_public_url(url)
        if self._fetch is not None:
            data = self._fetch(url, self.max_bytes)
        else:
            data = self._default_fetch(
                url,
                self.max_bytes,
                timeout_s=timeout_s or self.timeout_s,
            )
        if not isinstance(data, bytes):
            raise StickerError("invalid_download", "downloader did not return bytes")
        if len(data) > self.max_bytes:
            raise StickerError("download_too_large", "download exceeds size limit")
        return data

    def _default_fetch(
        self, url: str, max_bytes: int, *, timeout_s: float | None = None,
    ) -> bytes:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AIJiYi-StickerUpdater/1.0",
                "Accept": "application/json,image/*;q=0.9,*/*;q=0.1",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout_s or self.timeout_s,
            ) as response:
                self._validate_public_url(response.geturl())
                length = int(response.headers.get("Content-Length") or 0)
                if length > max_bytes:
                    raise StickerError("download_too_large", "download exceeds size limit")
                data = response.read(max_bytes + 1)
        except StickerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StickerError("download_failed", str(exc), status=502) from exc
        if len(data) > max_bytes:
            raise StickerError("download_too_large", "download exceeds size limit")
        return data

    def _validate_public_url(self, url: str) -> None:
        parsed = urllib.parse.urlparse(str(url or ""))
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise StickerError("invalid_url", "http(s) URL required")
        if parsed.username or parsed.password:
            raise StickerError("invalid_url", "URL credentials are not allowed")
        try:
            addresses = (self._resolver or self._resolve_host)(parsed.hostname)
        except OSError as exc:
            raise StickerError("dns_failed", str(exc), status=502) from exc
        if not addresses:
            raise StickerError("dns_failed", "host did not resolve")
        for raw in addresses:
            try:
                ip = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if (
                ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified
            ):
                raise StickerError("private_url", "private network URL is not allowed")

    @staticmethod
    def _resolve_host(host: str) -> list[str]:
        return sorted({
            str(item[4][0])
            for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        })

    def _read_manifest(self) -> dict[str, Any]:
        try:
            payload = json.loads((self.directory / "manifest.json").read_text("utf-8-sig"))
        except (OSError, ValueError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _read_state(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.state_path.read_text("utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _valid_entries(self, raw_items: list[Any]) -> list[dict[str, Any]]:
        items = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            name = Path(str(raw.get("file") or "")).name
            path = self.directory / name
            if (
                name and path.suffix.lower() in _IMAGE_SUFFIXES
                and path.is_file()
            ):
                items.append({
                    "file": name,
                    "mood": str(raw.get("mood") or ""),
                })
        return items


class StickerUpdateLoop:
    def __init__(
        self,
        service: Any,
        *,
        initial_delay_s: float = 120.0,
        interval_s: float = 24 * 3600.0,
    ) -> None:
        self.service = service
        self.initial_delay_s = max(0.0, float(initial_delay_s))
        self.interval_s = max(0.01, float(interval_s))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.runs = 0
        self.failures = 0
        self.last_error = ""
        self.last_update: dict[str, Any] | None = None
        self.started_at = 0.0
        self.stopped_at = 0.0

    def start(self) -> "StickerUpdateLoop":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self.started_at = time.time()
        self.stopped_at = 0.0
        self._thread = threading.Thread(
            target=self._run,
            name="aiji-sticker-updater",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self, *, timeout: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.1, float(timeout)))
        if thread is not None and not thread.is_alive():
            self.stopped_at = time.time()

    def _run(self) -> None:
        if self._stop.wait(self.initial_delay_s):
            return
        while not self._stop.is_set():
            try:
                result = self.service.update()
                with self._lock:
                    self.last_update = dict(result)
                    self.last_error = ""
                    self.runs += 1
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self.failures += 1
                    self.last_error = f"{type(exc).__name__}: {exc}"[:500]
                    self.last_update = {
                        "ok": False,
                        "status": "failed",
                        "error": str(exc)[:500],
                    }
            if self._stop.wait(self.interval_s):
                break

    def snapshot(self) -> dict[str, Any]:
        service_snapshot = (
            self.service.snapshot()
            if hasattr(self.service, "snapshot") else {}
        )
        with self._lock:
            return {
                "running": bool(
                    self._thread is not None
                    and self._thread.is_alive()
                    and not self._stop.is_set()
                ),
                "initial_delay_s": self.initial_delay_s,
                "interval_s": self.interval_s,
                "runs": self.runs,
                "failures": self.failures,
                "last_error": self.last_error,
                "last_update": self.last_update,
                "started_at": self.started_at,
                "stopped_at": self.stopped_at,
                "service": service_snapshot,
            }


__all__ = [
    "DEFAULT_STICKER_FEED",
    "StickerError",
    "StickerService",
    "StickerUpdateLoop",
]
