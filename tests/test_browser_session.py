"""浏览器会话的离线可测部分：profile 残留清理（不需要真的起 Edge）。

为什么值得单独测：2026-11-05 用户报"login_douyin.bat 打不开"，根因是有头会话
结束后残留的 msedge.exe 还握着 `user-data-dir`，下一次 launch 直接失败/超时；
清理逻辑本身是纯文件操作，可以离线断言，不该靠"再跑一次看看"。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from aiji.left_hand.browser import reap_profile


def workdir(name: str) -> Path:
    path = Path(".tmp") / name
    path.mkdir(parents=True, exist_ok=True, mode=0o777)
    return path


class ReapProfileTest(unittest.TestCase):
    def test_removes_stale_lockfile(self):
        prof = workdir("reap_profile_1")
        lock = prof / "lockfile"
        lock.write_text("stale", encoding="utf-8")
        killed = reap_profile(prof)
        self.assertIsInstance(killed, int)
        self.assertFalse(lock.exists(), "残留 lockfile 必须被清掉")

    def test_missing_profile_dir_is_not_fatal(self):
        prof = Path(".tmp") / "reap_profile_missing_dir" / "nope"
        # 目录不存在时不能抛（清理是 best-effort，不该挡住主流程）
        self.assertIsInstance(reap_profile(prof), int)

    def test_singleton_files_are_removed(self):
        prof = workdir("reap_profile_2")
        names = ("SingletonLock", "SingletonCookie")
        for name in names:
            (prof / name).write_text("x", encoding="utf-8")
        reap_profile(prof)
        for name in names:
            self.assertFalse((prof / name).exists(), f"{name} 应该被清掉")


if __name__ == "__main__":
    unittest.main()
