# AIJiYi — AI 智能体分层记忆与本地陪伴服务

> 当前功能与运行条件以 [功能清单](docs/FEATURES.md) 为准。下文的性能数字是相应日期的历史测量结果，不代表当前机器的实时保证；`docs/agent/` 中的审计和设计文档保留当时结论。

**一句话**：SQLite 存事实与画像，内存存最近对话，向量索引可选；写与识别全异步走积分模型网关，读是 100ms 硬超时快召回，注入是带来源标注的软引导块；对外提供 OpenAI 兼容入口，IDE / IM 网关可直接接入。

三件事，一个闭环，全部不阻塞主链路：

```
互动 ──写(µs)──> 沉淀：事实 / 知识 / 关系 / 画像 / 风格 / 摘要（worker 线程 + 免费模型池，兜底不丢写）
                    │
                    ├── 注入：选择性拼装 + 来源标注 + 关系标签（<10ms，只出文本块）
                    │
                    └── 反馈：主模型用了哪几条 → 命中计数 + 注入留痕 → 下轮排序更准
```

- **学习**：事实、知识、关系、风格。异步、可追溯（`source` / `source_kind` / 时间 / 置信度），**人格核心不动**（风格单独一张表，只描述表达方式）
- **总结**：流水消息压成摘要 + 知识条目 + 画像更新（带来源；冲突只打失效时间，**不删除历史**）
- **操控**：选择性注入、来源标注、关系标签引导回答方向（**软引导**：不生成回复、不改人格；返回里显式 `persona_touched=False`、`generated_reply=None`）
- **记忆质量（v0.4）**：写入走「多值累积 / 同值合并 / 单值覆盖 / 否定失效」，召回用「相关度 + 重要度 + 时间衰减 + 反馈」多信号排序并做 1 跳关联扩展，超长会话强制压缩——详见下文。

## 识别层：任意输入 → 标准文本 + 元数据 → 同一条管线

```
text   直用 ─┐
emoji  映射 ─┤
image  OCR  ─┼─> Envelope(text, meta, confidence, status) ─> 反思层（同一条抽取管线，不按类型分叉）
voice  ASR  ─┤
file   提取 ─┤
link   抓标题┤
video  仅元数据（不做视觉理解）
```

| 类型 | 初期支持 | 状态 |
|---|---|---|
| text | 直用 | ✅ |
| emoji | 常见表情 → 语义标签（开心/生气/赞同…） | ✅ |
| image | 自动接线已安装的 OCR，或由调用方注入；缺引擎记 `unsupported` | ✅（依赖引擎） |
| voice | 自动接线已安装的 ASR，或由调用方注入；缺引擎记 `unsupported` | ✅（依赖引擎） |
| file | 调用方给文本或 MIME 可读时提取 | ⚠️ 部分 |
| link | 抓 `<title>` | ✅ |
| video | **只存元数据**，明确不做理解 | ✅（有意为之） |

三条硬约束：**超时**（默认 2s，超时记 `timeout`）、**置信度门槛**（默认 0.5，低于则 `low_confidence` 且不喂抽取）、**原始文件不入库**（只存 `ref`/`url`，`media` 表没有任何 BLOB 列）。识别器缺引擎时**如实返回 `unsupported` + 原因，绝不编造文本**。

## 数据模型

身份主键 **`(platform, platform_user_id)`**；`scope` 是同一身份下的命名空间，不参与身份。加平台只是多一个 `platform` 值，表结构不动。

| 表 | 存什么 | 关键列 |
|---|---|---|
| `account` | 身份注册 | `platform + platform_user_id`(复合主键)、`scope`、`display_name`、`extra` |
| `fact` | 事实 / 知识 / 关系 | `kind`(fact\|knowledge\|relation)、`subject/predicate/object/text/confidence/status`、`hit_count`、`last_used_at`、`superseded_by`、`importance`、`mentions`、`valid_from/valid_to` |
| `fact_link` | 事实间关联（A-MEM 式） | `src_id/dst_id/weight`，双向、同身份，`UNIQUE(身份, src, dst)` |
| `fact_log` | 写入决策审计 | `op`(add\|merge\|supersede\|expire\|noop)、`fact_id`、`target_id`、`detail`(JSON) |
| `fact_fts` | 全文索引 | FTS5 `tokenize='trigram'`，触发器跟 `fact` 同步 |
| `profile` | 画像键值 | 复合主键 `(platform, platform_user_id, scope, key)` |
| `style` | 表达风格 | 复合主键 `(…, dimension)`，`value/confidence/evidence` —— 与人格核心隔离 |
| `turn` | 对话原文 | `…session, role, content, extra, ts` |
| `summary` | 滚动摘要 | `…session, upto_turn, text, extra` |
| `media` | **只存引用 + 识别结果** | `kind, ref, url, mime, size_bytes, expires_at, text, confidence, status, recognizer` |
| `injection` | 注入留痕 | `query, item_ids, prompt, feedback` |
| `write_queue` | 抽取死信 | `kind, payload, state, attempts, last_error` |

## 记忆质量（v0.4）：写入决策、排序、关联、压缩

