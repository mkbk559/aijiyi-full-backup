> **历史文档（设定已废弃）**：这里的「大肥鱼 / 鱼缸」是 2026-11-03 之前的人设，
> 那一天已整体替换为「玛卡巴卡」（约 6 岁半的小孩，见
> `docs/agent/design/persona-child-2026-11-03.md`），相关记忆与素材均已清空。
> 本文只作当时的决策/研究记录，不要当作现行设定。

# 全量模块审计 —— AIJiYi（2026-11-01）

> 目标：把"这个项目到底有哪些功能模块、哪些真在生产里跑、哪些没实现/没接线"逐条钉死，每条都带可复算的证据。
> 方法：静态盘点（AST 导出 63 个模块的 docstring + 公开 API）+ 全量单测 + 只读查生产库 `data/aiji.db` + 活服务 `/health` + 端口/日志/代码路径交叉验证。
> 口径：**"代码存在" ≠ "已接线" ≠ "生产在用" ≠ "已验证"**，本表按这四档分开标注。

证据归档（本次审计，全部为机器原始输出）：

| 编号 | 内容 | 归档 |
|---|---|---|
| E1 | 全量单测（项目解释器 Python 3.13） | `C:\Users\22166\.dsh\evidence\2026-11-01\201214-audit-unittest-py313-7c79f5\stdout.txt` → `Ran 438 tests ... OK`，exit=0 |
| E2 | 全量单测（`.venv` Python 3.12，空依赖） | `...\200648-audit-unittest-full-b13129\stdout.txt` → `Ran 438 ... FAILED (failures=1, skipped=1)`，exit=1 |
| E3 | 模块 API 清单（63 模块） | `...\201028-audit-api-inventory-9b99a1\stdout.txt` |
| E4 | 未实现/占位标记扫描 | `...\201049-audit-markers-b4753b\stdout.txt`（markers 9、空函数体 3、裸 except 0） |
| E5 | 模块↔测试映射 | `...\201141-audit-test-map-8834c3\stdout.txt` |
| E6 | 生产库行数/状态分布 | `...\201654-audit-db-stats-e01a93\stdout.txt` |
| E7 | 生产库媒体/反馈细节 | `...\201724-audit-db-detail-86d282\stdout.txt` |
| E8 | 图片 media 行原文（诊断 OCR） | `...\201748-audit-image-media-a7afa7\stdout.txt` |
| E9 | 活服务 /health（v0.8.3） | `...\201912-audit-live-health-dee017\stdout.txt` |
| E10 | 监听端口 | `...\201945-audit-listen-ports-9a3dca\stdout.txt` |
| E11 | 工作区默认关闭实测 | `...\202016-audit-shell-default-5d5299\stdout.txt` → `403 {"error":"workspace_disabled"}` |
| E12 | 路由表静态提取 | `...\202026-audit-routes-0ce3ee\stdout.txt` |
| E13 | AstrBot 现场日志（图片描述失败计数） | `D:\AstrBot\astrbot-stable-20261101-183646.out.log` → `image description failed` ×108 |
| E14 | 活服务 `/being`（自我生活当前状态） | `...\204411-audit-live-being-931444\stdout.txt` → energy 0.01 / social 0.01 / proactive_ok false |
| E15 | being_event / life_card 全貌 | `...\204159-audit-life-being-93ba8a\stdout.txt` |
| E16 | 能量收支与每日事件曲线 | `...\204316-audit-life-econ-77d988\stdout.txt` → 24h 839 事件、energy 净 -77 |
| E17 | 反思产出率 / trait 分布 | `...\204400-audit-reflection-2be543\stdout.txt` → 1122 行、41% 有产出、52.8% summary 空洞 |

