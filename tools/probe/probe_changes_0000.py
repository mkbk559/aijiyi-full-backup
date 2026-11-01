"""针对性验证：反思降载 / QQ 发送节流 / 控制台 11 个入口的数据形状。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.probe._harness import (  # noqa: E402
    Probe, cleanup, http_call, make_memory, quiet_env, start_server, workdir,
)

from aiji import store  # noqa: E402
from aiji.db import DB  # noqa: E402
from aiji.memory import SendThrottled  # noqa: E402
from aiji.reflect import Reflective  # noqa: E402
from aiji.store import Ident  # noqa: E402

quiet_env()
probe = Probe("changes-verify")
base = workdir("changes")
IDENT = Ident("qq", "g1", "group:1")


class RecordingPool:
    _states = [object()]

    def __init__(self, text: str = "{}") -> None:
        self.text = text
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        from types import SimpleNamespace
        return SimpleNamespace(text=self.text, route="rec", latency_ms=1.0)

    def stats(self):
        return []


def turns(db: DB, role: str, n: int, text: str = "哈哈哈") -> None:
    with db.tx() as c:
        for i in range(n):
            store.add_turn(c, IDENT, "s1", role, f"{text}{i}")


try:
    # ================= 1. 反思：降载参数 + 前置闸 =================
    d1 = base / "r1"
    d1.mkdir(mode=0o777)
    db = DB(d1 / "r.db")
    turns(db, "user", 40)                      # 只有用户发言
    pool = RecordingPool(json.dumps({"traits": [], "summary": ""}))
    ref = Reflective(db, pool, every_turns=30, min_interval_s=0)
    out = ref.tick(IDENT, "s1")
    probe.case("反思：只有用户发言时跳过（前置闸生效）",
               out is None and ref.stats["skipped"] >= 1 and not pool.calls,
               f"out={out} skipped={ref.stats['skipped']} calls={len(pool.calls)}")

    turns(db, "assistant", 1, "回一句")
    payload = json.dumps({"traits": [{"name": "爱吐槽", "value": "常用反问", "evidence": 2}],
                          "summary": "s"}, ensure_ascii=False)
    pool.text = payload
    out = ref.tick(IDENT, "s1")
    call = pool.calls[0] if pool.calls else {}
    probe.case("反思：每次只调一次模型", len(pool.calls) == 1, f"calls={len(pool.calls)}")
    probe.case("反思：timeout_s=60", call.get("timeout_s") == 60.0, f"{call.get('timeout_s')}")
    probe.case("反思：走后台额度（background=True）", call.get("background") is True,
               f"{call.get('background')}")
    probe.case("反思：单路由只试 1 次", call.get("attempts_per_route") == 1,
               f"{call.get('attempts_per_route')}")
    probe.case("反思：有 assistant 发言后产出 trait", out is not None and bool(out["deltas"]),
               f"out={bool(out)}")

    # 空输出不应计入 runs / 不应写库
    pool.calls.clear()
    pool.text = "{}"
    before = len(db.read("SELECT id FROM reflection"))
    out2 = ref.tick(IDENT, "s1", force=True)
    after = len(db.read("SELECT id FROM reflection"))
    probe.case("反思：空输出不写 reflection 行", out2 is None and after == before,
               f"out={out2} rows={after - before}")

    # 失败一次不重试
    class Boom(RecordingPool):
        def __init__(self):
            super().__init__()
            self.n = 0

        def chat(self, messages, **kwargs):
            self.n += 1
            raise RuntimeError("gateway down")

    boom = Boom()
    turns(db, "assistant", 1, "再回一句")
    ref2 = Reflective(db, boom, every_turns=1, min_interval_s=0)
    ref2.tick(IDENT, "s1", force=True)
    probe.case("反思：上游报错只调一次（不三连击）", boom.n == 1, f"calls={boom.n}")
    db.close()

    # ================= 2. QQ 群发送节流（4 条/分钟） =================
    mem, _ = make_memory(base / "throttle")
    try:
        sent = []
        mem.onebot_client.send = lambda event, text: (sent.append(text) or True)
        group = Ident("qq", "u1", "group:42")
        codes = []
        for i in range(5):
            try:
                mem._heart_send(group, f"第{i}条", {})
                codes.append("ok")
            except SendThrottled as exc:
                codes.append(f"throttled@{getattr(exc, 'retry_at', 0):.0f}")
        probe.case("群聊：60s 内第 5 条被客户端节流",
                   codes[:4] == ["ok"] * 4 and codes[4].startswith("throttled"),
                   f"codes={codes}")
        probe.case("群聊：节流发生在真正发送之前", len(sent) == 4, f"sent={len(sent)}")
        private = Ident("qq", "u1", "default")
        ok_private = 0
        for i in range(6):
            if mem._heart_send(private, f"p{i}", {}):
                ok_private += 1
        probe.case("私聊不参与群限流", ok_private == 6, f"ok={ok_private}")
    finally:
        mem.close()

    # ================= 3. 控制台 11 个入口的数据形状 =================
    mem2, _ = make_memory(base / "console")
    mem2.remember("我叫李雷，住在杭州", ("wechat", "u1"), session="s1")
    mem2.flush(20)
    server, url = start_server(mem2)
    q = "platform=wechat&platform_user_id=u1&scope=default"
    checks = [
        ("/api/memory-graph", ("nodes", "edges")),
        ("/api/self", ("floor", "traits")),
        ("/api/life", ("snapshot", "context")),
        ("/api/accounts", ("items",)),
        ("/api/models", ("routes", "active", "total", "cooldown")),
        ("/api/scheduler", ("outbox", "send_queue", "write_queue")),
        ("/api/actions/pending", ("items", "stats")),
        ("/api/actions/stats", ("stats", "root")),
        ("/api/proactive", ("items",)),
        ("/api/capabilities", ("ocr", "asr", "settings")),
    ]
    try:
        for path, keys in checks:
            status, payload, text = http_call(url, "GET", f"{path}?{q}" if "?" not in path else path)
            body = payload if isinstance(payload, dict) else {}
            missing = [k for k in keys if k not in body]
            probe.case(f"控制台 {path} 字段齐全", status == 200 and not missing,
                       f"status={status} missing={missing}")
        status, payload, _ = http_call(url, "GET", f"/api/life?{q}")
        snap = (payload or {}).get("snapshot") or {}
        probe.case("/api/life 含激素与人格",
                   {"hormones", "personality"} <= set(snap), f"keys={sorted(snap)}")
        # 工作区视图（默认关闭 → 403，开启后可用）
        status, payload, _ = http_call(url, "GET", "/api/fs?path=")
        probe.case("控制台 工作区默认关闭", status == 403, f"status={status}")
    finally:
        server.shutdown()
        server.server_close()
        mem2.close()
finally:
    cleanup(base)

raise SystemExit(probe.finish())