借鉴 mem0 / Graphiti / A-MEM 的三条原则，落成确定性、离线可跑的实现（设计来源见文末）：

| 场景 | 处理 | 审计 |
|---|---|---|
| 同一个值又说一遍（"用户喜欢爬山" × N） | `merge`：置信度取大、`mentions+1`、文本取长 | `fact_log.op='merge'` |
| 多值属性（偏好/兴趣/爱好/备注）出现新值 | `add`：**累积成多条，不互相覆盖** | `op='add'` |
| 单值属性（居住地/职业/年龄/邮箱/电话/名字…）换值 | `supersede`：旧行 `status='superseded'`、`superseded_by` 指向新行、`valid_to=now` | `op='supersede'` |
| 用户否定（"我不再住在南京了"） | `expire`：命中的旧行 `status='expired'`、`valid_to=now`，不新增正面事实 | `op='expire'` |
| 否定但没有旧值 | `noop`：不写垃圾 | `op='noop'` |

所有历史行都保留（Graphiti 式"失效不删除"）：`valid_from` 是它开始为真的时间，`valid_to` 是失效时间；`status='active'` 才参与召回。

**排序**（`aiji/hippocampus/recall.py::score_fact`）不再是"FTS 名次 × 新近度"，而是：

```
score = (0.55·相关度 + 0.20·重要度 + 0.15·新近度 + 0.10·置信度)
        × (1 + 反馈命中加成 + 重复提及加成) + 名次微调
```

- **重要度**写入时算好：身份事实（名字/电话/居住地）≈0.9，关系 0.7，知识 0.6，随口偏好 0.5~0.66，再按置信度调三成。
- **新近度**按属性类型给不同半衰期：居住地 240 天、职业 365 天、偏好 60 天、备注 45 天、知识 180 天——变化慢的事实不该被"新"压掉。
- **反馈**：`hit_count`（主模型实际引用过）与 `mentions`（用户重复说过）做温和加成，不改变相关度的主导地位。

**关联记忆**（A-MEM 式）：新事实写入时，在同身份同 scope 内找 `subject` 或 `predicate` 相同的近邻，按 2-gram 相似度 ≥0.35 连最多 3 条双向边（`fact_link`）。召回时从得分最高的 3 条事实做 1 跳扩展，邻居按 `父分数 × (0.45 + 0.4×权重)` 打折，**永远不会压过直接命中**；跨身份/跨 scope 的边查不出来（`links_for` 两侧都带 `Ident.where`）。

**超长会话压缩**：`max_turns_per_session`（默认 400）/ `turn_keep`（默认 40）。超过上限时，先把将被淘汰的用户发言摘要成一条 `summary(extra.forced=true, evicted=N)`，再删除旧 `turn` 行，并清掉召回侧的内存缓冲——删原文不丢信息，也不装看不见。

**摘要**优先用免费模型池（`summary_model=true`），模型不可用或输出不是合法 JSON 时退回本地压缩（去重 + 截断），这一步在 worker 线程、不持写锁。

**向量层**（`vector="hash"` 时开启）：启动时从 `fact` 表重建索引（只收 active）；写入侧在 supersede/expire 后删除旧 id；召回侧命中后**再按身份过滤一次**——语义索引是全局的，这一步漏了就是跨平台串号。

## 两条延迟保证 + 一条注入保证

1. **写不阻塞调用方**：只做内存 append + `queue.put`，实测 p99 **0.062ms**；抽取/识别在 worker 线程。
2. **读有硬预算**：慢查询丢线程池，主线程最多等 `remaining` 毫秒；等不到返回画像/缓冲快照并标 `degraded=["deadline"]`，**绝不超时**。
3. **注入快且短**：软引导块有硬字符预算（默认 700 字 / 12 条），实测 p99 **9.3ms**，0 次超预算、0 次越权。

实测（5000 条事实分散在 4 个平台、2026-10-29，v0.4 重建索引 + 关联扩展之后）：

| 路径 | p50 | p95 | p99 | max |
|---|---|---|---|---|
| 进程内召回（静默 / 写压下） | 1.4 / 3.3ms | 2.2 / 5.3ms | 2.6 / 6.0ms | 8.1 / 6.7ms |
| HTTP 端到端（4 客户端 × 240 样本） | 4.6ms | 7.3ms | 8.3ms | 9.2ms |
| 注入块生成（含写留痕，且在写压下） | 4.7ms | 6.5ms | 9.3ms | 11.5ms |

记忆质量（`bench/bench_memory_quality.py`）：冲突消解 6/6 场景正确；排序 top-1 = **1.0**（重要度 / 衰减 / 反馈三组各 20 条）；1 跳关联召回 30/30。

## 快速开始

