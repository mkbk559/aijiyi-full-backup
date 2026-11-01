"""Global being-state and human pacing regressions."""

from __future__ import annotations

import random
import shutil
import time
import uuid
from pathlib import Path
from unittest import mock

from aiji.being import BeingState, BeingStore
from aiji.config import Settings
from aiji.db import DB
from aiji.head import Head
from aiji.humanize import HumanPacer
from aiji.memory import Memory
from aiji.pool import PoolExhausted
from aiji.store import Ident

ROOT = Path(__file__).resolve().parent.parent / ".tmp"


def workdir() -> Path:
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / f"being-{uuid.uuid4().hex[:8]}"
    path.mkdir()
    return path


def noon() -> float:
    return time.mktime((2026, 10, 31, 12, 0, 0, 0, 0, -1))


def test_energy_recovers_during_sleep_and_drains_during_day():
    sleeping = BeingState(energy=25, last_tick=1000.0)
    sleeping.tick(now=1000.0 + 3600, hour=2)
    awake = BeingState(energy=25, last_tick=1000.0)
    awake.tick(now=1000.0 + 3600, hour=14)
    assert sleeping.energy > awake.energy

    tired = BeingState(energy=70, last_tick=2000.0)
    tired.tick(now=2000.0 + 4 * 3600, hour=15)
    assert tired.energy < 70


def test_emotion_has_inertia_and_returns_to_baseline():
    state = BeingState(valence=0.0, last_tick=1000.0)
    state.observe("anger", 1.0, now=1001.0, hour=12)
    assert state.valence < 0
    state.tick(now=1001.0 + 86400, hour=12)
    assert state.valence > -4
    assert state.valence <= 0


def test_proactive_requires_energy_and_awake_window():
    assert not BeingState(energy=8, last_tick=0).can_proactive(
        now=1000, hour=12)
    assert not BeingState(energy=60, last_tick=0).can_proactive(
        now=1000, hour=2)
    assert BeingState(energy=60, last_tick=0).can_proactive(
        now=1000, hour=12)


def test_busy_day_keeps_enough_energy_for_normal_conversation():
    start = time.mktime((2026, 10, 31, 0, 0, 0, 0, 0, -1))
    state = BeingState(energy=72, social=65, last_tick=start)
    awake_samples = 0
    proactive_samples = 0
    low_energy_samples = 0
    for minute in range(1, 24 * 60 + 1):
        now = start + minute * 60
        hour = time.localtime(now).tm_hour
        state.observe("neutral", 0.25, now=now, hour=hour)
        state.advance_life(now=now, roll=(minute % 97) / 97)
        if 8 <= hour < 23:
            awake_samples += 1
            proactive_samples += int(state.can_proactive(now=now, hour=hour))
            low_energy_samples += int(state.energy < 28)

    assert proactive_samples / awake_samples >= 0.65
    assert low_energy_samples / awake_samples <= 0.25


def test_store_persists_state_events_and_private_notes():
    path = workdir()
    db_path = path / "being.db"
    try:
        db = DB(db_path)
        store = BeingStore(db, persona="sunba")
        snapshot = store.observe(
            "谢谢你陪我，今天有点累", now=2000.0, hour=14)
        assert snapshot["energy"] < 100
        assert len(store.events(limit=5)) == 1
        store.note("其实今天也有点想被抱一下", now=2001.0)
        assert "想被抱一下" in store.notes(limit=5)[0]["text"]
        db.close()

        db2 = DB(db_path)
        restored = BeingStore(db2, persona="sunba").snapshot(
            now=2002.0, hour=14)
        assert restored["energy"] == snapshot["energy"]
        db2.close()
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_repeated_hostile_chat_does_not_fill_private_notes_with_duplicates():
    path = workdir()
    db = DB(path / "notes.db")
    store = BeingStore(db, persona="sunba")
    try:
        for offset in range(12):
            store.observe("你个笨蛋", now=noon() + offset * 30, hour=12)
        notes = store.notes(limit=20, private_only=True)
        assert len(notes) == 1
        assert "被刺到" in notes[0]["text"]
    finally:
        db.close()
        shutil.rmtree(path, ignore_errors=True)


