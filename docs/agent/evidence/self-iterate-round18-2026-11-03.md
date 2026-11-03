# 自我迭代第 18 轮（2026-11-03 23:45–00:00）：服务面板与识别层打到 100%

两个模块都不是"难看"的地方，是**手**和**眼**：
`service_control.py` 是控制台按钮的手（真去 `schtasks` 注册/删除计划任务、真拉起脚本），
`recognize.py` 是所有输入的眼睛（OCR/ASR/链接/表情 → 标准文本）。
它们的共同点是"写错了不报错"：按钮点了没反应，或者识别层开始编文本。

## `service_control.py`：84% → **100%**（`tests/test_service_control.py`，45 条）

| 组 | 钉住的行为 |
|---|---|
| `_run` | stdout+stderr 合并；`OSError`/`TimeoutExpired` 都转成 `(-1, 消息)` 而不抛 |
| `_local_port` | `127.0.0.1:8787`→8787、`[::]:8899`→8899、`8787`/`0.0.0.0:abc`/`:`→None |
| `_netstat_rows` | 只收 `TCP`/`TCPV6` 五行行（UDP 丢掉）；**2 秒内复用同一份快照**；过期后重取 |
| `_port_state` | LISTENING/PID、ESTABLISHED 计数；PID 非数字→None |
| `_resolve_processes` | 多个 PID **只查一次** powershell；新鲜缓存不查；空/0 PID 直接 `{}`；解析不出来留空串 |
| `_listening` | netstat 说了就不做 TCP 连接；**presence(8789) 永不做裸连接**（会在对方日志刷 ERROR traceback）；其他端口回落 connect，失败即 False |
| `watchdog()` | 已注册；未注册时 detail 取第一行非空输出、没有输出才用退出码；**定义文件 `System32\Tasks\AIJiYi Console Watchdog` 存在算第二证人** |
| `_watchdog_on/off` | 非 Windows / 缺脚本 → RuntimeError；注册参数核到 `-SC MINUTE -MO 5 -TR <脚本>`；删除核到 `/Delete`；失败把 schtasks 的原文抛出去 |
| `launch/control` | 未知动作 ValueError；非 Windows/缺脚本 RuntimeError；Popen 校验 `-File <脚本> -Action restart`、`cwd` 为仓库根；白名单四个动作逐一派发 |
| `snapshot()` | 全停→blocked 指向第一个、全通→all_ready、onebot 看 ESTABLISHED、本进程 PID 标成"核心（本进程）"、进程名查不到留空、脚本缺失时 `actions_available=false` |

### 顺手修掉一个真隐患

`snapshot()` 里 `_established(port)`（onebot 那条）写在 `try/except OSError` **外面**：

```python
try:
    listening = _listening(port)
    connections = _port_state(port)[1]
except OSError:
    listening, connections = False, 0
linked = bool(_established(port)) if key == "onebot" else listening   # ← 在 try 外
```

`_established` 内部还是走 `_port_state` → `_netstat_rows`。`_run` 自己吞掉了 `OSError`，
所以平时不炸；但只要 `_port_state` 抛一次 `OSError`，整个
`/api/operations/services` 就是 500 —— 控制台服务面板白屏。
已把这一句移进 `try`，`except` 里连 `linked` 一起兜成 `False`，
并加用例 `test_probe_failure_still_renders_panel`（探针全抛 OSError 时面板仍要出结果）。

## `recognize.py`：82% → **100%**（`tests/test_recognize_edges.py`，35 条）

钉住的都是硬约束（模块 docstring 里写死的那几条）：

- **空结果 ≠ 低置信**：`("", 0.9)` → `status="empty"` + `reason=no_text`；
  低置信单独标 `low_confidence`（这条是 2026-11-03 体检踩过的坑：3 760 条 low_confidence
  里 880 条其实是"空"）
- **没有引擎不许编**：无 OCR → `unsupported/no_ocr_engine` 且 `text=""`；无 ASR 同理
- **超时要能取消**：`_timed` 抛 `FutureTimeout` 前 `future.cancel()`；
  `recognize` 转成 `status="timeout"` 并记 `timeout_s`
