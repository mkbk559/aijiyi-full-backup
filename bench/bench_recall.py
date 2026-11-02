"""召回延迟压测：证明"主链路 100ms 硬超时"不是口号。

两阶段：
  A. 静默期：N 条事实 + 200 次召回，看纯召回延迟分布；
  B. 压力期：4 个线程持续提交写入（每次触发抽取+落库），同时测召回，看写是否拖慢读。

--platforms > 1 时事实分散到多个平台身份下，用来验证"多租户共用一套表"不会互相拖慢。
判定：任一 p99 > budget_ms 或出现 degraded 超时，退出码非 0。
"""

from __future__ import annotations

import json
import random
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiji import Memory  # noqa: E402
from aiji.right_hand.config import Settings  # noqa: E402
from aiji.hippocampus.store import FactDraft, Ident, add_fact  # noqa: E402

CITIES = ["杭州", "北京", "上海", "深圳", "成都", "武汉", "西安", "南京", "苏州", "广州"]
JOBS = ["后端工程师", "产品经理", "设计师", "数据分析师", "运维工程师", "算法工程师"]
HOBBIES = ["爬山", "摄影", "跑步", "围棋", "做饭", "潜水", "骑行", "读书"]
NAMES = ["李雷", "韩梅梅", "张伟", "王芳", "刘洋", "陈静", "赵强", "周敏", "吴磊", "徐丽"]
PLATFORMS = ["wechat", "douyin", "telegram", "cli"]


def ident(i: int = 0) -> Ident:
    return Ident(PLATFORMS[i % len(PLATFORMS)], f"u{i}", "default")


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    idx = min(len(s) - 1, max(0, int(round(p / 100 * (len(s) - 1)))))
    return s[idx]


def seed(path: Path, n: int, platforms: int = 1, seed_val: int = 7) -> list[str]:
    rnd = random.Random(seed_val)
    m = Memory(Settings(db_path=path, budget_ms=100, routes=()))
    now = time.time()
    with m.db.tx() as c:
        for i in range(n):
            who = ident(i % max(1, platforms))
            name = NAMES[i % len(NAMES)]
            kind = i % 5
            if kind == 0:
                city = CITIES[i % len(CITIES)]
                d = FactDraft("user", "居住地", city, f"{name}住在{city}", 0.9)
            elif kind == 1:
                job = JOBS[i % len(JOBS)]
                d = FactDraft("user", "职业", job, f"{name}是{job}", 0.85)
            elif kind == 2:
                hobby = HOBBIES[i % len(HOBBIES)]
                d = FactDraft("user", "偏好", hobby, f"{name}喜欢{hobby}", 0.8)
            elif kind == 3:
                stack = rnd.choice(["Python", "Rust", "Go", "TypeScript"])
                d = FactDraft(f"项目{i}", "技术栈", stack, f"项目{i}的技术栈是{stack}", 0.75)
            else:
                rel = rnd.choice(["同事", "同学", "客户"])
                d = FactDraft(f"联系人{i}", "关系", rel, f"联系人{i}是{name}的{rel}", 0.7)
            add_fact(c, who, d, source="bench", source_kind="bench", now=now - rnd.random() * 60 * 86400)
    queries = [f"{rnd.choice(CITIES)}", f"{rnd.choice(NAMES)}住在哪", f"{rnd.choice(HOBBIES)}",
               "项目12的技术栈", f"{rnd.choice(JOBS)}", "完全不存在的记忆片段xyz"]
    m.close()
    return queries


def measure(m: Memory, queries: list[str], who: Ident, n_rounds: int, budget_ms: int) -> dict[str, Any]:
    lat: list[float] = []
    degraded = 0
    over = 0
    for _ in range(n_rounds):
        for q in queries:
            res = m.recall(q, who, session="bench", budget_ms=budget_ms)
            lat.append(res.took_ms)
            if res.degraded:
                degraded += 1
            if res.took_ms > budget_ms:
                over += 1
    return {
        "n": len(lat),
        "p50": round(pct(lat, 50), 3),
        "p95": round(pct(lat, 95), 3),
        "p99": round(pct(lat, 99), 3),
        "max": round(max(lat), 3),
        "mean": round(statistics.fmean(lat), 3),
        "degraded": degraded,
        "over_budget": over,
    }


def run(args) -> int:
    budget = args.budget_ms
    root = Path(__file__).resolve().parent.parent / ".tmp"
    root.mkdir(mode=0o777, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root, ignore_cleanup_errors=True) as td:
        db = Path(td) / "bench.db"
        t0 = time.perf_counter()
        queries = seed(db, args.facts, getattr(args, "platforms", 1))
        seed_s = time.perf_counter() - t0

        m = Memory(Settings(db_path=db, budget_ms=budget, routes=()))
        who = ident(0)
        quiet = measure(m, queries, who, max(1, args.queries // len(queries)), budget)

        stop = threading.Event()
        submit_lat: list[float] = []
        written = 0
        lock = threading.Lock()

        def writer(i: int) -> None:
            nonlocal written
            k = 0
            while not stop.is_set():
                txt = f"我叫测试员{i}，住在{CITIES[k % len(CITIES)]}，喜欢{HOBBIES[k % len(HOBBIES)]}"
                t = time.perf_counter()
                m.turn(ident(i % 4), f"live{i}", "user", txt)
                with lock:
                    submit_lat.append((time.perf_counter() - t) * 1000)
                    written += 1
                k += 1
                time.sleep(0.01)

        threads = [threading.Thread(target=writer, args=(i,), daemon=True) for i in range(args.workers)]
        for t in threads:
            t.start()
        time.sleep(0.5)
        busy = measure(m, queries, who, max(1, args.queries // len(queries)), budget)
        stop.set()
        for t in threads:
            t.join(timeout=5)
        flushed = m.flush(30)
        stats = m.stats()
        m.close()

    payload = {
        "facts": args.facts,
        "platforms": getattr(args, "platforms", 1),
        "seed_s": round(seed_s, 2),
        "budget_ms": budget,
        "quiet": quiet,
        "under_write": busy,
        "submit_p99_ms": round(pct(submit_lat, 99), 3),
        "submit_max_ms": round(max(submit_lat), 3),
        "writes_submitted": written,
        "flushed": flushed,
        "store": stats["store"],
        "ok": (
            quiet["p99"] <= budget and busy["p99"] <= budget
            and quiet["over_budget"] == 0 and busy["over_budget"] == 0 and flushed
        ),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        Path(args.json).write_text(text, "utf-8")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", type=int, default=5000)
    ap.add_argument("--queries", type=int, default=200)
    ap.add_argument("--budget-ms", type=int, default=100)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--platforms", type=int, default=1)
    ap.add_argument("--json", default=None)
    raise SystemExit(run(ap.parse_args()))
