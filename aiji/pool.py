"""免费模型池：多路由、轮转、冷却、故障转移。

路由之间互不阻塞：一个被 429 / 超时 / 5xx 打中就进冷却，下一个顶上；
全部不可用时抛 PoolExhausted，让调用方走本地规则兜底（数据不丢）。
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from .actions import ToolCall
from .config import Route


class PoolExhausted(RuntimeError):
    pass


class _RouteError(RuntimeError):
    def __init__(self, msg: str, *, retryable: bool = True):
        super().__init__(msg)
        self.retryable = retryable


@dataclass(slots=True)
class PoolResult:
    text: str
    route: str
    attempts: int
    latency_ms: float
    errors: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)


class _RouteState:
    __slots__ = ("route", "fails", "cooldown_until", "sem", "calls", "ok_calls", "fail_calls")

    def __init__(self, route: Route):
        self.route = route
        self.fails = 0
        self.cooldown_until = 0.0
        self.sem = threading.BoundedSemaphore(route.max_inflight)
        self.calls = 0
        self.ok_calls = 0
        self.fail_calls = 0

    def cooling(self, now: float) -> bool:
        return self.cooldown_until > now

    def succeed(self) -> None:
        self.fails = 0
        self.cooldown_until = 0.0
        self.ok_calls += 1

    def failed(self, retryable: bool) -> None:
        self.fails += 1
        self.fail_calls += 1
        backoff = min(120.0, 2.0 ** min(self.fails, 6))
        self.cooldown_until = time.monotonic() + (backoff if retryable else 300.0)


class ModelPool:
    def __init__(
        self,
        routes: tuple[Route, ...],
        *,
        opener: Callable | None = None,
        attempts_per_route: int = 1,
    ):
        self._states = [_RouteState(r) for r in routes]
        self._rr = 0
        self._lock = threading.Lock()
        self._opener = opener or urllib.request.urlopen
        self.attempts_per_route = max(1, int(attempts_per_route))
        # 最近一次成功调用的响应头（配额模块从中读 x-credits-remaining 之类）
        self.last_headers: dict = {}

    # ---- 路由选择 ----

    def _order(self, now: float) -> list[_RouteState]:
        with self._lock:
            start = self._rr
            self._rr = (self._rr + 1) % max(len(self._states), 1)
        n = len(self._states)
        return [self._states[(start + i) % n] for i in range(n)]

    def chat(
        self,
        messages: list[dict],
        *,
        background: bool = False,
        timeout_s: float | None = None,
        attempts_per_route: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
    ) -> PoolResult:
        if not self._states:
            raise PoolExhausted("no routes configured")
        t0 = time.perf_counter()
        errors: list[str] = []
        posts = 0
        limit = max(1, int(attempts_per_route or self.attempts_per_route))
        for st in self._order(time.monotonic()):
            if st.cooling(time.monotonic()):
                errors.append(f"{st.route.name}:cooldown")
                continue
            if background and getattr(st.sem, "_value", 1) <= 1:
                errors.append(f"{st.route.name}:reserved")
                continue
            if not st.sem.acquire(blocking=False):
                errors.append(f"{st.route.name}:busy")
                continue
            try:
                for attempt in range(limit):
                    st.calls += 1
                    posts += 1
                    try:
                        text, tool_calls = self._post(
                            st.route, messages, timeout_s or st.route.timeout_s,
                            tools=tools, tool_choice=tool_choice,
                            temperature=temperature, top_p=top_p, max_tokens=max_tokens,
                        )
                        st.succeed()
                        return PoolResult(
                            text=text,
                            route=st.route.name,
                            attempts=posts,
                            latency_ms=(time.perf_counter() - t0) * 1000,
                            errors=errors,
                            tool_calls=tool_calls,
                        )
                    except _RouteError as exc:
                        st.failed(exc.retryable)
                        errors.append(f"{st.route.name}:{exc}")
                        if not exc.retryable or attempt + 1 >= limit:
                            break
                        time.sleep(min(0.25, 0.05 * (attempt + 1)))
            finally:
                st.sem.release()
        raise PoolExhausted("; ".join(errors) or "all routes failed")

    def _post(
        self,
        route: Route,
        messages: list[dict],
        timeout_s: float,
        *,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[str, list[ToolCall]]:
        payload = {
            "model": route.model,
            "messages": messages,
            # 默认 0.0 保持旧行为；调用方显式传参时才透传（控制台/OpenAI 兼容层）。
            "temperature": 0.0 if temperature is None else float(temperature),
            "stream": False,
        }
        if top_p is not None:
            payload["top_p"] = float(top_p)
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{route.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {route.api_key}",
            },
        )
        try:
            with self._opener(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8", "replace")
                # 顺手抄一个积分余额（网关愿意在响应头里吐的话最省事，见 aiji/quota.py）
                try:
                    self.last_headers = dict(resp.headers or {})
                except Exception:  # noqa: BLE001 - 拿不到头不影响正文
                    self.last_headers = {}
        except urllib.error.HTTPError as exc:
            if tools and exc.code in (400, 422):
                # 部分积分网关仍是纯文本 OpenAI 兼容，不支持 tools 字段。
                return self._post(
                    route, messages, timeout_s, tools=None,
                    temperature=temperature, top_p=top_p, max_tokens=max_tokens,
                )
            retryable = exc.code in (408, 409, 425, 429) or exc.code >= 500
            raise _RouteError(f"http {exc.code}", retryable=retryable) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
            raise _RouteError(f"network {type(exc).__name__}", retryable=True) from exc
        try:
            data = json.loads(raw)
            message = data["choices"][0]["message"]
            calls = []
            for item in message.get("tool_calls") or []:
                try:
                    function = item.get("function") or {}
                    arguments = json.loads(function.get("arguments") or "{}")
                    if not isinstance(arguments, dict):
                        arguments = {}
                    calls.append(ToolCall(
                        id=str(item.get("id") or f"call-{len(calls)}"),
                        name=str(function.get("name") or ""),
                        arguments=arguments,
                    ))
                except (TypeError, ValueError, AttributeError):
                    continue
            return str(message.get("content") or ""), calls
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise _RouteError("bad_response", retryable=True) from exc

    def stats(self) -> list[dict]:
        now = time.monotonic()
        return [
            {
                "name": st.route.name,
                "model": st.route.model,
                "calls": st.calls,
                "ok": st.ok_calls,
                "failed": st.fail_calls,
                "cooldown_s": round(max(0.0, st.cooldown_until - now), 1),
            }
            for st in self._states
        ]
