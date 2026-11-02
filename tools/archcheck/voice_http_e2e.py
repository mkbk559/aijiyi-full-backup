"""真机 HTTP 验收：语音相关接口是不是真的能出声音（2026-11-03）。

之前只测过 `/v1/voices` 的 200+JSON，没验过**真合成**。这里在同一个进程里：
起 `python -m aiji serve`（或已起的服务）→ GET /v1/voices → POST /v1/audio/speech
→ 校验返回音频字节数/时长 → 关服务。全部走 127.0.0.1（绕开全局代理）。

用法：python tools/archcheck/voice_http_e2e.py
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
PORT = 18778
BASE = f"http://127.0.0.1:{PORT}"
OUT = ROOT / ".tmp" / "voice_http"


def op():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get_json(path: str, timeout: float = 10.0):
    with op().open(f"{BASE}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode())


def post_raw(path: str, payload: dict, timeout: float = 300.0):
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with op().open(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read()


def wav_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as w:
            return round(w.getnframes() / (w.getframerate() or 1), 2)
    except Exception:
        return None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    py = Path(sys.executable)
    proc = subprocess.Popen(
        [str(py), "-m", "aiji.senses.cli", "serve", "--port", str(PORT)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    report: dict = {"base": BASE, "steps": []}
    try:
        up = False
        for _ in range(60):
            try:
                # 用 /v1/voices 当存活探针（服务没有 /api/health，别拿 404 当没起来）
                get_json("/v1/voices", timeout=3)
                up = True
                break
            except Exception:
                if proc.poll() is not None:
                    break
                time.sleep(1.0)
        report["server_up"] = up
        if not up:
            report["server_rc"] = proc.poll()
            raise SystemExit(json.dumps(report, ensure_ascii=False, indent=2))

        voices = get_json("/v1/voices")
        items = (voices.get("profiles") or voices.get("voices")
                 or voices.get("items") or [])
        report["steps"].append({"step": "GET /v1/voices", "ok": True,
                                "count": len(items),
                                "ids": [v.get("id") for v in items][:6],
                                "last_skipped": voices.get("last_skipped")})

        text = "真机验收：这条语音是走 TTS 再换声出来的。"
        t = time.perf_counter()
        code, ctype, body = post_raw("/v1/audio/speech",
                                     {"input": text, "voice": "dafeiyu"})
        elapsed = round((time.perf_counter() - t) * 1000.0, 1)
        target = OUT / "speech.wav"
        target.write_bytes(body)
        report["steps"].append({
            "step": "POST /v1/audio/speech", "status": code, "ctype": ctype,
            "ms": elapsed, "bytes": len(body),
            "seconds": wav_seconds(target),
            "head": body[:16].hex() if body else "",
        })
        voices2 = get_json("/v1/voices")
        report["steps"].append({"step": "GET /v1/voices (after)",
                                "last_skipped": voices2.get("last_skipped")})
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
    (ROOT / "tools" / "archcheck" / "results" / "voice_http_e2e.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2)[:2200])


if __name__ == "__main__":
    main()
