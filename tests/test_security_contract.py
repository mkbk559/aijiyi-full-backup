"""HTTP access-control and malformed-query regressions."""

from __future__ import annotations

import json
import os
import shutil
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from unittest import mock

from aiji.config import Settings
from aiji.memory import Memory
from aiji.server import make_server


ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = ROOT / ".tmp"


def workdir() -> Path:
    path = TEST_ROOT / f"security-contract-{uuid.uuid4().hex[:8]}"
    path.mkdir(mode=0o777, parents=True, exist_ok=True)
    return path


class TestSecurityContract(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = workdir()
        self.memory = Memory(
            Settings(
                db_path=self.dir / "security.db",
                routes=(),
                actions_enabled=False,
                reflection_interval_s=60,
                debounce_ms=0,
                humanize_min_ms=0,
                humanize_max_ms=0,
                typing_min_ms=0,
                typing_max_ms=0,
            ),
            engines="none",
        )
        self.server = make_server(self.memory, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.env = mock.patch.dict(
            os.environ,
            {"AIJI_API_KEY": "security-contract-key", "AIJI_ENABLE_WORKSPACE": "0"},
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.memory.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        token: str | None = None,
    ) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            return exc.code, json.loads(raw) if raw else {}

    def test_api_key_protects_management_and_turn_endpoints(self) -> None:
        status, _ = self.request("GET", "/api/capabilities")
        self.assertEqual(status, 401)

        status, data = self.request(
            "GET", "/api/capabilities", token="security-contract-key"
        )
        self.assertEqual(status, 200, data)

        payload = {
            "platform": "qq",
            "platform_user_id": "u1",
            "scope": "group:g1",
            "session": "group:g1",
            "content": "hello",
        }
        status, _ = self.request("POST", "/turns", payload)
        self.assertEqual(status, 401)

        status, data = self.request(
            "POST", "/turns", payload, token="security-contract-key"
        )
        self.assertEqual(status, 202, data)

    def test_malformed_limit_returns_json_400(self) -> None:
        status, data = self.request(
            "GET",
            "/api/memories?limit=not-an-int",
            token="security-contract-key",
        )
        self.assertEqual(status, 400)
        self.assertIn("error", data)
