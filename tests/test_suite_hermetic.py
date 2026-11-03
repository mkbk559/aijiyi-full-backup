"""套件必须自带隔离：默认不许开任何会自己上外网的后台循环。

2026-11-04 的 `suite_v53` 日志里有 **549 行**
`HTTP Request: GET https://raw.githubusercontent.com/...` ——
测试里 boot 出来的 `make_server()` 默认会把三个 opt-out 的后台循环都拉起来，
其中"研究循环"更是在**启动瞬间**就搜 GitHub + 抓 20 个 README（它压根没用 `initial_delay_s`）。
后果：套件依赖出网、结果随网络抖动、还把不受信内容拉进进程。

这里把三件事钉住：
1. `tests/__init__.py` 的默认值真的进了 `os.environ`；
2. 默认 `make_server()` 起来的 server 上，三个循环都没跑；
3. 研究循环的首轮延迟不再是摆设（另见 `tests/test_mcp_research.py` 的两条）。
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import tests as tests_pkg  # noqa: E402
from tests._tempdir import make_tmp  # noqa: E402
from aiji.hippocampus.memory import Memory  # noqa: E402
from aiji.right_hand.config import Settings  # noqa: E402


class HermeticDefaultsTest(unittest.TestCase):
    def test_defaults_are_declared_and_applied(self):
        self.assertEqual(
            set(tests_pkg.HERMETIC_DEFAULTS),
            {"AIJI_LIFE_LOOP", "AIJI_MCP_RESEARCH_LOOP", "AIJI_STICKER_UPDATE"},
        )
        for key, value in tests_pkg.HERMETIC_DEFAULTS.items():
            self.assertEqual(os.environ.get(key), value,
                             f"{key} 没进环境：套件就会去连外网")

    def test_default_server_starts_no_background_loops(self):
        from aiji.throat.server import make_server

        root = pathlib.Path(make_tmp(self, "hermetic"))
        memory = Memory(Settings(db_path=root / "h.db", routes=()), engines="none")
        server = make_server(memory, "127.0.0.1", 0)
        try:
            self.assertIsNone(getattr(server, "life_loop", None))
            self.assertIsNone(getattr(server, "sticker_loop", None))
            self.assertIsNone(getattr(server, "mcp_research_loop", None),
                              "研究循环默认不能跑：它一启动就会请求 GitHub")
            # 注册表只是读本地配置文件，不联网，所以它应该在
            self.assertIsNotNone(getattr(server, "mcp_registry", None))
        finally:
            server.server_close()
            memory.close()


if __name__ == "__main__":
    unittest.main()
