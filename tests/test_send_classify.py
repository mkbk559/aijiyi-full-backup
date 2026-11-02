# -*- coding: utf-8 -*-
"""心脏层真发货 + 发送失败分类（2026-11-03）。

两段：
  A. 纯分类表：`aiji/heart/sender.py::classify` 对 HTTP/retcode 的判决，逐条钉死。
  B. 真链路：起一个假 OneBot HTTP 服务，让 `Memory` 的常驻 `Dispatcher` 线程把
     `send_queue` 里的行真发出去，验证 sent / dead / pending 三种落点。

为什么值得单测：改前所有失败都走同一档指数退避，`retcode=1401`(token 失效) 这种
确定性失败要白试到 MAX_ATTEMPTS 才进 dead，期间还会堵住后面的真消息。
"""
import json
import sys
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiji.heart import sender as classify_mod
from aiji.heart.heart import OutboxEvent, SendFatal, SendThrottled
from aiji.hippocampus.memory import Memory
from aiji.hippocampus.store import Ident
from aiji.right_hand.config import Settings
from aiji.throat.bridge.onebot import SendOutcome

TMP = Path(__file__).resolve().parent.parent / ".tmp"


def workdir(tag: str) -> Path:
    d = TMP / f"test_send_{tag}_{uuid.uuid4().hex[:8]}"
    d.mkdir(mode=0o777, parents=True, exist_ok=True)
    return d


class TestClassifyTable(unittest.TestCase):
    def test_success(self):
        self.assertEqual(classify_mod.classify(SendOutcome(ok=True, retcode=0, http_status=200)),
                         classify_mod.OK)

    def test_throttle_by_http_429(self):
        self.assertEqual(classify_mod.classify(SendOutcome(ok=False, http_status=429)),
                         classify_mod.THROTTLE)

    def test_throttle_by_retcode(self):
        self.assertEqual(classify_mod.classify(SendOutcome(ok=False, http_status=200, retcode=1200)),
                         classify_mod.THROTTLE)

    def test_fatal_retcodes(self):
        for code in (100, 104, 1400, 1401, 1403, 1404):
            self.assertEqual(
                classify_mod.classify(SendOutcome(ok=False, http_status=200, retcode=code)),
                classify_mod.FATAL, f"retcode={code} 应当判致命")

    def test_fatal_http_auth(self):
        for status in (400, 401, 403, 404):
            self.assertEqual(classify_mod.classify(SendOutcome(ok=False, http_status=status)),
                             classify_mod.FATAL, f"HTTP {status} 应当判致命")

    def test_retry_transient(self):
        self.assertEqual(classify_mod.classify(SendOutcome(ok=False, http_status=200, retcode=-1)),
                         classify_mod.RETRY)
        self.assertEqual(classify_mod.classify(SendOutcome(ok=False, http_status=502)),
                         classify_mod.RETRY)
        self.assertEqual(classify_mod.classify(SendOutcome(ok=False, http_status=None)),
                         classify_mod.RETRY, "连不上（网络层）应当重试")

    def test_raise_for_raises_typed_exceptions(self):
        self.assertTrue(classify_mod.raise_for(SendOutcome(ok=True)))
        with self.assertRaises(SendFatal):
            classify_mod.raise_for(SendOutcome(ok=False, http_status=200, retcode=1401))
        with self.assertRaises(SendThrottled) as ctx:
            classify_mod.raise_for(SendOutcome(ok=False, http_status=429), now=1000.0)
        self.assertGreater(ctx.exception.retry_at, 1000.0)
        self.assertFalse(classify_mod.raise_for(SendOutcome(ok=False, http_status=200, retcode=-1)))


class _FakeOneBot(BaseHTTPRequestHandler):
    retcode = 0
    http_status = 200
    seen: list = []

    def log_message(self, *a):
        return

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        type(self).seen.append({"path": self.path, "body": json.loads(raw or b"{}")})
        body = json.dumps({
            "status": "ok" if self.retcode == 0 else "failed",
            "retcode": self.retcode,
            "data": {"message_id": 1},
        }).encode("utf-8")
        self.send_response(self.http_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def fake_onebot(retcode: int = 0, http_status: int = 200):
    handler = type("H", (_FakeOneBot,), {"retcode": retcode, "http_status": http_status, "seen": []})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1], handler


class TestHeartDelivers(unittest.TestCase):
    def _memory(self, port: int, tag: str) -> Memory:
        return Memory(Settings(
            db_path=workdir(tag) / "s.db", routes=(), debounce_ms=0,
            humanize_min_ms=0, humanize_max_ms=0, typing_min_ms=0, typing_max_ms=0,
            onebot_api=f"http://127.0.0.1:{port}", onebot_token="t",
        ), engines="none")

    def _wait_status(self, memory: Memory, want: set[str], timeout: float = 12.0) -> str:
        deadline = time.time() + timeout
        last = "?"
        while time.time() < deadline:
            rows = memory.db.read("SELECT status FROM send_queue")
            if rows:
                last = rows[-1]["status"]
                if last in want:
                    return last
            time.sleep(0.2)
        return last

    def test_success_marks_sent_and_hits_onebot(self):
        srv, port, handler = fake_onebot(0, 200)
        memory = self._memory(port, "ok")
        try:
            memory.heart.enqueue(Ident("qq", "u1", "default"),
                                 OutboxEvent(kind="mention", text="在呢"))
            self.assertEqual(self._wait_status(memory, {"sent"}), "sent")
            self.assertTrue(handler.seen, "假 OneBot 应当收到请求")
            hit = handler.seen[0]
            self.assertEqual(hit["path"], "/send_private_msg")
            self.assertEqual(hit["body"]["user_id"], "u1")
        finally:
            memory.close()
            srv.shutdown()

    def test_token_failure_goes_dead_without_retry_storm(self):
        srv, port, handler = fake_onebot(1401, 200)
        memory = self._memory(port, "dead")
        try:
            memory.heart.enqueue(Ident("qq", "u2", "default"),
                                 OutboxEvent(kind="mention", text="在呢"))
            self.assertEqual(self._wait_status(memory, {"dead"}), "dead")
            row = memory.db.read("SELECT attempts FROM send_queue")[0]
            self.assertEqual(int(row["attempts"]), 1, "致命失败不该反复重试")
            logs = memory.db.read("SELECT payload FROM event_log WHERE kind='send_fatal'")
            self.assertEqual(len(logs), 1, "死信要留 event_log")
            self.assertIn("1401", json.loads(logs[0]["payload"])["reason"]
                          + str(json.loads(logs[0]["payload"])["retcode"]))
        finally:
            memory.close()
            srv.shutdown()

    def test_rate_limit_keeps_pending_and_backs_off(self):
        srv, port, handler = fake_onebot(0, 429)
        memory = self._memory(port, "throttle")
        try:
            t0 = time.time()
            memory.heart.enqueue(Ident("qq", "u3", "default"),
                                 OutboxEvent(kind="mention", text="在呢"))
            time.sleep(3.0)
            row = memory.db.read("SELECT status, next_retry_at, attempts FROM send_queue")[0]
            self.assertEqual(row["status"], "pending")
            self.assertGreater(float(row["next_retry_at"]), t0, "应推迟到未来")
            self.assertEqual(int(row["attempts"]), 0, "限流不该消耗重试次数")
        finally:
            memory.close()
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
