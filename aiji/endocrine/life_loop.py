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

from aiji.endocrine.being import BeingStore


class LifeLoop:
    def __init__(
        self,
        store: BeingStore,
        *,
        poll_seconds: float = 60.0,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.time,
        learner: Callable[[str, str], dict] | None = None,
    ) -> None:
        """learner(activity_id, summary) -> result：真实学习动作的钩子（B2）。

        life_loop 本身不 import brain（endocrine 是底层，brain 是高层，反向依赖
        会把测试拖进联网）。watch_videos 活动发生时若有 learner，就把
        "顺手记下一个想聊的话题"从空话变成真学一次。返回值只进 stats。
        """
        self.store = store
        self.poll_seconds = max(5.0, float(poll_seconds))
        self.rng = rng or random.Random()
        self.clock = clock
        self.learner = learner
        self.stats = {
            "steps": 0,
            "created": 0,
            "skipped": 0,
            "errors": 0,
            "last_error": "",
            "last_event_at": 0.0,
            "learn_tried": 0,
            "learn_ok": 0,
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
            # B2：刷视频/写小本子的活动不能只是模板句。有 learner 时真学一次。
            if self.learner is not None and str(event.get("id")) in (
                    "watch_videos", "write_note"):
                try:
                    self.stats["learn_tried"] += 1
                    res = self.learner(str(event["id"]), str(event.get("summary") or ""))
                    if isinstance(res, dict) and res.get("ok"):
                        self.stats["learn_ok"] += 1
                except Exception as exc:  # noqa: BLE001 - 学习失败不影响生活推进
                    self.stats["last_error"] = f"learner: {type(exc).__name__}"
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
