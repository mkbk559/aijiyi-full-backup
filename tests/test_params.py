"""参数表自身的自检：快照、来源标注、口径常量。

`params.snapshot()` 会进 `baseline.json` 供漂移检测，它的"source"字段是从
**本文件源码注释**里正则抓出来的：注释一丢，快照里就变成"(无来源标注！)"，
而且没人会发现——除非有人专门测它。这里就是那个"专门测它"。
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aiji.right_hand import params  # noqa: E402

NO_SOURCE = "(无来源标注！)"


class SnapshotTest(unittest.TestCase):
    def test_snapshot_contains_only_numbers(self):
        snap = params.snapshot()
        self.assertGreater(len(snap), 40, "参数表不该这么少")
        for name, entry in snap.items():
            self.assertEqual(set(entry), {"value", "source"}, name)
            self.assertIsInstance(entry["value"], (int, float), name)
            self.assertNotIsInstance(entry["value"], bool, name)

    def test_snapshot_skips_helpers_and_strings(self):
        snap = params.snapshot()
        for skipped in ("snapshot", "annotations", "INJECTION_SEMANTICS"):
            self.assertNotIn(skipped, snap)
        self.assertFalse([k for k in snap if k.startswith("_")])
        self.assertIsInstance(params.RECALL_DECAY_MODE, str)
        self.assertNotIn("RECALL_DECAY_MODE", snap, "字符串参数不进数值快照")

    def test_snapshot_values_match_module_attributes(self):
        for name, entry in params.snapshot().items():
            self.assertEqual(entry["value"], getattr(params, name), name)

    def test_every_snapshot_entry_has_a_real_source(self):
        """硬约束：每个数值参数都得写注释来源，否则快照里是占位符。"""
        missing = sorted(
            name for name, entry in params.snapshot().items()
            if entry["source"] == NO_SOURCE)
        self.assertEqual(missing, [], "这些参数缺来源注释：" + ", ".join(missing))

    def test_source_of_reads_inline_comment(self):
        source = params._source_of("FSRS_F")
        self.assertIn("R=(1+F", source)
        self.assertEqual(params._source_of("不存在的参数"), NO_SOURCE)

    def test_injection_semantics_states_it_is_not_attack_count(self):
        text = params.INJECTION_SEMANTICS
        self.assertIn("不是 prompt 注入攻击计数", text)
        self.assertIn("log_injection", text)


class LintTest(unittest.TestCase):
    def test_params_module_has_no_orphan_comment_block(self):
        """回归：以前 `RECALL_DECAY_MODE` 下方残留了一行"默认 fsrs"的孤儿注释，
        既和真实默认(stability)矛盾，又会被 `_source_of` 误抓给下一个参数。"""
        source = (pathlib.Path(params.__file__).read_text(encoding="utf-8"))
        orphans = [
            line for line in source.splitlines()
            if re.match(r"^\s{4,}#", line) and "默认 fsrs" in line
        ]
        self.assertEqual(orphans, [])

    def test_params_module_still_compiles_standalone(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile",
             str(pathlib.Path(params.__file__))],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
