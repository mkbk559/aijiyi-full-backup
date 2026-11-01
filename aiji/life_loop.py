"""Bounded background life progression.

The loop advances the character's own state without calling a language model.
It creates small, auditable activities that are later available to chat and
proactive prompts. External platform actions are intentionally out of scope.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable

from .being import BeingStore


class LifeLoop:
    def __init__(
        self,
        store: BeingStore,
        *,
        poll_seconds: float = 60.0,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.poll_seconds = max(5.0, float(poll_seconds))
        self.rng = rng or random.Random()
        self.clock = clock
        self.stats = {
            "steps": 0,
            "created": 0,
            "skipped": 0,
            "errors": 0,
            "last_error": "",
            "last_event_at": 0.0,
        }
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> LifeLoop:
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="aiji-life-loop", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def step(self, *, now: float | None = None, roll: float | None = None) -> dict | None:
        self.stats["steps"] += 1
        try:
            event = self.store.advance_life(
                now=self.clock() if now is None else now,
                roll=self.rng.random() if roll is None else roll,
            )
        except Exception as exc:  # noqa: BLE001
            self.stats["errors"] += 1
            self.stats["last_error"] = f"{type(exc).__name__}: {exc}"
            return None
        if event is None:
            self.stats["skipped"] += 1
        else:
            self.stats["created"] += 1
            self.stats["last_event_at"] = float(event["created_at"])
        return event

    def snapshot(self) -> dict:
        return {
            **dict(self.stats),
            "poll_seconds": self.poll_seconds,
            "running": bool(self._thread and self._thread.is_alive()),
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            self.step()
            if self._stop.wait(self.poll_seconds):
                return


__all__ = ["LifeLoop"]