可复算脚本（本次新增，只读，留在 `tools/_audit_*.py`）：`_audit_dump_api.py`（模块 API）、
`_audit_markers.py`（未实现标记）、`_audit_coverage_map.py`（模块↔测试）、`_audit_db_stats.py` /
`_audit_db_detail.py` / `_audit_image_media.py` / `_audit_life_being.py` / `_audit_life_econ.py` /
`_audit_reflection.py`（生产库，`immutable=1` 只读打开）、`_audit_routes.py`（路由）。

规模：`aiji/` 63 个模块 16,419 行；`tests/` 43 文件 8,096 行 448 个 test 方法；`tools/` 43 个 py + 2 个 ps1；`bench/` 5 个脚本；HTTP 约 65 条路由。

---

## 一、模块清单（按层，状态四档）

状态图例：**🟢 生产在用**（库表有量或活服务在跑）｜**🟡 已实现·未接线/未启用**｜**🔵 已实现·仅测试或模拟器覆盖**｜**⚪ 外部依赖/未实现**

### 1. 记忆内核（memory-core）

| 模块 | 职责 | 状态 | 证据 |
|---|---|---|---|
| `db.py` | schema v10 + v1→v10 迁移、按线程连接、写事务串行化 | 🟢 | E6 `user_version: 10`；`fact_fts` 5503 行 |
| `store.py` | 身份/事实/冲突消解/画像/风格/摘要/注入/关联/回放/队列 | 🟢 | E6：fact 5503、fact_log 6100、injection 750、summary 359、style 877、profile 411 |
| `recall.py` | 100ms 硬预算、多信号排序、1/2 跳关联、概率回忆 | 🟢（概率回忆除外） | 召回 p99 见 PROJECT_STATE；`mode="chat"` 无调用方（见 G6） |
| `inject.py` | 软引导注入 + 来源标注 + 硬字符预算 | 🟢 | injection 750 行 |
| `writer.py` | 异步写、模型/规则抽取、摘要、超长压缩 | 🟢 | turn 10832、模型摘要计数见 stats |
| `extract.py` | 规则+模型抽取、注入防线、风格/口癖、纠错识别 | 🟢 | E7：heuristic 3110 / model 2418 |
| `pool.py` | 6 路由轮转/冷却/故障转移 | 🟢 | E9 `/health` 返回 6 条路由及冷却秒数 |
| `vectors.py` | hash / hybrid 语义索引（暴力余弦） | 🟡 默认 `vector="off"` | `config.py:50`；无 embedding 端点（excluded #40） |
| `recognize.py` + `real_engines.py` + `media_audio.py` | 识别层 + RapidOCR/FunASR/whisper 适配 | 🟢 语音 / ⚪ 图片（见 G1） | E7：voice ok 420、image 空 1823 |
| `config.py` / `params.py` | 配置、21+ 可调参数集中 | 🟢 | E3 |

### 2. 心脏层（可靠性/调度）

| 模块 | 职责 | 状态 | 证据 |
|---|---|---|---|
| `heart.py` | outbox 消费、CAS 领取、租约、指数退避、死信、回放 | 🟡 生产 0 行 | E6：`outbox 0`、`event_log 0`、`heart_state 0`、`instance 0` |
| `dispatch.py` | 常驻发送线程、限流感知退避、撤回 | 🟡 生产 0 行 | E6：`send_queue 0` |
| `proactive.py` | 静默/冷却/额度/抢占主动搭话 | 🟡 死代码 | 全仓仅 `tests/test_dispatch.py` 引用（G5） |
| `janitor.py` | 软删清理/去重/边封顶/清理窗口 | 🟢 部分 | `STALE_DAYS` 预留未接线（G7） |
| `gate.py` | 出口人格闸门（凭据/长度/黑名单，fail-closed） | 🟢 | `head.py` 调用链 |
| `humanize.py` / `debounce.py` | 打字节奏、连发合并 | 🟢 | `memory.py` 初始化、head 默认启用 |

### 3. 认知与人格

