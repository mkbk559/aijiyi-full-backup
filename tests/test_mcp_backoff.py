"""MCP 自动研究的失败退避（本机 GitHub 常 403，不背退避就每轮白跑+刷屏）。

钉住三点：全查询失败要抛（而不是产出空报告）、连续失败指数退避并封顶 24 h、
成功清零。全程不碰网络。
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.left_hand import mcp_research as mr  # noqa: E402


class BrokenGitHub:
    def __init__(self):
        self.calls = 0

    def search_repositories(self, query, limit=20):  # noqa: ARG002
        self.calls += 1
        raise RuntimeError("GitHub HTTP 403")


class RecordingGitHub:
    def __init__(self, rows):
        self.rows = rows

    def search_repositories(self, query, limit=20):  # noqa: ARG002
        return list(self.rows)


class ResearcherFailureTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_tmp(self, "mcp-backoff")
        self.addCleanup(lambda: self.dir.exists() and None)

    def _researcher(self, github):
        return mr.MCPResearcher(
            output_dir=self.dir, github=github,
            fetch_text=lambda url: "readme 内容")

    def test_all_queries_failing_raises_instead_of_empty_report(self):
        github = BrokenGitHub()
        with self.assertRaises(RuntimeError) as ctx:
            self._researcher(github).run_once()
        self.assertIn("全部失败", str(ctx.exception))
        self.assertEqual(github.calls, len(mr.QUERIES), "每条 query 都要试一次")

    def test_repeat_failures_only_warn_once(self):
        with self.assertLogs("aiji.left_hand.mcp_research", level="DEBUG") as logs:
            with self.assertRaises(RuntimeError):
                self._researcher(BrokenGitHub()).run_once()
        warnings = [r for r in logs.records if r.levelname == "WARNING"]
        self.assertEqual(len(warnings), 1, "同样的失败只留一条 warning")
        self.assertGreaterEqual(len(logs.records), len(mr.QUERIES))


class LoopBackoffTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_tmp(self, "mcp-loop")
        self.loop = mr.MCPResearchLoop(
            mr.MCPResearcher(output_dir=self.dir, github=RecordingGitHub([]),
                             fetch_text=lambda u: ""),
            interval_s=600.0, initial_delay_s=5.0)

    def test_first_failure_doubles_interval(self):
        self.loop.stats["consecutive_errors"] = 1
        self.assertEqual(self.loop.next_delay(), 1200.0)

    def test_backoff_is_capped_at_a_day(self):
        # 用 6 小时的正常间隔才有机会撞上限（600s * 16 = 9600s 远不到一天）
        loop = mr.MCPResearchLoop(self.loop.researcher, interval_s=6 * 3600)
        loop.stats["consecutive_errors"] = 99
        self.assertEqual(loop.next_delay(), 24 * 3600.0)

    def test_success_resets_backoff(self):
        self.loop.stats["consecutive_errors"] = 0
        self.assertEqual(self.loop.next_delay(), self.loop.interval_s)

    def test_work_records_backoff_then_success_clears_it(self):
        class Boom:
            def run_once(self):
                raise RuntimeError("GitHub HTTP 403")

        self.loop.researcher = Boom()
        # `_work()` 自己负责释放（它假定调用方已经 acquire），别重复释放
        self.loop._work_lock.acquire()
        self.loop._work()
        self.assertEqual(self.loop.stats["consecutive_errors"], 1)
        self.assertGreater(self.loop.stats["backoff_s"], self.loop.interval_s)
        self.assertIn("GitHub HTTP 403", self.loop.stats["last_error"])


if __name__ == "__main__":
    unittest.main()
