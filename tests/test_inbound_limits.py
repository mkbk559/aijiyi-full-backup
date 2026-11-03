"""入站上限的回归：超长文本要截断并如实回报，超大 body 要 413。

对应探针发现（归档 2026-11-03/144357-security-probe-21d7d1）：1.4 MB 文本被 202 收下。
"""
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.right_hand.config import Settings  # noqa: E402
from aiji.hippocampus.memory import Memory  # noqa: E402
from aiji.throat import server as server_mod  # noqa: E402


class InboundLimitTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_tmp(self, "inbound-limits")
        self.memory = Memory(
            Settings(db_path=self.dir / "console.db", routes=(),
                     actions_enabled=False, debounce_ms=0, sticker_update=False),
            engines="none",
        )
        self.srv = server_mod.make_server(self.memory, port=0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.memory.close()

    def post(self, path: str, body: dict):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_overlong_text_is_clipped_and_reported(self):
        status, payload = self.post("/ingest", {"kind": "text", "text": "啊" * 50_000})
        self.assertEqual(status, 202, payload)
        self.assertTrue(payload.get("truncated"), payload)
        self.assertEqual(payload.get("original_chars"), 50_000)

    def test_remember_and_turns_clip_too(self):
        for path, key in (("/remember", "text"), ("/turns", "content")):
            status, payload = self.post(path, {key: "字" * 40_000})
            self.assertEqual(status, 202, (path, payload))
            self.assertTrue(payload.get("truncated"), (path, payload))
            self.assertEqual(payload.get("original_chars"), 40_000)

    def test_normal_text_is_untouched(self):
        status, payload = self.post("/ingest", {"kind": "text", "text": "今天在杭州"})
        self.assertEqual(status, 202, payload)
        self.assertNotIn("truncated", payload)

    def test_huge_body_is_rejected_before_parsing(self):
        # 9 MB 的 body：连 JSON 都不该解析
        body = json.dumps({"kind": "text", "text": "x" * (9 * 1024 * 1024)}).encode()
        req = urllib.request.Request(
            self.base + "/ingest", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                status, payload = r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            status, payload = exc.code, json.loads(exc.read() or b"{}")
        self.assertEqual(status, 413, payload)
        self.assertEqual(payload.get("limit_bytes"), 8 * 1024 * 1024)

    def test_queue_does_not_stall_after_clipping(self):
        self.post("/ingest", {"kind": "text", "text": "啊" * 100_000})
        time.sleep(0.3)
        status, payload = self.post("/ingest", {"kind": "text", "text": "之后还能收"})
        self.assertEqual(status, 202, payload)
        self.assertTrue(payload.get("queued"))


if __name__ == "__main__":
    unittest.main()
