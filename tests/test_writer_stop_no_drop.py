"""关停兜底的回归测试：慢模型 + 短 flush 超时，close() 不许丢任务。

复现 v58 的间歇性失败：CLI remember --timeout 5，模型网关 >5s 才回，
旧 stop() 直接丢队列 → recall 空。新 stop() 应离线兜底落库。
"""
from __future__ import annotations

import pathlib
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.hippocampus.memory import Memory  # noqa: E402
from aiji.right_hand.config import Settings  # noqa: E402


class SlowPool:
    """每次 chat 都慢到 flush 必超时，但**结果是好的**（模拟网关抖动，不是故障）。"""

    name = "slow"

    def chat(self, messages, background=False):
        time.sleep(1.2)
        payload = ("{\"facts\": [{\"subject\": \"user\", \"predicate\": \"居住地\", "
                   "\"object\": \"杭州\", \"text\": \"用户住在杭州\"}]}")
        return type("R", (), {"text": payload, "route": "slow"})()


def make_memory(case, name):
    root = pathlib.Path(make_tmp(case, name))
    return Memory(Settings(
        db_path=root / "m.db", routes=(), debounce_ms=0,
        humanize_min_ms=0, humanize_max_ms=0,
        typing_min_ms=0, typing_max_ms=0,
    ), engines="none")


class StopNoDropTest(unittest.TestCase):
    def _db_path(self, m):
        return pathlib.Path(m.settings.db_path)

    def _facts(self, m):
        import sqlite3
        con = sqlite3.connect(str(self._db_path(m)))
        try:
            return con.execute("SELECT predicate, object FROM fact").fetchall()
        finally:
            con.close()

    def test_slow_model_does_not_lose_the_write(self):
        """worker 手里正在跑的 job：stop() 要等它收尾，不许被 daemon 硬杀。"""
        m = make_memory(self, "stop-nodrop")
        try:
            with mock.patch.object(m.writer, "pool", SlowPool()):
                m.remember("我住在杭州", ("local", "u1", "default"))
                ok = m.flush(0.2)
                self.assertFalse(ok, "模型慢时 flush 应超时（这是触发条件）")
                # close 在同一个 with 里调：worker 还在 pool.chat 里睡着
                m.close()
            rows = self._facts(m)
            self.assertTrue(any(r[0] == "居住地" and "杭州" in r[1] for r in rows),
                            f"关停丢掉了正在处理的写入：{rows}")
        finally:
            try:
                m.close()
            except Exception:  # noqa: BLE001
                pass

    def test_queued_jobs_are_drained_offline(self):
        """flush 超时后**队列里**还没轮到的 job：离线兜底（heuristic）落库。"""
        m = make_memory(self, "stop-drain")
        try:
            # 池子挂死：第一次调用就永远不回（PoolExhausted 场景之外的另一种抖动）
            class DeadPool:
                name = "dead"

                def chat(self, messages, background=False):
                    time.sleep(30)

            with mock.patch.object(m.writer, "pool", DeadPool()):
                # 第一个 job 会被唯一的 worker 拿去死等；第二个留在队列里
                m.remember("我住在苏州", ("local", "u3", "default"))
                time.sleep(0.3)
                m.remember("我的猫叫土豆", ("local", "u3", "default"))
                m.flush(0.2)
                m.close()
            rows = self._facts(m)
            objects = [r[1] for r in rows]
            self.assertTrue(any("苏州" in o for o in objects)
                            or any("土豆" in o for o in objects),
                            f"关停兜底一个都没落：{rows}")
        finally:
            try:
                m.close()
            except Exception:  # noqa: BLE001
                pass

    def test_close_with_slow_pool_still_counts_processed(self):
        m = make_memory(self, "stop-stats")
        try:
            with mock.patch.object(m.writer, "pool", SlowPool()):
                m.remember("我的猫叫土豆", ("local", "u2", "default"))
                m.flush(0.2)
            m.close()
            rows = self._facts(m)
            self.assertGreaterEqual(len(rows), 1, "兜底路径的 fact 要落库")
        finally:
            try:
                m.close()
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    unittest.main()
