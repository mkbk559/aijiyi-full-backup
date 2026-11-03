# 自我迭代第 1 轮（2026-11-03 14:18–14:45）

用户：「自我迭代，不要问我，直到所有问题消失」。这一轮只做一件事：**把"没有测量"的地方补上测量**，
然后修掉量出来的东西。全部数字都来自落盘的机器输出。

## 一、审计脚本第一版（`.tmp/audit_round1.py`，归档 `…/141846-audit-round1-7ca688`）

| 类别 | 数量 | 结论 |
|---|---|---|
| 吞异常（`except` 里只有 `pass/continue/日志`） | 30 | 5 处该留痕，其余是"可选能力失败继续跑"，正确 |
| 技术债标记 `TODO/FIXME/临时方案` | 1 | 就是审计脚本自己的正则，代码里已清零 |
| 测试里直接碰网络 | 36 | 只有 `test_link_title` 是真外网，已修（见下） |
| 文档引用了不存在的东西 | 84 | 拆分后：**现行文档 20 处真漂移**，其余在历史段落里（正确的） |
| 体积 | — | `voice-engine/rvc/.venv` 7.2 GB 是 CUDA torch，本机有 RTX 3050 → **不是浪费** |

## 二、这一轮修的东西

### 1. 控制台终于有"真浏览器"检查（补上长期空白）
`tools/archcheck/console_render.py`：无头 Edge/Chrome `--dump-dom` + `--enable-logging=stderr`，
断言 ①11 个导航项都在（9 系统 + 配置 + 生命周期）②首屏节点标题全在 ③没有
`Uncaught/TypeError/ReferenceError` ④渲染后 DOM 比静态文件大（证明确实执行了 JS）
⑤没有 `>undefined<` / `>NaN<` 漏出来。
结果：`{"ok": true, "navs_found": 11, "node_titles": 7, "js_errors": [], "js_ran": true,
"dom_bytes": 32422, "html_bytes": 24895}`；用例 `tests/test_console_render.py`（2 条，缺浏览器则 skip）。
**顺手纠正一条旧结论**：控制台里根本没有 ECharts/canvas——那是重构前桌宠版的事，
excluded #49 里"聊天统计与 ECharts 无自动化测试"已过期。

### 2. 文档引用漂移检查（新增常驻守卫）
`tools/archcheck/doc_ref_check.py`：只查**现行**文档（README、PROJECT_STATE 的非决策日志部分、
未标"历史文档"的 design/rules），文件类引用必须真的存在，路由类引用必须在 `server.py` 里。
首轮 20 处漂移 → 修：
- `borrow-plan-2026-11-02.md`：`aiji/quota.py` → `aiji/brain/quota.py`（重组后路径）；
  agent2api 侧 `/api/me`、`/v1/usage` 去掉反引号（那不是我们的路由）；
- `voice-qq-hardening.md`（2026-10-30）：加"历史文档（模块路径已过期）"横幅，说明当时是扁平结构；
- `config/runtime.json` 属"按需创建"（`operations.py` 的 `CONFIG_PATH`），进 `LAZY_OK` 白名单。
现在：`{"ok": true, "checked": 66, "problem_count": 0}`；用例 `tests/test_doc_refs.py`（2 条）。

### 3. 体检探针：串行改并行 + 开机预热 + 点的时候跟着等
- 三个探针原先串行跑，实测 **34.1 s**；改并行（各克隆一份库、各自 `Head`）后
  **19.7 s**（归档 `…/142456-checkup-cold-parallel-4859d7`），三条都走 `workbuddy` 真路由、`degraded=False`。
- 开机预热（`serve()` 里 daemon 线程预热默认身份 + 最近活跃身份）→ 之后点体检
  **0.03 s**（`cached=True, age_s=55.5`）。
- 预热期间点体检：**等它**（`Event.wait`，最多 120 s）而不是自己再克隆一遍库，
  实测 11.4 s 后拿到 `cached=True`，没有第二次模型调用。
- **踩坑**：预热一开始放在 `make_server()` 里，于是每个控制台用例都跟着打真模型；
  `test_checkup_live_is_cached_and_refreshable` 直接多出一次探针（`3 != 2`）。
  已把它挪进 `serve()`（只有真起服务才预热）。

### 4. 五处沉默的错误改成"继续跑但留痕"
`voice_service`（后期处理失败保原声 / 读不出音频时长）、`sticker_service`（失败快照落盘失败）、
`song_service`（旧歌清理失败）、`memory`（模型密钥桥失败——不然只表现为"模型全线降级"）。

### 5. 并行化引出的测试债
`tests/test_being_checkup.py::FakePool` 原来按**调用顺序** `pop(0)` 发回复，
并行后错配（`identity_resisted` 拿到"你记岔了…"）。改成按提示词关键词匹配
（李四 / 大语言模型 / Python）+ 加锁，2 条用例恢复绿。

## 三、验证

- `run_suite.py` v23：572 条，571 ok / 1 fail（就是上面那条顺序依赖，已修）
- `run_suite.py` v24：见 `suite_v24.json`
- 服务真机：`/api/being/checkup/live` 冷 14.7 s（被预热抢占时 11.4 s 等到结果）、热 0.03 s；
  `/api/growth` 仍是 `xp 0.0 / primary`（账本没被测试污染）

## 四、下一轮要看的

1. 30 处吞异常里剩下的 25 处要不要分级（`debug` 而不是静默）；
2. `data/backups` 158 MB、`data/voices` 786 MB 的构成是否都有用；
3. `tests/` 里那些 `with urllib.request.urlopen(self.base + ...)` 是否需要统一开关（现在都是指向本地服务，正确）；
4. 覆盖率热点：哪些模块还是 0%。
