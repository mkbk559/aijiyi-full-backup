"""补充度量：分离出来的「伴奏」有多响？——这才是"带 BGM"的可量化证据。

`voice_dataset_check.py` 里的 SNR 是「同一文件内语音帧 vs 静音帧」，只能说明动态范围，
**不能**证明没伴奏（BGM 铺满整段时会被算进"语音"）。本脚本改用两个 stem 的能量比：

    bgm_ratio_db = 20*log10(rms(instrumental) / rms(vocals))

- ≤ -20 dB：基本没有伴奏（干净）
- -20 ~ -10 dB：有可听伴奏，但人声占主导
- > -10 dB：伴奏很响，分离后仍可能残留/受损

用法： voice-engine\sep-venv\Scripts\python.exe tools\voice_bgm_check.py
产物： .tmp/voice-check/bgm.json（并入 report.json 的 bgm 字段）
"""
from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
VOCALS = ROOT / ".tmp" / "voice-check" / "vocals"
REPORT = ROOT / ".tmp" / "voice-check" / "report.json"


def rms(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
        ch = w.getnchannels()
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0


def main() -> int:
    voc = {p.name.replace("_(Vocals)_UVR-MDX-NET-Voc_FT.wav", ""): p
           for p in VOCALS.glob("*_(Vocals)_*.wav")}
    ins = {p.name.replace("_(Instrumental)_UVR-MDX-NET-Voc_FT.wav", ""): p
           for p in VOCALS.glob("*_(Instrumental)_*.wav")}
    rows = []
    for key, vp in sorted(voc.items()):
        ip = ins.get(key)
        if ip is None:
            continue
        v, i = rms(vp), rms(ip)
        if v <= 0:
            continue
        rows.append({
            "clip": key,
            "vocals_rms_db": round(20 * np.log10(max(v, 1e-9)), 1),
            "instrumental_rms_db": round(20 * np.log10(max(i, 1e-9)), 1),
            "bgm_ratio_db": round(20 * np.log10(max(i, 1e-9) / v), 1),
        })
    if not rows:
        raise SystemExit("没有找到成对的 Vocals/Instrumental 产物")
    ratios = np.array([r["bgm_ratio_db"] for r in rows])
    clean = [r for r in rows if r["bgm_ratio_db"] <= -20]
    mild = [r for r in rows if -20 < r["bgm_ratio_db"] <= -10]
    heavy = [r for r in rows if r["bgm_ratio_db"] > -10]
    summary = {
        "clips": len(rows),
        "bgm_ratio_db_median": round(float(np.median(ratios)), 1),
        "bgm_ratio_db_max": round(float(np.max(ratios)), 1),
        "clean_le_-20": len(clean),
        "mild_-20_to_-10": len(mild),
        "heavy_gt_-10": len(heavy),
        "per_clip": rows,
    }
    out = ROOT / ".tmp" / "voice-check" / "bgm.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    if REPORT.exists():
        rep = json.loads(REPORT.read_text("utf-8"))
        rep["bgm"] = {k: summary[k] for k in summary if k != "per_clip"}
        REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), "utf-8")

    print(f"成对 stem：{len(rows)} 段")
    print(f"伴奏/人声能量比 中位 {summary['bgm_ratio_db_median']} dB，最大 {summary['bgm_ratio_db_max']} dB")
    print(f"干净(≤-20dB) {len(clean)} 段 | 轻微(-20~-10) {len(mild)} 段 | 很响(>-10) {len(heavy)} 段")
    if heavy:
        print("很响的样本：", [r["clip"] for r in sorted(heavy, key=lambda r: -r["bgm_ratio_db"])][:8])
    return 0


if __name__ == "__main__":
    sys.exit(main())
