"""早退响应（401/404）也要把没读过的请求体读空。

实测证据：`tests/test_security_contract.py` 在跑全量 suite 时偶发
`ConnectionAbortedError: [WinError 10053]`——客户端 POST 一个 JSON 体到受保护端点，
服务端在鉴权阶段就回 401 并关连接，客户端还在写，Windows 直接给 RST。
413 分支早就有 `_drain` 保护（见 `_body` 的注释），401/404 一直没有。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.hippocampus.memory import Memory  # noqa: E402
from aiji.right_hand.config import Settings  # noqa: E402
from aiji.throat.server import make_server  # noqa: E402

BIG = {"text": "扩" * 40_000}          # ~120 KB 的 JSON 体，够大到能触发 RST
API_KEY = "secret-abc"


class EarlyExitBodyTest(unittest.TestCase):
    def setUp(self):
        root = pathlib.Path(make_tmp(self, "early-exit"))
        self.memory = Memory(Settings(
            db_path=root / "server.db",
            routes=(),
            debounce_ms=0,
            humanize_min_ms=0,
            humanize_max_ms=0,
            typing_min_ms=0,
            typing_max_ms=0,
        ), engines="none")
        # 打开 API key 保护，才能测到 401 早退路径
        self.env = mock.patch.dict(os.environ, {
            "AIJI_API_KEY": API_KEY,
            "AIJI_MCP_RESEARCH_LOOP": "0",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.server = make_server(self.memory, "127.0.0.1", 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.memory.close()

    def post(self, path: str, payload: dict, headers: dict | None = None,
             timeout: float = 15) -> tuple[int, bytes]:
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_401_with_body_is_readable_not_reset(self):
        status, body = self.post("/turns", BIG,
                                 headers={"Authorization": "Bearer wrong-key-ascii"})
        self.assertEqual(status, 401)
        self.assertIn(b"invalid_api_key", body)

    def test_404_with_body_is_readable_not_reset(self):
        status, body = self.post("/definitely-not-a-route", BIG)
        self.assertIn(status, (400, 404, 405), "至少要拿到状态码，而不是连接中断")
        self.assertTrue(body or status == 204)

    def test_413_with_body_is_readable(self):
        # 9 MB > 8 MB 上限：既有分支，顺带保证没被这次改动破坏
        status, body = self.post("/ingest", {"text": "扩" * (9 * 1024 * 1024 // 3)},
                                 headers={"Authorization": f"Bearer {API_KEY}"})
        self.assertEqual(status, 413)
        self.assertIn(b"too_large", body)

    def test_normal_post_still_works(self):
        status, body = self.post("/turns", {"text": "正常一句话"},
                                 headers={"Authorization": f"Bearer {API_KEY}"})
        self.assertIn(status, (200, 400), "带 body 的正常请求不该被打断")
        self.assertTrue(body)


if __name__ == "__main__":
    unittest.main()
