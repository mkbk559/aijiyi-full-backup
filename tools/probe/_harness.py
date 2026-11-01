"""深度探测用的公共脚手架（隔离 DB / 假模型池 / 真 HTTP 服务 / 结果记录）。

设计约束：
- 只在 `.tmp/` 下建库，绝不碰 `data/aiji.db`；
- 模型走 FakePool（确定性、零成本），真模型另开 `--live` 脚本；
- 每个 case 记录 name/ok/detail，最后打印 JSON 汇总，exit!=0 表示有 FAIL。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
TMP = ROOT / ".tmp"
TMP.mkdir(mode=0o777, exist_ok=True)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def workdir(prefix: str) -> Path:
    path = TMP / f"probe-{prefix}-{uuid.uuid4().hex[:8]}"
    path.mkdir(mode=0o777)
    return path


class FakePool:
    """模型池替身：固定回复，可按指令注入异常。"""

    _states = [object()]

    def __init__(self, text: str = "网关回复") -> None:
        self.text = text
        self.calls: list[list[dict]] = []
        self.fail_next = 0

    def chat(self, messages, *, timeout_s=None, **kwargs):
        self.calls.append(messages)
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("fake pool failure")
        return SimpleNamespace(text=self.text, route="fake", latency_ms=1.0)

    def stats(self):
        return [{
            "name": "fake", "model": "fake-model", "calls": len(self.calls),
            "ok": len(self.calls), "failed": 0, "cooldown_s": 0,
        }]


def quiet_env() -> None:
    """关掉后台循环，避免探测时后台线程写库/联网。"""
    os.environ.setdefault("AIJI_LIFE_LOOP", "0")
    os.environ.setdefault("AIJI_MCP_RESEARCH_LOOP", "0")
    os.environ.setdefault("AIJI_STICKER_UPDATE", "0")
    os.environ.setdefault("AIJI_CONSOLE_SHELL", "0")
    os.environ["AIJI_API_KEY"] = ""


def make_memory(directory: Path, *, engines: str = "none", **overrides):
    from aiji.config import Settings
    from aiji.memory import Memory

    kwargs = dict(
        db_path=directory / "probe.db",
        routes=(),
        reflection_interval_s=3600,
        debounce_ms=0,
        humanize_min_ms=0,
        humanize_max_ms=0,
        typing_min_ms=0,
        typing_max_ms=0,
    )
    kwargs.update(overrides)
    memory = Memory(Settings(**kwargs), engines=engines)
    pool = FakePool()
    memory.pool = pool
    memory.reflective.pool = pool
    if getattr(memory, "writer", None) is not None:
        memory.writer.pool = pool
    return memory, pool


def start_server(memory):
    from aiji.server import make_server

    server = make_server(memory, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[0], server.server_address[1]
    return server, f"http://{host}:{port}"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http_call(base: str, method: str, path: str, body=None, headers=None,
              timeout: float = 20.0):
    """返回 (status, payload, text)；4xx/5xx 不抛异常。"""
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(base + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
            ctype = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
        ctype = exc.headers.get("Content-Type", "")
    except Exception as exc:  # noqa: BLE001 - 网络层失败也记成一次结果
        return 0, None, f"{type(exc).__name__}: {exc}"
    text = raw.decode("utf-8", "replace")
    payload = None
    if "json" in ctype:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
    return status, payload, text


class Probe:
    def __init__(self, name: str) -> None:
        self.name = name
        self.rows: list[dict] = []
        self.started = time.time()

    def case(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append({"case": name, "ok": bool(ok), "detail": str(detail)[:300]})
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}" + (f"  |  {detail}" if detail else ""), flush=True)
        return bool(ok)

    def expect(self, name: str, got, want, detail: str = "") -> bool:
        ok = got in want if isinstance(want, (list, tuple, set)) else got == want
        return self.case(name, ok, detail or f"got={got!r} want={want!r}")

    def finish(self) -> int:
        passed = sum(1 for row in self.rows if row["ok"])
        failed = [row for row in self.rows if not row["ok"]]
        summary = {
            "probe": self.name,
            "cases": len(self.rows),
            "passed": passed,
            "failed": len(failed),
            "duration_s": round(time.time() - self.started, 2),
            "failures": failed,
        }
        print("\n=== SUMMARY ===")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if not failed else 1


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