```bash
python -m aiji.senses.cli doctor                                   # 自检：SQLite/FTS5/哪些模型路由在场

# 学习
python -m aiji.senses.cli --db data/aiji.db remember --platform wechat --user u1 \
        "我叫李雷，住在杭州，张伟是我的同事，我说话比较随意呀~~"
python -m aiji.senses.cli --db data/aiji.db ingest --platform wechat --user u1 \
        --kind image --ref msg-7781 --url "https://cdn.example/fapiao.jpg"

# 召回 / 注入 / 反馈
python -m aiji.senses.cli --db data/aiji.db recall --platform wechat --user u1 "我住哪"
python -m aiji.senses.cli --db data/aiji.db inject --platform wechat --user u1 "我住哪" --session s1
python -m aiji.senses.cli --db data/aiji.db feedback --platform wechat --user u1 --injection-id 1 --used 2 3

# 看沉淀结果
python -m aiji.senses.cli --db data/aiji.db style    --platform wechat --user u1
python -m aiji.senses.cli --db data/aiji.db media    --platform wechat --user u1
python -m aiji.senses.cli --db data/aiji.db accounts
python -m aiji.senses.cli --db data/aiji.db serve --port 8787
```

配置：`config/aiji.example.json`（`AIJI_CONFIG` 指定）。路由 key 一律走环境变量；缺 key 的路由自动跳过，抽取退化为本地规则（仍能存事实/画像/风格，`source_kind='heuristic'`）。

OCR/ASR 引擎可自动接线已安装的依赖，也可从代码注入；模型权重不随主包打包：

```python
m = Memory.open("data/aiji.db",
                ocr=lambda item: (my_ocr(item.url), 0.85),   # -> (text, confidence)
                asr=lambda item: (my_asr(item.url), 0.80))
```

## HTTP 接口

身份三个字段：`platform` / `platform_user_id` / `scope`（缺省 `local` / `default` / `default`）。

```bash
curl -X POST localhost:8787/turns  -d '{"platform":"wechat","platform_user_id":"u1","session":"s1","role":"user","content":"我叫李雷，住在杭州"}'
curl -X POST localhost:8787/ingest -d '{"platform":"wechat","platform_user_id":"u1","kind":"image","ref":"msg-1","url":"https://…"}'
curl -X POST localhost:8787/recall -d '{"platform":"wechat","platform_user_id":"u1","query":"我住哪","session":"s1"}'
curl -X POST localhost:8787/inject -d '{"platform":"wechat","platform_user_id":"u1","query":"我住哪","session":"s1"}'
curl -X POST localhost:8787/feedback -d '{"platform":"wechat","platform_user_id":"u1","injection_id":1,"used_ids":[2,3]}'
curl -X POST localhost:8787/flush  -d '{"timeout":10}'
curl "localhost:8787/style?platform=wechat&platform_user_id=u1"
curl localhost:8787/health
```

`/turns`、`/remember`、`/ingest` 返回 202 只代表**已入队**；等落库用 `/flush`。`/inject` 返回 `text`（直接塞进 system/context）+ `items`（结构化条目，自己再筛）。

## 万能接入：OpenAI 兼容入口

已有 Agent2API / WorkBuddy 等积分制反代网关时，只需把 key 放进环境变量：

```powershell
$env:AGENT2API_KEY = "<在 Agent2API 后台生成的新 key>"
python -m aiji.senses.cli serve --host 127.0.0.1 --port 8787
```

浏览器打开 `http://127.0.0.1:8787/` 是运行与功能页面；任意支持自定义
OpenAI API 的 IDE 或其他客户端填写：

```text
Base URL: http://127.0.0.1:8787/v1
Model:    aiji
API Key:  留空；若设置了 AIJI_API_KEY，则填同一个值
```

### 运行与功能页面（`http://127.0.0.1:8787/`）

双击 `启动AIJiYi.bat` 启动 AIJiYi、AstrBot 和 NapCat，并打开页面；`tools/结束AIJiYi.bat`
结束服务链路，QQ 客户端保持打开。页面分为运行总览、全部功能、功能配置、启动与结束。
运行总览展示各服务状态、消息处理链路和后台任务；全部功能按实际接口列出 22 项能力，
点开后读取实时数据，并为记忆、对话、语音、歌曲、社交、表情包、审批等提供操作表单。
功能配置保存到 `config/runtime.json`，需要重启服务生效；密钥不在页面显示或保存。
页面也能直接用 `file:///D:/AIJiYi/aiji/right_hand/web/operations.html#lifecycle` 打开（服务端只为
`null` 与 `127.0.0.1:8787` 两个本地来源放行 CORS）；`/`、`/operations`、`/operations.html`
是同一份页面。
旧 15 页控制台与 ECharts 资源已经移除。工作区 API 默认关闭
（`AIJI_ENABLE_WORKSPACE=1` 才开）；原有终端 API 仍需显式设置 `AIJI_UNSAFE_SHELL=1`。

**启动与结束**（`#lifecycle`）是唯一会真的动本机服务的地方，读的都是实时探测：

- 依赖链按 NapCat → QQ 反向连接 → AstrBot → AIJiYi → 实时状态 排列，每张卡显示
  端口、PID、进程名、已建立连接数；第一个没就绪的就是**卡点**，卡点给出对应处置建议。
- 顶部状态条给出「整套服务在线 / 部分未就绪 / 未启动」、`x / 5 就绪`、目标状态
  （`data/service_state.txt`：`stopped` 时保活不会拉起）、检查时间与卡点。
