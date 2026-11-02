# -*- coding: utf-8 -*-
"""RVC 客户端（tools/rvc_convert.py）在**不装 torch** 的前提下可测的部分。

真跑换声要 RVC venv（torch + 模型），那是 `tools/archcheck/rvc_bench.py` 的事；
这里只测两条分支的**接线**：服务在跑时走 HTTP、参数映射对不对、服务不在时按 `--via` 决定报错还是回退。
"""
from __future__ import annotations

import importlib.util
import json
import socketserver
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".tmp" / "test_rvc_client"


def load_client():
    spec = importlib.util.spec_from_file_location("rvc_convert_under_test",
                                                  ROOT / "tools" / "rvc_convert.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Stub(BaseHTTPRequestHandler):
    got: list[dict] = []
    ok: bool = True

    def log_message(self, *a):
        return

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/health"):
            self._json(200, {"ok": True, "model": "stub", "loaded_ms": 1.0})
        else:
            self._json(404, {"ok": False})

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        _Stub.got.append(payload)
        if _Stub.ok:
            Path(payload["out"]).write_bytes(b"RIFFstub")
            self._json(200, {"ok": True, "ms": 1.0, "out": payload["out"]})
        else:
            self._json(500, {"ok": False, "error": "stub failure"})


class TestClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)
        cls.mod = load_client()
        cls.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Stub)
        cls.srv.daemon_threads = True
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls._orig_server = cls.mod.SERVER
        cls.mod.SERVER = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.mod.SERVER = cls._orig_server
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        _Stub.got = []
        _Stub.ok = True

    def test_health_probe(self):
        self.assertIsNotNone(self.mod.health())
        self.mod.SERVER = "http://127.0.0.1:1"
        self.assertIsNone(self.mod.health(url="http://127.0.0.1:1"))
        self.mod.SERVER = f"http://127.0.0.1:{self.port}"

    def test_payload_mapping(self):
        payload = self.mod.build_payload(
            "in.wav", "out.wav",
            ["--pitch", "2", "--index-rate", "0.5", "--f0-method", "fcpe",
             "--protect", "0.2", "--rms-mix-rate", "0.8", "--speaker-id", "3"],
        )
        self.assertEqual(payload["in"], "in.wav")
        self.assertEqual(payload["pitch"], 2)
        self.assertIsInstance(payload["pitch"], int)
        self.assertAlmostEqual(payload["index_rate"], 0.5)
        self.assertEqual(payload["f0_method"], "fcpe")
        self.assertAlmostEqual(payload["protect"], 0.2)
        self.assertAlmostEqual(payload["rms_mix_rate"], 0.8)
        self.assertEqual(payload["speaker_id"], 3)

    def test_auto_uses_server(self):
        src = WORK / "in.wav"
        src.write_bytes(b"RIFFin")
        dst = WORK / "out_auto.wav"
        if dst.exists():
            dst.unlink()
        rc = self.mod.main([str(src), str(dst), "--via", "auto", "--pitch", "1"])
        self.assertEqual(rc, 0)
        self.assertTrue(dst.exists())
        self.assertEqual(len(_Stub.got), 1)
        self.assertEqual(_Stub.got[0]["pitch"], 1)

    def test_server_failure_is_reported(self):
        _Stub.ok = False
        src = WORK / "in2.wav"
        src.write_bytes(b"RIFFin")
        dst = WORK / "out_fail.wav"
        rc = self.mod.main([str(src), str(dst), "--via", "server"])
        self.assertEqual(rc, 1)
        self.assertFalse(dst.exists())

    def test_via_server_down_does_not_silently_fall_back(self):
        self.mod.SERVER = "http://127.0.0.1:1"
        try:
            called = []
            orig = self.mod.convert_inproc
            self.mod.convert_inproc = lambda *a, **k: called.append(1) or 0
            try:
                rc = self.mod.main(["a.wav", "b.wav", "--via", "server"])
            finally:
                self.mod.convert_inproc = orig
            self.assertEqual(rc, 1)
            self.assertEqual(called, [], "--via server 不该偷偷回退到一次性进程")
        finally:
            self.mod.SERVER = f"http://127.0.0.1:{self.port}"

    def test_auto_falls_back_when_downed(self):
        self.mod.SERVER = "http://127.0.0.1:1"
        try:
            called = []
            orig = self.mod.convert_inproc

            def fake_inproc(src, dst, extra):
                called.append((src, dst, extra))
                return 0

            self.mod.convert_inproc = fake_inproc
            try:
                rc = self.mod.main(["a.wav", "b.wav", "--via", "auto", "--pitch", "1"])
            finally:
                self.mod.convert_inproc = orig
            self.assertEqual(rc, 0)
            self.assertEqual(len(called), 1, "服务不在时 auto 应回退到一次性进程")
        finally:
            self.mod.SERVER = f"http://127.0.0.1:{self.port}"

    def test_usage_error(self):
        self.assertEqual(self.mod.main(["only-one-arg"]), 2)


if __name__ == "__main__":
    unittest.main()