| 模块 | 职责 | 状态 | 证据 |
|---|---|---|---|
| `head.py` | 编排：召回→注入→自我层→模型→闸门→写回；工具循环 | 🟢 | `/v1/chat/completions` 主链路 |
| `selfhood.py` | 物理底线（常量）+ 涌现性格（数据） | 🟢 | E6：`trait 727` |
| `reflect.py` | 后台反思，归纳"我长成什么样" | 🟢 | `reflection 1065` |
| `being.py` | 生命状态/情绪余波/私有笔记/生活事件 | 🟢 | `being_event 857`、`being_note 15` |
| `life.py` | 生命卡七引擎（内分泌/陈述/程序/扩散/预算…） | 🟢 | `life_card 192` |
| `life_loop.py` | 有界后台生活推进 | 🟢 | E9：`steps 199 / created 5 / errors 0` |
| `persona.py` | 孙吧语气（例句/提醒/few-shot） | 🟢 | `data/persona/sunba.json`、`being_state.persona=sunba` |
| `role_boundary.py` | IM 角色空间禁代码/禁执行物理动作 | 🟢 | `head.py` 前置判定 + `test_selfhood` |
| `being_checkup.py` | 四层"成精体检" + 真模型探针 | 🟢 | `/api/being/checkup[/live]` |

### 4. 接入层

| 模块 | 职责 | 状态 | 证据 |
|---|---|---|---|
| `server.py` | 59 条静态路由 + OpenAI 兼容 `/v1/models` `/v1/chat/completions`、SSE、控制台托管、鉴权 | 🟢 | E9/E12；`AIJI_API_KEY` 保护 `/v1` `/api` |
| `cli.py` | doctor/serve/remember/recall/inject/feedback/stats/export | 🟢 手工在用 / 零单测 | E5：tests 无 `aiji.cli` 引用 |
| `admin.py` | 控制台只读数据面 | 🟢（经 server 间接覆盖） | `tests/test_console_api.py` |
| `bridge/onebot.py` | NapCat/OneBot11 直连桥 | 🟡 未启用（改走 AstrBot 插件） | `AIJI_ONEBOT_API` 未设即关闭 |
| `bridge/octop_gateway.py` | Octop Gateway 桥 | ⚪ 需 `octop-gateway` 包（optional extra `im`） | `pyproject.toml` |
| `social_service.py` | QQ 空间发说说/评论/头像/签名/分享 | ⚪ 需 NapCat HTTP | `enabled=bool(settings.onebot_api)` |
| `sticker_service.py` | 本地图库 + 公网 feed 原子更新 | 🟡 本机未开 | E9：`sticker_update: null` |
| `song_service.py` | 点歌→截段→分离→Seed-VC 换声→混音 | ⚪ 需 ffmpeg/分离模型/Seed-VC 9880 | E10：9880 未监听 |
| `voice_service.py` | 声线注册表 + Edge/SAPI/GPT-SoVITS/CosyVoice/HTTP | 🟢 | `data/voices/dafeiyu` |
| `mcp_client.py` + `mcp_research.py` | 只读白名单 MCP 客户端 + GitHub 自动研究 | 🟢/🟡 | E9：`runs 4`、68 仓库、10 README、1 候选；无 token 时 GitHub 403 |
| `actions/*` | 文件/终端/桌面工具 + 策略 + 人工审批 | 🟡 默认关在 IM 侧 | `actions_enabled=True`，但 `role_boundary` 在 IM 拦；GUI 动作需审批 |
| `pet/*` | 桌宠（精灵/养成/语音/视觉/dsh 桥） | 🟢 | `data/pet_state.json`、`pet.log` |

### 5. 前端与工具

