"""写入队列积压告警：越过阈值要骂一句（60s 不重复），回落后说一声。

背景（真机实测）：64 条并发 /ingest 单条抽取 ≈4.9s，队列一度积压 56 条，
但没有任何日志——只有主动 /flush 才能看到 queue_depth。
"""

import pathlib
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aiji.brain.pool import ModelPool  # noqa: E402
from aiji.hippocampus.db import DB  # noqa: E402
from aiji.hippocampus.recall import Recaller  # noqa: E402
from aiji.hippocampus.recognize import Recognizers  # noqa: E402
from aiji.hippocampus.store import Ident  # noqa: E402
from aiji.hippocampus.writer import Writer  # noqa: E402
from aiji.right_hand.config import Settings  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOG = "aiji.hippocampus.writer"


def workdir() -> Path:
    path = ROOT / ".tmp" / f"writer-backlog-{uuid.uuid4().hex[:8]}"
    path.mkdir(mode=0o777, parents=True, exist_ok=True)
    return path


def writer(settings: Settings) -> Writer:
    return Writer(
        DB(settings.db_path),
        ModelPool(()),
        Recaller(settings.db_path, budget_ms=50, recent=10),
        settings,
        Recognizers(timeout_s=0.1),
    )


def settings(directory: Path, warn: int = 200) -> Settings:
    return Settings(db_path=directory / "w.db", routes=(), workers=0,
                    writer_backlog_warn=warn)


class BacklogWarningTest(unittest.TestCase):
    """workers=0：没有 worker 消费，提交多少积多少，方便逼出阈值。"""

    def test_crossing_threshold_warns(self):
        w = writer(settings(workdir(), warn=2))
        ident = Ident("probe", "u1", "default")
        with self.assertLogs(LOG, level="WARNING") as logs:
            for _ in range(3):
                w.submit_text(ident, "hello")
        self.assertEqual(len([r for r in logs.records if r.levelname == "WARNING"]), 1)
        self.assertIn("积压", logs.records[0].getMessage())

    def test_warning_does_not_repeat_within_window(self):
        w = writer(settings(workdir(), warn=2))
        ident = Ident("probe", "u1", "default")
        with self.assertLogs(LOG, level="WARNING") as logs:
            for _ in range(5):
                w.submit_text(ident, "hello")
        self.assertEqual(len([r for r in logs.records if r.levelname == "WARNING"]), 1,
                         "60s 窗口内不许刷屏")

    def test_drained_logs_recovery_and_rearms(self):
        w = writer(settings(workdir(), warn=4))
        ident = Ident("probe", "u1", "default")
        with self.assertLogs(LOG, level="INFO") as logs:
            for _ in range(5):
                w.submit_text(ident, "hello")          # 越阈值 → warning
            for _ in range(4):
                w._q.get_nowait()                       # 模拟消化
            w._note_backlog(w._q.qsize())               # 回落到一半以下
        warnings = [r for r in logs.records if r.levelname == "WARNING"]
        infos = [r for r in logs.records if r.levelname == "INFO" and "消化" in r.getMessage()]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(len(infos), 1, "回落要提示'积压已消化'")
        # 闸门要重置：再次越过阈值能再骂
        with self.assertLogs(LOG, level="WARNING") as logs2:
            for _ in range(5):
                w.submit_text(ident, "hello again")
        self.assertEqual(len([r for r in logs2.records if r.levelname == "WARNING"]), 1)

    def test_below_threshold_is_silent(self):
        w = writer(settings(workdir(), warn=200))
        ident = Ident("probe", "u1", "default")
        for _ in range(3):
            with self.assertNoLogs(LOG, level="WARNING"):
                w.submit_text(ident, "hello")

    def test_docstring_is_honest_about_what_a_kill_loses(self):
        source = pathlib.Path(ROOT / "aiji" / "hippocampus" / "writer.py").read_text("utf-8")
        self.assertIn("所有未处理事件", source)
        self.assertNotIn("最多丢最后一个未处理的事件", source,
                         "旧说法在实测面前是错的：kill 丢的是整个队列")


if __name__ == "__main__":
    unittest.main()