- **启动前置检查**：Python、AstrBot 运行环境、NapCat 注入器、QQ 客户端、运行配置
  （可选）五项路径实测；缺哪一项就直接标 ✗。
- **守护方式**：登录启动项（开始菜单 `AIJiYi Startup.lnk`）与计划任务
  `AIJiYi Console Watchdog` 两项实测，页面可直接注册/注销该保活任务。
  `schtasks /Query` 在某些沙箱/受限账户下会假阴性，此时按
  `C:\Windows\System32\Tasks\<任务名>` 定义文件二次判定。
- 启动/重启/结束下发后，页面每 2 秒复查真实端口，直到全部就绪（或结束服务后与核心
  断开）才收工，最多等 90 秒；不使用预设动画。
- 下方日志框显示 `data/lifecycle.log` 尾部，启停失败时先看这里。

### 日志文件（`data/`）

| 文件 | 谁在写 | 说明 |
|---|---|---|
| `lifecycle.log` | `tools/lifecycle.ps1` | 每次启停追加一行（ASCII，UTF-8） |
| `console.out.log` / `console.err.log` | AIJiYi 核心 | 模型下载、引擎回退等；**UTF-8** |
| `astrbot.out.log` / `astrbot.err.log` | AstrBot | 群消息流水与告警；由启动脚本强制 **UTF-8** |
| `field_monitor.jsonl` | `tools/field_monitor.py` | 每 15 秒一条现场采样 |
| `*.jsonl` / `*.log.<时间戳>.log` | 审计脚本 / 轮转产物 | 轮转产物保留最近 5 个 |

启动脚本做了三件事，都是被真实现场故障逼出来的：

- **轮转而不是截断**：`Start-Process -RedirectStandardOutput` 会清空目标文件，所以
  `tools/ensure_console.ps1` 在拉起前把非空日志改名成 `<名字>.<yyyyMMdd-HHmmss>.log`，
  只保留最近 5 个。否则每次重启都把上一次的证据抹掉。
- **强制 UTF-8**：AstrBot 往控制台写中文/emoji，cp936 下一天能刷出两千条
  `UnicodeEncodeError: 'gbk'`，日志还读不出中文。启动前设 `PYTHONUTF8=1` +
  `PYTHONIOENCODING=utf-8`。
- **QQ 媒体绕开代理**：AstrBot 的下载用 aiohttp `trust_env=True`，会继承机器上的
  `HTTP_PROXY`。QQ 富媒体（`multimedia.nt.qq.com.cn`）走隧道会被改写成 4xx，
  所以启动脚本把该域名加进子进程的 `NO_PROXY`（其余照旧走代理）。

- **对话测试**：功能详情中的对话表单调用真实 `/v1/chat/completions`。
- **声音**：默认走 Edge TTS（`pip install edge-tts`，微软在线音色，`AIJI_VOICE` 可选
  `zh-CN-XiaoxiaoNeural` / `YunxiNeural` 等）；网络不可用时自动回退 Windows SAPI。
  `AIJI_TTS=sapi` 可强制离线。
- **多人群聊身份**：前端没有专用请求头时，把 OpenAI `user` 写成
  `qq_group_<群号>_user_<QQ号>`（或 `qq:group:...:user:...`），AIJiYi 会映射成
  `platform=qq / scope=group:<群号>`，不同群/不同人记忆隔离。
- **普通聊天不套预制台词**：空 @、限流、断线和空回复直接静默；模型提示词不再
  注入可照抄例句。回复若命中“咋了 / 咕噜 / 泡泡 / 鱼缸 / 水里”等模板意象，
  会保留原意自动重写一次。

### 定制声音与唱歌

声线放在 `data/voices/<id>/profile.json`，当前 `dafeiyu` 档案使用
`data/voices/dafeiyu/reference_seedvc.wav` 作为 21.5 秒参考音。Seed-VC 作为
独立 GPL-3.0 进程运行，AIJiYi 只通过 `127.0.0.1:9880` 调用，不把模型源码并进
记忆服务。

```powershell
powershell -ExecutionPolicy Bypass -File voice-engine\seed-vc\start-aiji.ps1

curl http://127.0.0.1:8787/v1/voices
curl -X POST http://127.0.0.1:8787/v1/audio/speech `
  -H "Content-Type: application/json" `
  -d '{"input":"我在呢","voice":"dafeiyu"}'
curl -X POST http://127.0.0.1:8787/v1/songs `
  -H "Content-Type: application/json" `
  -d '{"query":"晴天","profile":"dafeiyu","pitch":0,"max_seconds":60}'
```

QQ 端命令：`/voice list`、`/voice use dafeiyu`、`/voice preview`、
`/sing 晴天`。群里需要先 @机器人再发命令；群聊里的切换/试听/点歌默认只允许
`AIJI_QQ_VOICE_ADMINS` 中列出的 QQ；私聊不受此限制。翻唱任务先做 60 秒截段、
人声/伴奏分离、Seed-VC 换声，再由 ffmpeg 混音并发语音条。公开歌曲搜索使用
网易云公开接口，不绕过会员、版权或登录限制。

