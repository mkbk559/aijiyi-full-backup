"""测试包的公共入口：先把"会自己上外网"的常驻循环关掉。

`make_server()` 默认会启动三个后台循环（`AIJI_LIFE_LOOP` / `AIJI_MCP_RESEARCH_LOOP` /
`AIJI_STICKER_UPDATE` 都是 opt-out）。整包跑一次要好几分钟，**每个** boot 出来的服务实例
都会在首轮延迟（60s/120s）之后真的发起请求——2026-11-04 的 suite_v53 日志里就有
549 行 `HTTP Request: GET https://raw.githubusercontent.com/...`：
测试套件在跑真实 GitHub 搜索 + 抓 README。

后果不是"慢一点"：套件从此依赖出网、结果随网络抖动、还会把不受信内容拉进进程。
所以这里统一设默认值；要测循环本身的用例**显式**patch 环境变量再建服务
（见 `tests/test_openai_api.py::test_server_runs_bounded_life_loop`）。
"""
from __future__ import annotations

import os

HERMETIC_DEFAULTS = {
    "AIJI_LIFE_LOOP": "0",
    "AIJI_MCP_RESEARCH_LOOP": "0",
    "AIJI_STICKER_UPDATE": "0",
    "AIJI_JANITOR": "0",
    "AIJI_PROACTIVE": "0",
}

for _key, _value in HERMETIC_DEFAULTS.items():
    os.environ.setdefault(_key, _value)
