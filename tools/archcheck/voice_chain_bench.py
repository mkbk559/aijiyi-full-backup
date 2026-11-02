"""真机端到端：TTS → RVC 换声 这条产品链路到底多久（2026-11-03 重测）。

为什么要重测：之前那批数字（RTF 1.6~2.1、"换声 9.5 s"）是用 `voice-engine/.venv`
（torch cpu）量的，而真实运行时是 `voice-engine/rvc/.venv`（torch cu128 + RTX 3050）。
`rvc_gpu_bench.py` 已经证明单段换声 384~503 ms；这里量**用户真正会走的那条链**：

    VoiceRegistry.synthesize(text)  →  Edge TTS  →  profile.postprocess（rvc_convert.py → 常驻服务）

做法：同一段文本跑两遍——
  A. 原 profile（TTS + 换声）
  B. 把 postprocess 清空（只有 TTS）
A - B = 换声那一段的真实开销。另外检查输出时长/音量（避免"变快是因为没干活"），
以及 4 GB 显存下长文本会不会 OOM。

用法：python tools/archcheck/voice_chain_bench.py
"""
from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from aiji.heart.voice_service import VoiceProfile, VoiceRegistry  # noqa: E402

OUT = ROOT / ".tmp" / "voice_chain"
TEXTS = [
    ("short", "今天下午三点在楼下咖啡店见，别忘了带上那份材料。"),
    ("medium", "我今天刷到一个视频，讲的是有人用一张显卡在本地跑语音克隆，"
               "效果居然比在线服务还自然，而且延迟只有几百毫秒，评论区都在问怎么部署。"),
    ("long", "".join(["我最近在折腾一个本地的记忆系统，它会把你说过的话拆成事实存起来，"
                      "过一段时间不用就慢慢忘掉，用到了又会重新变牢。"] * 3)),
]
DAEMON = ROOT / "tools" / "rvc_daemon.ps1"


def wav_stats(path: Path) -> dict:
    try:
        with wave.open(str(path), "rb") as w:
            frames, rate = w.getnframes(), w.getframerate() or 1
            raw = w.readframes(min(frames, rate * 60))
        import array

        samples = array.array("h", raw)
        peak = max((abs(s) for s in samples), default=0)
        rms = (sum(s * s for s in samples) / max(1, len(samples))) ** 0.5
        return {"seconds": round(frames / rate, 2), "rate": rate,
                "peak": peak, "rms": round(rms, 1),
                "bytes": path.stat().st_size if path.exists() else 0}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def gpu_mem() -> dict:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20)
        used, total = [int(x) for x in out.stdout.strip().split(",")[:2]]
        return {"used_mib": used, "total_mib": total}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def daemon_up() -> dict:
    """确保常驻服务在跑（走用户会用的那个脚本，不是另起一套）。"""
    ps = shutil.which("powershell") or "powershell"
    t0 = time.perf_counter()
    try:
        proc = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
                               "-File", str(DAEMON)],
                              capture_output=True, text=True, timeout=180,
                              encoding="utf-8", errors="replace")
        return {"seconds": round(time.perf_counter() - t0, 2),
                "stdout": (proc.stdout or "").strip()[-300:], "rc": proc.returncode}
    except subprocess.TimeoutExpired:
        return {"seconds": round(time.perf_counter() - t0, 2),
                "stdout": "", "rc": -1, "error": "daemon script timed out"}


def run_one(reg: VoiceRegistry, profile: VoiceProfile, name: str, text: str,
            tag: str) -> dict:
    target = OUT / f"{name}-{tag}.wav"
    if target.exists():
        target.unlink()
    before = gpu_mem()
    t0 = time.perf_counter()
    try:
        reg.synthesize(text, target, profile_id=profile.id)
        ms = round((time.perf_counter() - t0) * 1000.0, 1)
        res = {"ok": True, "ms": ms, "stats": wav_stats(target)}
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "ms": round((time.perf_counter() - t0) * 1000.0, 1),
               "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    res["gpu_before"] = before
    res["gpu_after"] = gpu_mem()
    res["last_skipped"] = reg.last_skipped
    return res


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict = {"daemon": daemon_up(), "gpu": gpu_mem(), "cases": []}

    voices_root = ROOT / "data" / "voices"
    reg = VoiceRegistry(root=voices_root)
    profile = reg.get("dafeiyu")
    report["profile"] = {"id": profile.id, "provider": profile.provider,
                         "voice": profile.voice,
                         "postprocess": profile.postprocess}
    plain = VoiceProfile.from_dict({**dataclasses.asdict(profile), "postprocess": {}})

    for name, text in TEXTS:
        chain = run_one(reg, profile, name, text, "chain")
        tts = run_one(reg, plain, name, text, "tts")
        row = {
            "case": name, "chars": len(text),
            "chain_ms": chain["ms"], "tts_ms": tts["ms"],
            "rvc_ms": round(chain["ms"] - tts["ms"], 1) if chain["ok"] and tts["ok"] else None,
            "audio_seconds": chain.get("stats", {}).get("seconds"),
            "rms": chain.get("stats", {}).get("rms"),
            "chain_ok": chain["ok"], "tts_ok": tts["ok"],
            "chain_err": chain.get("error"), "tts_err": tts.get("error"),
            "skipped": chain.get("last_skipped"),
            "gpu_peak_mib": max(chain["gpu_after"].get("used_mib", 0),
                                tts["gpu_after"].get("used_mib", 0)),
        }
        report["cases"].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    ok = [c for c in report["cases"] if c["chain_ok"]]
    if ok:
        report["verdict"] = {
            "cases": len(ok),
            "chain_ms": {c["case"]: c["chain_ms"] for c in ok},
            "rvc_ms": {c["case"]: c["rvc_ms"] for c in ok},
            "audio_seconds": {c["case"]: c["audio_seconds"] for c in ok},
            "skipped_any": any(c["skipped"] for c in ok),
        }
    (ROOT / "tools" / "archcheck" / "results" / "voice_chain.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nSUMMARY " + json.dumps(report.get("verdict", {}), ensure_ascii=False))


if __name__ == "__main__":
    main()
