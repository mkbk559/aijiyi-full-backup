"""注入链路压测：软引导块必须够快、够短，且**永不**生成回复或改人格。

判定（退出码非 0 即失败）：
  - p99 <= --budget-ms（默认 50；注入不是主链路召回，但它调召回，别拖累）
  - 任何一次超出字符预算 => 失败
  - 任何一次 persona_touched=True 或 generated_reply 不为 None => 失败
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiji import Memory  # noqa: E402
from aiji.right_hand.config import Settings  # noqa: E402
from bench_recall import ident, pct, seed  # noqa: E402


def run(args) -> int:
    root = Path(__file__).resolve().parent.parent / ".tmp"
    root.mkdir(mode=0o777, exist_ok=True)
    db = root / "bench_inject.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)

    queries = seed(db, args.facts, seed_val=args.seed)
    m = Memory(Settings(db_path=db, routes=(), inject_chars=args.max_chars,
                        inject_items=args.max_items))
    who = ident(0)

    lat: list[float] = []
    chars: list[int] = []
    items: list[int] = []
    over_budget = 0
    violations = 0
    empty = 0
    lock = threading.Lock()

    # 一半在写压之下跑：注入要跟 writer 抢 SQLite 写锁（写注入日志）
    stop = threading.Event()

    def writer() -> None:
        k = 0
        while not stop.is_set():
            m.turn(ident(k % 4), f"live{k}", "user", f"我叫测试员{k}，住在杭州，喜欢爬山")
            k += 1
            time.sleep(0.01)

    wt = threading.Thread(target=writer, daemon=True)
    wt.start()
    time.sleep(0.3)

    for _ in range(args.rounds):
        for q in queries:
            inj = m.inject(q, who, session="bench")
            local = (inj.took_ms, len(inj.text), len(inj.items), inj.persona_touched,
                     inj.generated_reply, len(inj.text) > args.max_chars, not inj.text)
            with lock:
                lat.append(local[0])
                chars.append(local[1])
                items.append(local[2])
                if local[3] or local[4] is not None:
                    violations += 1
                if local[5]:
                    over_budget += 1
                if local[6]:
                    empty += 1

    stop.set()
    wt.join(timeout=5)
    m.flush(30)
    stats = m.stats()
    m.close()

    payload = {
        "path": "inject",
        "n": len(lat),
        "p50": round(pct(lat, 50), 3),
        "p95": round(pct(lat, 95), 3),
        "p99": round(pct(lat, 99), 3),
        "max": round(max(lat), 3),
        "mean": round(statistics.fmean(lat), 3),
        "budget_ms": args.budget_ms,
        "max_chars": args.max_chars,
        "avg_chars": round(statistics.fmean(chars), 1),
        "avg_items": round(statistics.fmean(items), 1),
        "over_char_budget": over_budget,
        "persona_violations": violations,
        "empty_blocks": empty,
        "injections_logged": stats["injection"]["injections"],
        "ok": (pct(lat, 99) <= args.budget_ms and over_budget == 0
               and violations == 0 and len(lat) > 0),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", type=int, default=5000)
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--budget-ms", type=int, default=50)
    ap.add_argument("--max-chars", type=int, default=700)
    ap.add_argument("--max-items", type=int, default=12)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", default=None)
    raise SystemExit(run(ap.parse_args()))
