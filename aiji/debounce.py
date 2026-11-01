"""同会话消息合并：用短窗口把连发短句并成一次模型调用。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class _Batch:
    first_messages: list[dict[str, Any]]
    later_messages: list[list[dict[str, Any]]] = field(default_factory=list)
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None


def _last_user(messages: list[dict[str, Any]]) -> str:
    for item in reversed(messages):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def merge_messages(batches: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """保留第一份完整上下文，将后来者的最后一条用户消息按顺序追加。"""
    if not batches:
        return []
    merged = [dict(item) for item in batches[0] if isinstance(item, dict)]
    seen_users = {
        str(item.get("content") or "").strip()
        for item in merged if item.get("role") == "user"
    }
    for messages in batches[1:]:
        text = _last_user(messages)
        if text and text not in seen_users:
            merged.append({"role": "user", "content": text})
            seen_users.add(text)
    return merged


class ConversationDebouncer:
    """所有调用共享同一实例；相同 key 的请求只执行一次生成。"""

    def __init__(self, window_ms: int = 800):
        self.window_s = max(0.0, float(window_ms)) / 1000.0
        self._lock = threading.Lock()
        self._batches: dict[tuple[str, ...], _Batch] = {}
        self.stats = {"calls": 0, "batches": 0, "merged": 0}

    def call(
        self,
        key: tuple[str, ...],
        messages: list[dict[str, Any]],
        generate: Callable[[list[dict[str, Any]]], T],
    ) -> T:
        with self._lock:
            self.stats["calls"] += 1
            batch = self._batches.get(key)
            leader = batch is None
            if batch is None:
                batch = _Batch(first_messages=messages)
                self._batches[key] = batch
                self.stats["batches"] += 1
            else:
                batch.later_messages.append(messages)
                self.stats["merged"] += 1

        if not leader:
            # 领导请求最多占用窗口 + 一次模型调用；调用方自己的 HTTP 超时仍在兜底。
            batch.done.wait()
            if batch.error is not None:
                raise batch.error
            return batch.result

        if self.window_s:
            time.sleep(self.window_s)
        with self._lock:
            if self._batches.get(key) is batch:
                self._batches.pop(key, None)
            later = list(batch.later_messages)

        merged = merge_messages([batch.first_messages, *later])
        try:
            batch.result = generate(merged)
            return batch.result
        except BaseException as exc:
            batch.error = exc
            raise
        finally:
            batch.done.set()


__all__ = ["ConversationDebouncer", "merge_messages"]
