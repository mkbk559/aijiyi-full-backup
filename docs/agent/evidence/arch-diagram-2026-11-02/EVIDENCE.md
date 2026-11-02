# 证据：全项目交互式架构图（docs/architecture.html）

日期：2026-11-02 · 交付物：`docs/architecture.html`（单文件、零外部依赖，53,528 B，sha256 `d6fdd2bd…`）

## 这张图的事实来源（不是凭印象画的）

| 事实 | 取法 | 落盘 |
|---|---|---|
| aiji/ 下 55 个 .py 模块的职责 | 用 `ast` 读每个模块的 docstring 首行 + 顶层 class/def 名 | `C:\Users\22166\.dsh\evidence\2026-11-02\204957-aiji-module-map-utf8-8fe12c\stdout.txt`（13,227 B 派生文件） |
| 数据库真实规模 | `sqlite3` 以 `file:data/aiji.db?mode=ro` 只读打开，逐表 COUNT(*) | `…\205047-aiji-db-inventory-ro-a6babe\stdout.txt` |
| 服务端口与依赖链 | `aiji/service_control.py` 第 26–30 行常量 | 直接读源码 |
| HTTP 路由 | `aiji/server.py` 的 `path ==` / `startswith` 分支 | 直接读源码 |
| 控制台视图 | `aiji/web/operations.html` 的 `data-view` 四个取值 | 直接读源码 |
| 模型路由 | `aiji/config.py` 的 `DEFAULT_ROUTES`（workbuddy 8788 / agent2api 3065 / 4 条公网） | 直接读源码 |
| 未决与已知边界 | `docs/agent/PROJECT_STATE.md` 的「未决 / 下一步」「已知风险」 | 直接读文件 |

只读统计（2026-11-02，`mode=ro`，未写库）：schema `user_version=11`；
`fact` 21,365 · `turn` 24,817 · `turn_archive` 13,719 · `fact_log` 23,469 · `media` 7,639 ·
`style` 1,918 · `reflection` 1,843 · `injection` 1,512 · `trait` 1,511 · `account` 722 ·
`heart_state` 5 · `instance` 2 · `being_event` 1,000 · `being_note` 28 · `life_card` 376 ·
`summary` 1,350 · `profile` 1,151 · `fact_link` 4,494；
`outbox` / `send_queue` / `write_queue` / `event_log` / `self_memory` = **0 行**。
共 30 个 table 名（24 张业务表 + FTS5 的 5 张影子表 + `sqlite_sequence`）。

## 怎么验证它是好的

1. **语法**：把页面内联脚本抽出来跑 `node --check` → exit 0
   （`…\205839-arch-html-js-syntax-1d2d8e\stdout.txt`）。
2. **几何 + 交互（机械判定，不靠眼睛）**：用 `node:vm` 造最小 DOM 桩，直接跑页面真实脚本，
   断言 15 条 —— 节点/边齐全、路径无 NaN、每层 x 严格递增不重叠、点节点出详情并只保留邻居、
   搜索能过滤、分层开关生效、主链路只留主链路、hash 深链生效。
   结果 **15/15 PASS**（`…\210246-arch-html-smoke-interaction-2-1a0d49\stdout.txt`，`SMOKE: PASS`）。
   第一次跑是 14/15，失败项是我自己的断言写错（搜索用例前没清掉 pinned），改断言后全绿。
3. **真机视觉**：真 Edge 窗口打开 `file:///D:/AIJiYi/docs/architecture.html`，桌面截图 +
   视觉模型复核：9 条分层带齐全、无文字溢出、无穿框、右侧面板没有遮住任何方框、四边无截断
   （`…\.vision-router\artifacts\.runs\vision-run-mvz9p6sz-...\screenshot-1793624470960.png`）。

复现脚本（当时放在 `.tmp/arch/`，会被 `tools/cleanup_junk.py` 清掉，需要时按下面重建）：
`dump_modules.py`（模块 docstring）、`db_inventory.py`（只读表统计）、
`extract_js.py`（抽 `<script>`）、`smoke_layout.js`（DOM 桩 + 15 条断言）。

## 验证边界（如实）

- **Chrome/Edge headless 在本沙箱不可用**（`vision_html_screenshot` 报
  `secure Chrome sandbox launch failed`），所以像素级证据来自真窗口截图，不是 headless 渲染。
- 真机只验到「加载后 9 层全在、无裁切」。**逐层点开每个节点的抽屉内容没有逐个肉眼复核**，
  它由上面第 2 条机械断言 + 详情模板覆盖。
- 视觉后端在收尾阶段超时（`VISION_TIMEOUT` → `VISION_BACKEND_UNAVAILABLE_THIS_TURN`），
  所以「`#head` 深链打开后右侧显示对话头详情」这一条只有机械断言（`hashPin: true`），没有截图复核。
- **一次误操作**：为了让 Edge 打开 `#head` 深链，我用 SendInput 往「当前前台窗口」打了
  `ctrl+l` + 45 个字符的 URL + 回车；随后截图显示前台其实是 Chrome 的抖音直播间（有群成员聊天面板），
  而不是 Edge。无法确认那串 URL 是否作为一条发言被发出。**若直播间里出现
  `file:///D:/AIJiYi/docs/architecture.html#head`，需要人工撤回。**
  教训：驱动真实桌面时，`gui_*` 报的 focusedWindow 与视觉截图里的真实前台可能不是同一个窗口，
  发送按键前必须先用截图确认前台，而不是相信句柄。
