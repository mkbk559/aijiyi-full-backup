"""One-hour passive monitor of QQ proactive delivery."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOG = DATA / "astrbot.out.log"
OUT = DATA / "proactive_audit_1h.jsonl"
SUMMARY = DATA / "proactive_audit_1h_summary.json"
PID = DATA / "proactive_audit_1h.pid"
DURATION_S = 3600
INTERVAL_S = 30


def decode_log(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gb18030", "replace")


def summarize(samples: list[dict]) -> dict:
    lines = "\n".join(str(item.get("new_log") or "") for item in samples).splitlines()
    return {
        "samples": len(samples),
        "connected_samples": sum(bool(item.get("connected")) for item in samples),
        "health_failures": sum(not item.get("health_ok") for item in samples),
        "send_successes": sum("AIJiYi proactive " in line and " -> " in line for line in lines),
        "send_failures": sum("AIJiYi proactive 发送失败" in line for line in lines),
        "generation_failures": sum("AIJiYi proactive 生成失败" in line for line in lines),
        "api_not_available": sum("ApiNotAvailable" in line for line in lines),
    }


def connected() -> bool:
    try:
        result = subprocess.run(["netstat", "-an"], capture_output=True,
                                text=True, timeout=5, check=False)
        return any("ESTABLISHED" in line.upper() and ":6199" in line
                   for line in result.stdout.splitlines())
    except (OSError, subprocess.TimeoutExpired):
        return False


def health_ok() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=5) as response:
            return bool(json.load(response).get("ok"))
    except (OSError, ValueError):
        return False


def new_log(offset: int) -> tuple[str, int]:
    try:
        with LOG.open("rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(offset if size >= offset else 0)
            raw = stream.read()
            return decode_log(raw), size
    except OSError:
        return "", offset


def worker() -> None:
    started = time.time()
    deadline = started + DURATION_S
    offset = LOG.stat().st_size if LOG.exists() else 0
    samples: list[dict] = []
    SUMMARY.write_text(json.dumps({"status": "running", "started_at": started,
                                   "deadline": deadline}, ensure_ascii=False), encoding="utf-8")
    while True:
        now = time.time()
        if now >= deadline:
            break
        lines, offset = new_log(offset)
        item = {"ts": now, "time": datetime.now().isoformat(timespec="seconds"),
                "connected": connected(), "health_ok": health_ok(), "new_log": lines}
        samples.append(item)
        safe = {key: value for key, value in item.items() if key != "new_log"}
        safe.update(summarize([item]))
        with OUT.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(safe, ensure_ascii=False) + "\n")
        time.sleep(min(INTERVAL_S, max(0, deadline - time.time())))
    result = summarize(samples)
    result.update({"status": "complete", "started_at": started,
                   "finished_at": time.time(), "duration_s": DURATION_S})
    SUMMARY.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    PID.unlink(missing_ok=True)


def start() -> None:
    if PID.exists():
        try:
            old_pid = int(PID.read_text("utf-8").strip())
            os.kill(old_pid, 0)
            if not SUMMARY.exists() or json.loads(SUMMARY.read_text("utf-8")).get("status") == "running":
                print(f"AUDIT_PID={old_pid}")
                return
        except (OSError, ValueError):
            pass
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__)), "--worker"],
        cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True,
    )
    PID.write_text(str(process.pid), encoding="utf-8")
    print(f"AUDIT_PID={process.pid}")
    print(f"SUMMARY={SUMMARY}")


if __name__ == "__main__":
    worker() if "--worker" in sys.argv else start()
