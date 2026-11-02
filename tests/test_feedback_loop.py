# -*- coding: utf-8 -*-
"""used_ids 反馈闭环的端到端证明（2026-11-03）。

链路：外部客户端（AstrBot 插件）→ `POST /v1/chat/completions` → 响应 `aiji.injection_id` + `aiji.used_ids`
→ `POST /feedback` → `store.apply_feedback` 落库。

这里不起真模型：用一个假的 OpenAI 兼容上游顶替，其余全是真服务（真 HTTP、真 SQLite）。
"""
import json
import sys
import threading
import unittest
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from aiji.hippocampus.memory import Memory
from aiji.hippocampus.store import FactDraft, Ident, add_fact
from aiji.right_hand.config import Route, Settings
from aiji.throat.server import make_server

TMP = Path(__file__).resolve().parent.parent / ".tmp"


def workdir(tag: str) -> Path:
    d = TMP / f"test_fb_{tag}_{uuid.uuid4().hex[:8]}"
    d.mkdir(mode=0o777, parents=True, exist_ok=True)
    return d


class _FakeUpstream(BaseHTTPRequestHandler):
    reply = "我记得你住杭州。"

    def log_message(self, *a):
        return

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": self.reply},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TestFeedbackLoop(unittest.TestCase):
    def setUp(self):
        self.up = ThreadingHTTPServer(("127.0.0.1", 0), _FakeUpstream)
        self.up.daemon_threads = True
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        port = self.up.server_address[1]
        self.memory = Memory(Settings(
            db_path=workdir("loop") / "f.db",
            routes=(Route(name="fake", base_url=f"http://127.0.0.1:{port}", model="fake",
                          api_key="k", timeout_s=5),),
            debounce_ms=0, humanize_min_ms=0, humanize_max_ms=0,
            typing_min_ms=0, typing_max_ms=0,
        ), engines="none")
        self.server = make_server(self.memory, "127.0.0.1", 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.ident = Ident("qq", "u_fb", "default")

    def tearDown(self):
        for srv in (self.server, self.up):
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
        try:
            self.memory.close()
        except Exception:
            pass

    # ---- helpers ----
    def _post(self, path: str, body: dict) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def _seed(self) -> list[int]:
        with self.memory.db.tx() as c:
            f1, _ = add_fact(c, self.ident, FactDraft("我", "居住地", "杭州", "用户住在杭州", 0.9))
            f2, _ = add_fact(c, self.ident, FactDraft("我", "偏好", "吃面", "用户喜欢吃面", 0.8))
        return [f1, f2]

    # ---- 用例 ----
    def test_response_carries_injection_and_used_ids(self):
        self._seed()
        res = self._post("/v1/chat/completions", {
            "model": "aiji",
            "messages": [{"role": "user", "content": "我住哪？"}],
            "metadata": {"platform": "qq", "platform_user_id": "u_fb", "scope": "default"},
        })
        meta = res.get("aiji") or {}
        self.assertGreater(meta.get("injection_id") or 0, 0, "响应必须带 injection_id")
        self.assertTrue(meta.get("used_ids"), "响应必须带 used_ids")
        self.assertTrue(all(isinstance(i, int) and i > 0 for i in meta["used_ids"]))

    def test_feedback_updates_store(self):
        self._seed()
        res = self._post("/v1/chat/completions", {
            "model": "aiji",
            "messages": [{"role": "user", "content": "我住哪？"}],
            "metadata": {"platform": "qq", "platform_user_id": "u_fb", "scope": "default"},
        })
        meta = res["aiji"]
        used = [int(i) for i in meta["used_ids"]]
        before = self.memory.db.read(
            "SELECT COALESCE(SUM(hit_count),0) FROM fact WHERE id IN (%s)" % ",".join("?" * len(used)),
            tuple(used))[0][0]
        out = self._post("/feedback", {
            "platform": "qq", "platform_user_id": "u_fb", "scope": "default",
            "injection_id": meta["injection_id"], "used_ids": meta["used_ids"], "helpful": True,
        })
        self.assertGreaterEqual(int(out.get("updated") or 0), 1, out)
        after = self.memory.db.read(
            "SELECT COALESCE(SUM(hit_count),0) FROM fact WHERE id IN (%s)" % ",".join("?" * len(used)),
            tuple(used))[0][0]
        fb = self.memory.db.read(
            "SELECT feedback FROM injection WHERE id=?", (meta["injection_id"],))[0][0]
        self.assertNotIn(fb, (None, ""), "注入记录应写上反馈")
        self.assertGreater(after, before, "被引用的fact应当 hit_count+1")

    def test_feedback_on_unused_ids_is_safe(self):
        self._seed()
        res = self._post("/v1/chat/completions", {
            "model": "aiji",
            "messages": [{"role": "user", "content": "我住哪？"}],
            "metadata": {"platform": "qq", "platform_user_id": "u_fb", "scope": "default"},
        })
        out = self._post("/feedback", {
            "platform": "qq", "platform_user_id": "u_fb", "scope": "default",
            "injection_id": res["aiji"]["injection_id"], "used_ids": [999999], "helpful": True,
        })
        self.assertIn("updated", out)


if __name__ == "__main__":
    unittest.main()
