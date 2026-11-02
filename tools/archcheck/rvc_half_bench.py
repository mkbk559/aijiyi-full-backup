"""半精度 vs 全精度：4 GB 显存下的换声耗时/显存（2026-11-03）。

背景：真机链路里 30.8 s 素材换声 65.7 s、显存 3946/4096 MiB（快爆）。怀疑是 fp32。
这里对同一批素材分别用 `--half 0/1` 起服务，量 warm 耗时与显存峰值。

用法：python tools/archcheck/rvc_half_bench.py
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
TMP = ROOT / ".tmp" / "rvc_half"
CLIPS = [5.0, 13.0, 30.0]


def op():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(url, timeout=10.0):
    with op().open(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post(url, payload, timeout=900.0):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with op().open(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except Exception:
            return {"ok": False, "error": f"HTTP {exc.code}: {body[:200]}"}
    except Exception as exc:  # 服务进程被 OOM 杀掉时是 ConnectionReset/URLError
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


def gpu_used() -> int:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                          "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=20)
    return int(out.stdout.strip().splitlines()[0])


def clip(seconds: float) -> Path:
    path = TMP / f"clip_{seconds}s.wav"
    if not path.exists():
        subprocess.run([sys.executable, str(ROOT / "tools" / "archcheck" / "make_bench_wav.py"),
                        str(path), str(seconds)], check=True, capture_output=True)
    return path


def run_mode(port: int, half: str) -> dict:
    srv = subprocess.Popen(
        [str(GPU_PY), str(ROOT / "tools" / "rvc_server.py"), "--port", str(port),
         "--device", "cuda", "--half", half, "--log", str(TMP / f"server-{half}.log")])
    base = f"http://127.0.0.1:{port}"
    result: dict = {"half": half, "rows": []}
    try:
        for _ in range(240):
            try:
                h = get(f"{base}/health")
                break
            except Exception:
                if srv.poll() is not None:
                    tail = (TMP / f"server-{half}.log").read_text(
                        encoding="utf-8", errors="replace")[-800:]
                    return {"half": half, "error": "server died", "log": tail}
                time.sleep(1.0)
        else:
            return {"half": half, "error": "health timeout"}
        result["health"] = h
        for seconds in CLIPS:
            src = clip(seconds).resolve()
            dst = (TMP / f"out_{seconds}_{half}.wav").resolve()
            if dst.exists():
                dst.unlink()
            post(f"{base}/convert", {"in": str(src), "out": str(dst)})  # warm-up
            before = gpu_used()
            t = time.perf_counter()
            res = post(f"{base}/convert", {"in": str(src), "out": str(dst)})
            wall = (time.perf_counter() - t) * 1000.0
            peak = gpu_used()
            result["rows"].append({
                "clip_s": seconds, "ok": res.get("ok"), "ms": res.get("ms"),
                "wall": round(wall, 1), "rtf": round(wall / 1000.0 / seconds, 3),
                "vram_before": before, "vram_peak": peak,
                "bytes": dst.stat().st_size if dst.exists() else 0,
                "err": res.get("error")})
            print(json.dumps(result["rows"][-1], ensure_ascii=False), flush=True)
    finally:
        try:
            post(f"{base}/shutdown", {})
        except Exception:
            pass
        try:
            srv.wait(timeout=20)
        except Exception:
            srv.kill()
    return result


def main() -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    # 4 GB 卡上不能同时跑两个服务：先把常驻的那个停掉，否则新服务一起步就 OOM。
    ps = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                         "-File", str(ROOT / "tools" / "rvc_daemon.ps1"), "-Stop"],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=120)
    print("stopped daemon:", (ps.stdout or "").strip()[-120:], flush=True)
    time.sleep(2.0)
    out = {"rows": [], "gpu_idle_mib": gpu_used()}
    for half, port in (("0", 18611), ("1", 18612)):
        out["rows"].append(run_mode(port, half))
    (ROOT / "tools" / "archcheck" / "results" / "rvc_half_bench.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {}
    for mode in out["rows"]:
        for row in mode.get("rows", []):
            summary[f"{mode['half']}/{row['clip_s']}s"] = {
                "ms": row["wall"], "rtf": row["rtf"], "vram": row["vram_peak"]}
    print("\nSUMMARY " + json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