| 模块 | 说明 | 状态 |
|---|---|---|
| `aiji/web/console.html` | 控制台，实际 **4 页签**（仪表盘/对话/记忆/日志），另有 **11 个 section 是死视图** | 🟢 但含死代码 |
| `aiji/web/vendor/echarts.min.js` | 1.1MB ECharts | 🟢 |
| `aiji/pet/app.py` | Tk 桌宠（Alt+Space/划词/截图/语音/翻译） | 🟢 |
| `bench/*` 5 个 | recall/http/inject/memory_quality/demo | 🟢 |
| `tools/*` 45 个 | 现场启停、探针、e2e、数据修复、评测 | 🟢 |

---

## 二、未实现 / 未生效 / 名存实亡（按严重度）

### P0 —— 功能"看起来有、生产里没生效"

**G1. 图片 OCR 在生产里 100% 空产出**
- 现象：`media` 表 `kind=image` **1823 条全部** `status=low_confidence`、`text` 为空、`confidence=0.0`、`elapsed_ms≈0.1`。
- 根因（代码路径，非猜测）：`memory.py:140-151` 的 OCR 适配器只读 `item.extra["bytes"] / ["bytes_list"]`，没有 url 分支；`bridge/onebot.py` 与 AstrBot 插件都只传 `url`（插件注释原文：`without sending bytes`）。对照：ASR 有 url 回退 `media_audio.transcribe_item_audio`，所以语音 420 条正常。
- 影响：图片既不进记忆、也不参与抽取；"任意输入降维"这条承诺对图片不成立。
- 证据：E7、E8、`aiji/memory.py:151` vs `aiji/memory.py:161`。

**G2. 反馈闭环从未被真实流量触发**
- 现象：`injection` 750 行 **feedback 全为 NULL**（752/752 统计口径），`fact.hit_count > 0` 的行数 **= 0**。
- 根因：只有 `/feedback`、CLI `feedback`、`Memory.feedback()` 三个入口，AstrBot 插件从不回报 `used_ids`。
- 影响：排序的"反馈加成"、纠错的 `cited_ids` 精确作用域、S3 全闭环，在生产里全部未启用——只在 sim/单测里成立。
- 证据：E7。

**G3. 视觉对话在核心缺失**
- `head._content_text` 只挑 `type in ("text","input_text")`，`/v1/chat/completions` 里的 `image_url` part 被静默丢弃。
- 图片描述走插件外部的 `AIJI_VISION_URL`（默认 `127.0.0.1:3070`，E10 未监听）；现场日志 108 次 `image description failed`（Pillow 解码错误）。
- 影响：核心不是多模态的；视觉能力依赖一个未随项目交付的外部端点。

**G4. 心脏层（outbox/dispatch/死信/租约/回放）生产闲置**
- `outbox / send_queue / event_log / heart_state / instance` 全为 0 行；实际发送由 AstrBot 插件自己完成。
- 影响：这套"崩溃恢复 ≤TTL、幂等、CAS 领取"只在模拟器与单测里被证明过（E6）。

### P1 —— 已实现但没接线 / 边界未做

- **G5** `proactive.py` 全仓无生产调用方（仅 `test_dispatch`），主动搭话实际在插件内。
- **G6** `recall(mode="chat")` 概率回忆无任何调用方（`grep` 仅命中 recall.py/params.py）。
- **G7** `janitor.STALE_DAYS` 预留未接线，`stats["stale_deleted"]` 恒 0（E4 标记扫描）。
- **G8** `media.expires_at` 只记录不刷新，签名 url 过期需调用方重取。
- **G9** 向量层默认 off；网关无 `/v1/embeddings`（excluded #40）；无近似索引，仍暴力余弦。
- **G10** 工作区/终端默认关闭（E11 实测 403）；开了也是单命令 + 15s，无 PTY，命令本身不可沙箱。
- **G11** 控制台 11 个 section（graph/persona/accounts/life/models/actions/proactive/voice/integrations/config/workspace）为**死 markup**：`titles` 只含 4 个键，`navigate()` 到不了；聊天统计与 ECharts 无自动化浏览器测试。
- **G12** 桌宠状态 `data/pet_state.json` 跨进程后写覆盖；无多宠物。
- **G13** 语音打断/VAD 未做（`pet/voice.py` 只有 `stop_speaking`）。
- **G14** 长回复分段、通用白名单/管理员、死信重试 UI 未做（对应 `acceptance-checklist.md` P1-13/P1-14/P3-05）。
- **G15** OCR/ASR 质量只在合成样本验证；密集表格短单元格确定性漏检（excluded #48）。
- **G16** MCP 研究：无 `GITHUB_TOKEN` 时 403；部分 README 抓取 ConnectError；发现结果只记录不自动接入（设计如此）。
- **G17** 无日志轮转（无 `RotatingFileHandler`）；`data/` 已 556MB、`console.err.log` 41KB 全是未处理连接异常堆栈。

