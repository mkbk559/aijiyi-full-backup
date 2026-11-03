"""模型池的故障转移契约（真 HTTP 假服务器，不走外网）。

最要紧的一条：**200 但正文是空的，不算成功**。网关积分耗尽、max_tokens 被推理
吃光、上游流式截断，都表现为 `content: ""`；如果当成成功，用户端看到的是
"机器人突然不说话"，而且一次重试都不会发生。
"""
from __future__ import annotations

import http.server
import json
import pathlib
import sys
import threading
import unittest
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aiji.brain.pool import ModelPool, PoolExhausted  # noqa: E402
from aiji.right_hand.config import Route  # noqa: E402


class Scripted:
    """按顺序返回预设响应，并记录收到的请求体。"""

    def __init__(self, responses: list[tuple[int, str]]):
        self.responses = list(responses)
        self.requests: list[dict] = []


def make_server(script: Scripted) -> tuple[http.server.ThreadingHTTPServer, str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            try:
                script.requests.append(json.loads(raw or b"{}"))
            except ValueError:
                script.requests.append({"raw": raw.decode("utf-8", "replace")})
            status, body = (script.responses.pop(0) if script.responses
                            else (200, json.dumps(completion("后备"))))
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # 静音
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


def completion(text: str) -> dict:
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}]}


def completion_with_tools(name="do_thing") -> dict:
    return {"choices": [{"index": 0, "message": {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": name, "arguments": json.dumps({"x": 1})}}],
    }, "finish_reason": "tool_calls"}]}


def direct_opener():
    """绕过本机 Clash 代理，直连假服务器。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open


class PoolCase(unittest.TestCase):
    def pool_for(self, script: Scripted, *, attempts=1, routes=1) -> ModelPool:
        server, base = make_server(script)
        # addCleanup 是后进先出：先注册 close、后注册 shutdown，
        # 才会按 shutdown → close 的顺序收尾（反了会在 select 上抛 WinError 10038）
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        routes_ = tuple(Route(name=f"r{i}", base_url=base, model="m", api_key="k",
                              timeout_s=5.0) for i in range(routes))
        return ModelPool(routes_, opener=direct_opener(), attempts_per_route=attempts)


class EmptyContentTest(PoolCase):
    def test_empty_content_retries_the_next_attempt(self):
        script = Scripted([(200, json.dumps(completion(""))),
                           (200, json.dumps(completion("好的呀")))])
        pool = self.pool_for(script, attempts=2)
        result = pool.chat([{"role": "user", "content": "在吗"}])
        self.assertEqual(result.text, "好的呀")
        self.assertEqual(result.attempts, 2)
        self.assertTrue(any("empty_content" in e for e in result.errors), result.errors)

    def test_all_routes_empty_raises_pool_exhausted(self):
        script = Scripted([(200, json.dumps(completion("")))] * 3)
        pool = self.pool_for(script, attempts=3)
        with self.assertRaises(PoolExhausted) as ctx:
            pool.chat([{"role": "user", "content": "在吗"}])
        self.assertIn("empty_content", str(ctx.exception))

    def test_whitespace_content_counts_as_empty(self):
        script = Scripted([(200, json.dumps(completion("   \n "))),
                           (200, json.dumps(completion("在的")))])
        pool = self.pool_for(script, attempts=2)
        self.assertEqual(pool.chat([{"role": "user", "content": "在吗"}]).text, "在的")

    def test_tool_calls_without_text_is_a_success(self):
        script = Scripted([(200, json.dumps(completion_with_tools()))])
        pool = self.pool_for(script)
        result = pool.chat([{"role": "user", "content": "开灯"}],
                           tools=[{"type": "function",
                                   "function": {"name": "do_thing", "parameters": {}}}])
        self.assertEqual(result.text, "")
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].name, "do_thing")
        self.assertEqual(result.tool_calls[0].arguments, {"x": 1})


class ProtocolFailureTest(PoolCase):
    def test_bad_json_is_bad_response(self):
        script = Scripted([(200, "这不是 JSON")] * 2)
        pool = self.pool_for(script, attempts=2)
        with self.assertRaises(PoolExhausted) as ctx:
            pool.chat([{"role": "user", "content": "在吗"}])
        self.assertIn("bad_response", str(ctx.exception))

    def test_missing_choices_is_bad_response(self):
        script = Scripted([(200, json.dumps({"error": "no choices"}))])
        pool = self.pool_for(script)
        with self.assertRaises(PoolExhausted) as ctx:
            pool.chat([{"role": "user", "content": "在吗"}])
        self.assertIn("bad_response", str(ctx.exception))

    def test_400_with_tools_retries_without_tools(self):
        script = Scripted([(400, "{}"), (200, json.dumps(completion("工具不支持，我直接答")))])
        pool = self.pool_for(script)
        result = pool.chat([{"role": "user", "content": "开灯"}],
                           tools=[{"type": "function",
                                   "function": {"name": "do_thing", "parameters": {}}}])
        self.assertEqual(result.text, "工具不支持，我直接答")
        self.assertEqual(len(script.requests), 2)
        self.assertIn("tools", script.requests[0])
        self.assertNotIn("tools", script.requests[1], "降级重试必须真的去掉 tools")

    def test_401_is_not_retried(self):
        script = Scripted([(401, "{}")] * 3)
        pool = self.pool_for(script, attempts=3)
        with self.assertRaises(PoolExhausted):
            pool.chat([{"role": "user", "content": "在吗"}])
        self.assertEqual(len(script.requests), 1, "鉴权失败不该烧配额重试")

    def test_500_is_retried(self):
        script = Scripted([(500, "{}"), (200, json.dumps(completion("缓过来了")))])
        pool = self.pool_for(script, attempts=2)
        self.assertEqual(pool.chat([{"role": "user", "content": "在吗"}]).text, "缓过来了")

    def test_network_error_is_retryable(self):
        pool = ModelPool(
            (Route(name="dead", base_url="http://127.0.0.1:1/v1", model="m",
                   api_key="k", timeout_s=2.0),),
            opener=direct_opener(), attempts_per_route=2)
        with self.assertRaises(PoolExhausted) as ctx:
            pool.chat([{"role": "user", "content": "在吗"}])
        self.assertIn("network", str(ctx.exception))


class MultiRouteTest(PoolCase):
    def test_second_route_covers_for_the_first(self):
        # 两条路由指向同一台假服务器：第一条拿空正文，第二条拿到正文
        script = Scripted([(200, json.dumps(completion(""))),
                           (200, json.dumps(completion("换路由答你")))])
        pool = self.pool_for(script, attempts=1, routes=2)
        result = pool.chat([{"role": "user", "content": "在吗"}])
        self.assertEqual(result.text, "换路由答你")
        self.assertEqual(result.attempts, 2)

    def test_max_tokens_is_forwarded_when_given(self):
        script = Scripted([(200, json.dumps(completion("短")))])
        pool = self.pool_for(script)
        pool.chat([{"role": "user", "content": "在吗"}], max_tokens=400)
        self.assertEqual(script.requests[0]["max_tokens"], 400)

    def test_temperature_defaults_to_zero(self):
        script = Scripted([(200, json.dumps(completion("嗯")))])
        pool = self.pool_for(script)
        pool.chat([{"role": "user", "content": "在吗"}])
        self.assertEqual(script.requests[0]["temperature"], 0.0)


if __name__ == "__main__":
    unittest.main()
