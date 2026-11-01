# -*- coding: utf-8 -*-
"""真机冒烟：验证 pool 的真实 HTTP 路径。

2026-10-29 升级：优先走本机两个反代网关（workbuddy 8788 / agent2api 3065，
无出网 SSL 问题），pollinations 作为远端兜底对照。失败就如实打印——
用来区分"代码对"和"网络通"。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from tools.load_keys import bridge  # noqa: E402
from aiji.config import Route  # noqa: E402
from aiji.extract import build_messages, parse  # noqa: E402
from aiji.pool import ModelPool  # noqa: E402

TEXT = "我叫李雷，住在杭州，在做后端开发。"

bridge()  # refs → env（不存在的才设）

GATEWAYS = [
    ("workbuddy", "http://127.0.0.1:8788/v1", "glm-5.3-flash", "WORKBUDDY_HUB_KEY", 30),
    ("agent2api", "http://127.0.0.1:3065/v1", "glm-5.3-flash", "AGENT2API_KEY", 30),
    ("pollinations", "https://text.pollinations.ai/openai", "openai", "POLLINATIONS_API_KEY", 25),
]

results = []
for name, base, model, key_env, timeout in GATEWAYS:
    import os
    key = os.environ.get(key_env, "")
    if not key:
        results.append({"gateway": name, "ok": False, "error": f"{key_env} 未设，跳过"})
        continue
    route = Route(name=name, base_url=base, model=model, api_key=key, timeout_s=timeout)
    pool = ModelPool((route,))
    t0 = time.perf_counter()
    try:
        res = pool.chat(build_messages(TEXT, "smoke"))
        ext = parse(res.text)
        results.append({
            "gateway": name, "ok": True, "route": res.route,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "raw_head": res.text[:120],
            "facts": [f"{f.predicate}={f.object}" for f in ext.facts],
            "profile": ext.profile,
        })
    except Exception as exc:  # noqa: BLE001
        results.append({"gateway": name, "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "latency_ms": round((time.perf_counter() - t0) * 1000, 1)})

n_ok = sum(1 for r in results if r.get("ok"))
print(json.dumps({"ok": n_ok, "total": len(results), "results": results},
                 ensure_ascii=False, indent=2))
sys.exit(0 if n_ok else 1)