额外的本地 TTS 引擎使用同一套声线注册表，不需要改调用方：

- `provider: edge`：Edge TTS。
- `provider: gptsovits`：默认兼容旧版 GET；配置 `extra.api_style: "v2"` 后走
  `/tts` JSON POST，参考音路径会自动解析成本机绝对路径。
- `provider: cosyvoice`：支持 `sft`、`instruct`、`zero_shot`、
  `cross_lingual`。
- `provider: openai` / `http`：任意 OpenAI 兼容或模板化 HTTP TTS。

模板在 `config/voices/gptsovits.example.json` 和
`config/voices/cosyvoice.example.json`。复制到
`data/voices/<id>/profile.json`，并把 `reference.wav` 放在同目录即可：

```powershell
Copy-Item config\voices\gptsovits.example.json data\voices\dafeiyu-gpt\profile.json
# 再把参考音频放到 data\voices\dafeiyu-gpt\reference.wav
curl http://127.0.0.1:8787/v1/voices
```

模型和推理进程仍由外部引擎目录负责；AIJiYi 只发 HTTP 请求，不把
GPT-SoVITS / CosyVoice 的模型源码和权重并进主包。

### QQ 空间与分享

QQ 空间写入通过 NapCat 的扩展 OneBot action，不使用网页模拟点击。先在 NapCat
启用 HTTP Server（或反向代理到本机），再设置：

```powershell
$env:AIJI_ONEBOT_API = "http://127.0.0.1:3000"  # NapCat HTTP API
$env:AIJI_SOCIAL_MEDIA_DIR = "D:\AIJiYi\data\social\media"
$env:AIJI_STICKER_DIR = "D:\AstrBot\data\sticker_packs\aiji"
```

`/api/*` 会继承 `AIJI_API_KEY` 鉴权。可用接口：

```text
GET  /api/social                    # 传输/目录/本地表情包状态
POST /api/social/qzone/publish      # content, images, visibility, target_uins
POST /api/social/qzone/comment      # tid, content, target_uin, images
POST /api/social/qzone/auto-comment # target_uin, content, post_index
POST /api/social/avatar             # file
POST /api/social/signature          # signature
POST /api/social/share/music        # query/song_id, caption
POST /api/social/share/bilibili     # url/bvid, title, caption
```

例子：

```powershell
curl -X POST http://127.0.0.1:8787/api/social/qzone/publish `
  -H "Content-Type: application/json" `
  -d '{"content":"今天路过一片很安静的晚霞。","visibility":"friends"}'

curl -X POST http://127.0.0.1:8787/api/social/qzone/auto-comment `
  -H "Content-Type: application/json" `
  -d '{"target_uin":"10001","content":"拍得真好","post_index":0}'
```

`images` 和头像 `file` 只接受 `AIJI_SOCIAL_MEDIA_DIR`（或本地表情包目录）内的
图片路径，或显式 `http(s)` 地址。

### 联网表情包与自动更新

图库默认保留本地素材，并可定时合并公开远程图库：

```powershell
$env:AIJI_STICKER_UPDATE = "1"
$env:AIJI_STICKER_FEED_URL = "https://cdn.jsdelivr.net/gh/cheems135/dsh-qq-bridge@main/assets/preset/manifest.json"
$env:AIJI_STICKER_UPDATE_INITIAL = "30"
$env:AIJI_STICKER_UPDATE_INTERVAL = "3600"
$env:AIJI_STICKER_MAX_MB = "8"
```

- 远程更新只接受公网 `http(s)`，拒绝环回、私网、链路本地地址、URL 凭据和
  80/443 之外的端口。
- 图片必须通过 PNG/JPEG/GIF/WebP/BMP 文件头校验，超过大小上限会放弃。
- 下载先写临时文件，再原子替换；任何一步失败都保留旧 `manifest.json` 和旧图库。
- 远程条目与本地条目会合并，不会删除你自己放进目录的自定义表情。
- AstrBot 插件会在下一次发图前检查 `manifest.json` 的 mtime；更新后无需重启。

```text
GET  /api/stickers          # 本地数量、mood、最近更新状态、循环状态
POST /api/stickers/update   # 立即从远程 feed 更新
POST /api/stickers/import   # {"url":"…","mood":"开心","description":"…"}
```

`/api/stickers/*` 和其它 `/api/*` 一样受 `AIJI_API_KEY` 保护。

### QQ 群聊接入与现场监控

AstrBot 插件通过 QQ 号区分机器人自己、群友和其他机器人。昵称或头像只用于展示，
不参与回复归属判断。推荐的现场测试参数：

