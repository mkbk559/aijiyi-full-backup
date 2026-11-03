"""线上服务 A/B：重启到当前代码前后，空白消息的行为。

用法：python tools/live_probe_turns.py before|after
它只做三件事：读 /health、POST 一条纯空白、POST 一条正常文本。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8787"
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, payload: dict | None = None) -> tuple[int, str]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with _opener.open(request, timeout=15) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "probe"
    print(f"== {label} ==")

    status, body = call("GET", "/health")
    version = "?"
    if status == 200:
        try:
            version = json.loads(body).get("version", "?")
        except ValueError:
            pass
    print(f"GET  /health            -> {status} version={version}")

    status, body = call("POST", "/turns", {"content": "   ", "platform": "local"})
    print(f"POST /turns  (空白)     -> {status} {body[:160]}")

    status, body = call("POST", "/turns",
                        {"content": "线上探针：这句是真的", "platform": "local"})
    print(f"POST /turns  (正常)     -> {status} {body[:160]}")

    status, body = call("GET", "/api/operations/services")
    keys = ""
    if status == 200:
        try:
            payload = json.loads(body)
            keys = (f"ready={payload.get('ready')}/{payload.get('total')} "
                    f"actions_available={payload.get('actions_available')}")
        except ValueError:
            keys = body[:120]
    print(f"GET  /api/operations/services -> {status} {keys}")

    status, body = call("GET", "/api/capabilities")
    print(f"GET  /api/capabilities  -> {status} bytes={len(body)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
