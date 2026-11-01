"""生命体运行层：自动反思、防抖、拟人延迟、状态广播与降级兜底。"""

from __future__ import annotations

import json
import shutil
import threading
import time
import unittest
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from aiji import Memory, store
from aiji.config import Route, Settings
from aiji.db import DB
from aiji.head import Head
from aiji.pool import ModelPool, PoolExhausted
from aiji.presence import PresenceHub, PresenceServer
from aiji.pet.brain import FishBrain
from aiji.pet.presence_client import PresenceClient
from aiji.reflect import Reflective, ReflectionWorker
from aiji.server import make_server
from aiji.store import Ident

TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp"
TMP_ROOT.mkdir(mode=0o777, exist_ok=True)


def workdir() -> Path:
    path = TMP_ROOT / f"lifecycle-{uuid.uuid4().hex[:8]}"
    path.mkdir(mode=0o777)
    return path


class FakePool:
    def __init__(self, text: str):
        self.text = text
        self._states = [object()]
        self.calls: list[list[dict]] = []
        self.options: list[dict] = []
        self.lock = threading.Lock()

    def chat(self, messages: list[dict], *, timeout_s: float | None = None, **kwargs):
        with self.lock:
            self.calls.append(messages)
            self.options.append({"timeout_s": timeout_s, **kwargs})
        return type("Result", (), {
            "text": self.text,
            "route": "fake",
            "latency_ms": 1.0,
        })()


class TestReflectionWorker(unittest.TestCase):
    def test_user_only_backlog_does_not_call_reflection_model(self) -> None:
        path = workdir()
        db = DB(path / "user-only.db")
        ident = Ident("qq", "u1", "group:1")
        try:
            with db.tx() as conn:
                for index in range(35):
                    store.add_turn(
                        conn, ident, "s1", "user", f"群友消息 {index}")
            pool = FakePool(json.dumps({"traits": [], "summary": ""}))
            reflective = Reflective(
                db, pool, every_turns=30, min_interval_s=0)

            self.assertIsNone(reflective.tick(ident, "s1"))
            self.assertEqual(pool.calls, [])
        finally:
            db.close()
            shutil.rmtree(path, ignore_errors=True)

    def test_empty_reflection_output_is_not_persisted(self) -> None:
        path = workdir()
        db = DB(path / "empty-reflection.db")
        ident = Ident("qq", "u1", "group:1")
        try:
            with db.tx() as conn:
                store.add_turn(conn, ident, "s1", "user", "在吗")
                store.add_turn(conn, ident, "s1", "assistant", "在")
            pool = FakePool(json.dumps({"traits": [], "summary": ""}))
            reflective = Reflective(
                db, pool, every_turns=2, min_interval_s=0)

            self.assertIsNone(reflective.tick(ident, "s1"))
            count = db.read("SELECT COUNT(*) AS c FROM reflection")[0]["c"]
            self.assertEqual(count, 0)
        finally:
            db.close()
            shutil.rmtree(path, ignore_errors=True)

    def test_reflection_worker_turns_conversation_into_trait(self) -> None:
        path = workdir()
        db = DB(path / "reflection.db")
        ident = Ident("qq", "u1", "group:1")
        try:
            with db.tx() as conn:
                for role, text in (
                    ("user", "你说话怎么这么爱吐槽"),
                    ("assistant", "不然呢，难道要我给你唱摇篮曲？"),
                    ("user", "你这反问句又来了"),
                    ("assistant", "被你发现了，看来我确实有点爱反问。"),
                ):
                    store.add_turn(conn, ident, "s1", role, text)

            payload = json.dumps({
                "traits": [{"name": "爱反问", "value": "常用反问吐槽", "evidence": 2}],
                "summary": "我习惯用反问接话",
            }, ensure_ascii=False)
            pool = FakePool(payload)
            reflective = Reflective(db, pool, every_turns=2, min_interval_s=0)
            reflective.tick(ident, "s1")
            self.assertEqual(pool.options[0]["timeout_s"], 60.0)
            self.assertTrue(pool.options[0]["background"])
            self.assertEqual(pool.options[0]["attempts_per_route"], 1)
            worker = ReflectionWorker(reflective, interval_s=0.02).start()
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    traits = store.trait_snapshot(db.conn(), ident)
                    if traits:
                        break
                    time.sleep(0.02)
                traits = store.trait_snapshot(db.conn(), ident)
                self.assertTrue(traits, traits)
                self.assertEqual(traits[0]["name"], "爱反问")
            finally:
                worker.stop()
        finally:
            db.close()
            shutil.rmtree(path, ignore_errors=True)

    def test_memory_starts_reflection_worker(self) -> None:
        path = workdir()
        memory = Memory(Settings(
            db_path=path / "memory.db", routes=(), reflection_interval_s=0.01,
        ), engines="none")
        try:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if memory.reflection_worker.stats["scans"] > 0:
                    break
                time.sleep(0.01)
            self.assertGreater(memory.reflection_worker.stats["scans"], 0)
        finally:
            memory.close()
            shutil.rmtree(path, ignore_errors=True)


