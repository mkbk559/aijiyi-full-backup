# 自我迭代第 17 轮（2026-11-03 23:20–23:45）：server.py 的开关门逐条走一遍（并抓到一个真 bug）

`server.py` 是全库最大的模块（923 语句），覆盖率 80%，189 行没跑到。
但这一轮不是"追行数"：没覆盖的那部分**几乎全是开关门**——

- 工作区默认关（`AIJI_ENABLE_WORKSPACE`）
- 终端三重开关（工作区 → `AIJI_CONSOLE_SHELL` → `AIJI_UNSAFE_SHELL`）
- 越界路径（`../`）、超大请求体、空内容的入队入口

这些分支写错了不会抛异常：要么"点了没反应"，要么更糟——**本该拒绝的放行了**。
所以每一条都朝两个方向各试一遍。

## 新增 `tests/test_server_routes.py`（32 条，全真 HTTP）

| 组 | 内容 |
|---|---|
| `GetPanelTest` | 28 个面板路由逐个断言 200 + 关键字段（`/health`、`/stats`、`/v1/voices`、`/v1/songs`、`/api/social`、`/api/stickers`、`/api/memories`、`/api/memory-graph`、`/api/self`、`/api/scheduler`、`/api/models`、`/api/capabilities`、`/api/operations/*`、`/api/growth`、`/api/recent-turns`、`/api/injections`、`/api/proactive`、`/api/actions/*`、`/api/integrations/onebot`、`/api/being/checkup`、`/media`、`/style`、`/being`、`/accounts`、`/v1/models`）；`/` 返回控制台 HTML；未知路径 404 JSON；`limit=abc` 400 |
| `MemoryEndpointTest` | `/turns`、`/remember`、`/media`、`/ingest`、`/recall`、`/inject`、`/feedback`、`/flush`、`/being/note`、`/being/event` 的空内容 400 与正常 202/201；未知审批 404；`/v1/embeddings` 正常与 257 条拒绝 |
| `WorkspaceGateTest` | 关闭时五个工作区端点全 403 `workspace_disabled`；打开后写→列目录→读→取原始字节全通；`../outside.txt` 与 `notes/../../outside.txt` 都 403 `path_outside_root`；写空路径/写目录的 400 |
| `ShellGateTest` | 三重开关逐层：工作区关 403、`AIJI_CONSOLE_SHELL=0` 403 `shell_disabled`、`AIJI_UNSAFE_SHELL=0` 403 `unsafe_shell_disabled`、空命令 400、真跑一条 `echo` 断言 exit 0 与 stdout、`cwd=../..` 403 |
| `UnconfiguredServiceTest` | 没配 NapCat 时空间发布 503 `social_not_configured`（**不许假成功**）、空正文先 400、OneBot webhook 503、网关空正文 400、点歌空源、未知 song id、`/v1/voices/use` 无 profile 400 `profile_required`、空文本合成 400 `text_required` |

## 抓到真 bug：一条**空白消息也算一条对话**

写 `/turns` 的"空内容要 400"用例时它返回 **202**：

```python
# server.py
def _clip_text(value, limit=32_768):
    text = str(value or "")          # 没有 strip
    if len(text) <= limit: return text, {}
```

`"   "` 是真值 → 一路穿过 `if not content: 400` → `memory.turn(...)` 入队。
后果不只是脏数据：空白轮次会占掉一格对话、进反思批次、参与 `turn_count` 统计
（而 `turn_count` 决定摘要触发点）。

修法：`_clip_text` 先 `strip()`（`original_chars` 仍报原始长度，截断信息不丢），
顺带给 `/ingest` 的 text 分支补上与 `/turns`、`/remember` 一致的空白检查。

## 验证

- `tests/test_server_routes.py` 32/32
- 全量 suite + 整包覆盖率：v46（见日志）
- ruff 全过

## 下一轮候选

1. 整包覆盖率往 93%+ 推：`recognize.py` 82%、`mcp_client.py` 83%、`service_control.py` 84%
2. 控制台按钮级 E2E（真 Edge 窗口）
3. `being_*` 表按 persona 存、人格只有一份 —— 多实例语义
