> **历史文档（模块路径已过期）**：本文写于 2026-10-30，当时包还是扁平结构
> （`aiji/head.py`、`aiji/bridge/onebot.py` 等）。2026-11-02 之后已按九大身体系统
> 重组为 `aiji/{senses,throat,brain,hippocampus,endocrine,heart,left_hand,right_hand,skeleton}/`，
> 本文里的文件路径只代表当时的位置；结论与取舍仍然有效。

# 底座稳定化 + 语音定制 + QQ/IM 接入 方案

> 2026-10-30。目标：把 AIJiYi 从“能跑的记忆内核 + 控制台”补成“**稳定、能接 QQ、声音可定制**”的个人 AI 底座。
> 先出方案，不动代码。所有“现状”都有代码/探测依据；看不到的 bug 需要你给复现路径。

## 0. 一句话结论

- **不建议重写底座**：记忆内核（多平台单表、100ms 召回、软注入、ADD-only）是差异点，重写会丢掉已验证的指标。
- 真正缺的是三块：**缺陷扫荡（P0）**、**语音引擎/音色管理（P0）**、**QQ 生产级接入（P0/P1）**。
- 现状不是“没有 QQ 代码”，而是 **NapCat/OneBot 只有骨架、限流参数没接线、媒体消息被丢、官方 QQ 只有可选进程没有托管**；语音只有 **Windows SAPI 两个音色**。

## 1. 现状体检（本轮实测）

| 项 | 现状 | 证据 |
|---|---|---|
| 单测 | 281/281 通过 | `C:\Users\22166\.dsh\evidence\2026-10-30\201225-unittest-full-console-final-6fb955\stdout.txt` |
| TTS 引擎 | 仅 Windows SAPI；本机只有 2 个音色：`Microsoft Huihui Desktop`（中）+ `Microsoft Zira Desktop`（英） | `.tmp/probe_env.py` 输出 |
| TTS 可定制 | 只有全局 `rate/volume` 参数；无音色选择、无音高、无风格、无克隆、无 per-character 绑定 | `aiji/pet/voice.py` |
| 停止说话 | **坏**：`stop_speaking()` 新建一个 SpVoice 去 Purge，停不掉正在说话的那个实例。实测运行态 2→2（没停）→同一个实例 Purge 才 1（停了） | `.tmp/probe_sapi_stop.py` |
| 叠音 | 每次 `speak()` 新建 SpVoice；`SVSFPurgeBeforeSpeak` 只作用于自己 → 连续两条会各说各的 | 同上 + SAPI 语义 |
| 语音包 | `edge_tts` 未安装、`piper` 未安装；`ffmpeg 9.0.1` 可用；`funasr/whisper/rapidocr` 可用 | 探测输出 |
| QQ（个人） | OneBot 11 代码在，但本机 NapCat 未起（3000/3001 关闭），无 `AIJI_ONEBOT_API` | 端口探测 |
| QQ 限流 | `params.py` 定义了 `QQ_RATE_PER_MIN/QQ_COOLDOWN_S/QQ_MAX_REPLY_CHARS`，**onebot.py 里 0 处引用** | grep 计数 0 |
| QQ 媒体 | 图片/语音/文件在 `_parse_message` 里只变成 `[图片]/[语音]/[文件]` 占位符，不下载、不走 OCR/ASR | `aiji/bridge/onebot.py` |
| QQ 并发 | 单个 worker 串行处理；一条慢回复会堵住所有群/私聊 | `OneBotBridge._loop` |
| 入站鉴权 | 默认 `AIJI_API_KEY` 为空时 `/v1/gateway/onebot` 完全开放；无签名/IP 白名单 | `aiji/server.py::_authorized` |
| 官方 QQ | `octop-gateway 1.0.3` 已安装，但只是独立进程；无 `QQ_APP_ID/QQ_SECRET`，控制台只显示“已安装” | 探测 + `aiji/bridge/octop_gateway.py` |
| OpenAI 兼容 | `temperature/top_p/max_tokens` 被忽略（server 不读、Head 不传） | `aiji/server.py::_chat_completion`、`aiji/head.py` |
| 桌宠 DSH 桥 | 运行时曾报 `@wenaixi/dsh-ponytail` 与 dsh 不兼容，`dsh ask ok=False` | `data/pet.log` 2026-10-30 00:02 |
| 文档漂移 | 文档写 schema v4/61 测，实际 `SCHEMA_VERSION=8`/281 测 | `aiji/db.py:31`、`PROJECT_STATE.md` |

