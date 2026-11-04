"""Proactive 接线测试（用户 2026-11-04 拍板"让玛卡巴卡主动开口"后接线）。

引擎本身的行为（静默/额度/退避/预扣退还）在 tests/test_dispatch*.py 已锁；
这里锁的是**接线**：
- onebot_api 配了 + 默认开 → make_server 挂出 memory.proactive + 线程；
- AIJI_PROACTIVE=0 → 不起线程；
- onebot_api 空（不可投递）→ 不起线程（没有通道的主动是负资产）；
- 真实对话（/v1/chat/completions）触发 note_event 重置静默；
  session=restyle 的机器调用不重置（那是机器自言自语，不是用户说话）。
"""
from __future__ import annotations

import http.client
import json
import threading
import unittest
import unittest.mock as mock
from types import SimpleNamespace

from tests._tempdir import make_tmp

from aiji.hippocampus.memory import Memory
from aiji.hippocampus.store import Ident
from aiji.right_hand.config import Settings
from aiji.throat.server import make_server


def _memory(case):
    return Memory(Settings(
        db_path=make_tmp(case) / "proactive_wiring.db",
        routes=(),
        reflection_interval_s=60,
        debounce_ms=0,
        humanize_min_ms=0,
        humanize_max_ms=0,
        typing_min_ms=0,
        typing_max_ms=0,
    ), engines="none")


class _FakePool:
    def __init__(self, text: str = "网关回复"):
        self.text = text

    def chat(self, messages, *, timeout_s=None):
        return SimpleNamespace(text=self.text, route="fake", latency_ms=1.0)

    def stats(self):
        return []


class ProactiveWiringTest(unittest.TestCase):
    def _boot(self, *, onebot_api: str, env: dict[str, str]):
        memory = _memory(self)
        memory.settings.onebot_api = onebot_api
        memory.settings.onebot_token = ""
        memory.pool = _FakePool()
        memory.reflective.pool = memory.pool
        patcher = mock.patch.dict("os.environ", env, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        srv = make_server(memory, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        for attr in ("proactive_stop", "janitor_stop"):
            stop = getattr(memory, attr, None)
            if stop is not None:
                self.addCleanup(stop.set)
        return memory, srv

    def _chat(self, srv, body: dict) -> int:
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1],
                                          timeout=15)
        conn.request("POST", "/v1/chat/completions",
                     body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return resp.status

    def test_configured_and_default_on_boots_proactive(self):
        memory, _ = self._boot(onebot_api="http://127.0.0.1:3000",
                               env={"AIJI_PROACTIVE": "1"})
        self.assertTrue(hasattr(memory, "proactive"))
        self.assertTrue(memory.proactive_thread.is_alive())

    def test_env_zero_disables_proactive(self):
        memory, _ = self._boot(onebot_api="http://127.0.0.1:3000",
                               env={"AIJI_PROACTIVE": "0"})
        self.assertFalse(hasattr(memory, "proactive"))

    def test_no_delivery_channel_means_no_thread(self):
        memory, _ = self._boot(onebot_api="", env={"AIJI_PROACTIVE": "1"})
        self.assertFalse(hasattr(memory, "proactive"))

    def test_chat_resets_silence_but_restyle_does_not(self):
        memory, srv = self._boot(onebot_api="http://127.0.0.1:3000",
                                 env={"AIJI_PROACTIVE": "1"})
        ident = Ident("qq", "10001", "default")

        status = self._chat(srv, {
            "messages": [{"role": "user", "content": "在吗"}],
            "platform": ident.platform, "platform_user_id": ident.platform_user_id,
            "scope": ident.scope,
        })
        self.assertEqual(status, 200)
        last = memory.heart.state_get(ident, "last_event_ts")
        self.assertTrue(last and float(last) > 0)

        memory.heart.state_set(ident, "last_event_ts", 1.0)
        status2 = self._chat(srv, {
            "messages": [{"role": "user", "content": "改写这条评论：你好"}],
            "session": "restyle",
            "aiji": {"remember": False, "tools": False},
            "platform": ident.platform, "platform_user_id": ident.platform_user_id,
            "scope": ident.scope,
        })
        self.assertEqual(status2, 200)
        self.assertEqual(memory.heart.state_get(ident, "last_event_ts"), 1.0)


if __name__ == "__main__":
    unittest.main()
