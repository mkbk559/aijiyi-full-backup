"""分块换声基准：同一段长素材，整段推理 vs 切段推理（2026-11-03）。

背景：4 GB 显存在 30 s 素材上一次推理就打满（23.8 s、RTF 0.79）。
分块后每段 ≤10 s，显存恒定、耗时近似线性。这里用真服务量出差距。

用法（先起 daemon）：python tools/archcheck/rvc_chunk_bench.py
产物：tools/archcheck/results/rvc_chunk_bench.json
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:18601"
TMP = ROOT / ".tmp" / "chunk_bench"
GPU_PY = ROOT / "voice-engine" / "rvc" / ".venv" / "Scripts" / "python.exe"


def op():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(path: str, payload: dict, timeout: float = 900.0):
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with op().open(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": exc.read().decode("utf-8", "replace")[:300]}


def health():
    with op().open(f"{BASE}/health", timeout=10) as r:
        return json.loads(r.read().decode())


def vram() -> int:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return -1


def seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return round(w.getnframes() / (w.getframerate() or 1), 2)


def make_clip(path: Path, secs: float) -> None:
    script = ROOT / "tools" / "archcheck" / "make_bench_wav.py"
    if not path.exists():
        subprocess.run([sys.executable, str(script), str(path), str(secs)],
                       check=True, capture_output=True)


def run_case(clip: Path, chunk: float, label: str) -> dict:
    dst = TMP / f"{clip.stem}-chunk{chunk:g}.wav"
    peak = vram()
    t0 = time.perf_counter()
    res = post("/convert", {"in": str(clip.resolve()), "out": str(dst.resolve()),
                            "chunk_seconds": chunk})
    wall = round((time.perf_counter() - t0) * 1000.0, 1)
    peak = max(peak, vram())
    return {"case": label, "source_s": seconds(clip), "chunk_seconds": chunk,
            "wall_ms": wall, "server_ms": res.get("ms"), "chunks": res.get("chunks"),
            "ok": res.get("ok"), "error": res.get("error"),
            "out_s": seconds(dst) if dst.exists() else None,
            "vram_peak_mib": peak,
            "rtf": round((res.get("ms") or wall) / 1000.0 / max(seconds(clip), 0.01), 3)}


def main() -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    info = health()
    report = {"health": info, "rows": []}
    print("health:", json.dumps(info, ensure_ascii=False))

    make_clip(TMP / "clip30.wav", 30.0)
    make_clip(TMP / "clip60.wav", 60.0)

    plan = [
        (TMP / "clip30.wav", 0, "30s 整段"),
        (TMP / "clip30.wav", 10, "30s 分成 10s"),
        (TMP / "clip30.wav", 20, "30s 分成 20s"),
        (TMP / "clip60.wav", 10, "60s 分成 10s"),
    ]
    for clip, chunk, label in plan:
        row = run_case(clip, chunk, label)
        report["rows"].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    base = {r["case"]: r for r in report["rows"]}
    slow = base.get("30s 整段", {}).get("server_ms") or 1
    fast = base.get("30s 分成 10s", {}).get("server_ms") or 0
    report["verdict"] = {
        "30s_whole_ms": slow, "30s_chunked_ms": fast,
        "speedup": round(slow / fast, 2) if fast else None,
        "chunk_seconds": health().get("chunk_seconds"),
        "index_load_ms": health().get("index_load_ms"),
    }
    (ROOT / "tools" / "archcheck" / "results" / "rvc_chunk_bench.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nSUMMARY " + json.dumps(report["verdict"], ensure_ascii=False))


if __name__ == "__main__":
    main()
