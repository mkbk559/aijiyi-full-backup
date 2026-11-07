"""A1 作息硬闸（2026-11-07）：IM 平台睡觉段不进模型、白天本地入口不受影响。

事故依据：2026-11-06 凌晨 00:41–03:08 QQ 群"睡/起"拉扯 30+ 条——模型每轮
重新生成"我睡了，晚安"然后继续陪聊。本文件守住两件事：
1. IM 平台（qq）在 sleep 时段：确定性模板短回，零模型调用；
2. 本地/控制台入口（local）同时段照常——白天测试不被作息绑架。
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path
from unittest import mock

from aiji.right_hand.config import Settings
from aiji.hippocampus.memory import Memory
from aiji.brain.head import Head
from aiji.hippocampus.store import Ident

ROOT = Path(__file__).resolve().parent.parent / ".tmp"


def workdir() -> Path:
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / f"daygate-{uuid.uuid4().hex[:8]}"
    path.mkdir()
    return path


def night() -> float:
    return time.mktime((2026, 11, 6, 2, 0, 0, 0, 0, -1))


def _memory(path: Path) -> Memory:
    return Memory(Settings(
        db_path=path / "gate.db", routes=(), debounce_ms=0,
        humanize_min_ms=0, humanize_max_ms=0,
        typing_min_ms=0, typing_max_ms=0,
    ), engines="none")


def test_qq_sleep_hard_gate_no_model_call():
    path = workdir()
    memory = _memory(path)
    calls = []

    class Pool:
        _states = [object()]

        def chat(self, messages, *, timeout_s=None, **kwargs):
            calls.append(messages)
            return type("R", (), {"text": "不该出现", "route": "fake",
                                  "latency_ms": 1.0})()

    memory.pool = Pool()
    try:
        with mock.patch("aiji.brain.head.time.time", return_value=night()):
            reply = Head(memory).reply(
                [{"role": "user", "content": "别睡，起来嗨"}],
                Ident("qq", "609001709", "group:1025643092"),
                session="group:1025643092",
            )
        assert calls == [], "睡觉段不应调用模型"
        assert "睡了" in reply.text
        assert reply.finish_reason == "stop"
        assert reply.reason == "day_gate:sleep"
    finally:
        memory.close()
        shutil.rmtree(path, ignore_errors=True)


def test_local_console_not_gated_at_night():
    path = workdir()
    memory = _memory(path)

    class Pool:
        _states = [object()]

        def chat(self, messages, *, timeout_s=None, **kwargs):
            return type("R", (), {"text": "本地调试照常", "route": "fake",
                                  "latency_ms": 1.0})()

    memory.pool = Pool()
    try:
        with mock.patch("aiji.brain.head.time.time", return_value=night()):
            reply = Head(memory).reply(
                [{"role": "user", "content": "测试一下"}],
                Ident("local", "owner", "default"),
                session="default",
                remember=False,
            )
        assert reply.text == "本地调试照常"
    finally:
        memory.close()
        shutil.rmtree(path, ignore_errors=True)


def test_day_line_hardens_talk_none_blocks():
    path = workdir()
    memory = _memory(path)

    class Pool:
        _states = [object()]

        def chat(self, messages, *, timeout_s=None, **kwargs):
            full = "\n".join(str(m.get("content") or "")
                             for m in messages if m.get("role") == "system")
            assert "硬规矩" in full, "上课时段的 day line 必须带硬规矩"
            return type("R", (), {"text": "在上课，下课再说", "route": "fake",
                                  "latency_ms": 1.0})()

    memory.pool = Pool()
    afternoon = time.mktime((2026, 11, 5, 15, 0, 0, 0, 0, -1))
    try:
        with mock.patch("aiji.brain.head.time.time", return_value=afternoon):
            reply = Head(memory).reply(
                [{"role": "user", "content": "在吗"}],
                Ident("qq", "u1", "default"),
                session="default",
                remember=False,
            )
        assert reply.text
    finally:
        memory.close()
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    import unittest
    unittest.main()
