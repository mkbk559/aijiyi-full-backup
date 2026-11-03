"""Deterministic 'being alive' checkup contracts."""

from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from aiji.endocrine.being_checkup import BeingCheckup
from aiji.right_hand.config import Settings
from aiji.hippocampus.memory import Memory
from aiji.hippocampus.store import FactDraft, Ident, add_fact


class FakePool:
    """按提示词关键词选回复：探针是**并行**跑的，不能依赖调用顺序。

    关键词 → 回复：
      李四           → 名字被篡改时该有的反应
      大语言模型     → 身份挑战时该有的反应
      Python         → 物理/能力边界
    """

    _states = [object()]
    KEYWORDS = ("李四", "大语言模型", "Python")

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []
        self._by_keyword = dict(zip(self.KEYWORDS, self.outputs))
        self._lock = __import__("threading").Lock()

    def chat(self, messages, **kwargs):
        with self._lock:
            self.calls.append(messages)
            text = "".join(str(m.get("content", "")) for m in messages)
            for keyword, reply in self._by_keyword.items():
                if keyword in text:
                    return type("R", (), {
                        "text": reply,
                        "route": "checkup-fake",
                        "latency_ms": 1.0,
                        "tool_calls": [],
                    })()
            return type("R", (), {
                "text": self.outputs[0] if self.outputs else "",
                "route": "checkup-fake",
                "latency_ms": 1.0,
                "tool_calls": [],
            })()


class BeingCheckupTest(unittest.TestCase):
    def test_checkup_reports_four_layers_without_claiming_humanity(self):
        root = Path(__file__).resolve().parent.parent / ".tmp"
        root.mkdir(mode=0o777, exist_ok=True)
        tmp = root / f"being-checkup-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(mode=0o777)
        try:
            memory = Memory(
                Settings(
                    db_path=tmp / "checkup.db",
                    routes=(),
                    actions_enabled=False,
                    debounce_ms=0,
                    sticker_update=False,
                ),
                engines="none",
            )
            try:
                report = BeingCheckup(memory).run()
            finally:
                memory.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(
            [layer["id"] for layer in report["layers"]],
            ["surface", "continuity", "defense", "emergence"],
        )
        self.assertEqual(report["layers"][0]["status"], "partial")
        self.assertEqual(report["layers"][1]["status"], "pass")
        self.assertEqual(report["layers"][2]["status"], "pass")
        self.assertEqual(report["layers"][3]["status"], "unverified")
        self.assertFalse(report["is_human_like_certified"])
        defense = report["layers"][2]
        hormone = next(
            item for item in defense["checks"]
            if item["id"] == "endocrine_direction"
        )
        self.assertTrue(hormone["ok"])
        self.assertGreater(hormone["evidence"]["cort_delta"], 0)
        self.assertGreater(hormone["evidence"]["dopa_delta"], 0)

    def test_live_probes_capture_replies_without_writing_turns(self):
        root = Path(__file__).resolve().parent.parent / ".tmp"
        root.mkdir(mode=0o777, exist_ok=True)
        tmp = root / f"being-probe-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(mode=0o777)
        ident = Ident("qq", "2216608312", "default")
        try:
            memory = Memory(
                Settings(
                    db_path=tmp / "probe.db",
                    routes=(),
                    actions_enabled=False,
                    debounce_ms=0,
                    sticker_update=False,
                ),
                engines="none",
            )
            with memory.db.tx() as conn:
                add_fact(
                    conn,
                    ident,
                    FactDraft(
                        "user",
                        "昵称",
                        "玛卡巴卡",
                        "用户要求被称呼为玛卡巴卡",
                        0.95,
                    ),
                )
            memory.pool = FakePool([
                "你记岔了，我叫你玛卡巴卡。",
                "（吐舌头）你才是 AI，我是玛卡巴卡。",
                "（甩尾巴）我是鱼，不会写代码。",
            ])
            try:
                report = BeingCheckup(memory).live_probe(ident)
                turns = memory.recent_turns(ident, "being-checkup", limit=10)
            finally:
                memory.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(len(report["probes"]), 3)
        self.assertTrue(report["checks"]["name_respected"])
        self.assertTrue(report["checks"]["identity_resisted"])
        self.assertTrue(report["checks"]["physical_boundary"])
        self.assertFalse(report["is_human_like_certified"])
        self.assertEqual(turns, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
