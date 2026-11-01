"""第二条独立取证路径：走真实 HTTP（/recall），端到端计时。

和 bench_recall.py 的区别：那边是进程内直接调 Memory.recall（省掉 socket 与序列化），
这边起服务、4 个客户端并发打 HTTP，计时包含网络栈和 JSON 编解码。
两条路径都以"p99 <= budget"作为退出码，因此 verify_pair 的退出码一致即互相印证。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiji import Memory  # noqa: E402
from aiji.config import Settings  # noqa: E402
from aiji.server import make_server  # noqa: E402
from bench_recall import ident, pct, seed  # noqa: E402


def run(args) -> int:
    root = Path(__file__).resolve().parent.parent / ".tmp"
    root.mkdir(mode=0o777, exist_ok=True)
    db = root / "bench_http.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    queries = seed(db, args.facts, seed_val=args.seed)

    mem = Memory(Settings(db_path=db, budget_ms=args.budget_ms, routes=()))
    srv = make_server(mem, "127.0.0.1", 0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    who = ident(0)
    body_base = {"platform": who.platform, "platform_user_id": who.platform_user_id, "scope": who.scope}

    def recall(q: str) -> dict:
        payload = dict(body_base, query=q, session="bench", budget_ms=args.budget_ms)
        req = urllib.request.Request(
            base + "/recall",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    recall("杭州")  # 预热连接与 FTS 缓存

    lat: list[float] = []
    errors = 0
    lock = threading.Lock()

    def client(n: int) -> None:
        nonlocal errors
        local: list[float] = []
        for _ in range(n * args.rounds):
            for q in queries:
                try:
                    res = recall(q)
                    local.append(float(res["took_ms"]))
                    if res.get("degraded"):
                        with lock:
                            errors += 1
                except Exception:  # noqa: BLE001
                    with lock:
                        errors += 1
        with lock:
            lat.extend(local)

    threads = [threading.Thread(target=client, args=(1,), daemon=True) for _ in range(args.clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    mem.close()
    srv.shutdown()

    payload = {
        "path": "http",
        "clients": args.clients,
        "n": len(lat),
        "p50": round(pct(lat, 50), 3),
        "p95": round(pct(lat, 95), 3),
        "p99": round(pct(lat, 99), 3),
        "max": round(max(lat), 3),
        "mean": round(statistics.fmean(lat), 3),
        "budget_ms": args.budget_ms,
        "degraded": errors,
        "ok": pct(lat, 99) <= args.budget_ms and errors == 0 and len(lat) > 0,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", type=int, default=5000)
    ap.add_argument("--budget-ms", type=int, default=100)
    ap.add_argument("--clients", type=int, default=4)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", default=None)
    raise SystemExit(run(ap.parse_args()))
