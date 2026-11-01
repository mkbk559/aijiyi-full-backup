"""检查"大肥鱼"素材够不够 RVC 训练门槛：先人声分离，再量底噪/信噪比。

RVC 官方要求（README）：「推荐至少收集 **10 分钟**低底噪语音数据」。
本脚本给出：分离后可用总时长、每段 SNR 估计、削波、静音占比，以及"还差多少分钟"。

用法（用 voice-engine/.venv 的 python，里面装了 audio-separator + numpy）：
    python tools/voice_dataset_check.py                       # 分离 + 量测 + 报告
    python tools/voice_dataset_check.py --no-separate         # 只量已有产物
    python tools/voice_dataset_check.py --reuse               # 已分离过的跳过

产物：
    .tmp/voice-check/vocals/*.wav     分离后的人声
    .tmp/voice-check/report.json      每段逐条指标
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CLIPS = ROOT / "data" / "voices" / "dafeiyu" / "dataset_v2" / "clips"
WORK = ROOT / ".tmp" / "voice-check"
VOCALS = WORK / "vocals"
MODEL_DIR = ROOT / "voice-engine" / "models"
MODEL = "UVR-MDX-NET-Voc_FT.onnx"

# 判据（写死在这里，报告里会明确列出，便于以后改）
MIN_DUR_S = 3.0          # RVC 切片太短没意义
MIN_SNR_DB = 15.0        # 分离后估计 SNR 下限
MAX_CLIP_RATIO = 0.01    # 削波样本占比上限
TARGET_MINUTES = 10.0    # RVC 官方建议的数据量


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        raise SystemExit(f"{path.name}: 只支持 16-bit PCM（实际 {sw * 8}-bit）")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, sr


def metrics(path: Path) -> dict:
    x, sr = read_wav(path)
    dur = len(x) / sr if sr else 0.0
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    clip_ratio = float(np.mean(np.abs(x) > 0.999)) if len(x) else 0.0
    win = max(int(sr * 0.025), 1)
    hop = max(int(sr * 0.010), 1)
    if len(x) < win:
        frames = np.array([np.sqrt(np.mean(x ** 2)) if len(x) else 0.0])
    else:
        idx = range(0, len(x) - win + 1, hop)
        frames = np.array([np.sqrt(np.mean(x[i:i + win] ** 2)) for i in idx])
    eps = 1e-8
    p10, p90 = float(np.percentile(frames, 10)), float(np.percentile(frames, 90))
    snr = 20 * np.log10(max(p90, eps) / max(p10, eps)) if p90 > 0 else 0.0
    floor_dbfs = 20 * np.log10(max(p10, eps))
    silence_gate = max(p10 * 1.5, 10 ** (-55 / 20))
    silence_ratio = float(np.mean(frames < silence_gate)) if len(frames) else 1.0
    # 静音段之间的语音占比（判断"是不是一整段都在说话"）
    speech_ratio = float(np.mean(frames > max(p10 * 3, 10 ** (-45 / 20))))
    return {
        "file": path.name,
        "seconds": round(dur, 2),
        "sample_rate": sr,
        "peak_dbfs": round(20 * np.log10(max(peak, eps)), 1),
        "clip_ratio": round(clip_ratio, 5),
        "noise_floor_dbfs": round(float(floor_dbfs), 1),
        "snr_db": round(float(snr), 1),
        "silence_ratio": round(silence_ratio, 3),
        "speech_ratio": round(speech_ratio, 3),
    }


def separate_all(files: list[Path], reuse: bool) -> dict[str, Path]:
    """返回 {源文件名: 分离后人声路径}。"""
    from audio_separator.separator import Separator  # 延迟导入，--no-separate 时不需要

    VOCALS.mkdir(parents=True, exist_ok=True)
    todo = []
    for f in files:
        hit = list(VOCALS.glob(f"{f.stem}_(Vocals)*.wav"))
        if reuse and hit:
            continue
        todo.append(f)
    if todo:
        sep = Separator(
            log_level=30,
            output_dir=str(VOCALS),
            output_format="WAV",
            model_file_dir=str(MODEL_DIR),
            sample_rate=44100,
        )
        sep.load_model(MODEL)
        t0 = time.time()
        for i, f in enumerate(todo, 1):
            print(f"  [{i}/{len(todo)}] 分离 {f.name}", flush=True)
            sep.separate(str(f))
        print(f"  分离完成，用时 {(time.time() - t0) / 60:.1f} 分钟", flush=True)
    out: dict[str, Path] = {}
    for f in files:
        hit = sorted(VOCALS.glob(f"{f.stem}_(Vocals)*.wav"))
        if hit:
            out[f.name] = hit[0]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", default=str(CLIPS))
    ap.add_argument("--no-separate", action="store_true")
    ap.add_argument("--reuse", action="store_true", help="已分离的跳过")
    args = ap.parse_args(argv)

    files = sorted(Path(args.clips).glob("*.wav"))
    if not files:
        raise SystemExit(f"没找到 wav：{args.clips}")
    print(f"素材目录 {args.clips}：{len(files)} 段", flush=True)

    WORK.mkdir(parents=True, exist_ok=True)
    if args.no_separate:
        src_metrics, vocals = [], {}
        for f in files:
            hit = sorted(VOCALS.glob(f"{f.stem}_(Vocals)*.wav"))
            if hit:
                vocals[f.name] = hit[0]
    else:
        vocals = separate_all(files, args.reuse)
        src_metrics = [metrics(f) for f in files]

    rows = []
    for f in files:
        vp = vocals.get(f.name)
        if vp is None:
            continue
        m = metrics(vp)
        m["source"] = f.name
        rows.append(m)

    if not rows:
        raise SystemExit("没有可量测的分离产物（先不加 --no-separate 跑一次）")

    usable = [r for r in rows
              if r["seconds"] >= MIN_DUR_S and r["snr_db"] >= MIN_SNR_DB
              and r["clip_ratio"] <= MAX_CLIP_RATIO]
    total_min = sum(r["seconds"] for r in rows) / 60
    usable_min = sum(r["seconds"] for r in usable) / 60
    snrs = np.array([r["snr_db"] for r in rows])

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "clips_dir": str(args.clips),
        "clips": len(rows),
        "total_minutes": round(total_min, 2),
        "usable_clips": len(usable),
        "usable_minutes": round(usable_min, 2),
        "target_minutes": TARGET_MINUTES,
        "thresholds": {"min_dur_s": MIN_DUR_S, "min_snr_db": MIN_SNR_DB,
                       "max_clip_ratio": MAX_CLIP_RATIO},
        "snr_db_median": round(float(np.median(snrs)), 1),
        "snr_db_min": round(float(np.min(snrs)), 1),
        "snr_db_max": round(float(np.max(snrs)), 1),
        "source_median_snr_db": (round(float(np.median([m["snr_db"] for m in src_metrics])), 1)
                                 if src_metrics else None),
        "per_file": rows,
    }
    (WORK / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8")

    print("\n== 结论 ==")
    print(f"分离后总量   {total_min:.1f} 分钟（{len(rows)} 段）")
    print(f"可用量       {usable_min:.1f} 分钟（{len(usable)} 段过线）")
    print(f"SNR 中位/最低 {report['snr_db_median']} / {report['snr_db_min']} dB"
          + (f"（分离前中位 {report['source_median_snr_db']} dB）"
             if report["source_median_snr_db"] is not None else ""))
    gap = TARGET_MINUTES - usable_min
    if usable_min >= TARGET_MINUTES:
        print(f"判定：**够**（RVC 建议 ≥{TARGET_MINUTES:.0f} 分钟，超出 {-gap:.1f} 分钟）")
    elif usable_min >= TARGET_MINUTES * 0.8:
        print(f"判定：**勉强**（差 {gap:.1f} 分钟；建议补素材或降级到 8–10 分钟试训）")
    else:
        print(f"判定：**不够**（差 {gap:.1f} 分钟）")
    print(f"逐段明细：{WORK / 'report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
