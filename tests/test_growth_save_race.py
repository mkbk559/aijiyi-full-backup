"""成长账本的并发写：同进程多人格副本 + 跨进程替换窗口。

真实触发场景：`POST /api/being/checkup/live` 会开 3 个线程，各自克隆一份库并跑一次
`Head.reply`，三条线都会结算成长账本。以前 `save()` 用**固定**临时名 `growth.tmp`、
直接 `replace`、无锁无重试，Windows 上撞
`PermissionError: [WinError 32] 另一个程序正在使用此文件`，
而 `Head.reply` 会把异常当成"回复"返回——体检报告于是显示模型答非所问
（2026-11-04 v51 全量跑时就是这样，报告里三个探针两个是 PermissionError）。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.brain import growth as growth_mod  # noqa: E402


class SaveRaceTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(make_tmp(self, "growth"))
        self.path = self.dir / "persona" / "growth.json"
        self.ledger = growth_mod.Growth(self.path)

    def test_parallel_saves_never_collide(self):
        errors: list[BaseException] = []
        start = threading.Barrier(8)

        def worker(index: int) -> None:
            start.wait()
            try:
                for round_no in range(15):
                    self.ledger.save({
                        "schema": growth_mod.SCHEMA,
                        "xp": float(index * 100 + round_no),
                        "writer": index,
                    })
            except BaseException as exc:  # noqa: BLE001 - 要把它记下来做断言
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(errors, [], f"并发写账本抛了异常：{errors[:3]}")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], growth_mod.SCHEMA, "落盘必须是完整 JSON")
        leftovers = sorted(p.name for p in self.path.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [], "不许留下临时文件")

    def test_replace_retries_when_target_is_locked(self):
        real_replace = os.replace
        attempts = {"count": 0}

        def flaky(src, dst):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise PermissionError(32, "另一个程序正在使用此文件")
            return real_replace(src, dst)

        with mock.patch.object(growth_mod.os, "replace", side_effect=flaky):
            self.ledger.save({"schema": growth_mod.SCHEMA, "xp": 1.0})
        self.assertEqual(attempts["count"], 3, "占用要重试，不是第一次就放弃")
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8"))["xp"], 1.0)

    def test_failure_cleans_up_temp_and_raises(self):
        def always_busy(_src, _dst):
            raise PermissionError(32, "一直被占用")

        sentinel = {"value": 0.0}
        self.ledger.save({"schema": growth_mod.SCHEMA, "xp": 7.0})
        with mock.patch.object(growth_mod.os, "replace", side_effect=always_busy):
            with self.assertRaises(PermissionError):
                self.ledger.save({"schema": growth_mod.SCHEMA,
                                  "xp": sentinel["value"]})
        leftovers = sorted(p.name for p in self.path.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [], "失败也要把临时文件删掉")
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8"))["xp"], 7.0,
            "写失败不能破坏上一份账本")

    def test_temp_name_is_unique_per_call(self):
        seen = set()
        real_replace = os.replace

        def capture(src, dst):
            seen.add(pathlib.Path(src).name)
            return real_replace(src, dst)

        with mock.patch.object(growth_mod.os, "replace", side_effect=capture):
            for value in range(5):
                self.ledger.save({"schema": growth_mod.SCHEMA, "xp": float(value)})
        self.assertEqual(len(seen), 5, f"临时名必须每次不同，实际 {sorted(seen)}")
        self.assertTrue(all(str(os.getpid()) in name for name in seen),
                        "临时名要带 pid，跨进程才不会撞")

    def test_state_migrates_legacy_file_once(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"schema": 1, "birth_at": 1_700_000_000.0}),
                             encoding="utf-8")
        data = self.ledger.state()
        self.assertEqual(data["schema"], growth_mod.SCHEMA)
        self.assertEqual(data["xp"], 0.0)
        self.assertEqual(data["migrated_from"], 1)
        self.assertEqual(data["born_at"], 1_700_000_000.0)


if __name__ == "__main__":
    unittest.main()
