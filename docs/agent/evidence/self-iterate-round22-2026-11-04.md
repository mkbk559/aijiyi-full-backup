# 自我迭代第 22 轮（2026-11-04 00:52–01:05）：测试套件在偷偷上外网——549 次真实请求 → 0

这一轮的发现完全是"审计"出来的：我本来在核对 v53 的覆盖率，日志里却滚出一堆

```
HTTP Request: GET https://raw.githubusercontent.com/ricklamers/shell-mcp/main/README.md "HTTP/1.1 200 OK"
HTTP Request: GET https://raw.githubusercontent.com/robots.txt "HTTP/1.1 404 Not Found"
```

数一下：**v51 = 527 次、v52 = 441 次、v53 = 549 次**真实外网请求。测试套件不隔离。

## 逐层刨出来的三个根因

### a) `make_server()` 默认拉起三个"会自己上外网"的常驻循环

`aiji/throat/server.py::make_server()` 里三个循环全是 **opt-out**（默认开）：

| 循环 | 开关 | 首轮延迟 | 会做什么 |
|---|---|---|---|
| `LifeLoop` | `AIJI_LIFE_LOOP`（默认开） | 60s | 主动行为（可能触发研究/学习） |
| `MCPResearchLoop` | `AIJI_MCP_RESEARCH_LOOP`（默认开） | 声称 60s | 搜 GitHub + 抓 README |
| `StickerUpdateLoop` | `settings.sticker_update`（默认 True） | 120s | 拉 jsdelivr 表情包 |

测试里 boot 服务 → 循环全起来；整包要跑 5–7 分钟 → 首轮延迟到点后真的开始干活。

### b) 研究循环的 `initial_delay_s` 是**假的**（这条是产品缺陷，不只是测试）

```python
def _run(self):
    while not self._stop.is_set():
        if self._stop.wait(self.due_delay()):   # ← 首跑时 due_delay() 返回 0.0
            return
        self.trigger_and_wait(...)

def due_delay(self, *, now=None):
    latest = self.researcher.snapshot().get("latest") or {}
    last = float(latest.get("created_at") or 0.0)
    if not last:
        return 0.0                              # ← 从没跑过 = 立刻跑
```

`initial_delay_s` 只出现在 `snapshot()` 里，**从没参与调度**。所以部署时"`AIJI_MCP_RESEARCH_INITIAL=60`
表示 60 秒后才研究"是错的：**服务一启动就搜 GitHub、抓 20 个 README**。
修法：首轮 `wait(max(initial_delay_s, due_delay()))`；跑完之后按
`due_delay() or interval_s` 排下一次（保留"重启不重跑"的原意）。

### c) `AIJI_STICKER_UPDATE` 这个开关根本不存在

我一开始在 `tests/__init__.py` 里关三个 env，结果 sticker 循环照样起来：
它只看 `Settings.sticker_update`，没有任何环境变量入口。
现在 `make_server` 里统一成"配置意愿 **且** 环境开关不为 0"。

## 修法（三层）

1. `tests/__init__.py`：`HERMETIC_DEFAULTS` 把三个开关设成 `0`（`setdefault`，要测循环的用例自己 patch 回来）
2. `AijiHTTPServer.server_close()` 本来就会停所有循环——查过了，泄漏点不是它，是"循环在跑的时候测试还在继续"；配合 1 才干净
3. 研究循环真的等首轮延迟（产品修复，见上）

回归守卫：`tests/test_suite_hermetic.py`（默认值进环境、默认服务三个循环都为 `None`、注册表照样在）、
`tests/test_mcp_research.py::test_loop_honours_initial_delay_before_first_run`（延迟内不许研究）
与 `test_loop_runs_once_the_delay_elapses`（延迟过了要跑一次）。

## A/B（同一台机器、同一套用例，唯一变量是本轮改动）

| 版本 | 用例数 | 结果 | 日志里的真实外网请求 |
|---|---|---|---|
| v51 | 1158 | 1157 ok / **1 failure** | **527** |
| v52 | 1203 | 1203 ok | **441** |
| v53 | 1203 | 1203 ok | **549** |
| **v54** | **1207** | **1207 ok / 0 / 0** | **0** |

计数命令（可复跑）：对 `tools/archcheck/results/suite_vNN.log` 数 `HTTP Request:` 行。

顺带修出的两个"看起来在跑其实没跑"：

- `/api/mcp` 以前要求**注册表和研究循环都存在**，否则 503 `mcp_unavailable`。
  于是 `AIJI_MCP_RESEARCH_LOOP=0` 一开，控制台「MCP 研究」面板整块 503——
  **可选组件拖垮整页**。现在：注册表在就 200，`research: null` + `research_enabled: false`。
- `srv.mcp_research_loop` 在关闭时仍被赋了一个"没 start 的循环对象"，
  自检/面板看起来像在跑。现在没跑就是 `None`。
- MCP 注册表/研究循环启动失败原来是裸 `except: pass` 式静默（`/api/mcp` 只会 503），
  现在有 `logger.warning`。

## 线上验证（重启 + 探针，原始输出）

源码改了必须重启：`taskkill /PID 2952 /F` → `Start-ScheduledTask 'AIJiYi Console Watchdog'`
→ 5s 后 `/health` 200 version=0.8.3。

```
# 归档 …\010429-live-probe-round22-9bee79\stdout.txt
GET  /health            -> 200 version=0.8.3
POST /turns  (空白)     -> 400 {"error": "content_required"}
POST /turns  (正常)     -> 202 {"queued": true, "queue_depth": 1}
GET  /api/operations/services -> 200 ready=4/5 actions_available=True
GET  /api/capabilities  -> 200 bytes=2427

# 归档 …\010452-live-probe-loops-v2-111d4f\stdout.txt
/api/mcp 200 research_enabled= True running= True servers= 3
/api/stickers 200 count= 204 auto_update_capable= True loop_running= True
```

（线上是"默认开启"的正常部署，所以 `research_enabled=True`；关掉时的 200 + null 行为由
`tests/test_openai_api.py::test_mcp_registry_and_manual_research_endpoint` 钉住。）

## 数字

- 全量 **v54：1207 ok / 0 errors / 0 failures**（整包覆盖率 93%，10024 语句 / 680 未覆盖）
- 新增/改写用例：`test_sticker_edges.py` 49、`test_growth_save_race.py` 5、
  `test_suite_hermetic.py` 2、`test_mcp_research.py` 2（本轮），`test_openai_api` 两条改为"默认关 / 显式开"

## 本轮边界

- `LifeLoop` 与 `StickerUpdateLoop` 的"首轮延迟是否也被忽略"没有逐个验（研究循环已验）；
  sticker 循环的 `_run` 是第一句 `wait(initial_delay_s)`，读过代码是好的
- 线上部署默认仍开三个循环：这是产品选择，不是缺陷；要关现在有 `AIJI_*_LOOP` / `AIJI_STICKER_UPDATE` 三个入口
- 下一轮候选：`extract.py` 86%、`real_engines.py` 88%、`server.py` 85%；控制台按钮级 E2E
