"""WAL 截断的回归：先制造一个超阈值 WAL，再让 janitor 截断它。

背景（实测）：生产库 WAL 涨到 46.18 MB（库本身 41 MB），
`wal_autocheckpoint` 只重置写指针不缩文件。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.hippocampus import janitor as janitor_mod  # noqa: E402
from aiji.hippocampus.db import DB  # noqa: E402


class WalTruncateTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_tmp(self, "wal-truncate")
        self.path = self.dir / "wal.db"
        self.db = DB(self.path)

    def tearDown(self):
        self.db.close()

    def _grow_wal(self, rows: int = 6000) -> int:
        """把 WAL 撑到 MB 级：一个大事务写 6000 行（约 25 MB），
        期间不让自动检查点把文件缩小（TRUNCATE 只有我们显式调用才做）。"""
        conn = self.db.conn()
        conn.execute("CREATE TABLE IF NOT EXISTS wal_blob (v TEXT)")
        conn.commit()
        with self.db.tx() as c:
            c.executemany("INSERT INTO wal_blob (v) VALUES (?)",
                          [("x" * 4000,) for _ in range(rows)])
        return pathlib.Path(str(self.path) + "-wal").stat().st_size

    def test_wal_is_truncated_after_janitor_run(self):
        size = self._grow_wal()
        wal = pathlib.Path(str(self.path) + "-wal")
        self.assertGreater(wal.stat().st_size, 1024 * 1024,
                           f"没把 WAL 撑起来（{size} 字节），这个用例没意义")
        original = janitor_mod.WAL_TRUNCATE_MB
        janitor_mod.WAL_TRUNCATE_MB = 0.5   # 降低阈值，避免测试写 32 MB
        try:
            janitor = janitor_mod.Janitor(self.db)
            stats = janitor.run()
        finally:
            janitor_mod.WAL_TRUNCATE_MB = original
        self.assertEqual(stats.get("wal_truncated"), 1, stats)
        self.assertLess(wal.stat().st_size, 1024 * 1024,
                        "截断后 WAL 应该回到 0")

    def test_small_wal_is_left_alone(self):
        with self.db.tx() as c:
            c.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
        janitor = janitor_mod.Janitor(self.db)
        stats = janitor.run()
        self.assertNotIn("wal_truncated", stats)


if __name__ == "__main__":
    unittest.main()
