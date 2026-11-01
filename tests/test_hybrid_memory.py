"""Hybrid index startup wiring regression."""

from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aiji.config import Route, Settings
from aiji.db import DB
from aiji.memory import Memory
from aiji.store import FactDraft, Ident, add_fact


ROOT = Path(__file__).resolve().parent.parent


class TestHybridStartup(unittest.TestCase):
    def test_existing_facts_get_semantic_dimensions_during_rebuild(self) -> None:
        directory = ROOT / ".tmp" / f"hybrid-startup-{uuid.uuid4().hex[:8]}"
        directory.mkdir(mode=0o777, parents=True, exist_ok=True)
        db = DB(directory / "hybrid.db")
        ident = Ident("qq", "u1")
        with db.tx() as conn:
            fid, _ = add_fact(
                conn,
                ident,
                FactDraft("user", "居住地", "杭州", "用户住在杭州", 0.9),
            )
        db.close()

        class FakePool:
            _states = [object()]

            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def chat(self, *_args, **_kwargs):
                return SimpleNamespace(text="[" + ",".join("0.1" for _ in range(10)) + "]")

            def stats(self):
                return []

        settings = Settings(
            db_path=directory / "hybrid.db",
            vector="hybrid",
            routes=(Route("fake", "http://fake.invalid/v1", "fake", "key"),),
            workers=1,
            reflection_interval_s=60,
        )
        memory = None
        try:
            with mock.patch("aiji.memory.ModelPool", FakePool):
                memory = Memory(settings, engines="none")
            self.assertIsNotNone(memory._hybrid._items[fid][1])
        finally:
            if memory is not None:
                memory.close()
            shutil.rmtree(directory, ignore_errors=True)
