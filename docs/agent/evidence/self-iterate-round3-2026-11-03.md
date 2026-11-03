# 自我迭代第 3 轮（2026-11-03 15:10–16:00）

第 2 轮补的是"没人量过的功能"，这一轮盯**日志噪声、循环任务的假成功、以及覆盖率 40–55% 那一段**。
数字全来自落盘输出。

## 一、日志噪声：客户端断开被当成服务端故障

`data/console.err.20261103-152735.log`（17 KB）里成篇的都是同一坨：

```
ConnectionResetError: [WinError 10054] 远程主机强迫关闭了一个现有的连接。
  File ".../socketserver.py", line 697, in process_request_thread
  ...
```

浏览器/探针提前关连接就会这样，socketserver 的 `handle_error` 把**任何**异常都打成 traceback，
真问题反而被埋。处置：`AijiHTTPServer.handle_error` 把
`ConnectionResetError / ConnectionAbortedError / BrokenPipeError` 降成一行 debug，
其余照旧交给父类。`tests/test_server_errors.py` 3 条钉住"静默的静默、真异常照样打"。

## 二、自动研究：跑完了但什么都没查到，也算"成功"

`left_hand/mcp_research.py`：所有 GitHub query 都 403 时，代码 `continue` 到底，
照样写出一份空报告、`stats["runs"] += 1`，loop 认为这轮没问题 → 6 小时后照跑，
日志里每轮刷一屏 `GitHub MCP query failed: GitHub HTTP 403`（全量单测日志里数百行）。

处置：
- 全 query 失败 → 抛 `RuntimeError("GitHub 查询全部失败（N 条 query）")`（不再产假报告）
- 首个失败留 warning，其后同因失败降 debug；README 拉取失败前 3 条 warning、其后 debug
- `MCPResearchLoop`：连续失败按 `interval × 2**min(n,4)` 退避、封顶 24 h，成功清零，
  `snapshot()` 多出 `consecutive_errors` / `backoff_s`
- `tests/test_mcp_backoff.py` 6 条（含"退避封顶""成功清零"）

## 三、覆盖率 40–55% 那一段（`coverage run --source=aiji` + 全量 discover）

| 模块 | 补测前 | 补测后 | 新增用例 |
|---|---|---|---|
| `left_hand/actions/gui.py` | 33% | **98%** | 20 |
| `hippocampus/real_engines.py` | 41% | **88%** | 17 |
| `hippocampus/media_audio.py` | 53% | **98%** | 18 |
| `hippocampus/janitor.py` | 53% | **91%** | 6 |
| `heart/song_service.py` | 47% | **62%** | 22 |
| `brain/persona.py` | 50% | **93%** | 9（上一轮） |
| `endocrine/desktop_sense.py` | 0% | **92%** | 11（上一轮） |
| `left_hand/mcp_research.py` | — | **95%** | 6 |

这一轮补测又抓到两个真问题（都是"代码看起来在做事，其实没做"）：

1. **`gui._mouse_click` 的死三元**：
   `down, up = ((0x0002,0x0004) if button=="right" else (0x0002,0x0004))` 两边完全一样；
   紧跟的 `if button == "right"` 才是真逻辑。而且"非 right 一律当左键"——
   传 `"middle"` 或拼错会**静默左键点一下**，用户以为没反应。改成显式 `ValueError`。
2. **`real_engines` 注释与公式不符**：注释写 whisper `avg_logprob` 映射 `-0.2→0.85`，
   实际公式 `0.85 + lp*0.4` 得 0.77。按公式修正注释（并让用例断言 0.77）。

## 四、验证

- 全量 `tools/archcheck/run_suite.py` → **695/695 ok**（`suite_v30.json`）
- `ruff check aiji tools tests` 全过
- 覆盖率总览：`aiji` 包 84%（本轮目标模块 88–98%）
- 真机：服务重启后 `/api/operations/features`、`/api/capabilities`、`/api/self` 均 200；
  `data/aiji.db-wal` 从 46.18 MB → 3.93 MB（janitor TRUNCATE 生效）

### 补：`handle_error` 的真机验证（15:58）

不只是"单测过了"：重启服务后**真的**用 `SO_LINGER=0` 发了 3 个 RST 的 POST
（声明 `Content-Length: 1000000` 只发 100 字节就带 RST 关闭），这正是老版本刷出
17 KB traceback 的场景：

```
log: console.err.log baseline: 81
growth bytes: 0 in console.err.log
traceback count in whole file: 0
10054 mention count: 0
```

日志零增长、无 traceback。对照老版本 `console.err.20261103-152735.log`（17 KB 全是 10054）。


## 五、下一轮候选

1. `left_hand/actions/executor.py` 50%、`throat/bridge/octop_gateway.py` 53% 是现在最低的两块；
2. `song_service` 62% 剩的都是真跑 separator/ffmpeg 的分支（`_separate` / `_mix` / `_download`）——
   可以用假 `_run` 验命令拼装与产物识别，不必真跑；
3. 控制台浏览器检查只验首屏，点动作按钮后的 DOM 变化还没自动化；
4. `data/voices/makabaka` 786 MB 里 `dataset_v1..v3`(205 MB)+`separated`(161 MB) 是训练中间产物，
   删不删需要用户拍板（不属于"bug"，属取舍）。