### P2 —— 工程卫生 / 可交付性

- **G18** 无 git 仓库（`git log` → not a repository）、无 CI、无 LICENSE（README 提"开源"，但根目录无许可证）。
- **G19** 137 个残留临时目录（`tmp*`、`aiji-style-*`、`aiji-being-checkup-*`、`torchinductor_*`）+ 2 个 0 字节垃圾文件（名字里带中文引号/`finally`，是 shell 转义事故）。
- **G20** 版本漂移：`pyproject` 0.8.3 / 活服务 0.8.3，但 `dist/` 只有 0.4.0–0.5.1；README 写"426 项"，实际 438。
- **G21** `.venv`（Python 3.12、零可选依赖）跑测试会 1 失败（E2）；项目真实解释器是 `D:\Python313`（E1 全绿）。**别用 `.venv` 判健康**。
- **G22** 零测试引用的模块：`cli.py`、`admin.py`（仅经 server 间接）、`pet/avatar.py`、`pet/desk.py`、`pet/sprite.py`、`pet/voice.py`；`real_engines.py` 只有 mock 与独立 e2e 脚本。

---

## 三、测试与验证现状

- 全量：**438 个用例，Python 3.13 下 OK**（E1，耗时 239.7s）。`.venv` 下同一套代码 1 失败 1 跳过（E2），失败项 `test_unready_profile_falls_back_to_ready_edge_profile`，根因是 `_provider_ready()` 对 `edge` 要求 `import edge_tts`，空 venv 里缺包 → 回退目标不可用。**属环境差异，不是代码回归**。
- 覆盖：`test_memory.py` 84 例最多；`test_selfhood` 33、`test_openai_api` 30。仿真器（`tools/sim_platforms.py`）与 e2e（`tools/e2e_heart.py`）不在 unittest 内。
- 未覆盖的关键面：真实公网模型池（`smoke_live_pool.py` 只在本地假服务证过）、真实用户拍摄/录音的 OCR/ASR 质量、控制台浏览器交互、视觉端点。

## 四、建议下一步（按收益/成本排序）

1. **G1 图片 url→bytes 下载**（30 行以内，复用 `media_audio` 的下载+`net_guard` 私网拦截），让 OCR 真正生效——这是当前最大的"名存实亡"。
2. **G2 插件回传 `used_ids`**：回复生成时把实际引用的 object 映射回 fact id，POST `/feedback`。没有它，纠错/排序反馈全是死代码。
3. **G3 决定视觉归属**：要么核心透传 `image_url` 到上游（`head._content_text` 保留 part），要么把 3070 端点纳入交付（写进 README/启动脚本）。
4. **G4 二选一**：承认 AstrBot 是发送方、把 heart 层降级为可选（文档标注），或把插件发送收回心脏层。
5. **G11/G19/G20 清理**：删死视图与残留目录、加 LICENSE、对齐版本号与 README 数字、加 `.github` 最小 CI（unittest）。
6. **G7/G8/G13 收尾**：STALE_DAYS 接线或删参数；media 过期刷新；VAD/打断按需。

---

## 五、一句话结论

