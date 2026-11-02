"""换声那一段的耗时拆解：客户端进程 vs 服务端推理（2026-11-03）。

真机链路里 13 s 素材的换声段是 2.3 s，而直接打服务只有 682 ms——
差的那 1.6 s 到底在客户端进程、还是在服务端每次调用里（faiss 索引重载）？

用法：python tools/archcheck/rvc_leg_split.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GPU_PY = ROOT / "voice-engine" / "rvc" / ".venv" / "Scripts" / "python.exe"
CLIENT = ROOT / "tools" / "rvc_convert.py"
TMP = ROOT / ".tmp" / "leg_split"
BASE = "http://127.0.0.1:18601"


def op():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(path, payload, timeout=600.0):
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with op().open(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": exc.read().decode("utf-8", "replace")[:200]}


def health():
    with op().open(f"{BASE}/health", timeout=10) as r:
        return json.loads(r.read().decode())


def main() -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    clip = TMP / "clip13.wav"
    if not clip.exists():
        subprocess.run([sys.executable, str(ROOT / "tools" / "archcheck" / "make_bench_wav.py"),
                        str(clip), "13.0"], check=True, capture_output=True)
    out = {"health": health(), "client": [], "server": [], "client_boot": []}
    print("health:", json.dumps(out["health"], ensure_ascii=False))

    # 1) 客户端进程空转成本（不连服务：--via server 指向一个死端口会立刻失败）
    for i in range(2):
        t = time.perf_counter()
        p = subprocess.run([str(GPU_PY), "-c", "import json,os,sys,argparse,urllib.request"],
                           capture_output=True)
        out["client_boot"].append(round((time.perf_counter() - t) * 1000.0, 1))

    # 2) 完整客户端（--via server）：进程 + health + convert
    for i in range(3):
        dst = TMP / f"client_{i}.wav"
        t = time.perf_counter()
        p = subprocess.run([str(GPU_PY), str(CLIENT), str(clip), str(dst), "--via", "server"],
                           capture_output=True, text=True)
        out["client"].append({"ms": round((time.perf_counter() - t) * 1000.0, 1),
                              "rc": p.returncode,
                              "bytes": dst.stat().st_size if dst.exists() else 0})

    # 3) 只打服务端 /convert
    for i in range(3):
        dst = TMP / f"server_{i}.wav"
        t = time.perf_counter()
        res = post("/convert", {"in": str(clip.resolve()), "out": str(dst.resolve())})
        out["server"].append({"wall": round((time.perf_counter() - t) * 1000.0, 1),
                              "server_ms": res.get("ms"), "ok": res.get("ok")})

    cl = [c["ms"] for c in out["client"]]
    sv = [s["wall"] for s in out["server"]]
    out["verdict"] = {
        "client_median_ms": sorted(cl)[len(cl) // 2],
        "server_median_ms": sorted(sv)[len(sv) // 2],
        "client_boot_ms": out["client_boot"],
        "overhead_ms": round(sorted(cl)[len(cl) // 2] - sorted(sv)[len(sv) // 2], 1),
    }
    (ROOT / "tools" / "archcheck" / "results" / "rvc_leg_split.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out["verdict"], ensure_ascii=False))


if __name__ == "__main__":
    main()