- **异常不许炸 worker**：识别器抛错 → `status="error"` + `reason="RuntimeError: …"`
- **SSRF 先过关**：私网 → `error/private_url`；非 80/443 端口 → `port_not_allowed`；
  `UrlGuardError.code` 原样带出；解析器可注入（离线测试不该因 DNS 抖动变 error）
- 细节：`_clean` 的 NUL→空格/空白折叠/3+ 换行→2/`MAX_TEXT` 截断标记；
  `_default_fetch` 认 `charset=`、未知编码回落 utf-8；表情只有表情时
  "用户发了表情：难过"、有正文时"正文（表情：开心、庆祝）"；
  `file` 优先用调用方给的文本、纯文本 mime 缺文本时 `need_text_from_caller`、
  二进制 mime `no_extractor_for_mime`；`video` 只留元数据且 `usable=False`
- `stats()` 的计数与开关回显

## `mcp_client.py`：83% → **100%**（`tests/test_mcp_client.py`，30 条）

这是**把外部进程拉起来**的唯一入口，安全约束全在配置校验里。每条拒绝都断言到具体文案：

- 配置读不出来 / 不是 JSON / `servers` 不是对象 → `MCPConfigError`
- 非 dict 的 server 条目跳过；名字会被洗成 `[A-Za-z0-9_.-]`，洗完为空则丢弃
- `command` 必填；命令与 args 里的 shell 元字符（`; & | < > ` 换行）一律拒绝
- **启用的 server 命令必须命中白名单**（`uvx/npx/node/python`）；没启用的可以用别的命令，
  但 `get()` 会以 `disabled` 拒绝
- `args` 必须是字符串列表；`env` 必须是 str→str；`allowed_tools` 必须显式列出
- `timeout_s` 夹到 [5, 300]、`max_output_chars` 夹到 [1000, 100000]
- `env` 里的 `$VAR` 会展开
- **空 `allowed_tools` 是"什么都不许"，不是"全放行"**（专门钉住这条语义）
- 调用门槛：工具不在白名单 → `MCPToolDenied`；`read_only=false` → 拒绝；锁被占 → `busy`
- `_run_sync` 只透传 `MCPError` 家族，其余异常裹成 `MCPError(_error_text)`；
  `ExceptionGroup` 要摊平成 `甲; 乙`（否则真实报错只剩一句 "unhandled errors in a TaskGroup"）
- 会话行为：`list_tools` 按白名单过滤、`call_tool` 把非文本块记成 `[类名]`、
  超 `max_output_chars` 追加 `...[truncated]`、`is_error` 透传、`probe_sync` 写缓存
- 缺 `mcp` 依赖 → `MCPError("MCP dependency is missing")`（不是 ImportError 冒到 worker）
- 有 `env` 时传 `{**os.environ, **cfg.env}`，没有则传 `None`（完全继承）

## 验证

- `tests/test_service_control.py` 45/45、`tests/test_recognize_edges.py` 35/35、
  `tests/test_mcp_client.py` 30/30
- 覆盖率：`service_control.py` 100%（196 语句）、`recognize.py` 100%（156 语句）、
  `mcp_client.py` 100%（162 语句）
- ruff 全过
- 全量 suite v47：**1093 ok**（含 service_control；其余用例在这之后补的）
- 全量 suite v48：1128 条，**1127 ok / 1 failure**——失败的是我自己新写的
  `test_empty_result_is_empty_not_low_confidence`：识别器超时预算只给了 0.2 秒，
  整包满负载时"瞬时"的 lambda 排队也会超时，`empty` 抖成 `timeout`。
  已把 DispatchTest 的预算放宽到 2 秒（要测超时的用例自建 0.2 秒实例）。

## 下一轮候选

1. `left_hand/mcp_client.py` 83%、`sticker_service.py` 86%
2. 控制台按钮级 E2E（真 Edge 窗口，`gui_*` 驱动）
3. `data/voices/makabaka` 786 MB 的取舍（要不要进 git-lfs / 能不能瘦身）—— 这条需要人拍