> 这些是“静态审计 + 环境探测”能确认的；你遇到的其余 bug 需要复现路径（见 §10）。

## 2. 目标与非目标

**目标**
1. 常见操作不再静默失败：说话可停/不叠音、QQ 不掉消息、错误可见。
2. 语音可定制：音色/语速/音高/音量/风格可配；可选本地少样本克隆；桌宠与 QQ 共用一套 profile。
3. QQ 可用：个人 QQ（NapCat/OneBot）生产级；官方 QQ 机器人可选；图片/语音进出；限流/鉴权/重连。
4. 不破坏既有指标：召回 p99、注入预算、281 项测试。

**非目标（本轮不做）**
- 不做多租户/公网 SaaS；不改 SQLite 单表底座；不做分布式。
- 不做“什么平台都接一遍”：先 QQ，抽象出接口后再说 Telegram/微信。

## 3. 总体架构

```
外部平台
  QQ个人(NapCat) ─┐
  QQ官方(octop)  ─┼─> aiji/bridge/<adapter>.py ─> 归一化 Inbound ─> Head.reply
  通用 Webhook   ─┘         │                           │
                            ├─ 媒体下载/识别(OCR/ASR)     ├─ 记忆注入
                            └─ 限流/去重/鉴权             └─ 回复出站(文本/图片/语音)
语音
  aiji/voice/
    base.py     TTSProvider / VoiceProfile / SynthesisResult
    sapi.py     Windows SAPI（离线兜底）
    openai.py   OpenAI 兼容 /v1/audio/speech（通用）
    edge.py     可选：edge-tts（在线，音色多）
    piper.py    可选：Piper（离线神经，音色多）
    gpt_sovits.py 可选：本地少样本克隆（HTTP API）
    service.py  注册表/队列/打断/缓存/profile 解析
```

## 4. Phase 0：缺陷扫荡（P0，3-4 天）

> 原则：每条 bug 先写**会失败**的回归测试，再修；修完跑全量 + 真机 smoke。
> 台账：`docs/agent/quality/bug-ledger.md`（新建，含 ID/复现/根因/证据/状态）。

| ID | 问题 | 修法 | 验收 |
|---|---|---|---|
| B01 | `stop_speaking` 停不掉；连发叠音 | 新增 `VoiceEngine` 单例：持有 SpVoice、串行队列、`stop()` 对同一实例 Purge + 释放；`speak` 走队列 | 说话中调 stop → 运行态 1；连发两条只有一条在说 |
| B02 | `speak()` 的 SpVoice 生命周期随函数结束，可能被 GC | 由 VoiceEngine 持有；暴露 `is_speaking()` | 长文本播完前对象不回收 |
| B03 | QQ 限流参数未接线 | `OneBotBridge` 接 `QQ_RATE_PER_MIN/QQ_COOLDOWN_S/QQ_MAX_REPLY_CHARS`；按 group 桶 | 超频请求被延迟/拒绝，不丢事件 |
| B04 | OneBot 入站默认无鉴权 | 共享 secret + 可选 HMAC(`X-Signature`)+ IP allowlist；控制台可配 | 错误签名 401，正确放行 |
| B05 | QQ 图片/语音只留占位符 | 用 OneBot `get_image/get_record` 下载 → `memory.ingest`（OCR/ASR）→ 再回复 | 群里发图能 OCR、发语音能转写 |
| B06 | 单 worker 串行 | 每 scope 一个队列 + 固定 worker 池；全局背压；单条超时 | 一个慢群不堵另一个群 |
| B07 | OpenAI 参数被忽略 | `_chat_completion` 读 `temperature/top_p/max_tokens`，Head 透传给 pool | 同 prompt 下 temp=0/1 可区分 |
| B08 | `/api/shell`、`/api/fs/write` 无认证 | 控制台本地 token（启动生成，HTML 注入/请求头）；默认只绑 127.0.0.1 | 无 token 写/执行 403 |
| B09 | `pet_state.json` 跨进程后写覆盖 | 迁 SQLite `pet_state` 表（schema v9）或文件锁 + 版本号 | 两个进程同时摸头不丢分 |
| B10 | 静默失败（voice pick fallback、send 失败只计数） | 全部转成结构化错误 + 控制台“错误中心” | 失败可见、可重试 |
| B11 | 文档与实现漂移（schema v4 vs 8） | 文档生成/校验脚本；CI 检查 | `PROJECT_STATE` 与实际一致 |
| B12 | 桌宠 DSH 桥插件不兼容 | 启动自检 + 失败回退 FishBrain + 明确提示；固定 profile | `pet.log` 不再出现未处理报错 |

