"""线上服务探针：`/api/mcp` 与研究循环、`/api/stickers` 与更新循环。"""
from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8787"

with urllib.request.urlopen(BASE + "/api/mcp", timeout=10) as response:
    mcp = json.loads(response.read())
    print("/api/mcp", response.status,
          "research_enabled=", mcp.get("research_enabled"),
          "running=", (mcp.get("research") or {}).get("running"),
          "servers=", len(mcp["registry"]["servers"]))

with urllib.request.urlopen(BASE + "/api/stickers", timeout=10) as response:
    stickers = json.loads(response.read())
    service = stickers.get("service") or {}
    print("/api/stickers", response.status,
          "count=", service.get("count"),
          "auto_update_capable=", service.get("auto_update_capable"),
          "loop_running=", bool((stickers.get("loop") or {}).get("running")))
