"""量 RVC 常驻服务的冷/热延迟（2026-11-03）。

流程（全在一个进程里做完，因为沙箱里子进程活不过工具调用）：
  1. 起服务（RVC venv）→ 轮询 /health，记 loaded_ms；
  2. 连发 3 次 /convert（第 1 次算冷，第 2、3 次算热）；
  3. 再跑一次老路径（`tools/rvc_convert.py` 一次性 CLI，进程内）→ 冷启动总耗时；
  4. 输出 JSON：两者对比。

用法：python tools/archcheck/rvc_bench.py [--port 18601]
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
PY = ROOT / "voice-engine" / ".venv" / "Scripts" / "python.exe"
TMP = ROOT / ".tmp"
SRC = TMP / "rvc_bench_in.wav"


def post(url: str, payload: dict, timeout: float = 600.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def get(url: str, timeout: float = 10.0) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_health(port: int, timeout_s: float = 180.0) -> dict:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        try:
            return get(f"http://127.0.0.1:{port}/health")
        except Exception:
            time.sleep(1.0)
    raise SystemExit("RVC 服务没起来（/health 一直连不上）")


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"缺样本：{SRC}（先跑 tools/archcheck/make_bench_wav.py）")
    if not PY.exists():
        raise SystemExit(f"缺 RVC venv：{PY}")
    port = 18601
    out = {"src": str(SRC), "src_bytes": SRC.stat().st_size}
    t_start = time.perf_counter()
    srv = subprocess.Popen(
        [str(PY), str(ROOT / "tools" / "rvc_server.py"), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(ROOT),
    )
    try:
        health = wait_health(port)
        out["boot"] = {
            "seconds_until_health": round(time.perf_counter() - t_start, 2),
            "loaded_ms_in_process": health.get("loaded_ms"),
            "device": health.get("device"),
            "model": health.get("model"),
        }
        runs = []
        for i in range(3):
            dst = TMP / f"rvc_bench_out_srv{i}.wav"
            if dst.exists():
                dst.unlink()
            t0 = time.perf_counter()
            res = post(f"http://127.0.0.1:{port}/convert", {"in": str(SRC), "out": str(dst)})
            wall = (time.perf_counter() - t0) * 1000.0
            runs.append({
                "i": i, "ok": res.get("ok"), "server_ms": res.get("ms"),
                "wall_ms": round(wall, 1),
                "out_bytes": dst.stat().st_size if dst.exists() else 0,
                "error": res.get("error"),
            })
        out["server_runs"] = runs
    finally:
        try:
            post(f"http://127.0.0.1:{port}/shutdown", {})
        except Exception:
            pass
        try:
            srv.wait(timeout=20)
        except Exception:
            srv.kill()

    # 老路径：一次性 CLI（进程内跑，等价于 voice_service 调用的方式）
    dst = TMP / "rvc_bench_out_cli.wav"
    if dst.exists():
        dst.unlink()
    t0 = time.perf_counter()
    proc = subprocess.run(
        [str(PY), str(ROOT / "tools" / "rvc_convert.py"), str(SRC), str(dst)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=1800,
    )
    out["cold_cli"] = {
        "wall_ms": round((time.perf_counter() - t0) * 1000.0, 1),
        "returncode": proc.returncode,
        "out_bytes": dst.stat().st_size if dst.exists() else 0,
        "stderr_tail": (proc.stderr or "").strip().splitlines()[-2:],
    }
    hot = [r["wall_ms"] for r in out.get("server_runs", [])[1:] if r["ok"]]
    if hot and out["cold_cli"]["wall_ms"]:
        out["verdict"] = {
            "hot_median_ms": round(sorted(hot)[len(hot) // 2], 1),
            "cold_cli_ms": out["cold_cli"]["wall_ms"],
            "speedup_x": round(out["cold_cli"]["wall_ms"] / (sorted(hot)[len(hot) // 2] or 1), 2),
        }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