**验收**：台账全绿；281+ 新回归；`bench_recall/bench_inject` 指标不退化；真机：桌宠说/停/连发、QQ 限流、控制台错误中心。

## 5. Phase 1：语音定制（P0，5-7 天）

### 5.1 Provider 矩阵

| Provider | 离线 | 音色 | 克隆 | 依赖/成本 | 建议 |
|---|---|---|---|---|---|
| `sapi` | 是 | 本机 2 个（Huihui/Zira） | 否 | 0 | 默认兜底 |
| `openai` | 否 | 取决于网关 | 否 | `base_url/key/model/voice` | 通用首选（网关支持 `/v1/audio/speech` 时） |
| `edge` | 否 | 100+ 中文 | 否 | `pip install edge-tts` + 出网 | 质量好，但本机可能被 SSL 拦 |
| `piper` | 是 | 多 | 否 | 模型文件（hf-mirror） | 离线神经音色 |
| `gpt_sovits` | 是 | 你的参考音 | **是**（少样本） | torch + 模型 + 本地 9880 API；CPU 慢 | 唯一现实的“克隆”路径 |
| `fish/minimax` | 否 | 多 | 是（云） | key + 出网 | 本机大概率不通 |

**推荐路线**：v1 先做 `sapi + openai + (edge/piper 可选)`，把“换音色/语速/音高/音量/风格 + per-character 绑定”跑通；v2 再上 `gpt_sovits` 克隆（单独安装包，不拖慢主程序）。

### 5.2 数据模型（schema v9）

```sql
CREATE TABLE voice_profile (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  provider TEXT NOT NULL,          -- sapi|openai|edge|piper|gpt_sovits
  model TEXT DEFAULT '',
  voice TEXT NOT NULL,             -- 音色 id/名称
  rate REAL DEFAULT 0,             -- -100..100（SAPI/Edge 语义归一）
  pitch REAL DEFAULT 0,            -- -100..100
  volume REAL DEFAULT 1.0,         -- 0..1
  style TEXT DEFAULT '',           -- 情绪/风格
  sample_path TEXT DEFAULT '',     -- 克隆参考音
  extra TEXT DEFAULT '{}',
  created_at REAL, updated_at REAL
);
CREATE TABLE voice_binding (
  platform TEXT, platform_user_id TEXT, scope TEXT,
  profile_id INTEGER, PRIMARY KEY(platform, platform_user_id, scope)
);
```

### 5.3 API / 控制台

- `GET /api/voice/providers`：provider 是否安装、可用音色列表、缺失原因。
- `GET/POST/PATCH/DELETE /api/voice/profiles`：CRUD。
- `POST /api/voice/preview`：`{text, profile_id}` → wav/mp3（直接播放/下载）。
- `POST /api/voice/clone`：`{provider, name, sample_base64}` → 建 profile（gpt_sovits 异步任务）。
- `POST /api/voice/bind`：把 profile 绑到 `(platform,user,scope)`。
- 控制台“语音工坊”重做：
  - Provider 卡片（就绪/缺依赖/缺 key）
  - 音色下拉 + 语速/音高/音量滑杆 + 试听
  - 新建/复制/删除 profile；设为默认；绑定到桌宠/QQ
  - 克隆向导：录 10-30s/上传 → 训练/注册 → 试听 → 保存
- CLI：`aiji voice list / preview / profile add / bind`。

### 5.4 桌宠 / QQ 落地

- 桌宠 `FishBrain.voice_reply`：用绑定 profile；说话中可被 stop；可选 VAD（检测静音自动停录，不再固定 5s）。
- QQ：群/私聊可开关语音回复；TTS → ffmpeg → OneBot `record`；长文本切段；失败回退文本。
- 语音消息入站：QQ `record` → 下载 → ASR → 记忆（复用现有 `ingest`）。