def test_pacer_uses_context_energy_and_urgency():
    pacer = HumanPacer(rng=random.Random(7))
    hot = pacer.plan_ms(
        "在呢", "qq", energy=70, idle_s=10, urgent=False)
    normal = pacer.plan_ms(
        "在呢", "qq", energy=70, idle_s=600, urgent=False)
    cold = pacer.plan_ms(
        "在呢", "qq", energy=70, idle_s=7200, urgent=False)
    urgent = pacer.plan_ms(
        "现在马上回我", "qq", energy=20, idle_s=10, urgent=True)
    low = pacer.plan_ms(
        "在呢", "qq", energy=10, idle_s=600, urgent=False)
    high = pacer.plan_ms(
        "在呢", "qq", energy=95, idle_s=600, urgent=False)
    assert hot < normal < cold
    assert urgent < normal
    assert low > high


def test_head_injects_global_state_without_writing_user_turn():
    path = workdir()
    memory = Memory(Settings(
        db_path=path / "head.db", routes=(), debounce_ms=0,
        humanize_min_ms=0, humanize_max_ms=0,
        typing_min_ms=0, typing_max_ms=0,
    ), engines="none")
    calls = []

    class Pool:
        _states = [object()]

        def chat(self, messages, *, timeout_s=None, **kwargs):
            calls.append(messages)
            return type("R", (), {
                "text": "我回来啦", "route": "fake", "latency_ms": 1.0,
            })()

    memory.pool = Pool()
    ident = Ident("qq", "owner", "default")
    try:
        with mock.patch("aiji.head.time.time", return_value=noon()):
            first = Head(memory).reply(
                [{"role": "user", "content": "今天有点累"}],
                ident,
                session="private:owner",
            )
        assert first.text == "我回来啦"
        prompt = "\n".join(str(item["content"]) for item in calls[-1])
        assert "精力：" in prompt
        assert "情绪：" in prompt

        replies_before = len(calls)
        with mock.patch("aiji.head.time.time", return_value=noon() + 60):
            proactive = Head(memory).reply(
                [{"role": "user", "content": "（现在没人说话，只有你自己。）"}],
                ident,
                session="private:owner",
                remember=False,
                proactive=True,
            )
        assert proactive.text
        assert len(calls) == replies_before + 1
        turns = memory.recent_turns(ident, "private:owner")
        assert len(turns) == 2
    finally:
        memory.close()
        shutil.rmtree(path, ignore_errors=True)


def test_low_energy_skips_proactive_model_call():
    path = workdir()
    memory = Memory(Settings(
        db_path=path / "low.db", routes=(), debounce_ms=0,
        humanize_min_ms=0, humanize_max_ms=0,
        typing_min_ms=0, typing_max_ms=0,
    ), engines="none")
    calls = []

    class Pool:
        _states = [object()]

        def chat(self, messages, *, timeout_s=None, **kwargs):
            calls.append(messages)
            return type("R", (), {
                "text": "不该调用", "route": "fake", "latency_ms": 1.0,
            })()

    memory.pool = Pool()
    state = memory.being.load(now=1000.0, hour=12)
    state.energy = 5
    memory.being.save(state, now=1000.0)
    try:
        with mock.patch("aiji.head.time.time", return_value=noon()):
            reply = Head(memory).reply(
                [{"role": "user", "content": "（主动消息）"}],
                Ident("qq", "owner", "default"),
                session="private:owner",
                remember=False,
                proactive=True,
            )
        assert reply.text == ""
        assert reply.finish_reason == "skip"
        assert calls == []
    finally:
        memory.close()
        shutil.rmtree(path, ignore_errors=True)


def test_failed_proactive_does_not_send_fallback_text():
    path = workdir()
    memory = Memory(Settings(
        db_path=path / "failed-proactive.db", routes=(), debounce_ms=0,
        humanize_min_ms=0, humanize_max_ms=0,
        typing_min_ms=0, typing_max_ms=0,
    ), engines="none")

    class Pool:
        _states = []

        def chat(self, messages, *, timeout_s=None, **kwargs):
            raise PoolExhausted("offline")

    memory.pool = Pool()
    try:
        with mock.patch("aiji.head.time.time", return_value=noon()):
            reply = Head(memory).reply(
                [{"role": "user", "content": "（主动消息）"}],
                Ident("qq", "owner", "default"),
                session="private:owner",
                remember=False,
                proactive=True,
            )
        assert reply.text == ""
        assert reply.finish_reason == "skip"
    finally:
        memory.close()
        shutil.rmtree(path, ignore_errors=True)

