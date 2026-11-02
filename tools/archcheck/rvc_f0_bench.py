"""RVC 延迟归因：换 F0 方法、换素材长度，看时间花在哪（2026-11-03）。

问题："这引擎延迟太高，要不要换引擎？"
先别换——先量清楚 2.9 s 花在哪。一台上 CPU 跑 RVC，最贵的通常是 **F0 提取**，
而 F0 有三种算法（rmvpe 最准最慢 / fcpe 折中 / pm=parselmouth 最快最糙），
换算法的代价远小于换引擎。

做法：起一次常驻服务（模型只载一次），对 (素材长度 × F0 方法) 的每个组合跑 2 次，
取第 2 次（第 1 次含各算法首次初始化），输出 JSON 表格 + 结论。

用法：python tools/archcheck/rvc_f0_bench.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / "voice-engine" / ".venv" / "Scripts" / "python.exe"
TMP = ROOT / ".tmp"
PORT = 18602
CLIPS = [1.5, 6.0]
METHODS = ["rmvpe", "fcpe", "pm"]


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(url: str, timeout: float = 10.0) -> dict:
    with opener().open(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post(url: str, payload: dict, timeout: float = 1800.0) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with opener().open(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except Exception:
            return {"ok": False, "error": f"HTTP {exc.code}: {body[:300]}"}


def make_clip(seconds: float) -> Path:
    out = TMP / f"rvc_clip_{seconds}s.wav"
    subprocess.run([sys.executable, str(ROOT / "tools" / "archcheck" / "make_bench_wav.py"),
                    str(out), str(seconds)], check=True, capture_output=True)
    return out


def main() -> None:
    clips = {s: make_clip(s) for s in CLIPS}
    srv = subprocess.Popen(
        [str(PY), str(ROOT / "tools" / "rvc_server.py"), "--port", str(PORT)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(ROOT),
    )
    out: dict = {"clips": {str(k): v.strftime if False else str(v) for k, v in clips.items()},
                 "server_port": PORT, "rows": []}
    base = f"http://127.0.0.1:{PORT}"
    try:
        t0 = time.perf_counter()
        for _ in range(180):
            try:
                h = get(f"{base}/health")
                break
            except Exception:
                if srv.poll() is not None:
                    err = (srv.stderr.read() or b"").decode("utf-8", "replace")[-800:]
                    raise SystemExit("服务没起来：\n" + err)
                time.sleep(1.0)
        else:
            raise SystemExit("服务 /health 超时")
        out["boot_s"] = round(time.perf_counter() - t0, 2)
        out["loaded_ms"] = h.get("loaded_ms")

        for seconds, path in clips.items():
            for method in METHODS:
                runs = []
                for i in range(2):
                    dst = TMP / f"rvc_f0_{seconds}s_{method}_{i}.wav"
                    if dst.exists():
                        dst.unlink()
                    t = time.perf_counter()
                    res = post(f"{base}/convert", {
                        "in": str(path.resolve()), "out": str(dst.resolve()),
                        "f0_method": method,
                    })
                    wall = (time.perf_counter() - t) * 1000.0
                    runs.append({"ok": res.get("ok"), "ms": res.get("ms"),
                                 "wall": round(wall, 1),
                                 "bytes": dst.stat().st_size if dst.exists() else 0,
                                 "err": res.get("error")})
                row = {
                    "clip_s": seconds,
                    "f0": method,
                    "first": runs[0],
                    "warm": runs[1],
                    "rtf": round((runs[1]["wall"] / 1000.0) / seconds, 3) if runs[1]["ok"] else None,
                }
                out["rows"].append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        try:
            post(f"{base}/shutdown", {})
        except Exception:
            pass
        try:
            srv.wait(timeout=15)
        except Exception:
            srv.kill()

    ok_rows = [r for r in out["rows"] if r["warm"]["ok"]]
    if ok_rows:
        best = min(ok_rows, key=lambda r: r["warm"]["wall"])
        out["verdict"] = {
            "fastest": {"clip_s": best["clip_s"], "f0": best["f0"],
                        "warm_ms": best["warm"]["wall"], "rtf": best["rtf"]},
            "by_method_at_6s": {
                r["f0"]: r["warm"]["wall"] for r in ok_rows if r["clip_s"] == 6.0},
        }
    print("\nSUMMARY " + json.dumps(out.get("verdict", {}), ensure_ascii=False))
    Path(ROOT / "tools" / "archcheck" / "results" / "rvc_f0_bench.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