**验收**：3 个 provider 至少 2 个就绪；控制台试听/切换实时生效；桌宠 stop 生效；QQ 语音消息可回；克隆（若选）能用参考音生成。

## 6. Phase 2：QQ / IM 接入（P0/P1，5-7 天）

### 6.1 个人 QQ（NapCat + OneBot 11）— 先做

- 配置：`AIJI_ONEBOT_API`、`AIJI_ONEBOT_TOKEN`、`AIJI_ONEBOT_SECRET`、群模式、白名单、管理员 QQ。
- 入站：`/v1/gateway/onebot` 校验 secret/HMAC/IP；事件去重；媒体下载。
- 出站：文本分段（`QQ_MAX_REPLY_CHARS`）、@ 回发起人、引用 `message_id`、每群限流、失败重试。
- 并发：每 scope 队列 + worker；全局背压；单条超时。
- 运维：控制台显示连接/最后事件/队列/失败原因；一键测试发消息；NapCat 安装向导。
- 安全：私聊/群白名单；管理员命令（`/ping`、`/mute`、`/reload`）。

### 6.2 官方 QQ 机器人（QQ 开放平台）— 可选

- 优先复用已安装的 `octop-gateway 1.0.3`：作为受管子进程，注入 `QQ_APP_ID/QQ_TOKEN/QQ_SECRET`，控制台显示在线/重连/日志；`--sandbox` 支持。
- 若 octop 能力不够，再写原生 WebSocket 适配器（C2C/GROUP_AT/GUILD_AT、富媒体上传、markdown/buttons、token 刷新）。**不建议一上来自己撸协议**。
- 需要你提供：开放平台应用、AppID/Secret、沙箱/正式权限。

### 6.3 通用 IM

- 抽象 `InboundMessage/OutboundMessage/Adapter`；`/v1/gateway/messages` 保持兼容。
- Telegram/Discord/企业微信 后续各 1-2 天；先不做。

**验收**：真实 NapCat 群 @ + 私聊；发图 OCR、发语音 ASR；限流生效；断线重连；官方 QQ 沙箱消息（若提供凭证）。

## 7. 里程碑与工期（建议）

| 周 | 内容 | 产出 |
|---|---|---|
| W1 | Phase 0（B01-B07）+ 回归测试 | 稳定版 + bug 台账 |
| W2 | Phase 1 引擎/profile/控制台（SAPI+OpenAI） | 可换音色/试听/绑定 |
| W3 | Phase 2A OneBot 生产级 + 真机 NapCat | QQ 群/私聊可用 |
| W4 | 语音克隆（GPT-SoVITS 可选）+ QQ 语音 + 官方 QQ 托管 | 定制声音 + 双 QQ 路线 |
| W5 | soak/压测/文档/收尾 | 验收报告 |

## 8. 验收标准（硬指标）

1. 全量单测 ≥281 通过；新增 voice/qq 测试 ≥30。
2. 召回 p99 ≤100ms、注入 0 越权/0 超预算（沿用 bench）。
3. 桌宠：连发 5 条不叠音；stop 在 200ms 内静音；切换音色无需重启。
4. QQ：群 @ 回复 P95 <3s（模型耗时另计）；限流不丢消息；图片 OCR/语音 ASR 落库可查。
5. 控制台：语音工坊可试听/保存/绑定；错误中心能看到 QQ/语音失败原因。
6. 安全：控制台写/终端默认要本地 token；OneBot 入站无 secret 拒绝。

## 9. 风险

- **Edge TTS 出网被 SSL 拦**：先探测；不通就退 SAPI/Piper/OpenAI 兼容。
- **GPT-SoVITS 重**：torch CPU + 模型 5-10GB，推理慢；做成可选安装，不阻塞主程序。
- **NapCat 风控**：个人号有封号风险；默认低频、白名单、可一键停。
- **官方 QQ 资质**：需要开放平台审核；没有凭证只能做沙箱/模拟。
- **音色版权**：克隆他人声音需授权；只允许自己的参考音/已授权素材。
- **不要公网暴露**：控制台/QQ webhook 默认 127.0.0.1；远程用反代 + token。

## 10. 需要你拍板（影响工期）

1. **“语音定制”到哪一档？**
   - A. 只换音色/语速/音高/音量（SAPI + OpenAI 兼容/Edge/Piper）——1-2 天，不克隆。
   - B. 要**克隆你的声音**（本地 GPT-SoVITS 少样本）——+1-2 周，装 torch/模型，CPU 慢。
   - C. 云端克隆（Fish/Azure/MiniMax）——需要 key + 出网；本机可能不通。
