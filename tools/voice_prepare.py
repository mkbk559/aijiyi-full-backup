"""Prepare a local voice dataset from a video or existing vocal WAV.

The script deliberately does not download models. It extracts/normalizes audio,
detects speech intervals, exports short clips and writes a manifest that can be
consumed by RVC/GPT-SoVITS training tools.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path


def run(cmd: list[str], *, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def extract_audio(video: Path, output: Path, ffmpeg: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video), "-vn", "-ac", "1", "-ar", "44100",
        "-c:a", "pcm_s16le", str(output),
    ])


def normalize_audio(source: Path, output: Path, ffmpeg: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run([
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-af",
        "highpass=f=60,lowpass=f=16000,loudnorm=I=-18:TP=-2:LRA=11",
        "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(output),
    ])


def speech_intervals(
    source: Path,
    *,
    ffmpeg: str,
    duration: float,
    noise_db: float = -35.0,
    silence_s: float = 0.35,
) -> list[tuple[float, float]]:
    result = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-i", str(source), "-af",
            f"silencedetect=noise={noise_db}dB:d={silence_s}",
            "-f", "null", "NUL",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    starts = [float(v) for v in re.findall(
        r"silence_start: ([0-9.]+)", result.stderr)]
    ends = [float(v) for v in re.findall(
        r"silence_end: ([0-9.]+)", result.stderr)]
    silences: list[tuple[float, float]] = []
    for index, start in enumerate(starts):
        end = ends[index] if index < len(ends) else duration
        silences.append((start, end))
    intervals: list[tuple[float, float]] = []
    previous = 0.0
    for start, end in silences:
        if start - previous >= 0.5:
            intervals.append((previous, start))
        previous = end
    if duration - previous >= 0.5:
        intervals.append((previous, duration))
    return intervals


def choose_clips(
    intervals: list[tuple[float, float]],
    *,
    min_sec: float = 3.0,
    max_sec: float = 10.0,
    pad_sec: float = 0.15,
) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for start, end in intervals:
        start = max(0.0, float(start) - pad_sec)
        end = float(end) + pad_sec
        if end - start < min_sec:
            continue
        cursor = start
        while end - cursor >= min_sec:
            length = min(max_sec, end - cursor)
            out.append((cursor, cursor + length))
            cursor += length
    return out


def media_duration(path: Path, ffprobe: str) -> float:
    result = run([
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(result.stdout.strip())


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--vocals", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--profile-id", default="dafeiyu")
    parser.add_argument("--min-sec", type=float, default=3.0)
    parser.add_argument("--max-sec", type=float, default=10.0)
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe") or "ffprobe")
    args = parser.parse_args()

    root = args.out.resolve()
    clips_dir = root / "clips"
    root.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)
    source = args.input.resolve()
    if args.vocals:
        normalized = args.vocals.resolve()
    else:
        extracted = root / "source_44k.wav"
        normalized = root / "source_normalized.wav"
        extract_audio(source, extracted, args.ffmpeg)
        normalize_audio(extracted, normalized, args.ffmpeg)

    duration = media_duration(normalized, args.ffprobe)
    intervals = speech_intervals(
        normalized, ffmpeg=args.ffmpeg, duration=duration)
    clips = choose_clips(
        intervals, min_sec=args.min_sec, max_sec=args.max_sec)
    exported: list[dict] = []
    for index, (start, end) in enumerate(clips, 1):
        target = clips_dir / f"{args.profile_id}_{index:04d}.wav"
        run([
            args.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
            "-i", str(normalized), "-ar", "44100", "-ac", "1",
            "-c:a", "pcm_s16le", str(target),
        ])
        exported.append({
            "file": str(target),
            "start": round(start, 3),
            "end": round(end, 3),
            "seconds": round(end - start, 3),
        })

    reference = min(
        exported,
        key=lambda item: abs(float(item["seconds"]) - 7.0),
        default=None,
    )
    if reference:
        target = root / "reference.wav"
        shutil.copy2(reference["file"], target)
        reference["reference_copy"] = str(target)

    manifest = {
        "version": 1,
        "profile_id": args.profile_id,
        "created_at": time.time(),
        "source": str(source),
        "source_sha256": file_sha256(source),
        "vocals": str(normalized),
        "duration_sec": round(duration, 3),
        "speech_intervals": len(intervals),
        "clips": exported,
        "reference": reference,
        "text": "",
    }
    (root / "dataset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "out": str(root),
        "duration_sec": round(duration, 1),
        "intervals": len(intervals),
        "clips": len(exported),
        "clip_seconds": round(sum(x["seconds"] for x in exported), 1),
        "reference": reference,
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