**记忆内核、认知人格、接入层是"真在跑"的**（生产库 5503 事实 / 10832 轮 / 727 性格 / 359 摘要，活服务 v0.8.3）。
**心脏层、反馈闭环、概率回忆、图片识别是"代码在、链没通"的**；**视觉、真实 embedding、ANN、PTY、VAD、死信重试 UI 是"确实没做"的**；**工程面（git/CI/LICENSE/版本/目录卫生）是"还不能交付"的**。
自我生活**接了线但经济系统失衡**（详见第六节）：活着，但永远"很累"。

---

## 六、自我生活 / 高级逻辑层深剖（2026-11-01 追加）

证据：E14 `...\204411-audit-live-being-931444\stdout.txt`（活服务 `/being`）、
E15 `...\204159-audit-life-being-93ba8a\stdout.txt`、E16 `...\204316-audit-life-econ-77d988\stdout.txt`、
E17 `...\204400-audit-reflection-2be543\stdout.txt`；可复算脚本 `tools/_audit_life_being.py`、
`tools/_audit_life_econ.py`、`tools/_audit_reflection.py`。

### 6.1 七个引擎，真正接在对话链路上的只有三个

| 引擎 / 方法 | 生产调用方 | 结论 |
|---|---|---|
| `PersonalityKernel.apply_delta` | **无**（仅 `test_life.py`） | ❌ 死代码：人格向量永远是 `{"identity":0.25}`（抽样 200/200 全同） |
| `EndocrineEngine.perturb/regress` | `head._life_event` → `memory.life_event`（每轮） | ✅ 活：210 张卡里 25 张偏离基线，之后回归 |
| `endo_factor` | `head.py:219` 调注入条数（3~12） | ✅ 活（唯一进入行为参数的地方） |
| `skill_ceiling` | 仅 `LifeCard.snapshot` | ⚠️ 只进 `/api/life` 展示，不进对话 |
| `DeclarativeEngine`（短/中/长衰减、晋升、沉降） | **无** | ❌ 死代码；且与 `recall.half_life_days()` 的半衰期是**两套重复实现** |
| `ProceduralEngine`（遗忘曲线、间隔重复 EF） | **无** | ❌ 死代码 |
| `OfflineCompensation.compress_days` | `LifeCard.tick` | ✅ 活（离线时间压缩） |
| `ContextBudget.allocate` | `memory.life_context` ← `GET /api/life` | ⚠️ 可调用，**不在对话链路** |
| `SynapticDiffusion.diffuse` | **无** | ❌ 死代码 |
| 持久化 `LifeCard.to_dict` | 只存 `personality / endo / last_tick` | 声明性、程序性状态**即使算了也不落库** |

> 即：所谓"V5 七引擎"，生产里是 **情绪（内分泌）+ 离线补偿 + 注入配额** 三个在跑，
> 另外四个（人格增量、陈述性记忆、程序性记忆、突触扩散）是纯函数 + 单测。

### 6.2 自我生活（being）：活着，但永远"很累"

活服务 `/being` 原样（E14，2026-11-01 20:44）：

```json
{"energy": 0.01, "energy_word": "很累", "social": 0.01, "stress": 23.49,
 "proactive_ok": false, "activity": "电量不太够，安静歇了一会儿…",
 "current_focus": "在休息", "life_seq": 21, "online_minutes": 714.7}
```

- 近 24h：`being_event` 839 条，**能量净变化 -77**（每小时都是负的：00 时 -4.8、19 时 -4.2、23 时 -24.0）。
- 活动选择：20 次里 **16 次是"休息/睡觉"**，仅 2 次发呆、1 次听歌、1 次写小本子。
- 因果闭环（代码路径，非猜测）：
  `BeingState.observe()` 每条消息扣 `0.5+0.8×强度`（实测 -0.7/条）→
  白天 `tick()` 每小时再 -0.8 → 只有睡眠 `+3/h`、quiet_rest `+4/40min` →
  **能量永远 < 28** → `advance_life` 只走"电量不够"分支 → `prompt_block` 每轮都写
  "现在确实有点累：回复更短，少开新话题，不要硬装精神"。