```powershell
$env:AIJI_QQ_MODE = "auto"                 # mention 时只回真正 @ 本机器人
$env:AIJI_QQ_REPLY_TO_BOTS = "0"
$env:AIJI_QQ_OTHER_BOT_IDS = "2501440948,3816866825,3353891315"
$env:AIJI_QQ_BOT_CHAT_IDS = "2501440948,3816866825,3353891315"
$env:AIJI_QQ_BOT_CHAT_CHANCE = "0.25"
$env:AIJI_QQ_BOT_CHAT_COOLDOWN = "180"
$env:AIJI_QQ_BOT_CHAT_DAILY = "12"
$env:AIJI_QQ_ALIAS_CLONES_ARE_BOTS = "1"
$env:AIJI_QQ_TRUSTED_USER_IDS = "8846704,2216608312"
$env:AIJI_QQ_BACKFILL = "0"
$env:AIJI_AUTO_LEARN = "0"
$env:AIJI_QQ_ATTENTION = "1"
$env:AIJI_QQ_ATTENTION_DEBOUNCE = "6"
$env:AIJI_QQ_AUTO_INTERJECT_CHANCE = "0.35"
$env:AIJI_QQ_AUTO_HOURLY_MAX = "6"
$env:AIJI_QQ_STICKER_COLLECT = "1"
$env:AIJI_STICKER_CHANCE = "0.35"
$env:AIJI_STICKER_COOLDOWN = "90"
$env:AIJI_STICKER_DAILY = "30"
$env:AIJI_HEART_SILENCE = "300"
$env:AIJI_HEART_COOLDOWN = "1800"
$env:AIJI_HEART_DAILY = "8"
$env:AIJI_QQ_PROACTIVE_VOICE_CHANCE = "0.45"
$env:AIJI_QQ_PROACTIVE_STICKER_CHANCE = "0.4"
```

`AIJI_QQ_OTHER_BOT_IDS` 填其他机器人账号的 QQ 号，不要填昵称。模型池繁忙时，
后台抽取不会占满全部模型槽；消息积压达到阈值后改用本地规则抽取，避免回复链路
被记忆任务饿死。

群聊里实际 `@` 本机器人 QQ 时必定进入回复链路；直接叫“大肥鱼”也能触发，但
`@` 其他仿号的文本不会因为包含同名片段而误触发。连发刹车只压普通随机插话，
明确叫到机器人时仍会回复。

非 `@` 消息使用 Attention Scheduler：读取当前生命卡的多巴胺/皮质醇、100ms
记忆召回和话题显著性，先等群聊静默约 6 秒，再决定是否插话。`AIJI_QQ_BOT_CHAT_IDS`
里的群 AI 是允许对话的白名单，回复时会真正 `@` 对方；每一对机器人有独立冷却和
每日额度，避免无限接龙。对方出现明显攻击词时热度上升，道歉/冷静词会降温；
自身皮质醇高且互聊热度达到阈值时会主动熔断，不再继续接话。

主动心跳会读取最近记忆，偶尔发语音或带表情包；群友发来的图片会自动送入受控
图库导入接口。在线表情包改用 jsDelivr 镜像，单张失败不会丢掉已经下载成功的图。

用固定现场参数重启 AstrBot：

```powershell
python tools\start_astrbot_field.py
python tools\restart_aiji_field.py
python tools\start_field_monitor.py
```

脚本会写入独立的 `astrbot-stable-*.out.log`，保留其他机器人 QQ 号隔离名单，
并在重启 AIJiYi 时继承当前进程的模型密钥环境。

现场日志应同时出现这三类记录：

```text
AIJiYi 仅入库 group:...: 这次不插话
AIJiYi 群自动接话 group:...: 叫到了「大肥鱼」
Prepare to send - ...
```

只有 `Prepare to send` 且数据库新增 `assistant` 轮次才算真正回复。后台监控：

```powershell
python tools\field_monitor.py
```

监控每 15 秒写入 `data/field_monitor.jsonl`，包含端口、模型池、写队列、
`qq_assistant_turns`、`latest_assistant_turn`、`reply_stalled`、表情图库更新状态
和 MCP 研究状态。有真人流量但十分钟没有新回复时会记录
`assistant_reply_stalled`。Agent2API 账本也会写入 `agent2api`，包含最近 30 分钟
请求数、tokens、200/408 数量、今日累计和各 provider 的剩余额度；token 速率过高、
超时率过高或账本不可读会触发告警。

`field_monitor.py` 是常驻采集器，可以交给保活：建一个 `data\monitors.on` 空文件，
`tools\ensure_console.ps1`（计划任务每 5 分钟、以及每次启停）会检查
`data\field_monitor.jsonl` 的写入时间，超过 2 分钟没更新就重新拉起
`tools\start_field_monitor.py`（自带去重）。删掉该文件即不再自动拉起。
`tools\proactive_audit_1h.py` 是**一次性**审计（`DURATION_S=3600`，跑完自己退出并删 pid），
不要接进保活。

需要固定时长审计时运行：

```powershell
python tools\field_audit_30m.py
```

它每分钟采样一次，30 分钟后写入
`data/field_audit_30m.jsonl` 和 `data/field_audit_30m_summary.json`。

### 受控生活循环

Windows 登录时，当前用户的启动项会运行 `tools/ensure_console.ps1`；任务计划程序
中的 `AIJiYi Console Watchdog` 每 5 分钟再检查一次。脚本会按 NapCat、AstrBot、
AIJiYi 顺序补启缺失的进程，已健康运行的服务不会重复启动。任务以当前用户身份
运行，需用户已登录；NapCat 首次登录 QQ 仍可能需要人工扫码。可用
`schtasks /Query /TN "AIJiYi Console Watchdog"` 查看保活任务。

