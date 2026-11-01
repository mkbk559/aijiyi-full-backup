"""一次性探针：真实发一次最小请求，看两个反代的**响应头**里有没有积分/配额字段。

这是"反代积分"最省事的来源（正常调用顺手就带回余额），所以值得实测一次。
只发 1 个 token 的最小请求；不写库、不改配置。
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / ".tmp" / "probe_gw"
OUT.mkdir(parents=True, exist_ok=True)

CASES = [
    ("workbuddy", "http://127.0.0.1:8788", "hy4-preview"),
    ("agent2api", "http://127.0.0.1:3065/v1", "sn-sensenova-6-8-flash"),
]

report = {}
for name, base, model in CASES:
    body = json.dumps({
        "model": model, "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1, "temperature": 0.0, "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read(300).decode("utf-8", "replace")
            headers = dict(resp.headers or {})
        keys = {k: v for k, v in headers.items()
                if any(t in k.lower() for t in ("credit", "quota", "ratelimit", "remain", "usage"))}
        report[name] = {"status": 200, "quota_headers": keys,
                        "all_header_keys": sorted(headers.keys())[:25],
                        "body": payload[:160]}
    except Exception as exc:  # noqa: BLE001
        report[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}

(OUT / "headers.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
