"""RVC 在 GPU 上的延迟（2026-11-03 补测）。

为什么补这测：之前的结论（RTF 1.6~2.1，"必须上 GPU"）是用 `voice-engine/.venv`
（torch 2.15.0+**cpu**）量出来的；而这台机器其实有 **RTX 3050 Laptop (4 GB)**，
`voice-engine/rvc/.venv` 里装的是 torch 2.7.1+**cu128** 且 `cuda.is_available()==True`。
所以"换不换引擎"必须先在真 GPU 环境上重新量。

用法：python tools/archcheck/rvc_gpu_bench.py
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
TMP = ROOT / ".tmp"
PORT = 18607
CONFIGS = [(1.5, "rmvpe"), (1.5, "fcpe"), (6.0, "rmvpe"), (6.0, "fcpe")]


def op():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(url, timeout=10.0):
    with op().open(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post(url, payload, timeout=1800.0):
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
            return {"ok": False, "error": f"HTTP {exc.code}: {body[:300]}"}


def clip(seconds: float) -> Path:
    out = TMP / f"rvc_gpu_clip_{seconds}s.wav"
    if not out.exists():
        subprocess.run([sys.executable, str(ROOT / "tools" / "archcheck" / "make_bench_wav.py"),
                        str(out), str(seconds)], check=True, capture_output=True)
    return out


def main() -> None:
    if not GPU_PY.exists():
        raise SystemExit(f"缺 GPU venv：{GPU_PY}")
    base = f"http://127.0.0.1:{PORT}"
    srv = subprocess.Popen(
        [str(GPU_PY), str(ROOT / "tools" / "rvc_server.py"), "--port", str(PORT),
         "--device", "cuda"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(ROOT),
    )
    out: dict = {"python": str(GPU_PY), "device": "cuda", "rows": []}
    try:
        t0 = time.perf_counter()
        for _ in range(240):
            try:
                h = get(f"{base}/health")
                break
            except Exception:
                if srv.poll() is not None:
                    err = (srv.stderr.read() or b"").decode("utf-8", "replace")[-1200:]
                    raise SystemExit("GPU 服务没起来：\n" + err)
                time.sleep(1.0)
        else:
            raise SystemExit("/health 超时")
        out["boot"] = {"seconds": round(time.perf_counter() - t0, 2),
                       "loaded_ms": h.get("loaded_ms"), "device": h.get("device"),
                       "model": h.get("model")}
        for seconds, method in CONFIGS:
            src = clip(seconds).resolve()
            runs = []
            for i in range(2):
                dst = (TMP / f"rvc_gpu_{seconds}_{method}_{i}.wav").resolve()
                if dst.exists():
                    dst.unlink()
                t = time.perf_counter()
                res = post(f"{base}/convert",
                           {"in": str(src), "out": str(dst), "f0_method": method})
                wall = (time.perf_counter() - t) * 1000.0
                runs.append({"ok": res.get("ok"), "ms": res.get("ms"),
                             "wall": round(wall, 1),
                             "bytes": dst.stat().st_size if dst.exists() else 0,
                             "err": res.get("error"), "hint": res.get("hint")})
            row = {"clip_s": seconds, "f0": method, "first": runs[0], "warm": runs[1],
                   "rtf": round((runs[1]["wall"] / 1000.0) / seconds, 3) if runs[1]["ok"] else None}
            out["rows"].append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        try:
            post(f"{base}/shutdown", {})
        except Exception:
            pass
        try:
            srv.wait(timeout=20)
        except Exception:
            srv.kill()

    ok = [r for r in out["rows"] if r["warm"]["ok"]]
    if ok:
        best = min(ok, key=lambda r: r["warm"]["wall"])
        out["verdict"] = {
            "fastest": {"clip_s": best["clip_s"], "f0": best["f0"],
                        "warm_ms": best["warm"]["wall"], "rtf": best["rtf"]},
            "warm_by_config": {f"{r['clip_s']}s/{r['f0']}": r["warm"]["wall"] for r in ok},
        }
    (ROOT / "tools" / "archcheck" / "results" / "rvc_gpu_bench.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nSUMMARY " + json.dumps(out.get("verdict", {}), ensure_ascii=False))


if __name__ == "__main__":
    main()