QQ 主动搭话与自动学习在 AstrBot 插件内运行。目标会话、主动发送额度和上次成功
学习时间会落盘，重启后按实际经过的时间判断是否到期；超过 24 小时未互动的会话
不会因开机补发消息，每轮心跳最多主动发送一条。AIJiYi 生活循环在启动时检查持久化状态，MCP 研究按上次报告
时间判断到期，不要求电脑在固定时刻开机。

AIJiYi 服务启动后会运行独立的 `LifeLoop`，不需要用户先说话。它按时间和当前
精力、压力、社交状态，从固定活动目录里选出听歌、看收藏、整理表情包、看书、
发呆、休息等活动；每段活动都有持续时间、下一次触发时间和硬边界，不调用模型，
也不会自行执行 QQ 空间、文件或终端操作。

用户消息和她自己的经历已经分开：互动只改变情绪余波，不再被提示词描述成“她
自己的经历”；只有 `self_activity` 和外部明确写入的生活痕迹会进入自己的经历块。
`GET /being` 会返回当前活动、当前焦点、下一次生活推进时间和循环统计。

```powershell
$env:AIJI_LIFE_LOOP = "1"       # 默认开启
$env:AIJI_LIFE_LOOP_POLL = "60" # 每 60 秒检查一次，活动本身通常间隔 14-60 分钟
```

### “成精”体检

不要用一段回复判断她是不是“人”。系统提供四层检查：

```text
GET  /api/being/checkup       # 确定性检查：皮囊/连续性/情绪防御/涌现
POST /api/being/checkup/live  # 真实模型三探针；在临时 DB 副本运行，不写正式记忆
```

实时探针覆盖：错误称呼诱导、AI 身份挑战、写代码的物理边界。返回原始回复和
规则初筛，但 `is_human_like_certified` 永远是 `false`；第四层“意外感”只能由
真实群友长期盲测，不能被自动化分数代替。

```powershell
python tools\being_checkup.py --db data\aiji.db --user 2216608312
python tools\being_checkup.py --db data\aiji.db --user 2216608312 --live
```

QQ/微信群聊属于角色空间：要求写可运行代码、执行命令或控制电脑时，会触发物理
动作边界，用角色内的话拒绝，不输出代码块。桌面平台仍保留编码和工具能力。

### MCP 自动研究

AIJiYi 自己有一套只读 MCP 客户端，不依赖 AstrBot 的 LLM。当前默认接入：

- `duckduckgo-mcp-server`：公开网页搜索、正文抓取和长链接展开。
- `mcp-server-fetch`：官方参考 fetch MCP，保留 robots.txt 和私网地址保护。
- GitHub 官方 MCP：配置为停用的候选，安装二进制并配置
  `GITHUB_PERSONAL_ACCESS_TOKEN` 后才可手动启用。

MCP 配置在 `config/mcp_servers.json`。客户端只允许显式白名单工具，所有默认
启用的服务器都标记为只读，并拒绝 Shell 元字符、非白名单命令和未授权工具。

后台研究循环默认启动 60 秒后跑第一次，之后每小时一次。它会：

1. 用 GitHub API 搜索近期活跃的 MCP server。
2. 通过 fetch MCP 读取公开 README。
3. 按只读能力、写操作、终端、浏览器自动化、许可证、平台兼容性和安装方式分类。
4. 将候选保存到 `data/mcp/discovery/latest.json`。
5. 把研究结果写成大肥鱼自己的生活事件。

它**不会自动安装或执行发现到的仓库**。自动接入只属于人工确认过的静态配置。

```powershell
$env:AIJI_MCP_CONFIG = "D:\AIJiYi\config\mcp_servers.json"
$env:AIJI_MCP_RESEARCH_LOOP = "1"
$env:AIJI_MCP_RESEARCH_INITIAL = "60"
$env:AIJI_MCP_RESEARCH_INTERVAL = "3600"
$env:GITHUB_TOKEN = "<可选，提高 GitHub API 额度>"
```

```text
GET  /api/mcp           # MCP 服务、工具缓存和研究状态
POST /api/mcp/research  # 手动触发一轮，立即返回 202
```

`GET /health` 也返回精简的 `mcp_research` 状态，包括 `running`、`working`、
`runs`、`errors`、`last_run_at` 和最近一轮的仓库/README/候选数量。后台研究循环
默认启动 60 秒后跑第一次，之后每小时一次。

服务端兼容普通响应和 SSE：

```bash
curl http://127.0.0.1:8787/v1/models

curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-Aiji-Platform: desktop" \
  -H "X-Aiji-User: owner" \
  -H "X-Aiji-Session: chat" \
  -d '{"model":"aiji","messages":[{"role":"user","content":"我住在哪里？"}],"stream":true}'
```

每次请求都会复用同一条链路：100ms 快召回 → 记忆注入 → 自我层 →
积分网关生成 → 出口闸门 → 异步写回一问一答。标准 `user` 字段在无专用请求头时
映射为 `local/<user>`；多平台接入再传 `X-Aiji-Platform` / `X-Aiji-User` /
`X-Aiji-Scope` / `X-Aiji-Session`。