2. **QQ 先做哪条？**
   - A. 个人 QQ（NapCat/OneBot）——快，需装 NapCat 扫码，封号风险。
   - B. 官方 QQ 机器人（octop 已装）——稳，需 AppID/Secret/资质。
   - C. 两个都要（先 A 后 B）。
3. **你最烦的 3 个 bug 是什么？** 给“怎么操作 → 看到什么 → 期望什么”，我按台账优先修；否则我只能按静态审计顺序来。

> 方案文件：`docs/agent/design/voice-qq-hardening.md`。确认 1/2/3 后我开始 W1（先写失败测试再修）。

## 11. 二轮核对（2026-10-30，按用户反馈逐条验证）

用户拍板：**语音选 A（不克隆）**、**QQ 选 C（先 NapCat 后官方）**、**B05/B07 优先**。
以下“已有/没有”都按代码或 live 数据核对，避免重复造轮子：

| 用户判断 | 核对结果 | 证据 |
|---|---|---|
| 反思 Worker 没写自动流转 | **半对**：Worker 真在跑，但只产“性格 trait”，不产事实；事实抽取在 writer（每轮异步）。当前 `scans=25, runs=0, skipped=125`——阈值/间隔没被满足，所以看起来“没动” | `aiji/reflect.py`、`/stats` live |
| 激素是静态的 | **半对**：不是静态，`life_event`（成功/压力）会改、`tick` 会按离线时间回归；live 值在几次对话里从 sero .627→.647、dopa .429→.499。**缺的是自主扰动**（没人理/被骂/被夸的定时事件） | `aiji/life.py`、`/api/life` live |
| 消息防抖缺失 | **半对**：`debounce.py` 存在，Head 默认 `debounce=True`；但 OneBot 是**单 worker 串行**，第一条回复把 worker 占住 800ms，后续消息还在队列里 → QQ 场景下防抖实际失效。所以“连发 5 条回 5 次”可能是真的，根因是 B06 不是缺防抖 | `aiji/debounce.py`、`aiji/bridge/onebot.py` |
| 执行审批是摆设 | **不对**：8 个工具（list/read/write/terminal/screenshot/mouse/type/hotkey）+ ApprovalManager + 控制台审批都真实存在，`test_approval_api_releases_blocked_action` 通过。**真正的缺口**：上游网关不支持 `tools` 时 pool 会退纯文本（`pool.py` 174-176），模型永远不产生 tool_call → 看起来像摆设 | `aiji/actions/schema.py`、`test_console_api.py`、`pool.py` |
| 记忆星图节点是散的 | **对现象、错根因**：图代码有连线（`admin.memory_graph` + ECharts links），live 是 **9 节点 0 边**——因为 `fact_link` 没有达到阈值的边。`link_facts` 由 `add_fact` 调用，不是没写 | `/api/memory-graph` live、`store.py:412` |
| 主动消息是拨一下转一下 | **对**：`Proactive` 类写了，但全仓库没有实例化（grep 只有定义），所以永远不会 tick。这是真缺口 | `aiji/proactive.py`、grep |
| 桌宠不能看屏幕 | **半对**：`desk.look()` 能截屏 + OCR + 窗口信息；但没有 VLM（视觉大模型）语义理解。截图/OCR 已有，缺“看图说话”的 provider | `aiji/pet/desk.py`、`aiji/actions/executor.py` |
| 语音打断缺失 | **对**：固定 5s 录音、无 VAD；且 `stop_speaking` 已实测停不掉（B01） | `.tmp/probe_sapi_stop.py` |
| QQ 媒体只占位 | **对**：`_parse_message` 只变 `[图片]/[语音]`，不下载 | `aiji/bridge/onebot.py` |
| 参数透传缺失 | **对**：server 不读、Head 不传、**pool 还硬编码 `temperature: 0.0`** | `server.py`、`head.py`、`pool.py:154` |

### 已落地（本轮）

- **B07 参数透传**：`pool.chat/_post` 接受 `temperature/top_p/max_tokens`；`Head.reply` 透传；`server._chat_completion` 解析+夹取（0-2 / 0-1 / 1-8192）并在 `aiji.sampling` 回显；控制台对话页加了 temperature 输入（默认 0.8）。
- 回归：`tests.test_openai_api` 8/8（新增 `test_sampling_params_are_passed_through_and_clamped`）。

