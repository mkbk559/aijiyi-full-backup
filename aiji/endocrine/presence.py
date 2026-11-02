"""存在感中枢：把各入口的交互状态广播给控制台。"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

_LOG = logging.getLogger("aiji.presence")
_LOG.setLevel(logging.WARNING)
_LOG.addHandler(logging.NullHandler())


class PresenceHub:
    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()
        self._latest: dict[str, Any] = {
            "type": "presence",
            "state": "idle",
            "mood": "平静",
            "ts": time.time(),
        }
        self.stats = {"published": 0, "subscribers": 0, "dropped": 0}
        self.port = 0

    def publish(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = {"type": "presence", **event, "ts": time.time()}
        with self._lock:
            self._latest = payload
            self.stats["published"] += 1
            subscribers = list(self._subscribers)
        for sub in subscribers:
            try:
                sub.put_nowait(payload)
            except queue.Full:
                try:
                    sub.get_nowait()
                    sub.put_nowait(payload)
                except (queue.Empty, queue.Full):
                    self.stats["dropped"] += 1
        return payload

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._latest)

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        sub: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=64)
        with self._lock:
            self._subscribers.add(sub)
            self.stats["subscribers"] += 1
        return sub

    def unsubscribe(self, sub: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(sub)


class PresenceServer:
    """独立 WS 服务；HTTP 服务继续用 stdlib，不为此引入 Web 框架。"""

    def __init__(self, hub: PresenceHub, host: str = "127.0.0.1", port: int = 8789):
        self.hub = hub
        self.host = host
        self.requested_port = int(port)
        self._server = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.port = 0

    def _handler(self, websocket) -> None:
        sub = self.hub.subscribe()
        try:
            websocket.send(json.dumps(self.hub.snapshot(), ensure_ascii=False))
            while not self._stop.is_set():
                try:
                    event = sub.get(timeout=1.0)
                except queue.Empty:
                    websocket.ping()
                    continue
                websocket.send(json.dumps(event, ensure_ascii=False))
        except (ConnectionClosed, EOFError):
            pass
        finally:
            self.hub.unsubscribe(sub)

    def start(self) -> "PresenceServer":
        self._server = serve(
            self._handler, self.host, self.requested_port, logger=_LOG,
        )
        try:
            self.port = int(self._server.socket.getsockname()[1])
        except Exception:  # noqa: BLE001 - 版本差异时仍可用指定端口
            self.port = self.requested_port
        self.hub.port = self.port
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="aiji-presence-ws",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.shutdown()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)


__all__ = ["PresenceHub", "PresenceServer"]