class TestConversationDebounce(unittest.TestCase):
    def test_same_session_messages_merge_into_one_model_call(self) -> None:
        path = workdir()
        memory = Memory(Settings(
            db_path=path / "debounce.db", routes=(),
            reflection_interval_s=60, debounce_ms=200,
        ), engines="none")
        pool = FakePool("合并完成")
        memory.pool = pool
        memory.reflective.pool = pool
        server = make_server(memory, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def send(content: str) -> dict:
            body = json.dumps({
                "model": "aiji",
                "messages": [{"role": "user", "content": content}],
            }, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                base + "/v1/chat/completions", data=body, method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-Aiji-Platform": "qq",
                    "X-Aiji-User": "g1",
                    "X-Aiji-Session": "s1",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read())

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                replies = list(executor.map(send, ("第一句", "第二句")))
            self.assertEqual(len(pool.calls), 1, pool.calls)
            merged = json.dumps(pool.calls[0], ensure_ascii=False)
            self.assertIn("第一句", merged)
            self.assertIn("第二句", merged)
            self.assertEqual(replies[0]["choices"][0]["message"]["content"], "合并完成")
            self.assertEqual(replies[1]["choices"][0]["message"]["content"], "合并完成")
        finally:
            server.shutdown()
            server.server_close()
            memory.close()
            shutil.rmtree(path, ignore_errors=True)


class TestHumanDelay(unittest.TestCase):
    def test_im_platform_waits_before_reply(self) -> None:
        path = workdir()
        memory = Memory(Settings(
            db_path=path / "delay.db", routes=(), reflection_interval_s=60,
            debounce_ms=0, humanize_min_ms=30, humanize_max_ms=30,
            typing_min_ms=10, typing_max_ms=10,
        ), engines="none")
        pool = FakePool("收到")
        memory.pool = pool
        memory.reflective.pool = pool
        try:
            started = time.perf_counter()
            reply = Head(memory).reply(
                [{"role": "user", "content": "在吗"}],
                Ident("qq", "u1", "group:1"),
                session="s1",
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.assertEqual(reply.text, "收到")
            self.assertGreaterEqual(elapsed_ms, 39)
        finally:
            memory.close()
            shutil.rmtree(path, ignore_errors=True)

    def test_head_accepts_client_system_suffix(self) -> None:
        path = workdir()
        memory = Memory(Settings(
            db_path=path / "suffix.db", routes=(), reflection_interval_s=60,
            debounce_ms=0, humanize_min_ms=0, humanize_max_ms=0,
            typing_min_ms=0, typing_max_ms=0,
        ), engines="none")
        pool = FakePool("好")
        memory.pool = pool
        memory.reflective.pool = pool
        try:
            Head(memory).reply(
                [{"role": "user", "content": "你好"}],
                Ident("desktop", "owner", "default"),
                session="s1",
                system_suffix="桌宠气泡最多三句",
            )
            prompt = json.dumps(pool.calls[0], ensure_ascii=False)
            self.assertIn("桌宠气泡最多三句", prompt)
        finally:
            memory.close()
            shutil.rmtree(path, ignore_errors=True)


class TestPresenceBroadcast(unittest.TestCase):
    def test_websocket_receives_state_updates(self) -> None:
        from websockets.sync.client import connect

        hub = PresenceHub()
        server = PresenceServer(hub, "127.0.0.1", 0).start()
        try:
            with connect(
                f"ws://127.0.0.1:{server.port}",
                open_timeout=2, close_timeout=2,
            ) as socket:
                initial = json.loads(socket.recv(timeout=2))
                self.assertEqual(initial["state"], "idle")

                hub.publish({
                    "state": "thinking",
                    "platform": "qq",
                    "user": "g1",
                    "session": "s1",
                    "mood": "专注",
                })
                event = json.loads(socket.recv(timeout=2))
                self.assertEqual(event["state"], "thinking")
                self.assertEqual(event["platform"], "qq")
        finally:
            server.stop()

    def test_pet_presence_client_receives_remote_state(self) -> None:
        hub = PresenceHub()
        server = PresenceServer(hub, "127.0.0.1", 0).start()
        events = []
        client = PresenceClient(
            f"ws://127.0.0.1:{server.port}", events.append,
        ).start()
        try:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not client.stats["connected"]:
                time.sleep(0.01)
            hub.publish({"state": "speaking", "mood": "愉快", "platform": "qq"})
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not any(
                item.get("state") == "speaking" for item in events
            ):
                time.sleep(0.01)
            self.assertTrue(any(item.get("state") == "speaking" for item in events))
        finally:
            client.stop()
            server.stop()

    def test_head_publishes_presence_for_cross_client_sync(self) -> None:
        path = workdir()
        memory = Memory(Settings(
            db_path=path / "presence.db", routes=(), reflection_interval_s=60,
            debounce_ms=0, humanize_min_ms=0, humanize_max_ms=0,
            typing_min_ms=0, typing_max_ms=0,
        ), engines="none")
        pool = FakePool("我听见了")
        memory.pool = pool
        memory.reflective.pool = pool
        try:
            Head(memory).reply(
                [{"role": "user", "content": "去群里看看"}],
                Ident("qq", "g1", "group:1"),
                session="s1",
            )
            latest = memory.presence.snapshot()
            self.assertEqual(latest["state"], "speaking")
            self.assertEqual(latest["platform"], "qq")
            self.assertEqual(latest["session"], "s1")
            self.assertGreater(memory.presence.stats["published"], 1)
        finally:
            memory.close()
            shutil.rmtree(path, ignore_errors=True)

    def test_pet_brain_uses_shared_head_pipeline(self) -> None:
        path = workdir()
        memory = Memory(Settings(
            db_path=path / "pet-head.db", routes=(), reflection_interval_s=60,
            debounce_ms=0, humanize_min_ms=0, humanize_max_ms=0,
            typing_min_ms=0, typing_max_ms=0,
        ), engines="none")
        pool = FakePool("桌宠回答")
        memory.pool = pool
        memory.reflective.pool = pool
        try:
            result = FishBrain(memory, Ident("desktop", "owner", "default"), "pet").reply(
                "说句话",
            )
            self.assertEqual(result["text"], "桌宠回答")
            self.assertEqual(memory.presence.snapshot()["state"], "speaking")
            prompt = json.dumps(pool.calls[0], ensure_ascii=False)
            self.assertIn("桌宠气泡放不下长文", prompt)
        finally:
            memory.close()
            shutil.rmtree(path, ignore_errors=True)

    def test_thinking_starts_before_debounce_window_finishes(self) -> None:
        path = workdir()
        memory = Memory(Settings(
            db_path=path / "presence-debounce.db", routes=(), reflection_interval_s=60,
            debounce_ms=250, humanize_min_ms=0, humanize_max_ms=0,
            typing_min_ms=0, typing_max_ms=0,
        ), engines="none")
        pool = FakePool("想好了")
        memory.pool = pool
        memory.reflective.pool = pool
        worker = None
        try:
            worker = threading.Thread(target=lambda: Head(memory).reply(
                [{"role": "user", "content": "想一下"}],
                Ident("desktop", "owner", "default"),
                session="s1",
            ))
            worker.start()
            time.sleep(0.06)
            self.assertEqual(memory.presence.snapshot()["state"], "thinking")
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        finally:
            if worker is not None and worker.is_alive():
                worker.join(timeout=3)
            memory.close()
            shutil.rmtree(path, ignore_errors=True)


class TestUpstreamFallback(unittest.TestCase):
    def test_retryable_gateway_error_retries_same_route(self) -> None:
        class Flaky(BaseHTTPRequestHandler):
            calls = 0

            def log_message(self, *args):
                return

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                type(self).calls += 1
                if type(self).calls == 1:
                    self.send_response(500)
                    self.end_headers()
                    return
                data = json.dumps({
                    "choices": [{"message": {"content": "第二次成功"}}],
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Flaky)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        route = Route(
            name="flaky", base_url=f"http://127.0.0.1:{server.server_address[1]}",
            model="fake", api_key="k", timeout_s=3,
        )
        try:
            result = ModelPool((route,), attempts_per_route=2).chat(
                [{"role": "user", "content": "hi"}],
            )
            self.assertEqual(result.text, "第二次成功")
            self.assertEqual(Flaky.calls, 2)
        finally:
            server.shutdown()
            server.server_close()

    def test_exhausted_gateway_returns_in_character_fallback(self) -> None:
        path = workdir()
        memory = Memory(Settings(
            db_path=path / "fallback.db", routes=(), reflection_interval_s=60,
            debounce_ms=0, humanize_min_ms=0, humanize_max_ms=0,
            typing_min_ms=0, typing_max_ms=0,
        ), engines="none")

        class Down(FakePool):
            def chat(self, messages, *, timeout_s=None):
                raise PoolExhausted("agent2api:http 429")

        memory.pool = Down("")
        memory.reflective.pool = memory.pool
        try:
            reply = Head(memory).reply(
                [{"role": "user", "content": "帮我查一下"}],
                Ident("telegram", "u1", "default"),
                session="s1",
            )
            self.assertIn("刚才没接上", reply.text)
            self.assertTrue(reply.degraded)
            self.assertEqual(reply.finish_reason, "stop")
            self.assertEqual(memory.presence.snapshot()["state"], "degraded")
        finally:
            memory.close()
            shutil.rmtree(path, ignore_errors=True)