### 调整后的 W1（下一步）

1. **B05 QQ 媒体**：`get_image/get_record` 下载 → 识别（OCR/ASR）→ 写记忆 + 把识别文本拼进本轮 prompt；失败降级为可读占位。
2. **B06 并发 + B03 限流**：每 scope 队列/worker；只有并发起来，`debounce` 才能真正合并 QQ 连发。
3. **B01/B02 语音引擎**：单例 VoiceEngine + 队列 + 同实例 Purge；VAD 放 P1。
4. **主动消息接线**：把 `Proactive` 实例化，定时器 tick，候选 scope 从 heart/accounts 来；先只发桌宠，QQ 后接。
5. **星图补边**：新事实写入时降低连边阈值/补相似度回填；或在 `/api/memory-graph` 读时按 2-gram 相似度补临时边（不改库）。
6. **工具调用兜底**：上游不支持 tools 时，用“提示词约定 JSON 动作”或切到支持工具的路由；审批推送改 WS（现在轮询）。
7. **VLM 视觉**：新增 `vision` provider（OpenAI 兼容 vision / dsh-vision-router），桌宠“看一眼”走 VLM，OCR 只做文字兜底。

> 结论：先做 B05+B06+B01，四项里三项都在“感知/并发”主线上；主动消息和星图补边随后。

## 12. 2026-10-30 开工记录（按“产品现实核对”收敛）

### 已落地

1. **`/v1` 身份/Scope 契约**（Task 1）：
   - `user` 字段支持 `qq_group_67890_user_12345`、`qq:group:67890:user:12345`、
     `group_..._user_...`（无平台默认 qq）、`qq_12345`、裸 id。
   - 支持 `metadata` / `aiji` 对象里的 platform/user/scope/session；
   - 兼容 AstrBot 的 `x-astrbot-conversation-id` 作为 session 兜底；
   - 验证：`test_identity_hint_parsing`、`test_openai_user_field_maps_qq_group_to_isolated_memory`
     （群 67890 的 12345 记忆与 999/其他群隔离）。
2. **控制台砍到 4 页签**：仪表盘 / 对话测试 / 记忆库 / 日志·死信；工作区/星图/人设等
   不再出现在导航；`#graph` 等旧深链回落到仪表盘。
3. **工作区/终端默认关闭**：`/api/fs*`、`/api/shell` 默认 403；
   `AIJI_ENABLE_WORKSPACE=1` 才开（保留代码以便回滚）。测试：
   `test_workspace_is_disabled_by_default`。
4. **声音换 Edge TTS**：`aiji/pet/voice.py` 默认 `edge`（`zh-CN-XiaoxiaoNeural`），
   ffmpeg 转 wav + winsound 播放；失败自动回退 SAPI；`AIJI_TTS=sapi` 可强制离线；
   `AIJI_VOICE` 选音色。实测 `synthesize_edge` 生成 13824 bytes mp3；`speak` SAPI/Edge
   静音测试均 True；`stop_speaking` 现在同时停 winsound 和同一 SAPI 实例。
   `pyproject` 增加 optional `[voice] edge-tts>=7`。

### 关键修正：AstrBot 不能只填 Base URL

AstrBot 的 OpenAI provider 只发 `x-astrbot-conversation-id`，**不发 QQ 号/群号**；
直接填 `http://127.0.0.1:8787/v1` 会让全群共用 `local/default` 记忆。
Task 2 需要 AstrBot 侧插件（从消息事件拿 sender/group，调用 AIJiYi 时带
`user`/`X-Aiji-*`），或改 AstrBot 的 provider 注入身份。当前只完成了 AIJiYi 侧契约。

### 待办

- AstrBot 插件（30-50 行）：`on_message` 里取 `event.get_sender_id()` / `get_group_id()`，
  POST `/v1/chat/completions`，带 `user=qq_group_<g>_user_<u>`；再把回复发回。
- Edge TTS 播放队列/打断的 UI 状态（当前 stop 已可用，但桌宠状态未显示“正在说”）。
- B05 媒体、B06 并发、B03 限流、Proactive 接线、星图补边（按 §11 W1 顺延）。
