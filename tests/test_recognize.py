"""Recognizer timeout resource regressions."""

from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import TimeoutError as FutureTimeout

from aiji.recognize import Item, Recognizers, _timed


def _recognition_threads() -> int:
    return sum(
        1 for thread in threading.enumerate()
        if thread.name.startswith("aiji-recog")
    )


class TestRecognitionTimeout(unittest.TestCase):
    def test_timeouts_do_not_create_one_thread_per_call(self) -> None:
        baseline = _recognition_threads()
        for _ in range(24):
            with self.assertRaises(FutureTimeout):
                _timed(lambda: time.sleep(0.8), 0.02)
        time.sleep(0.05)
        created = _recognition_threads() - baseline
        self.assertLessEqual(created, 6)

    def test_private_link_is_rejected_before_fetch(self):
        called = []

        def should_not_fetch(_url):
            called.append(_url)
            return "<title>internal secret</title>"

        recognizers = Recognizers(fetcher=should_not_fetch)
        env = recognizers.recognize(Item(
            kind="link",
            ref="private",
            url="http://127.0.0.1/api/capabilities",
        ))

        self.assertNotEqual(env.status, "ok")
        self.assertIn("private", env.meta.get("reason", ""))
        self.assertEqual(called, [])
