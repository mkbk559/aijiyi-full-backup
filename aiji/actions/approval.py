"""人在回路审批：请求挂起，等待控制台明确允许或拒绝。"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(slots=True)
class Approval:
    id: str
    kind: str
    summary: str
    arguments: dict[str, Any]
    created_at: float
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    resolved: bool = False


class ApprovalManager:
    def __init__(self, on_request: Callable[[dict[str, Any]], None] | None = None):
        self._lock = threading.Lock()
        self._items: dict[str, Approval] = {}
        self.on_request = on_request
        self.stats = {"requested": 0, "approved": 0, "rejected": 0, "timeout": 0}

    def request(
        self,
        *,
        kind: str,
        summary: str,
        arguments: dict[str, Any],
        timeout_s: float = 120.0,
    ) -> bool:
        approval_id = uuid.uuid4().hex[:12]
        item = Approval(
            id=approval_id, kind=kind, summary=summary,
            arguments=arguments, created_at=time.time(),
        )
        with self._lock:
            self._items[approval_id] = item
            self.stats["requested"] += 1
        if self.on_request:
            try:
                self.on_request(self._public(item))
            except Exception:  # noqa: BLE001 - 通知失败仍需等待 API 批准
                pass
        try:
            return item.event.wait(timeout=max(1.0, float(timeout_s))) and item.approved
        finally:
            if not item.event.is_set():
                self.stats["timeout"] += 1
            with self._lock:
                self._items.pop(approval_id, None)

    def _public(self, item: Approval) -> dict[str, Any]:
        return {
            "id": item.id,
            "kind": item.kind,
            "summary": item.summary,
            "arguments": item.arguments,
            "created_at": item.created_at,
        }

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._public(item) for item in self._items.values()
                    if not item.resolved]

    def resolve(self, approval_id: str, approved: bool) -> bool:
        with self._lock:
            item = self._items.get(approval_id)
            if item is None or item.resolved:
                return False
            item.resolved = True
            item.approved = bool(approved)
            self.stats["approved" if approved else "rejected"] += 1
        item.event.set()
        return True

    def approve(self, approval_id: str) -> bool:
        return self.resolve(approval_id, True)

    def reject(self, approval_id: str) -> bool:
        return self.resolve(approval_id, False)


__all__ = ["Approval", "ApprovalManager"]