设置独立的服务端访问密钥后再暴露到局域网：

```powershell
$env:AIJI_API_KEY = "<给下游客户端使用的密钥>"
```

此时 `/v1/*`、`/api/*`、`/turns`、`/remember`、`/ingest`、`/recall`、
`/inject`、`/feedback`、`/media`、`/style`、`/accounts`、`/stats` 和 `/flush`
都需要 `Authorization: Bearer <AIJI_API_KEY>`；`/health` 与运行页面保持公开。
页面在首次请求受保护 API 时询问 API key，并仅保存在当前浏览器会话。上游
`AGENT2API_KEY` 与下游 `AIJI_API_KEY` 是两把不同的钥匙，不要混用。

本仓库**不开源**：`LICENSE` 为专有许可（保留所有权利，未授权使用/复制/分发）。
引用的第三方组件（Octop Gateway、Seed-VC、FunASR、RapidOCR、ECharts 等）各自遵循其原始许可，
边界与出处见 `docs/agent/design/open-source-assembly.md`。

## 自测

```bash
python -m unittest discover -s tests -t . -v
python bench/bench_recall.py --facts 5000 --platforms 4
python bench/bench_http.py   --facts 5000 --clients 4 --rounds 10
python bench/bench_inject.py --facts 5000 --rounds 20
python bench/bench_memory_quality.py             # 冲突消解 / 排序 / 关联召回
python bench/demo_loop.py                        # 端到端演示：学习→总结→注入→反馈
```

## 已知边界

- **OCR/ASR 可自动接线或注入**：`Memory.open(engines="auto")` 在相应依赖已安装时接入
  RapidOCR（图片）与 FunASR / faster-whisper（语音）；也可通过 `ocr=` / `asr=` 注入自己的引擎。
  自动 OCR 接线支持 `extra["bytes"]`、`extra["bytes_list"]`，以及可访问的图片 URL；URL 下载失败、引擎缺失或识别置信度不足时，不会编造识别文本。
- **模型摘要的真实质量没验证**：`summary_model=true` 时走网关模型，本机有假 OpenAI 服务的契约测试；
  模型不可用会退回本地压缩（去重 + 截断）。真实摘要质量还没做过人工评分。
- **否定识别是确定性的**：依赖抽取器给出 `negated`（规则看"不/没/别/不再"前缀，模型看显式字段）。复杂否定（反问、双重否定）会漏。
- **冲突消解不调 LLM**：多值/单值靠预定义谓词表，未知谓词默认按"多值累积"处理（宁可多留一条，不误删）。要更强的语义判断，可在抽取层加模型决策。
- **关联只有 1 跳**、只按 2-gram 相似度连边；弱语义（改述后几乎没有共同字）连不上，要真语义需向量层 + 更强 embedding。
- **超长会话压缩默认 400 条用户轮次触发**；`turn` 原文被删后只活在摘要里（摘要质量决定信息保留度）。
- **风格 4 个维度**（语气 / 长度 / 表情 / 口癖），来自启发式；用词偏好等更细的维度仍需要更多历史与统计。
- **注入是软引导**，主模型可能忽略；反馈靠调用方回报 `used_ids`，不回报就不会变准。
- 中文问句与陈述句不共享子串时靠二字组/单字 LIKE 兜底，不是真语义；要真语义请开向量层
  （`vector="hash"` 字面相似，`vector="hybrid"` 加 LLM 软嵌入 10 维，都还是弱语义）。
- 单字 LIKE 兜底是全表扫，**5 万条以上要换倒排索引**。
- 内存队列不落盘：进程被 kill 最多丢最后一个未处理事件。
- 向量索引在进程内（启动时从 fact 重建，重启后可用），但仍是暴力余弦，>5 万条要换 sqlite-vec / hnsw。
- `media.url` 可能带签名会过期，`expires_at` 只是记录，刷新逻辑要调用方按平台 API 再做。

## 设计参考（GitHub）

实现没有拷贝代码，只借鉴了机制：

- [mem0](https://github.com/mem0ai/mem0)：2026-04 的新算法转向 **ADD-only**（不再 UPDATE/DELETE，记忆累积）、实体链接、语义 + BM25 + 实体多信号融合 → 本项目对应"多值累积 / 同值合并 / 不删历史 + 多信号打分 + 关联扩展"。
- [Graphiti (Zep)](https://github.com/getzep/graphiti)：**双时态事实**（validity window）、失效不删除、episode 溯源、混合检索 → 对应 `valid_from/valid_to` + `fact_log` 审计。
- [A-MEM](https://github.com/agiresearch/A-mem)：Zettelkasten 式笔记、自动建立关联、记忆演化 → 对应 `fact_link` + 召回 1 跳扩展。
- [LangMem](https://github.com/langchain-ai/langmem) / [Letta](https://github.com/letta-ai/letta)：区分语义/情景/程序性记忆与"热路径/后台"分工 → 本项目保持"写异步、读硬预算、注入软引导"的分层。

调研笔记：`docs/agent/research/memory-systems-2026.md`。
