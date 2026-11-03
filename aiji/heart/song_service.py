"""Asynchronous QQ song-cover pipeline.

The service accepts a public song URL/id/query or a local file under the music
directory, separates vocals/instrumental, applies a configured RVC converter,
mixes the result and exposes a completed audio file for the QQ plugin.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from aiji.throat.net_guard import UrlGuardError, validate_public_http_url
from aiji.heart.voice_service import VoiceError, VoiceRegistry
from aiji import net

logger = logging.getLogger(__name__)


def _default_separator() -> str:
    """人声分离可执行文件的位置。

    2026-11-03：这里原来写死 `voice-engine/.venv/Scripts/audio-separator.exe`。
    那个 venv 是 CPU torch 的（为换声互通搭的），已被清掉；分离改用独立的
    `voice-engine/sep-venv`（只装 audio-separator，874 MB），换声则走
    `voice-engine/rvc/.venv`（CUDA）。两个环境分开，互不牵制。
    """
    for candidate in (
        Path("voice-engine") / "sep-venv" / "Scripts" / "audio-separator.exe",
        Path("voice-engine") / ".venv" / "Scripts" / "audio-separator.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return str(Path("voice-engine") / "sep-venv" / "Scripts" / "audio-separator.exe")


class SongError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass(slots=True)
class SongJob:
    id: str
    source: str
    profile_id: str
    pitch: int
    max_seconds: int
    status: str = "queued"
    stage: str = "排队"
    title: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    ended_at: float = 0.0
    output: str = ""
    work_dir: str = ""
    error_code: str = ""
    error: str = ""

    def public(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["audio_url"] = (
            f"/v1/songs/{self.id}/audio" if self.status == "done" else "")
        return payload


class SongService:
    def __init__(
        self,
        voices: VoiceRegistry,
        *,
        data_root: str | Path | None = None,
        separator: str | None = None,
        model_dir: str | Path | None = None,
        model_filename: str | None = None,
        ffmpeg: str | None = None,
        ffprobe: str | None = None,
    ) -> None:
        root = Path(data_root or os.environ.get("AIJI_DATA_DIR", "data")).resolve()
        self.voices = voices
        self.root = (root / "songs").resolve()
        self.music_root = (root / "music").resolve()
        self.cache_root = self.root / "cache"
        self.work_root = self.root / "work"
        self.jobs_root = self.root / "jobs"
        for directory in (
            self.root, self.music_root, self.cache_root,
            self.work_root, self.jobs_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.separator = separator or os.environ.get(
            "AIJI_AUDIO_SEPARATOR", _default_separator())
        self.model_dir = Path(model_dir or os.environ.get(
            "AIJI_SEPARATOR_MODEL_DIR", "voice-engine/models")).resolve()
        self.model_filename = model_filename or os.environ.get(
            "AIJI_SEPARATOR_MODEL", "UVR-MDX-NET-Voc_FT.onnx")
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe = ffprobe or shutil.which("ffprobe") or "ffprobe"
        self.max_download_bytes = max(
            8, min(120, int(os.environ.get("AIJI_SONG_MAX_MB", "60")))) * 1024 * 1024
        self._jobs: dict[str, SongJob] = {}
        self._queue: deque[str] = deque()
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._load_jobs()

    def _load_jobs(self) -> None:
        for path in self.jobs_root.glob("*.json"):
            try:
                raw = json.loads(path.read_text("utf-8"))
                if not isinstance(raw, dict):
                    continue
                known = {
                    key: raw[key]
                    for key in SongJob.__dataclass_fields__
                    if key in raw
                }
                job = SongJob(**known)
                if job.id != path.stem:
                    continue
                if job.status in ("queued", "running"):
                    job.status = "failed"
                    job.stage = "服务重启中止"
                    job.error_code = "interrupted"
                    job.error = "song job was interrupted by a service restart"
                    self._persist(job)
                if job.status == "done" and not Path(job.output).is_file():
                    job.status = "failed"
                    job.stage = "成品已清理"
                    job.error_code = "output_missing"
                    job.error = "completed song output is no longer on disk"
                    self._persist(job)
                self._jobs[job.id] = job
            except (OSError, ValueError, TypeError):
                continue

    def _persist(self, job: SongJob) -> None:
        """落一份任务快照。目录被清掉（临时目录回收/人工清理）要能自愈。

        之前这里直接 `temp.write_text()`：`jobs_root` 一旦不存在就抛
        `FileNotFoundError`，异常从 `_pump` 的 `finally` 抛出，整条歌曲队列线程
        静默死掉、任务丢失（2026-11-03 全量日志里真实抓到过）。
        """
        target = self.jobs_root / f"{job.id}.json"
        temp = target.with_suffix(".json.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(
                json.dumps(asdict(job), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, target)
        except OSError as exc:      # 落盘失败不能拖垮队列：任务还在内存里
            self._persist_errors = int(getattr(self, "_persist_errors", 0)) + 1
            logger.warning("song job 快照落盘失败（%s）：%s", target, exc)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(), key=lambda job: job.created_at, reverse=True)
            return {
                "ready": self.ready(),
                "separator": str(self.separator),
                "model": self.model_filename,
                "queue": len(self._queue),
                "jobs": [job.public() for job in jobs[:30]],
            }

    def ready(self) -> bool:
        sep = Path(self.separator)
        return (sep.is_file() or bool(shutil.which(self.separator))) and (
            self.model_dir / self.model_filename).is_file()

    def lookup(self, query: str) -> dict[str, str]:
        """Resolve a public song without starting a cover job."""
        value = str(query or "").strip()
        if not value:
            raise SongError("source_required", "song query is required")
        if re.fullmatch(r"\d{3,20}", value):
            song_id, title = value, f"网易云 {value}"
        else:
            song_id, title = self._search_netease(value)
        return {
            "id": song_id,
            "title": title,
            "url": f"https://music.163.com/song?id={song_id}",
            "source": "netease",
        }

    def submit(
        self,
        source: str,
        *,
        profile_id: str = "",
        pitch: int = 0,
        max_seconds: int = 60,
    ) -> SongJob:
        value = str(source or "").strip()
        if not value:
            raise SongError("source_required", "song url, id, query or local file is required")
        try:
            profile = self.voices.get(profile_id)
        except VoiceError as exc:
            raise SongError(exc.code, exc.message) from exc
        if not (
            profile.postprocess.get("command")
            or profile.postprocess.get("url")
        ):
            raise SongError(
                "voice_converter_not_configured",
                f"voice profile {profile.id} has no RVC converter command",
            )
        with self._lock:
            running = sum(
                1 for job in self._jobs.values()
                if job.status in ("queued", "running"))
            if running >= 5:
                raise SongError("queue_full", "too many song jobs")
            job = SongJob(
                id=uuid.uuid4().hex[:16],
                source=value,
                profile_id=profile.id,
                pitch=max(-24, min(24, int(pitch))),
                max_seconds=max(20, min(180, int(max_seconds))),
            )
            self._jobs[job.id] = job
            self._queue.append(job.id)
            self._persist(job)
            self._ensure_worker()
            return job

    def get(self, job_id: str) -> SongJob:
        with self._lock:
            job = self._jobs.get(str(job_id or ""))
            if job is None:
                try:
                    raw = json.loads(
                        (self.jobs_root / f"{job_id}.json").read_text("utf-8"))
                    if not isinstance(raw, dict):
                        raise TypeError("job metadata is not an object")
                    known = {
                        key: raw[key]
                        for key in SongJob.__dataclass_fields__
                        if key in raw
                    }
                    job = SongJob(**known)
                    self._jobs[job.id] = job
                except (OSError, ValueError, TypeError):
                    job = None
            if job is None:
                raise SongError("job_not_found", "song job not found")
            return job

    def audio_path(self, job_id: str) -> Path:
        job = self.get(job_id)
        if job.status != "done" or not job.output:
            raise SongError("job_not_ready", f"song job is {job.status}")
        path = Path(job.output).resolve()
        if self.root not in path.parents or not path.is_file():
            raise SongError("output_missing", "song output is missing")
        return path

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._pump, name="aiji-song", daemon=True)
        self._worker.start()

    def _pump(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._worker = None
                    return
                job_id = self._queue.popleft()
                job = self._jobs[job_id]
                job.status = "running"
                job.started_at = time.time()
                job.stage = "准备歌曲"
            try:
                self._process(job)
            except SongError as exc:
                self._fail(job, exc.code, exc.message)
            except Exception as exc:  # noqa: BLE001
                self._fail(job, "internal_error", f"{type(exc).__name__}: {exc}")
            finally:
                with self._lock:
                    job.ended_at = time.time()
                    self._persist(job)

    @staticmethod
    def _fail(job: SongJob, code: str, message: str) -> None:
        job.status = "failed"
        job.stage = "失败"
        job.error_code = code
        job.error = str(message or code)[:1200]

    def _process(self, job: SongJob) -> None:
        work = self.work_root / job.id
        work.mkdir(parents=True, exist_ok=True)
        job.work_dir = str(work)
        job.stage = "解析歌曲"
        source, job.title = self._resolve_source(job.source, work)
        duration = self._probe_duration(source)
        if duration <= 0:
            raise SongError("invalid_audio", "downloaded/local song is not readable audio")
        start, length = self._clip_window(duration, job.max_seconds)
        job.stage = "截取演唱片段"
        clip = work / "clip.wav"
        self._run([
            self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
            "-i", str(source), "-ar", "44100", "-ac", "2",
            "-c:a", "pcm_s16le", str(clip),
        ], timeout=180)
        job.stage = "分离人声和伴奏"
        vocals, instrumental = self._separate(clip, work / "separated")
        job.stage = "换成目标声线"
        converted = work / "vocals_converted.wav"
        try:
            self.voices.convert_audio(
                vocals, converted, profile_id=job.profile_id,
                pitch=job.pitch, mode="song")
        except VoiceError as exc:
            raise SongError(exc.code, exc.message) from exc
        job.stage = "混音"
        mixed = work / "mixed.wav"
        self._mix(converted, instrumental, mixed)
        job.stage = "生成语音条"
        output = self.cache_root / f"{job.id}.mp3"
        fade_start = max(0.0, length - 3.0)
        self._run([
            self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(mixed), "-af",
            f"afade=t=in:d=1.2,afade=t=out:st={fade_start:.3f}:d=2.5",
            "-b:a", "128k", str(output),
        ], timeout=180)
        if not output.is_file() or output.stat().st_size < 1000:
            raise SongError("empty_output", "song mix produced no audio")
        self._prune_cache(40)
        job.output = str(output)
        job.status = "done"
        job.stage = "完成"

    def _resolve_source(self, value: str, work: Path) -> tuple[Path, str]:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme in ("http", "https"):
            title = Path(parsed.path).stem or "网络歌曲"
            return self._download(value, work / "song.source"), title
        local = Path(value).expanduser()
        if local.is_file():
            resolved = local.resolve()
            if self.music_root != resolved and self.music_root not in resolved.parents:
                raise SongError(
                    "local_path_forbidden",
                    f"local songs must be under {self.music_root}",
                )
            return resolved, resolved.stem
        song_id = ""
        title = value
        if re.fullmatch(r"\d{3,20}", value):
            song_id = value
            title = f"网易云 {song_id}"
        else:
            song_id, title = self._search_netease(value)
        url = (
            "https://music.163.com/song/media/outer/url?"
            + urllib.parse.urlencode({"id": song_id})
        )
        return (
            self._download(
                url, work / "song.mp3", referer="https://music.163.com/"),
            title,
        )

    def _search_netease(self, query: str) -> tuple[str, str]:
        endpoint = "https://music.163.com/api/search/get/web?" + urllib.parse.urlencode({
            "s": query, "type": 1, "limit": 5, "offset": 0,
        })
        body = self._http_bytes(
            endpoint,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://music.163.com/",
            },
            timeout=20,
        )
        try:
            payload = json.loads(body.decode("utf-8", "replace"))
            songs = ((payload.get("result") or {}).get("songs") or [])
            for item in songs:
                if isinstance(item, dict) and str(item.get("id") or "").isdigit():
                    name = str(item.get("name") or query).strip()
                    artists = "、".join(
                        str(artist.get("name") or "").strip()
                        for artist in (item.get("artists") or [])
                        if isinstance(artist, dict)
                        and str(artist.get("name") or "").strip()
                    )
                    return str(item["id"]), (
                        f"{name} - {artists}" if artists else name)
        except (ValueError, TypeError, AttributeError):
            pass
        raise SongError("song_not_found", f"no playable song found for: {query}")

    def _download(self, url: str, target: Path, *, referer: str = "") -> Path:
        try:
            validate_public_http_url(url)
        except UrlGuardError as exc:
            raise SongError(exc.code, exc.message) from exc
        headers = {"User-Agent": "Mozilla/5.0"}
        if referer:
            headers["Referer"] = referer
        body = self._http_bytes(url, headers=headers, timeout=120)
        if len(body) < 4096:
            raise SongError("download_too_small", "song download returned no usable audio")
        if body[:1] == b"<":
            raise SongError(
                "song_unavailable",
                "the public song URL returned a web page (copyright/login restriction)",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return target

    def _http_bytes(
        self, url: str, *, headers: dict[str, str], timeout: int
    ) -> bytes:
        request = urllib.request.Request(url, headers=headers)
        try:
            with net.urlopen(request, timeout=timeout) as response:
                chunks: list[bytes] = []
                size = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_download_bytes:
                        raise SongError("download_too_large", "song exceeds download limit")
                    chunks.append(chunk)
                return b"".join(chunks)
        except SongError:
            raise
        except urllib.error.HTTPError as exc:
            raise SongError("download_failed", f"HTTP {exc.code}") from exc
        except Exception as exc:
            raise SongError("download_failed", str(exc)) from exc

    def _probe_duration(self, path: Path) -> float:
        try:
            result = subprocess.run(
                [
                    self.ffprobe, "-v", "error", "-show_entries",
                    "format=duration", "-of",
                    "default=noprint_wrappers=1:nokey=1", str(path),
                ],
                check=False, capture_output=True, text=True, timeout=30,
            )
            return float(result.stdout.strip())
        except (TypeError, ValueError, OSError, subprocess.SubprocessError):
            return 0.0

    @staticmethod
    def _clip_window(duration: float, max_seconds: int) -> tuple[float, float]:
        limit = max(20, min(180, int(max_seconds)))
        if duration <= limit:
            return 0.0, duration
        start = min(duration * 0.30, duration - limit)
        return max(0.0, start), float(limit)

    def _separate(self, source: Path, output: Path) -> tuple[Path, Path]:
        output.mkdir(parents=True, exist_ok=True)
        sep = self.separator
        if not Path(sep).is_file() and shutil.which(sep):
            sep = shutil.which(sep) or sep
        self._run([
            sep, str(source),
            "--model_filename", self.model_filename,
            "--model_file_dir", str(self.model_dir),
            "--output_dir", str(output),
            "--output_format", "WAV",
        ], timeout=20 * 60, env={
            "AUDIO_SEPARATOR_MODEL_DIR": str(self.model_dir),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        })
        files = [path for path in output.rglob("*.wav") if path.is_file()]
        vocals = next(
            (path for path in files if "(vocals)" in path.name.lower()), None)
        instrumental = next((
            path for path in files
            if "(instrumental)" in path.name.lower()
            or "(other)" in path.name.lower()
        ), None)
        if vocals is None or instrumental is None:
            raise SongError(
                "separation_failed", "separator did not produce vocals + instrumental")
        return vocals, instrumental

    def _mix(self, vocals: Path, instrumental: Path, output: Path) -> None:
        self._run([
            self.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(vocals), "-i", str(instrumental),
            "-filter_complex",
            (
                "[0:a]volume=1.0[v];[1:a]volume=0.8[i];"
                "[v][i]amix=inputs=2:duration=longest:normalize=0,"
                "alimiter=limit=0.95[a]"
            ),
            "-map", "[a]", "-ar", "44100", "-ac", "2",
            "-c:a", "pcm_s16le", str(output),
        ], timeout=180)

    @staticmethod
    def _run(
        command: list[str],
        *,
        timeout: int,
        env: dict[str, str] | None = None,
    ) -> None:
        merged = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        if env:
            merged.update(env)
        try:
            result = subprocess.run(
                command, check=False, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=timeout, env=merged,
            )
        except FileNotFoundError as exc:
            raise SongError("tool_missing", f"missing executable: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise SongError("tool_timeout", f"{Path(command[0]).name} timed out") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "command failed").strip()
            raise SongError(
                "tool_failed",
                f"{Path(command[0]).name}: {detail[-1000:]}",
            )

    def _prune_cache(self, keep: int) -> None:
        items = sorted(
            (
                (path, path.stat().st_mtime)
                for path in self.cache_root.glob("*.mp3")
                if path.is_file()
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        for path, _mtime in items[max(1, int(keep)):]:
            try:
                path.unlink()
            except OSError:
                continue


__all__ = ["SongError", "SongJob", "SongService"]
