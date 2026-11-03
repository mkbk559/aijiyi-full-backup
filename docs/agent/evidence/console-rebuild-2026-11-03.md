# 控制台重构为「架构图对应」+ 桌宠残迹清理（2026-11-03）

用户要求：**桌宠全部删除；控制台重构为架构图对应**。

## 一、桌宠：代码里早就没了，这轮清的是残迹

先说清楚现状（有机器证据，不是回忆）：

- `aiji/` 全文搜 `桌宠|pet_state|deskpet|tkinter` → **只剩 1 处文档字符串**
  （`service_control.py` 的 presence 描述），没有实现、没有 `data/pet_state.json`、
  没有任何宠物素材（`whale|pet|sprite|女仆` 全仓库 0 命中）。
- 回归测试 `tests/test_console_workspace.py::test_removed_pet_routes_do_not_create_state`
  一直在守：`/api/pet`、`/api/pet/pet`、`/assets/whale_spritesheet.webp` 必须 404，
  且不能生成 `pet_state.json`。

这轮清掉的残迹：

| 位置 | 处理 |
|---|---|
| `aiji/right_hand/service_control.py:30` | presence 描述从"供桌宠与前端同步"改成"WebSocket 状态广播，供前端与控制台实时同步" |
| `docs/agent/PROJECT_STATE.md` 未决 #11 | 划掉，改成"桌宠已整体删除（2026-11-03）"，点名守住它的回归测试 |
| `docs/agent/PROJECT_STATE.md` 已知风险「`pet_state.json` 两个写入方」 | 划掉（风险随功能消失） |
| `docs/agent/testing/acceptance-checklist.md` §4 | 标题改「控制台（桌宠…作废）」，删 P3-06/P3-07 两条桌宠验收项 |
| `docs/agent/evidence/console-2026-10-30/workspace-pet.png` | 删除（历史截图，功能已不存在） |

**保留**：历史审计/证据文档里提到桌宠的文字（那是当时的记录，删掉等于篡改历史）；
`PROJECT_STATE.md` 第 5 行本来就写着"宠物已按用户要求移除，下文仅作历史记录"。

## 二、控制台：从"手写 4 视图"改成"架构图生成"

### 问题

旧页面 `operations.html`（48.6 KB，手写）只有 4 个视图：概览 / 全部具体功能 / 功能配置 /
从启动到结束。而 `docs/architecture.html` 的模型是**九大身体系统 + 68 个节点**——
两边对不上，用户在控制台里看不到"这条链路属于哪个器官"。

### 做法：单一数据源生成

新增 `tools/console_build.py`：

- 读 `tools/archcheck/results/nodes.json`（由 `docs/architecture.html` 抽出，含 BANDS/NODES），
  按 band 生成每页的**节点卡**（标题 / 一句话 / 归属代码路径）；
- 每个系统配一份**真实接口清单**（面板 + 动作），生成 `aiji/right_hand/web/operations.html`。

生成的页面：左栏 9 个身体系统（① 感官 … ⑨ 骨架）+ 运维（⚙ 配置 / ⏻ 生命周期），
每页 = 该系统在架构图里的节点 + 实时数据面板 + 可执行动作按钮（带参数输入框和返回 JSON）。

各系统挂的接口（全部实测存在）：

| 系统 | 面板 | 动作 |
|---|---|---|
| ① 感官 | `/stats`、`/api/recent-turns`、`/api/capabilities` | `POST /ingest`、`POST /turns` |
| ② 咽喉 | `/health`、`/api/integrations/onebot`、`/api/capabilities` | `POST /v1/gateway/messages` |
| ③ 大脑 | `/api/models`、`/api/self`、`/api/operations/features`、`/api/proactive` | — |
| ④ 海马体 | `/api/memories`、`/api/memory-graph`、`/api/injections`、`/stats` | `/remember`、`/recall`、`/inject`、`/feedback` |
| ⑤ 内分泌 | `/api/life`、`/api/proactive`、`/api/scheduler`、`/api/being/checkup` | `POST /api/being/checkup/live` |
| ⑥ 心脏 | `/v1/voices`、`/api/stickers`、`/api/social`、`/v1/songs` | `/v1/audio/speech`、`/v1/voices/use`、`/api/stickers/update`、`/api/social/qzone/publish` |
| ⑦ 左手 | `/api/mcp`、`/api/actions/pending`、`/api/actions/stats` | `POST /api/mcp/research` |
| ⑧ 右手 | `/api/operations/{services,features,config}`、`/api/fs` | 启停服务、`/api/fs/write`、`/flush` |
| ⑨ 骨架 | `/stats`、`/api/operations/services`、`/media`、`/health` | — |

页面 23.7 KB、**零外链**（无 CDN、无外部字体），身份四件套
（platform / platform_user_id / scope / session）在左栏随时可改，带身份的接口自动拼查询串。

### 验收：`tools/archcheck/console_check.py`

三重机器校验（产物 `results/console_check.json`）：

```
{"ok": true, "unknown_paths": [], "external_refs": [], "page_status": 200,
 "nine_systems": true, "server_errors": []}
{"bands": 9, "nodes": 68, "systems": 9, "spec_paths": 55, "server_routes": 56, "html_bytes": 23686}
```

1. **规格完整性**：9 个 band、68 个节点全部有归属，没有空页；
2. **路由一致性**：页面引用的 **55 条** GET/POST 路径，逐条比对 `server.py` 的 **56 条**
   精确路由 + `startswith` 前缀，**0 条不存在**（这条抓出过一个真 bug：
   我一开始写的 `/api/operations/overview` 在服务端根本不存在，route_smoke 也证实没这条）；
3. **真机可达**：起服务 → `GET /operations` 200、9 个导航项都在、
   每个系统的第一块面板真打一遍（无 5xx）。

### 改动的测试

- `test_operations_views_have_navigation`：从"校验 4 个 `view-*` 元素"改成
  "校验生成的 SPEC 里必须是 entry/ingress/orch/memory/life/out/tools/ops/data 九项
  + settings/lifecycle 两项运维视图"。
- `test_operations_page_replaces_console`：标记词换成新页面（九大系统/海马体/骨架/
  功能配置/从启动到结束），并断言**无 `cdn` 外链**。

## 三、没做/做不到的

- **没有浏览器截图验证**：本机 Chrome/Edge headless 都被沙箱 ACL 拦住
  （`vision_html_screenshot` 报 `secure Chrome sandbox launch failed`，与 excluded #49 同因）。
  视觉层面目前只有"HTML 结构 + 真机 HTTP 200 + 九系统导航词存在"三重证据，
  渲染细节（卡片换行、窄屏）仍未自动验。
- 架构图改了之后要手动跑一次 `python tools/console_build.py` 重新生成页面
  （已写进脚本 docstring，但没有 CI 钩子）。
