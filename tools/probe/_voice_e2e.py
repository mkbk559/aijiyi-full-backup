"""只读探测：定制语音链路（/v1/voices 就绪状态 + 真实合成一段）。"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
import wave
from pathlib import Path

BASE = "http://127.0.0.1:8787"
OUT = Path(r"D:\AIJiYi\.tmp\voice-e2e.wav")


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def post(path: str, body: dict, timeout: float = 300):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type"), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type"), exc.read()


print("== /health ==")
status, health = get("/health")
print("status", status, "ok=", health.get("ok"), "pending=", health.get("pending"))

print("\n== /v1/voices ==")
status, voices = get("/v1/voices")
print("status", status, "active=", voices.get("active"), "ffmpeg=", voices.get("ffmpeg"))
for p in voices.get("profiles", []):
    print(f"  {p.get('id'):14s} ready={p.get('ready')} provider={p.get('provider')} "
          f"postprocess={'yes' if p.get('postprocess') else 'no'}")

print("\n== POST /v1/audio/speech (dafeiyu) ==")
t0 = time.time()
status, ctype, body = post("/v1/audio/speech", {
    "input": "你好呀，我是大肥鱼，今天也在深海里待着。",
    "voice": "dafeiyu",
    "format": "wav",
})
took = time.time() - t0
print(f"status={status} ctype={ctype} bytes={len(body)} took={took:.1f}s")
if status == 200 and body:
    OUT.write_bytes(body)
    try:
        with wave.open(str(OUT), "rb") as w:
            print(f"wav: rate={w.getframerate()} ch={w.getnchannels()} "
                  f"frames={w.getnframes()} seconds={w.getnframes() / w.getframerate():.2f}")
        print("saved:", OUT, OUT.stat().st_size, "bytes")
    except Exception as exc:  # noqa: BLE001
        print("不是标准 wav:", type(exc).__name__, exc, "head=", body[:12])
else:
    print("body head:", body[:300])