- 连带：`can_proactive` 要求 `energy≥20 且 social≥15`，两者恒为 0 → **主动搭话被永久阻断**
  （`proactive_ok:false`），这解释了上一节 G4 里"主动"为什么只剩插件侧自己发。
- 私有小本子：15 条里 **13 条是同一句**"刚才有点被刺到，先不全说出来。"
  根因 `infer_event()` 把"笨蛋/气死/滚/讨厌/生气/傻/垃圾"一律判成 anger，
  而这几句在孙吧式群聊里是日常玩笑 → 她每轮都被"刺到"，且这两条 note 每轮进 prompt。

### 6.3 反思与性格涌现：在跑，但一半空转

- `reflection` 1122 行：**41% 有 trait 产出**，**52.8% 的 summary 是空的/无意义**（"没有/暂无/无法"）。
- 今天一天跑了 **980 次**反思——每次都是一次模型调用（30s 超时、3 次重试）；
  实测最近的反思原文是"最近的对话记录里没有大肥鱼的发言，无法归纳新的说话习惯"，`deltas: []`。
- `trait` 786 行、24 条/身份封顶（`TRAIT_MAX`），置信度 **732 条落在 0.5–0.7**，进 prompt 的只有前 6 条。
- 结论：反思机制是通的（游标幂等、证据投票都对），但**触发阈值太松**（`every_turns` 很小 +
  worker 每 5s 扫一遍），产出的大部分是"没有可归纳的"；真性格信号被 700 多条弱 trait 稀释。

### 6.4 "高级逻辑"的真实作用面

| 层 | 真正影响输出吗 | 依据 |
|---|---|---|
| 自我层 `system_prefix`（物理底线 + 涌现性格前 6 条 + `STYLE_RULES`） | ✅ 每轮进 system | `head.py:253-266` |
| 生活状态块 `being_block`（精力/心情/社交/近况/小本子） | ✅ 每轮进 system | `being.prompt_block` |
| 心境一行 `_life_prompt` | ✅ 每轮进 system（但只有 4 种取值） | `head.py:539` |
| 内分泌 → 注入条数 `endo_factor` | ✅ 影响召回条数 | `head.py:219` |
| 记忆冲突消解 / 排序 / 关联 / 摘要 | ✅ 影响注入内容 | 见第一节 |
| 出口闸门 `gate` / 角色边界 `role_boundary` | ✅ 拦截回复内容 | `head.py:271,437` |
| 声明性/程序性记忆、人格增量、突触扩散、上下文预算 | ❌ 不影响任何输出 | 6.1 表 |

### 6.5 这一层的修复建议（按性价比）

1. **重做能量经济学**：按"每条消息 -0.7、白天 -0.8/h、恢复 ≤+6/h"算，任何活跃群都会把能量打到 0。
   要么把 `observe` 的消耗与消息重要度/是否真的回复挂钩（现在连"收到"也 -0.7），
   要么把睡眠/休息的恢复提高一个量级，并给 `social` 单独的回升路径。
2. **小本子去重 + 降权**：同一句反复写入没有信息量；按 `(text, 10 分钟窗口)` 去重，
   并把 anger 判定从"含关键词"改成"关键词 + 指向自己/否定语境"，否则孙吧群聊会把情绪系统打穿。
3. **反思加前置闸**：`fresh` 轮次不足或最近 N 条里没有 assistant 发言时直接跳过，
   不调模型（现在 59% 空转、930+ 次/天）。
4. **死引擎二选一**：`DeclarativeEngine/ProceduralEngine/SynapticDiffusion` 要么接进
   召回/淘汰链路（注意与 `recall.half_life_days` 合并，别留两套时间衰减），要么删掉，
   别在 `LifeCard` 里挂着却永不持久化。

---
