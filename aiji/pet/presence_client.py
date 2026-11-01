"""桌宠侧的存在感订阅：把核心广播映射成桌面动画状态。"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from typing import Any

from websockets.sync.client import connect


class PresenceClient:
    def __init__(
        self,
        url: str,
        on_event: Callable[[dict[str, Any]], None],
        *,
        reconnect_s: float = 2.0,
    ):
        self.url = url
        self.on_event = on_event
        self.reconnect_s = max(0.2, float(reconnect_s))
        self.stats = {"connected": False, "events": 0, "errors": 0}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "PresenceClient":
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._loop, name="aiji-pet-presence", daemon=True,
            )
            self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self.stats["connected"] = False

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                with connect(
                    self.url,
                    open_timeout=2,
                    close_timeout=1,
                    ping_interval=20,
                    ping_timeout=20,
                ) as socket:
                    self.stats["connected"] = True
                    while not self._stop.is_set():
                        try:
                            raw = socket.recv(timeout=1)
                        except TimeoutError:
                            continue
                        try:
                            event = json.loads(raw)
                        except (TypeError, ValueError):
                            continue
                        if isinstance(event, dict):
                            self.stats["events"] += 1
                            self.on_event(event)
            except Exception:  # noqa: BLE001 - 核心未启动时持续重连
                self.stats["errors"] += 1
                self.stats["connected"] = False
                self._stop.wait(self.reconnect_s)
        self.stats["connected"] = False


__all__ = ["PresenceClient"]
