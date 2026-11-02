"""Writer failure-path regressions."""

from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from aiji.right_hand.config import Settings
from aiji.hippocampus.db import DB
from aiji.brain.pool import ModelPool
from aiji.hippocampus.recall import Recaller
from aiji.hippocampus.recognize import Item, Recognizers
from aiji.hippocampus.store import Ident
from aiji.hippocampus.writer import Job, Writer


ROOT = Path(__file__).resolve().parent.parent


def workdir() -> Path:
    path = ROOT / ".tmp" / f"writer-contract-{uuid.uuid4().hex[:8]}"
    path.mkdir(mode=0o777, parents=True, exist_ok=True)
    return path


class TestWriterDeadLetter(unittest.TestCase):
    def _writer(self, directory: Path) -> Writer:
        db = DB(directory / "writer.db")
        settings = Settings(
            db_path=directory / "writer.db",
            routes=(),
            workers=1,
            reflection_interval_s=60,
        )
        return Writer(
            db,
            ModelPool(()),
            Recaller(db, budget_ms=100, recent=10),
            settings,
            Recognizers(timeout_s=0.1),
        )

    def test_extraction_uses_background_model_priority(self) -> None:
        directory = workdir()
        writer = self._writer(directory)
        calls = []

        class BackgroundPool:
            def chat(self, messages, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    text='{"facts":[],"profile":{}}',
                    route="background-fake",
                )

        writer.pool = BackgroundPool()
        try:
            result = writer._extract(
                "群友闲聊", Ident("qq", "u1", "group:g1"), "group:g1")
            self.assertEqual(result.source_kind, "model")
            self.assertTrue(calls[0]["background"])
        finally:
            writer.db.close()
            shutil.rmtree(directory, ignore_errors=True)

    def test_forced_heuristic_extraction_does_not_touch_model_pool(self) -> None:
        directory = workdir()
        writer = self._writer(directory)

        class NeverCalled:
            def chat(self, *_args, **_kwargs):
                raise AssertionError("model pool must not be called under backlog")

        writer.pool = NeverCalled()
        try:
            result = writer._extract(
                "高频群里的一句话",
                Ident("qq", "u1", "group:g1"),
                "group:g1",
                force_heuristic=True,
            )
            self.assertEqual(result.source_kind, "heuristic")
        finally:
            writer.db.close()
            shutil.rmtree(directory, ignore_errors=True)

    def test_failed_ingest_preserves_routing_context(self) -> None:
        directory = workdir()
        db = DB(directory / "writer.db")
        settings = Settings(
            db_path=directory / "writer.db",
            routes=(),
            workers=1,
            reflection_interval_s=60,
        )
        writer = Writer(
            db,
            ModelPool(()),
            Recaller(db, budget_ms=100, recent=10),
            settings,
            Recognizers(timeout_s=0.1),
        )
        writer._handle = lambda job: (_ for _ in ()).throw(RuntimeError("boom"))
        writer.start()
        try:
            job = Job(
                "ingest",
                Ident("qq", "u1", scope="group:g1"),
                "group:g1",
                item=Item(kind="image", ref="msg-1", url="https://example.test/a.png"),
                extra={"source": "qq", "bytes": b"not-json"},
            )
            writer._submit(job)
            self.assertTrue(writer.flush(5))
            row = db.read("SELECT payload FROM write_queue")[0]
            payload = json.loads(row["payload"])
            self.assertEqual(payload["job_kind"], "ingest")
            self.assertEqual(payload["session"], "group:g1")
            self.assertEqual(payload["item"]["ref"], "msg-1")
            self.assertEqual(payload["item"]["url"], "https://example.test/a.png")
            self.assertEqual(payload["extra"]["source"], "qq")
            self.assertNotIn("bytes", payload["extra"])
        finally:
            writer.stop(1)
            db.close()
            shutil.rmtree(directory, ignore_errors=True)

    def test_failed_exchange_preserves_assistant_text(self) -> None:
        directory = workdir()
        db = DB(directory / "writer.db")
        settings = Settings(
            db_path=directory / "writer.db",
            routes=(),
            workers=1,
            reflection_interval_s=60,
        )
        writer = Writer(
            db,
            ModelPool(()),
            Recaller(db, budget_ms=100, recent=10),
            settings,
            Recognizers(timeout_s=0.1),
        )
        writer._handle = lambda job: (_ for _ in ()).throw(RuntimeError("boom"))
        writer.start()
        try:
            job = Job(
                "exchange",
                Ident("qq", "u1", scope="private:u1"),
                "private:u1",
                text="你好",
                extra={"assistant": "在呢"},
            )
            writer._submit(job)
            self.assertTrue(writer.flush(5))
            row = db.read("SELECT payload FROM write_queue")[0]
            payload = json.loads(row["payload"])
            self.assertEqual(payload["job_kind"], "exchange")
            self.assertEqual(payload["text"], "你好")
            self.assertEqual(payload["extra"]["assistant"], "在呢")
        finally:
            writer.stop(1)
            db.close()
            shutil.rmtree(directory, ignore_errors=True)
