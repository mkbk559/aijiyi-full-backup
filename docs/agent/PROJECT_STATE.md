# PROJECT_STATE —— AIJiYi

> **历史工作日志**：本页从 schema v4 阶段持续追加，后文的历史测试数和“未决”记录是当时的快照，不能单独作为当前功能说明。当前功能以 [功能清单](../FEATURES.md) 为准，具体历史结论按其记录日期阅读。
>
> 2026-11-02：桌面与网页宠物已按用户要求从当前代码移除；下文关于 Tk 桌宠、养成状态和宠物资源的描述仅保留为历史记录。

## 这个项目是什么

一个 **AI 智能体分层记忆系统**（Python 包 + CLI + 本地 HTTP 服务），**多平台共用一套表**。
三件事合成一个闭环，全部不阻塞主链路：

```
互动 ──写(µs)──> 沉淀：事实/知识/关系/画像/风格/摘要（worker + 免费模型池，兜底不丢写）
                    ├── 注入：选择性 + 来源标注 + 关系标签（软引导，<10ms，只出文本块）
                    └── 反馈：命中计数 + 注入留痕（下轮排序更准）
```

- **识别层**把任意输入降维成 `Envelope(text, meta, confidence, status)`，之后走**同一条**反思管线（不按类型分叉）
- 身份主键 **`(platform, platform_user_id)`**；`scope` 是命名空间；平台特有字段进 `extra`(JSON 文本)
- 媒体只存**可重新获取的引用** + 识别结果（`media` 表无任何 BLOB 列）
- 入口：`aiji/__init__.py`（门面 `Memory`）、`aiji/senses/cli.py`、`aiji/throat/server.py`
- **包结构 = 九个人体系统**（2026-11-03 起，见 `docs/architecture.html`）：
  `senses/` 入口（cli）、`throat/` 咽喉（server/net_guard/bridge）、`brain/` 大脑（head/pool/gate/humanize/persona/selfhood/quota/role_boundary/debounce）、
  `hippocampus/` 海马体（memory/recognize/real_engines/media_audio/extract/reflect/recall/inject/store/vectors/writer/janitor/db）、
  `endocrine/` 内分泌（being/life/life_loop/being_checkup/proactive/presence/desktop_sense）、
  `heart/` 心脏（heart/dispatch/social/song/sticker/voice）、`left_hand/` 左手（actions/mcp_client/mcp_research）、
  `right_hand/` 右手（admin/operations/service_control/params/config/web）、`skeleton/` 骨架（paths：ROOT/DATA/CONFIG/TOOLS/BENCH/WEB_DIR）
- 核心模块：`hippocampus/db.py`（schema v11 + 迁移）、`hippocampus/store.py`（身份/事实/画像/风格/媒体/摘要/注入日志/检索/冲突消解/关联/淘汰）、
  `hippocampus/recognize.py`（识别层）、`hippocampus/extract.py`（反思：事实/知识/关系/风格/摘要 + 启发式兜底）、
  `hippocampus/recall.py`（硬预算召回）、`hippocampus/inject.py`（软引导注入）、`hippocampus/writer.py`（异步写）、
  `brain/pool.py`（模型池）、`hippocampus/vectors.py`（可选向量）
- 对外说明：`README.md`；示例配置：`config/aiji.example.json`

## 怎么验证它是好的

```bash
cd D:\AIJiYi
python -m unittest discover -s tests -t .                       # 2026-11-03：452 tests, OK（全绿，含 3 条 hypothesis 属性测试）
python -m unittest tests.test_console_workspace                 # 期望：Ran 4 tests, OK（桌宠/工作区/终端/资源）
python bench/bench_recall.py --facts 5000 --platforms 4         # 期望：ok=true，p99 < 100ms
python bench/bench_http.py   --facts 5000 --clients 4 --rounds 10
python bench/bench_inject.py --facts 5000 --rounds 20           # 期望：ok=true，0 越权
python bench/bench_memory_quality.py                            # 期望：冲突消解 6/6、top-1 1.0、关联 30/30
python bench/demo_loop.py                                       # 端到端：学习→总结→注入→反馈
```

上次验证：2026-10-29T00:56Z（v0.4），61/61 通过；p99 分别 2.6 / 8.3 / 9.3ms（召回 / HTTP / 注入）；
记忆质量 bench：冲突消解 6/6、排序 top-1 1.0、关联召回 30/30。
2026-10-31 当前全量 **324/324 通过**；新增 API 鉴权、坏查询、失败队列、
媒体 scope 隔离、OneBot 非 @ 入库和插件状态回归。真实 Edge 窗口截图见
`docs/agent/evidence/console-2026-10-30/`。

## 现状（已完成）

| 能力 | 证据 |
|---|---|
| 61 项单测（v4：冲突消解/排序/关联/压缩/索引重建/隔离 + 原有全部） | `C:\Users\22166\.dsh\evidence\2026-10-28\005614-unittest-v4-final-f97273\stdout.txt` |
| 281 项单测（新增控制台桌宠/工作区/终端/资源 + 修复两处 tempfile 测试目录） | `C:\Users\22166\.dsh\evidence\2026-10-30\201225-unittest-full-console-final-6fb955\stdout.txt`（exit=0，Ran 281 tests OK）；截图 `docs/agent/evidence/console-2026-10-30/` |
| 5000 事实 × 4 平台（v4）：静默 p99 2.61ms，写压 p99 5.99ms，submit p99 0.062ms | `...\005528-recall-budget-v4-primary-2-f63af6\stdout.txt` |
| HTTP 端到端 240 样本 p99 8.28ms | `...\005541-http-budget-v4-secondary-61d549\stdout.txt` |
| 注入 120 次 p99 9.30ms，0 次超字符预算，0 次人格越权，avg 418.8 字 | `...\005545-bench-inject-v4-b0c20f\stdout.txt` |
| 记忆质量：冲突消解 6/6、排序 top-1 1.0、关联召回 30/30 | `...\005650-bench-memory-quality-2-aa03fc\stdout.txt` |
| v0.4 端到端演示（schema v4 + 关联 + 摘要 + 注入 + 反馈） | `...\005549-demo-v4-closed-loop-76dc9d\stdout.txt` |
| 闭环端到端演示（OCR 注入→同管线、emoji、语音缺引擎如实 unsupported、视频仅元数据、摘要、注入块、反馈） | `...\000908-demo-v3-closed-loop-0742c6\stdout.txt` |
| 摘要并发缺陷定位（n 序列 1,1,1,3,3,5 永不到 4） | `...\001103-probe-summary-interleaved-0efd76\stdout.txt` |
| 摘要重复写定位（同一 upto_turn 写 2 条） | `...\001017-probe-summary-demo-seq-161b13\stdout.txt` |
| FTS 迁移必须 rebuild（3 字片段验证） | `...\235138-probe-fts-rebuild-3char-2ceb79\stdout.txt` |
| 真机外网冒烟**未通过**（SSL 出网被拦） | `...\230953-smoke-live-pool-9d7b9d\stdout.txt` |

## 决策日志

- 2026-11-03 **成长系统：人格随年龄长大**，设计+证据 `docs/agent/design/growth-2026-11-03.md`。
  用户问"怎么让他持续性成长，不可能一直是儿童人设吧"。
  **做法**：新增 `aiji/brain/growth.py`——**年龄是唯一真源**
  （`data/persona/growth.json` 的 `birth_at` + `aging_rate`，默认 1 真实天 = 30 角色天，
  即约 12 天长大一岁），六个阶段（幼儿 4–6.5 / 儿童 6.5–10 / 少儿 10–12 / 少年 12–15 /
  青年 15–18 / 成年 18+）各带"句长 / 概念 / 话题 / 产出 / 情绪"标尺，首尾相接不断档。
  人格文案从年龄**渲染**：<12 岁用 `persona.CHILD` 底色，≥12 岁切 `persona.GROWING_BASE`，
  再追加 `### 成长阶段（当前）` 覆盖层；few-shot 满 10 岁停喂；口癖随阶段递减；
  **成年语感 trait 满 10 岁自动解锁**（`selfhood.kid_safe_traits(..., age)`，不用清库）。
  可用 `AIJI_PERSONA_AGE` / 配置 `persona_age` 锁定年龄，`aging_rate` 调速，
  CLI `aiji grow [--set-age X] [--rate N]`，控制台 ③ 大脑 新增「成长状态 `/api/growth`」
  与两个动作（拨到 14 岁 / 恢复自然长大）。
  **真机验证**（同一套探针只改年龄）：8 岁 `avg 27 字 / clean 1.0`、14 岁 `avg 55 字 / clean 0.89`；
  同一问题"为什么天是蓝的"8 岁→"我不知道呀，我去问老师了"、14 岁→"蓝光被空气散射……
  好像叫瑞利散射？记不太清了"。
  **顺带修的两个真 bug**：①身份自曝只在 qq/wechat 修，API 入口漏了——14 岁档回
  "我其实是个 AI，在扮演一个叫玛卡巴卡的角色"，已把 `role_boundary._IDENTITY_LEAK`
  补上"我(其实)是个 AI/扮演…角色/我没有身体"并改成任意入口都兜底；
  ②少年档会输出 ``` 围栏代码——新增 `gate.strip_code_fences()`，15 岁以下出口确定性去围栏。

- 2026-11-03 **人格重塑：玛卡巴卡（六七岁的人类小孩）**，设定书
  `docs/agent/design/persona-child-2026-11-03.md`。
  用户三次要求依次是：①做成儿童、全部特征；②不要"在电脑里"、他就是人、不要鱼之类的词、全部重塑；
  ③叫他玛卡巴卡。
  **做法**：`selfhood.FLOOR` 六个键全部重写（身份/生活/身体/感知/认知/安全）——
  六七岁、小学一年级、人类小孩、用手机打字；`STYLE_RULES` 删掉旧约束"不要声称自己是人类"；
  `persona.CHILD` 按语言/认知/情绪/行为/社交/边界/禁语七个维度写死可执行规则；
  新增 `CHILD_REMINDER`（历史之后的强提醒）与 `CHILD_FEW_SHOT`（**单条 system 消息**，
  用 user/assistant 对会让模型把示例当真实对话——实测会回"诶，你刚才问过啦！"）；
  `style_block("child")` 跳过语料学到的成年特征；默认人格 `sunba` → `child`，
  没显式 tics 时自动带 `为什么呀/我跟你讲/才不是呢/好不好嘛/哼/我告诉你哦`。
  **去鱼化**：`大肥鱼`→`玛卡巴卡`（head/role_boundary/extract/reflect/being_checkup/selfhood），
  兜底文案"（吐了个泡泡）"→"（挠头）"；鱼的禁用词表**保留**作为防回流护栏。
  **真机验证**：`tools/archcheck/persona_child_live.py` 9 个探针真打
  `glm-5.3-flash`：`clean_ratio 1.0`、`avg_chars 31.2`、禁用词 0 命中；
  "你是不是 AI"→"你才是AI呢！我是玛卡巴卡呀。"、"你在哪"→"在家里呀，我的小床上。"
  **顺带**：`pyproject.toml` 固定 `[tool.ruff] select = ["E9","F"]`（项目导入按身体系统分组，
  不启用 I001），并修掉两处 F541。测试改 7 个文件（身份断言、名字冲突的用户昵称夹具改成"小笼包"）。

- 2026-11-03 **控制台重构为「架构图对应」+ 桌宠残迹清理**，见
  `docs/agent/evidence/console-rebuild-2026-11-03.md`。
  控制台从手写 4 视图（概览/功能/配置/生命周期）改成**从架构图生成**：
  新增 `tools/console_build.py`（读 `results/nodes.json`，按 band 生成节点卡 + 真实接口面板 +
  动作按钮），输出 `aiji/right_hand/web/operations.html`（23.7 KB、零外链）——
  左栏 9 个身体系统（① 感官…⑨ 骨架）+ ⚙ 配置 / ⏻ 生命周期。
  新增 `tools/archcheck/console_check.py` 三重机器校验：**9 band / 68 节点全有归属**、
  页面引用的 **55 条接口逐条比对 server.py 的 56 条路由（0 条不存在）**、
  真机 `GET /operations` 200 + 9 个导航项 + 每系统首块面板无 5xx。这条校验当场抓出一个真 bug：
  我最初写的 `/api/operations/overview` 服务端**根本不存在**（`route_smoke` 也没这条）。
  桌宠：代码里早已没有实现（`aiji/` 搜 `桌宠|pet_state|tkinter` 只剩 1 处文案），
  这轮清掉 `service_control` 的 presence 描述、`PROJECT_STATE` 未决 #11 与相关风险条目、
  `acceptance-checklist` 的 P3-06/P3-07、历史截图 `workspace-pet.png`；
  回归测试 `test_removed_pet_routes_do_not_create_state` 继续守着 404。
  测试更新 3 处（导航断言改成九系统、页面标记词、openai 用例）。
  **本机 Chrome/Edge headless 都被沙箱 ACL 拦住，控制台渲染没有浏览器截图证据**（同 excluded #49）。

- 2026-11-03 **体检前三项落地**（同 `docs/agent/evidence/project-audit-2026-11-03.md` §八）：
  ①新增 `tools/archcheck/cli_smoke.py`：17 个子命令 33 次真子进程调用 **0 失败**、
  全 JSON、在全新临时库上跑（30 张表）→ 补上 `cli.py` 的 0% 覆盖。
  ②`/api/being/checkup/live` 加 10 分钟结果缓存（`cached/age_s`，`{"refresh":true}` 强制重跑），
  带新测试；`/api/stickers/update` 不动（本身有 30 s 上限、是显式动作）。
  ③查清 `media_unrecognized` 2 001 条：**1 917 条是 QQ 多媒体 `rkey` 过期 → `HTTP 400`**，
  图片/语音压根没下载，**不能用来评价 OCR/ASR 质量**；`low_confidence` 里 2 807 条
  `elapsed<1ms` 集中在 10-31/11-01，是 `probe-2026-11-01` 记的 **G1/R1 遗留**，
  11-02 真实引擎接线后已修（当天 880/935 条走了真识别）。
  ④`recognize.py` 新增 `empty` 状态：跑完没识别出字（表情包/静音）不再混记成 `low_confidence`，
  只有"有文本但低于门槛"才算低置信；历史记录不回填。全量 **494/494**、`ruff aiji tools tests` 干净。

- 2026-11-03 **全项目体检**，见 `docs/agent/evidence/project-audit-2026-11-03.md`。
  实测口径：`coverage`（492 用例全绿）**总覆盖 81%**（9 243 语句 / 漏 1 774）；
  **`senses/cli.py` 285 语句 0%、`endocrine/desktop_sense.py` 80 语句 0%**；
  弱项 `persona` 33%、`actions/gui` 33%、`real_engines` 41%、`executor` 50%、`media_audio` 52%、
  `octop_gateway` 53%。HTTP 面 **58 条精确路由（30 GET + 28 POST）**，原 `http_smoke.py` 只覆盖 8 条
  → 新增 `tools/archcheck/route_smoke.py` 全量打一遍：**0 个 500**，GET 全 200+JSON，
  唯一 5xx 是 `/v1/gateway/onebot` 503（桥未配置，预期）。
  发现并修掉 2 个真 bug：①`POST /v1/voices/use` 空 body 会静默切换声线（控制台标为必填参数）
  → 改 400 `profile_required`；②`being_checkup.live_probe` 用 `tempfile.mkdtemp()`，
  本机 0o700 删不掉 → `%TEMP%` 残留 **33 个**目录（8.1 MB，最早 11-01），改到 `data/temp` 并清理。
  还测出两条"同步重活"接口：`/api/being/checkup/live` **33.8 s**、`/api/stickers/update` **27.3 s**，
  都把 HTTP 线程占住（待改 202+后台）。数据面：24 966 turns / 1 350 summaries /
  7 662 media（**2 001 未识别 = 26%**）/ 723 accounts / 队列 0 积压 / 6 条模型路由可用。

- 2026-11-03 **语音链路真机重测（第二轮）**，见 `docs/agent/evidence/voice-real-retest-2026-11-03.md`。
  全部在 `voice-engine/rvc/.venv` + RTX 3050 4 GB 上重跑：
  fp16 5 s 444 ms / 13 s 682 ms / **30 s 23 799 ms（断崖，显存 3 954/4 096）**；
  fp32 同样素材 588 / 993 / **59 983 ms** → **半精度必须开**，已在 `rvc_server.py` 里 cuda 默认开，
  `/health` 回报 `is_half`。真机整链：5 s 语音 ≈ 2.7 s（首次 10.7 s 含预热）、13 s ≈ 5.6 s、
  30.8 s **被 15 s 护栏跳过**（`last_skipped` 留证据）→ `VOICE_MAX_CONVERT_SECONDS` 改回 **15.0**。
  HTTP 真验收：`POST /v1/audio/speech` → 200 / audio/wav / 4.9 s / 470 444 B。
  换声段拆解：服务端推理 826 ms，客户端额外 546 ms（进程 + HTTP），共 1 371 ms。
  **这轮修掉我上一步制造的 3 个问题**：①删掉 separator venv 打坏了点歌 → 新建
  `voice-engine/sep-venv`(874 MB) + `_default_separator()`；②往 `rvc/.venv` 装 audio-separator
  弄坏了 numpy 元数据（`transformers` 版本检查报 `found=None`）→ 重装 numpy 1.26.4；
  ③`rvc_daemon.ps1 -Stop` 停不住服务 → pid 文件 + 强制兜底，改 `Win32_Process Create` 起进程
  （原来 `Start-Process` 会让捕获输出的调用方挂到超时）。

- 2026-11-03 **更正：RVC 不慢，是我跑错了环境。** 这台机器有 **RTX 3050 Laptop (4 GB)**，
  真正的 RVC 运行时是 `voice-engine/rvc/.venv`（Python 3.12 + torch 2.7.1+**cu128**，`torchfcpe` 也在），
  `data/voices/dafeiyu/profile.json` 一直指向它；我之前拿 `voice-engine/.venv`（torch **cpu**，我自己装的）
  量出来的 RTF 1.6~2.1 不算数。GPU 实测（`tools/archcheck/rvc_gpu_bench.py`）：
  1.5 s 素材 **384 ms**（RTF 0.26）、6 s 素材 **503 ms**（RTF 0.08），CLI 端到端 0.65~0.83 s。
  → **不换引擎**；`rvc_convert.py`/`rvc_daemon.ps1` 改为优先选 GPU 环境并默认 `--device cuda`；
  `VOICE_MAX_CONVERT_SECONDS` 15 → **60**（只在退回 CPU 时才真会触发）。
- 2026-11-03 **磁盘体检：15.28 GB → 10.68 GB（释放 4.61 GB）**，见
  `docs/agent/evidence/disk-usage-2026-11-03.md`。删的是：`rvc/assets/pymss_weights` 1.45 GB（零引用）、
  `rvc/logs` 训练残留 0.79 GB、`.tmp`（我的浏览器 profile + hypothesis 产物）0.95 GB、
  我为跑基准另装的 CPU venv 1.21 GB、`data/voices/.work`。删完重启守护验证：`/health` ok、
  转换输出 142 124 字节与清理前一致、热调用 0.65~0.83 s。
  剩下的大件都是**必需的**：`rvc/.venv` 7.03 GB（里面 torch cu128 5.53 GB）、`rvc/assets` 2.09 GB、
  `data/voices` 1.23 GB（声线训练集）。

- 2026-11-03 **语音引擎决策：不换 SVC，改"按场景分开"**。
  实测（`docs/agent/evidence/voice-engine-decision-2026-11-03.md`）：RVC 常驻后 1.5 s 素材 **2 892 ms**、
  6 s 素材 **9 519 ms**（RTF 1.60）；换 F0 方法只差 4%（rmvpe 3 264 vs pm 3 129 ms）→ **瓶颈是模型推理，不是 F0**；
  纯 TTS：Edge **2 420 ms**、SAPI **704 ms**（同一句 24 字）。结论：CPU 上任何 SVC 都是 RTF≥1，
  换引擎买不到延迟；要"大肥鱼音色的语音条"应换**单段零样本克隆 TTS**（GPT-SoVITS/CosyVoice，
  代码已支持 `provider`，模型未装故未实测），而不是 TTS+RVC 两段式。
  落地：`VOICE_MAX_CONVERT_SECONDS=15`（语音条超预算跳过换声、记 `last_skipped`；点歌不受限）；
  `rvc_server --log` 自写日志 + F0 缺依赖改 400+提示；新增 `tools/rvc_daemon.ps1`（探活/起服/停服/轮转，未注册计划任务）。
  另外删掉三个从未被调用的引擎（`DeclarativeEngine`/`ProceduralEngine`/`SynapticDiffusion`），
  架构图 `lifecard` 节点改成"生命卡四引擎 · 四个都在链路上"。全量 **492/492 全绿**。

- 2026-11-03 **缺口⑥「RVC 常驻化」：做完并实测，顺手纠了两处假话**。
  ☠️ 前提被推翻：`voice-engine/.venv` **缺 7 个 RVC 依赖**（`ffmpeg-python`/`av`/`faiss-cpu`/
  `praat-parselmouth`/`pyworld`/`torchaudio`/`transformers`），补装前 `import infer` 直接 ModuleNotFoundError——
  图里"专用 .venv 已就绪"是假的；且"每次 20s"实测只有 **14 064 ms**。
  ✅ 新增 `tools/rvc_server.py`（VC 对象留内存，HTTP /health /convert /shutdown，只绑 127.0.0.1，单线程锁）；
  `tools/rvc_convert.py` 加 `--via auto|server|inproc`（默认 auto：有服务走服务、没有回退一次性进程；
  `--via server` 服务不在就报错、不静默回退）。
  📈 1.5s 素材、CPU：老路径 14 064 ms → 服务第 2/3 次 2 768/2 842 ms，**热中位数 2 892 ms（×4.86）**，
  已进 5 s 目标；三次输出同为 142 124 字节。修过一个真 bug：服务端 `chdir` 后相对路径解析错（改发绝对路径 + 启动 cwd 兜底）。
  边界：**没有守护/自愈**、只测 CPU、只测 1.5s、profile 里加 `--via auto` 才吃到加速。
  证据：`docs/agent/evidence/gap-closure-2026-11-03/EVIDENCE-rvc-resident.md`；全量 **491/491 全绿**。

- 2026-11-03 **缺口⑤「生命卡引擎接线 / 能量经济学」：能量经济是真的，七引擎里 2 个是死代码**。
  新增 `tools/archcheck/wiring_audit.py`（AST 三条判据：LifeCard 字段组合 / LifeCard 方法内真调用 / 外部调用点，
  含模块级 helper，避免只匹配类名的假阴性）。实测：`LifeCard`、`EndocrineEngine` 在链路上；
  `PersonalityKernel.apply_delta`、`OfflineCompensation.compress_days`、`ContextBudget` 只经 LifeCard 内部调用
  （后者只由 `/api/life/context` 暴露）；**`DeclarativeEngine`/`ProceduralEngine` 组合了但没有任何调用方，
  `SynapticDiffusion` 连组合都没进**。同时改掉架构图一处错误说法（原文称 `apply_delta` 只被测试调用，
  实际 `life_event → card.apply_event` 每次情绪事件都跑）。能量账本（睡/醒收支、每条消息成本、力竭强制休息、
  `can_proactive` 闸门）与"激素→注入条数"（`head.py:240-250` 的 `endo_factor`）都已核对。
  证据：`docs/agent/evidence/gap-closure-2026-11-03/EVIDENCE-life-engines.md`。
  **待办**：Declarative/Procedural 删或接线（决定权在产品）；`SynapticDiffusion` 若用应落在 recall 的 2 跳联想上。

- 2026-11-03 **缺口④「FSRS-5 衰减」：其实早就实现了，缺的是"排序没接它"**。
  核对：`fact.stability/difficulty`（schema v11）、`store.retrievability()`（`R=(1+F·t/S)^-0.5`，F=19/81）、
  `bump_usage()`（被引用就涨 stability）全都在——但 `recall.score_fact` 的排序新近度一直用**按类型写死的半衰期**，
  跟"用没用过"无关。改法：`params.RECALL_DECAY_MODE="stability"`，`recall._recency_for_fact` 读 `fact.stability`。
  **我第一版另写了 `aiji/hippocampus/fsrs.py`，发现与 `store.retrievability` 重复，已删除**（不留第二份曲线）。
  证据：`docs/agent/evidence/gap-closure-2026-11-03/EVIDENCE-fsrs-decay.md`
  （365 天旧事实：half_life 0.0147 → stability 0.4964；**真库 400 条里只有 1 条被引用过**，故当前数据上排序几乎不变）。
  全量 **484/484 全绿**，bench_memory_quality 6/6 仍 ok。

- 2026-11-03 **缺口③「心脏层接管发送」：先纠错，再补真缺的那块**。
  **纠错**：`Memory` 早就给 `Heart` 注入了 `sender=self._heart_send` → 真调 OneBot `/send_*_msg`，
  "本仓库只落 send_queue、投递在 AstrBot 插件"这句话**是错的**（架构图已改正）。
  **真缺口**是失败不分类：`retcode=1401`（token 失效）/1404（目标不存在）/HTTP 401 也要白试到
  MAX_ATTEMPTS 才进死信。本轮加 `SendOutcome`/`send_ex`、`aiji/heart/sender.py::classify`（OK/RETRY/THROTTLE/FATAL）、
  `SendFatal` → 立即 `dead` + `event_log` 留痕。顺手修掉 `Dispatcher._tick` 把单条限流推迟当"平台已恢复"
  抹掉的 **429 热循环 bug**（改用 `_blocked` 实例记忆）。证据：`docs/agent/evidence/gap-closure-2026-11-03/EVIDENCE-heart-send.md`。
  全量 **478/478 全绿**。**未验**：真实 NapCat/QQ 的一次发送（只对假 OneBot 验了分类与落点）。

- 2026-11-03 **缺口②「used_ids 反馈闭环」已端到端证出来（代码本来就通，之前没人证过）**：
  `tests/test_feedback_loop.py` 3 条真 HTTP 用例：响应带 `aiji.injection_id/used_ids` → `POST /feedback` →
  `updated≥1`、被引用 fact 的 `hit_count` 增长、`injection.feedback` 写上、未知 fact id 不炸。全量 **467/467 全绿**。
  **留痕口径**：正向反馈且未下调过的 fact **不写 `fact_log`**，只 `hit_count+1`——别拿 fact_log 数量判断有没有生效。
  边界：插件侧"用户夸一句触发 /feedback"的触发点在 AstrBot 对话流程里，需要真实 QQ 消息，本轮未实测。
  证据：`docs/agent/evidence/gap-closure-2026-11-03/EVIDENCE-used-ids-loop.md`。

- 2026-11-03 **缺口①「回复长度硬上限」已落地（原来超长是整条丢弃，且 QQ 200 字上限声明了没人用）**：
  生成侧 `max_tokens` 夹到 `MAX_REPLY_TOKENS=400`（池子不收就不塞），出口侧新增 `gate.fit_reply` 按平台裁字符
  （IM 200 / 其它 500，切句末、不劈 emoji ZWJ 与 CQ 码），三条出口路径（head / heart / proactive）统一
  "先裁后校验"。新增 `tests/test_reply_limit.py` 12 条，全量 **464/464 全绿**。
  证据：`docs/agent/evidence/gap-closure-2026-11-03/EVIDENCE-reply-cap.md`。踩坑：假池子签名不含 `max_tokens` 会炸、
  `send_queue` 正文在 `payload` JSON 里而不是 `text` 列。

- 2026-11-03 **全量单测 452/452 全绿（此前一直红着的 77 例其实不是代码问题）**：
  那 77 例是 `.tmp/` 目录**陈旧 ACL**导致 `chmod 0o700` / `mkdir` 被拒；`python tools/cleanup_junk.py --apply`
  删掉整个 `.tmp/` 重建后同一套用例全绿。判据脚本已入库 `tools/archcheck/`（README 内有通过标准），
  结果落 `tools/archcheck/results/`。另加 `tests/test_property_isolation.py`（hypothesis，3 条：
  `Ident.where()` 占位符=参数、SQL 形状恒定不拼串、两个身份端到端互不可见）。
- 2026-11-03 **仓库瘦身 218 MB**：`tools/cleanup_junk.py --apply` 删 502 项（`.tmp` 测试工作目录、`build`/`dist` 旧包、
  空目录 `aiji-life-loop-*`/`tmp*`、视觉截图、根目录草稿、所有 `__pycache__`），另删桌宠遗留 `蓝发女仆_Tpose.png`（零引用）。
  根目录收敛到 8 个文件 + 12 个目录。**保留**：`data/`、`voice-engine/`、`.venv/`、`.tmp/research`、`.tmp/pdf`。
- 2026-11-03 **可借鉴开源清单**：`docs/agent/research/borrow-candidates-2026-11-03.md`（8 项，按收益/代价排序：
  评测骨架 inspect_ai → hypothesis → tenacity 重试 → FSRS-5 衰减 → sqlite-vec → mem0 冲突决策 → litellm 路由状态机 → Generative Agents importance）。

- 2026-11-03 **`aiji/` 按九个人体系统重排完成（55 个模块 → 9 个子包，449 例逐条结果与改造前完全一致）**：
  - 新增 `aiji/skeleton/paths.py` 作为 ROOT/DATA/CONFIG/TOOLS/BENCH/WEB_DIR 的唯一来源；8 处 `Path(__file__).resolve().parents[N]` 全部改走它（再分层不会炸）。
  - 迁移脚本 `tools/reorg_body.py`（AST 重写 import + `git mv` + 路径表达式修正），架构图同步脚本 `tools/arch_sync.py`。
  - 证据：`docs/agent/evidence/body-reorg-2026-11-03/EVIDENCE.md`；提交 `ceb7752`（改前）→ `90eeaf7`（改后）。
  - **踩过三个坑**（复现时必须先看）：① 包 `__init__.py` 的路径映射要剥掉 `.__init__`，否则 `from .actions import X` 只改成绝对路径却没换包；
    ② 字符串里的 mock 目标被重复加前缀（`aiji.aiji.hippocampus...`）；③ 函数体内的 import 重写必须保留缩进，否则整片语法错。
  - **仍未做**：心脏层接管发送、生命卡引擎接线/能量经济学、RVC 常驻化、回复长度硬上限、插件回传 used_ids、DSH 借鉴 3 项（见 goal）。

- 2026-11-03 **`docs/architecture.html`（人体架构图）体检：能用；1 处排版溢出、3 处数字过期、2 处符号挂错**：
  - **机器断言 23/24 通过**（DOM 桩里跑内联脚本真代码）：9 层 / 68 节点 / 88 边齐全，节点 id 唯一、两两零重叠、全在 1900×2400 画布内、路径无 NaN、无自环；点选只留邻居、搜索只亮命中、分层开关整层 off、主链路 18 节点全亮其余全暗、`#hash` 深链、抽屉渲染与关闭回总览都正常；`node --check` exit 0，单 script 块、无外链。唯一 fail 是我自己的文字宽度粗估，见下。
  - **真渲染（真 Edge 窗口 + CDP，headless 仍不可用）**：`file://` 打开正常，68 节点 / 88 边全部画出。CDP 实测**9 个节点第二行小字越过方框右边界**（≈1–20 SVG px，最重 qq / song / server），其中 **3 个被右侧相邻方框盖住 1–2 屏幕 px（≈4–8 SVG px）**：qq 的「QQ 已接入 · 微信/抖音/B站可接」肉眼读到「…B站可」，末字被 napcat 方框盖掉。根因：`.node .s` 无折行/省略，可用宽 113–125 px 而文案实宽 120–150 px。
  - **事实核对**：68 个节点的 `f` 路径**全部存在**；156 个关键符号中 153 个字面命中；3 个挂错位 —— `proactive_spend` 实际在 `memory.py`（`Memory.proactive_spend`，非 `proactive.py`）、`archive_turns` 在 `store.py`（非 `janitor.py`）、`Head.reply` 是限定名（源码为 `class Head` + `def reply`，可接受）。
    其余数字**都对**：schema v11（`PRAGMA user_version=11`、`quick_check ok`）、24 张业务表（29 表 − 5 张 FTS 影子表）、`aiji/` 55 个 .py、43 个测试文件、9 个 bench、`cli.py` 17 个子命令、控制台 4 个视图、IM 白名单 8 个平台、7 个端口全在听（6099/6199/6185/8787/8789/8788/3065）。
  - **三处数字过期**：① 顶栏「单测 478」→ 现在采集到 **449**（`discover` 与逐文件 `countTestCases` 一致；`test_being.py`、`test_voice_prepare.py` 现在 0 例）；② 库计数整体上漂：事实 21,365→**21,431**、轮次 24,817→**24,966**、事实日志 23,469→**23,542**、媒体 7,639→**7,660**、风格 1,918→**1,923**、注入 1,512→**1,533**、反思 1,843→**1,850**、trait 1,511→**1,521**、账户 722→**723**；③「heart / outbox / send_queue / write_queue 全 0 行」不成立：`heart_state` 已有 **8 行**（另外三个仍 0）。
  - **措辞**：正文「68 个模块全部常驻」——68 是**图里的节点数**，`aiji/` 实际 55 个模块，节点还含 NapCat / AstrBot / 反代 / 数据目录等非模块项。
  - 证据：`~/.dsh/evidence/2026-11-02/001359-archcheck-extract-and-node-check-2-0b2418/`（语法）、`...001753-archcheck-text-measure-2-354e73/`（DOM 断言）、`...001834-archcheck-cdp-measure-9b2ec9/`、`...002145-archcheck-cdp-cover-2-70544d/`（CDP 量宽 + 截图）、`...001436-archcheck-db-facts-c45a4c/`、`...002354-archcheck-facts-files-symbols-f1ee00/`、`...002453-archcheck-test-count-per-file-2-7686e5/`；脚本在 `.tmp/archcheck/`。
  - **本沙箱 `D:\AIJiYi\docs` 不可写**（`Set-Content docs\probe.txt` 被拒；根目录 / tools / tests / data / .tmp 都可写）→ 检查脚本与截图没能复制进 `docs/agent/evidence/`；**改这份 HTML 现在必须提权**。
  - **没验的**：Edge headless 仍被 crashpad 拦（`--headless=new` 零输出、无 PNG）；视觉后端中途 402（siliconflow 余额不足），只有前两张截图有视觉判读。

- 2026-11-02 **新增全项目交互式架构图 `docs/architecture.html`（真机 + 机械断言双验）**：
  - **形态**：单文件、零外部依赖（不引 CDN），9 条分层带 **68 个节点 / 88 条边**；
    滚轮缩放、拖拽平移、点节点出抽屉（文件位置 + 关键符号 + 相邻节点）、搜索、按层开关、
    「主链路」只留一次真实对话的路径、`#节点id` 深链。
  - **内容来源**：`aiji/` 55 个模块的 docstring 与顶层符号（ast 提取）、`service_control.py` 的
    5 项依赖链端口、`server.py` 路由、`web/operations.html` 四个视图、`config.py` 六条路由、
    `data/aiji.db` **只读**（`mode=ro`）逐表计数、本文件的未决与边界。
  - **验证**：① 内联脚本 `node --check` exit 0；② DOM 桩里跑真脚本 **15 条断言全绿**
    （节点/边齐全、路径无 NaN、每层 x 不重叠、点选只留邻居、搜索过滤、分层开关、主链路、
    hash 深链）；③ 真 Edge 窗口截图 + 视觉复核：9 层齐全、无文字溢出、无穿框、抽屉不挡图、四边无截断。
    证据与复现脚本说明：`docs/agent/evidence/arch-diagram-2026-11-02/EVIDENCE.md`。
  - **图中如实标出三处「代码在、生产不在」**：`outbox/send_queue/write_queue/event_log` 全 0 行、
    生命卡七引擎只有 3 个在链路、`aiji/pet` 已无 .py（桌宠已移除）。
  - **记下的教训**：headless 浏览器在本沙箱仍不可用（`vision_html_screenshot` 失败），像素证据只能
    来自真窗口；且 `gui_*` 报的 focusedWindow 可能与视觉真前台不是一个窗口——用 SendInput 打字前
    必须先截图确认前台（本次因此可能把一串 file:// URL 输进了一个抖音直播间）。

- 2026-11-02 **日志体系修复（当天全部后台日志审计后的四条改动，真机验证）**：
  - **轮转不截断**：`Start-Process -RedirectStandardOutput` 每次启停都会清空目标文件（今天两次
    重启就把 11 MB 的 AstrBot 日志、2.9 MB 的 err 日志洗掉了）。`tools/ensure_console.ps1` 新增
    `Rotate-Log`，拉起前把非空日志改名成 `<名字>.<yyyyMMdd-HHmmss>.log`，只留最近 5 个。
  - **强制 UTF-8**：AstrBot 写中文/emoji，cp936 下今天累计 **2022 条**
    `UnicodeEncodeError: 'gbk'`，`astrbot.err.log` 2.9 MB 全是 loguru handler 报错，`astrbot.out.log`
    是 GBK 字节、UTF-8 工具读成乱码。启动前设 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`。
  - **QQ 媒体绕开代理**：AstrBot 的 aiohttp 用 `trust_env=True`，会继承机器的
    `HTTP_PROXY=127.0.0.1:7897`；今天 13:10–20:35 群图片/语音下载 **4084 条** 4xx 失败
    （连带 `aiji_memory` 图片描述 1608 次 `ClientResponseError: 400`）。启动脚本把
    `multimedia.nt.qq.com.cn` 加进子进程 `NO_PROXY`。
  - **常驻采集器保活**：`data/field_monitor.jsonl` 昨天 23:25 后就没数据。`ensure_console.ps1`
    在 `data/monitors.on` 存在时检查该文件写入时间，超过 2 分钟没更新就重新拉起
    `tools/start_field_monitor.py`（自带 psutil 去重）。`tools/proactive_audit_1h.py` 是
    **一次性**审计（`DURATION_S=3600`，10:24 正常跑完并写出 summary），**不**接保活。
  - **实测**：重启后 `astrbot.err.log` 从 153 KB/2.9 MB 降到 **96 B、0 条 UnicodeEncodeError**；
    四个日志 `utf-8 OK / replacement_chars=0`；`astrbot.out.log` 中文可读；`AIJiYi 看图
    group:…第1张：…` 连续出现（2 分钟内十余张图正常描述）；轮转产物
    `astrbot.out.20261102-204932.log` 等 4 个已生成；`field_monitor.jsonl` 20:49 起按 15 秒
    继续写（20:54:52 / 20:55:09）；`tests.test_operations` 12/12 OK。
    证据：`C:\Users\22166\.dsh\evidence\2026-11-02\204917-restart-with-rotation-and-utf8-582ea0\stdout.txt`、
    `...\204928-confirm-rotation-after-real-restart-bcb136\stdout.txt`。
  - **没证明的**：今天那 4084 条 4xx 到底是代理隧道还是 CDN 拒签，没有拿到一条带 `rkey` 的
    原始 URL 做对照（AstrBot 日志按 `_safe_url_for_log` 抹掉 query）。目前只证明「重启后 0 失败」
    +「裸 GET 直连/走代理都回 400」，所以按"宁可绕开代理"处理，`NO_PROXY` 只在**下一次启动**生效。
  - **顺带修的**：`restart` 原来会把 QQ 一起杀掉（`Stop-AijiStack` 无条件杀 `NapCatWinBootMain.exe`，
    注入在 QQ 里的 NapCat 随宿主一起没）。`tools/lifecycle.ps1` 改成 `Stop-AijiStack -IncludeNapCat`，
    **只有 `stop` 才动 NapCat/QQ**，`restart` 只重启 AstrBot + 核心。
  - **收尾遗留（需人工，是我的操作造成的）**：20:49 那次 `restart` 后 QQ 在 20:52:23 断流；
    我随后四次 `Stop-Process` 杀 QQ 想让它自动登录，其中 **21:18 那次正好打断了 QQ 的自更新**
    （21:15:26/27 `D:\QQNT\versions\config.json` 被改写：`baseVersion=9.9.35-52892`、
    `curVersion=9.9.36-53644`、`readyVersion=""`，`versions\` 下只剩 `9.9.36-53644`），
    21:19 桌面弹出 **“文件已损坏，请重新安装QQ。”**。之后 QQ 启动只剩 1 个 22 MB 进程、
    `6099` 不监听、无窗口 → 机器人 `ready 3/5`。
    → **人工**：重装一次 QQ（账号数据在 `Documents\Tencent Files`，不受影响）→ 双击
      `启动AIJiYi.bat`（自动注入 NapCat 并拉起整套）→ 首次登录扫码。
    已验证与 QQ 无关的部分全部正常：`/health` 200、`AstrBot/AIJiYi/实时状态 ready`、
    四日志 UTF-8 无编码错误、`field_monitor.jsonl` 15 秒一条、6199 服务端配合 token 探测回 400。
    证据：`~/.dsh/evidence/2026-11-02/210905-watch-onebot-after-napcat-relaunch-5076c0/stdout.txt`、
    `...212417-relaunch-napcat-qq-again-efef62/stdout.txt`、`...212720-cleanup-and-health-after-qq-break-f2cdb6/stdout.txt`。
    **教训已进 excluded #86**：别用杀进程的方式"重启 QQ"。

- 2026-11-02 **「启动与结束」改成实测诊断页（前端 + 后端字段，真机验证）**：
  - `service_control.py` 重写：`snapshot()` 现在给每项服务 `state`（`ready` / `partial` = 端口在听
    但 QQ 没连上 / `stopped`）、`pid`、`process`、`connections`，外加 `order`、`blocked`
    （第一个没就绪的服务 + 处置建议）、`desired`（`data/service_state.txt`）、`watchdog`
    （登录启动项 + 计划任务）、`prerequisites`（5 条路径实测）、`log`（`data/lifecycle.log` 尾部）、
    `checked_at`。新增白名单动作 `watchdog-on` / `watchdog-off`（`schtasks` 注册/注销保活任务）。
  - 进程名取法：`tasklist` 在沙箱里 `Access denied`、CIM 查询也为空，改成**一次 PowerShell
    `Get-Process`** 批量解析；AIJiYi 自己的 pid 直接标「aiji 核心（本进程）」。
  - `server.py`：CORS 只放行 `null`（`file://`）与 `http://127.0.0.1:8787` / `localhost:8787`，
    白名单外的预检返回 403；新增 `/operations.html` 别名（此前直接访问是 `{"error":"not_found"}`）。
  - `operations.py`：`read_config` 多返回 `defaults`，页面「恢复本组默认」才有依据。
  - 页面：状态条 + 5 段依赖链（卡点高亮）+ 前置检查 + 守护开关 + 操作进度轮询（每 2s 复查真实
    端口，最多 90s）+ 日志尾部；配置区补了脏值计数、保存失败原因、恢复本组默认与「保存并重启」。
  - **实测**：`tests/test_operations.py` **12 条 OK**（含新字段、动作白名单、幂等注销）；
    operations + console_api + console_workspace + lifecycle **39 条 OK**；
    live `/api/operations/services` 返回 5/5 就绪、pid 29436=`QQ`(6099)、12980=`python`(6199/6185)；
    CORS 实测 `Origin: null` 预检 204 + `Access-Control-Allow-Origin: null`，陌生来源无该头且预检 403；
    配置 POST `budget_ms:120` → 落盘 `config/runtime.json` → GET 回读 120 → 非法值 400（测试后已删文件还原）；
    真机 Edge 窗口截图确认状态条 / 5 张卡 / PID 均来自实时数据。
    证据：`C:\Users\22166\.dsh\evidence\2026-11-02\110959-verify-cors-and-services-after-restart-4d9bef\stdout.txt`、
    `...\111552-config-save-roundtrip-live-83ebf8\stdout.txt`、`...\111542-dom-id-consistency-c85deb\stdout.txt`。
  - **顺带修掉**：`/v1/audio/speech` 原先用 `tempfile.TemporaryDirectory()`（0o700），沙箱下必报
    `PermissionError` → 400；改用 `voice._workdir()`（0o777）+ `finally` 清理，`test_openai_api`
    的语音用例转绿。
  - **验证边界**：Chrome/Edge headless 在本沙箱仍不可用（crashpad `OpenProcess` + mojo
    `platform_channel` 拒绝访问），页面视觉证据来自真窗口 + 桌面截图；「结束整套服务」按钮
    没有真按（会把控制台一起停掉，本次会话即失去操作面）；`file://` 打开只验了 CORS 头与代码
    路径，没做视觉复核。

- 2026-11-02 **修复「每次重启定制语音不启动」（真机验证）**：
  - **三个叠加根因**（都不是"引擎没装好"）：
    1. **没人拉起引擎**：`启动控制台.bat` 只起控制台，Seed-VC(9880) 要手动点。→ 新增
       `tools/ensure_voice_engine.ps1` + `启动语音引擎.bat`，控制台启动时后台调用（幂等，看端口不看 pid）。
    2. **进程会被控制台事件杀掉**：err log 里有 `forrtl: error (200): program aborting due to
       window-CLOSE event`。→ `start-aiji.ps1` 设 `FOR_DISABLE_CONSOLE_CTRL_HANDLER=1`；
       并且"是否在跑"改判端口（残留 pid 文件曾让它误判"已在运行"直接退出）。
    3. **转换请求因跨进程文件权限失败**：引擎读不到/写不进调用方的临时目录
       （`PermissionError: [WinError 5]`，跨完整性级别），`synthesize` 于是静默退回 edge 音色
       —— 用户听到的"定制语音"其实是微软晓依。→ 改成**纯字节协议**（`source_b64`/`target_b64`），
       引擎只在 `voice-engine/seed-vc/tmp`（0o777）写自己的临时文件；客户端用 `_workdir()` 显式 0o777。
  - **附带修掉**：Seed-VC float 输出峰值 >1.0，PCM16 直接削波（历史 `smoke_dafeiyu_real.wav`
    peak=1.000、4.1% 采样贴顶）→ 引擎在写盘前把峰值归一到 0.95；客户端 `synthesize` 记录
    `last_fallback` 并进 `/v1/voices`，以后降级可见。
  - **验证**：直接后处理 4.33s、输出 190,508B ≠ 原始 207,438B、peak=0.95/削波 0%；
    产品路径 `synthesize(profile=dafeiyu)` 4.16s、`last_fallback=None`；voice+song 单测 23/23。
  - 注意：`Get-NetTCPConnection -LocalPort 9880` 在本沙箱会对该端口假阴性，**用 netstat/TcpClient 判活**。
  - **仍需人工**：重启 8787 控制台才会加载新客户端代码；`dist/` 的 0.8.3 wheel 落后于
    `voice_service.py`(02:13)，打包前要重跑 `uv build`。
- 2026-11-02 **Seed-VC 已弃用（改走 RVC），但迁移只做了一半**（承接上条，实况核对 04:00）：
  - **事实**：3:57 另一个会话按"用户决定全部走 RVC"，删掉 `voice-engine/seed-vc`
    （8,005 MB / 35,176 文件，含它自己的 venv 与 checkpoints），3:59 提交归档
    `docs/agent/research/seed-vc-integration-archive.md`。**不是清理脚本，也不是本会话**。
  - **当前断点**：`data/voices/dafeiyu/profile.json` 的 `postprocess` **仍指向
    `http://127.0.0.1:9880/convert`**；全盘没有任何 `*rvc*` 文件/模型 → 定制音色当前
    **必然回退 edge**（`_ready()` 端口不通）。RVC 侧只有"该怎么接"的结论
    （`voice_service.py` 支持 `postprocess.command` + `{input}`/`{output}` 占位符），没有实现。
  - **本会话已适配**（避免每次开机报错）：
    - `tools/ensure_voice_engine.ps1` 重写为**按 profile 自适应**：`command` → 只查可执行文件；
      `url` → 端口判活 + 找启动脚本；两者都没有 → 打印"会回退 edge"并 **exit 0**（不再 `starter missing`）。
    - `启动语音引擎.bat` 文案同步（"检查/启动"，不再写死 Seed-VC 日志路径）。
    - **削波修复搬到客户端**：`VoiceRegistry._limit_peak()`，PCM16 峰值 >0.98 时缩到 0.95，
      CLI/HTTP 两种转换器都受益（实测 32767 → 31128 = 0.950），不再依赖易失的引擎实现。
  - **待办（RVC 侧）**：装 RVC 运行时 + 写 `rvc_convert.bat {input} {output}` +
    把 profile 的 `postprocess` 从 `url` 改成 `command`；改完 `ensure_voice_engine.ps1` 会自动识别。
- 2026-11-02 **记忆代谢按方案全量落地（schema v11，全绿）**：把上面那份调研/借用方案
  （`docs/agent/design/borrow-plan-2026-11-02.md` §4）**全部实现**，实施状态与实测见该文件 **§9**。
  - 代码：`db.py`（v11 迁移）、`store.py`（`bump_usage/retrievability/should_freeze/thaw_fact/
    freeze_facts/forget_facts/archive_turns/prune_archive/self_memory_*/fact_keys/summary_by_hash`）、
    `inject.py`（tier 档位 + 同事务强化 + 弃答门）、`recall.py`（`rank_mode`/`_fuse_rrf`/事实键）、
    `janitor.py`（`_stale_freeze` + 归档清理 + 自我记忆淘汰）、`reflect.py`（`_pending/_should_run` 批处理）、
    `writer.py`（摘要哈希复用 + 归档前写 summary）、`memory.py`/`cli.py`（forget/archive/self-memory）。
  - 新增 bench：`bench_decay_baseline.py`、`bench_paraphrase.py`、`probe_mark_used.py`、
    `bench_self_test.py`（27 探针 6 指标带门槛）。
  - **关键实测**：全量 **464 条单测 OK**（归档 `...\023625-aiji-suite-after-metabolism-492340\stdout.txt`，
    sha256 `ca2dd2f9…`）；生产库 8,173 条事实 **`never_used` 100%** → 坐实"读→强化从来没接上"；
    换说法 R@1 0.417 / R@5 0.500；自检 hit@5 1.0 / abstain 0.5 / stale 1.0 / **leak 0**。
  - **两条被实测打回的**（excluded #63/#64）：弃答门 0.15 会误伤短问句（默认关）；
    RRF 在单路候选上与手工权重**完全一致**（默认仍 weighted，等向量层接入再开影子对比）。
- 2026-11-02 **全项目体检 + 清垃圾（省 4.9 GB，测试仍全绿）**：
  - **先建回滚点**：仓库此前**一次提交都没有**（`git stash` 都会失败），清理前先 `git add -A && commit`
    → `46b83d1`；清理后 `ffe185d`。删错可 `git checkout 46b83d1 -- <path>` 取回。
  - 新增 `tools/cleanup_junk.py`（**默认 dry-run**，`--apply` 才删）+ `tools/audit_unused.py`
    （每个 .py 数一遍外部引用）+ `tools/audit_pkg_dead.py`（`aiji/` 包内有没有没人 import 的模块）。
  - **删了什么**（4,921.7 MB / 12,536 项）：`.tmp/` 下 12,373 个测试工作目录（4.4 GB）、
    `.dsh-vision-router/` 视觉截图（17.6 MB）、根目录 130+ 个 `aiji-style-*` 空目录、6 个 `tmp*`（86 MB）、
    `build/`、`dist/` 旧版本 6 个包（52 MB）、`.pytest_cache`/`.ruff_cache`/`.coverage`/`egg-info`/`__pycache__`、
    12 个一次性审计/调试脚本（`tools/probe/_audit6h_*`、`_check_*`、`_voice_*`、`summarize_field_audit_6h.py` 等）。
  - **明确保留**：`data/`（真库 22 MB + dafeiyu 音色 477 MB + 歌曲）、`voice-engine/`（9 GB：Seed-VC .venv
    6.1 GB + checkpoints 1.8 GB + audio-separator .venv 1 GB）、`.tmp/research` + `.tmp/pdf`（docs 引用的证据原文）、
    `dist/aiji-0.8.3*`、`baseline.json`、所有 `*_smoke.py`（OCR/语音/whisper 唯一的手工验证入口）。
  - **结论**：`aiji/` 包 **0 个死模块**（43 个模块都有真实 import 方）；4.6 GB 垃圾全是文件系统残留，不是代码。
  - 清理后全量 **478 条单测仍 OK**（`...\031800-aiji-suite-after-cleanup-740e12`，sha256 `076d8a00…`）。
- 2026-11-02 **主动配额改为"反代积分折算"（用户口径）+ 自我记忆进注入块**：
  - `aiji/brain/quota.py`：`allowed_24h = min(8, floor(剩余积分×0.8 ÷ 单条单价))`；积分取不到就退回 3/天。
    来源四级：配置 URL → 响应头 → `data/credits.json` → 静态/`AIJI_CREDITS`。
  - 接线：`head.py` 主动闸门 + `Memory.proactive_spend` + `Proactive(quota_provider=)` + `aiji quota [--set N]`。
  - **真网关实测**：单价 0.08/条（从 agent2api `/v1/models` 的 `credits` 字段读到）；
    填 40 积分 → `allowed_24h=8`（被防刷屏上限截断）。
  - **余额读不到**（excluded #65）：两个反代都没有 quota 响应头/余额 JSON，workbuddy 的 dashboard 是 SPA。
    故留"手填 + 按单价折算 + 显示填了多久（`file(3.2h)`）"的落地通道。
  - `self_memory` 进注入块：`kind="self"`，≤2 条 / ≤60 字 / 需与查询 2-gram 重叠 / 分数 0.45。
  - 测试：`tests/test_quota.py` 14 条；全量 **478 条 OK**（`...\030055-aiji-suite-with-quota-4c1252`，
    sha256 `23be842e…`）。
- 2026-11-02 **调研（未改代码）：SLEEP 框架开源生态 + `y083245/dsh-memory-brain`**：报告
  `docs/agent/research/sleep-framework-survey-2026-11-02.md`（16 仓库，README 原文落盘 + sha256；
  归档 `C:\Users\22166\.dsh\evidence\2026-11-01\011610-sleep-ecosystem-readmes-441817\`）。
  - **SLEEP（arXiv 2606.03979）是改权重的**（Knowledge Seeding 蒸馏 + RL Dreaming）；唯一论文忠实实现
    `EvolvingAgentsLabs/sleep-harness` 需读 residual stream + LoRA + Colab，且已 **[ARCHIVED]** → 不可用。
    可用的只有 **harness 级**（离线巩固/修剪/选择）那一支：`agent-sleep`（门禁表）、`dsh-somni`（双曲线 +
    间隔重复 + 可抢占 dream + 退役不删）、`anda-brain`（先结算后模型、维护后自检）、`MemHop`（写 1 次/轮、
    注入体积靠巩固收敛）、`Pseudolife`（唯一有 LongMemEval 全量数字）。
  - **对 AIJiYi 的三条 P0**（详见报告 §4）：① 每轮反思 → **空闲/夜间巩固作业（可抢占、按会话批、幂等）**；
    ② fact 加 `stability/difficulty/last_review/frozen` 一层**门禁**（FSRS-5 规格现成，`memory-decay.js` MIT），
    注入不出现但可查、"查到即解冻"；③ **淘汰改退役**（turn 原文归档而不是 `DELETE`）。
  - **否定性证据**（已进 excluded #58）：只给事实不给原文，LongMemEval 事实脊 0.416 vs naive RAG 0.688，
    multi-session 0.504→0.474 → "事实优先"必须保留回退原文的能力。
  - `dsh-memory-brain` = **同题不同栈的近亲**（QQ 陪伴 + 长期记忆；Node/DSH/Linux vs Python/SQLite/AstrBot/Windows；
    MIT）。可搬四件：FSRS-5 代谢、账本建议（候选 + 采用率）、人格判定的**影子模式**、RAG 总开关 + 失败降级 +
    换模型重建；**不搬**它的 785KB 单文件 `bridge.js` 与 Chroma+SQLite 双写。
  - 边界：**没有运行任何候选项目**；Pseudolife 的基准、手册 v4/v5 的 Recall@5 27.8→55.6 都是项目/作者自述（B 级）。
  - 配套两份内容级摘要：`docs/agent/research/manual-v4-v5-digest-2026-11-02.md`（手册讲了什么 + 三条
    手册独有的 P0：弃答四档进注入块 / 裁剪护栏 / 事实键扩展）、
    `docs/agent/research/memory-brain-digest-2026-11-02.md`（仓库地图 + 记忆/人格/主动三主线 + 可抄常数）。
- 2026-11-02 **反思降载 + 控制台 11 入口 + 打包 的针对性验证（本会话实跑）**：
  - **反思降载确认生效**：`reflect.py` 单次调用、`timeout_s=60`、`background=True`、
    `attempts_per_route=1`；新增两道前置闸——"没有 assistant 发言就跳过"、"模型输出为空不写
    reflection 行"。probe 23/23 验过（含"上游报错只调一次"）。
  - **控制台 15 入口确认接通**：nav 15 按钮、`titles` 15 键、`loadCurrent()` 的 loaders 映射 14 个
    （chat 常驻），9 个数据接口字段逐一比对通过；工作区默认 403、开启后可用。
  - **发现并修复**：①测试契约过期 → `tests/test_selfhood.py` 的 `_turns()` 只写 user 轮、
    FakePool 不接受新 kwargs，导致 6 例失败；改为 user+assistant 成对写入并放宽 fake 签名后
    **全量 454/454 OK**。②**wheel 是旧的**（0.8.3 在 00:33 打包，而 `reflect.py` 00:34 才改）——
    已 `uv build` 重建，逐个文件 sha256 比对 **STALE=[]**，资产齐全。
  - **README 已同步**：4 页签→4 主入口+11 功能页；OCR/ASR"未内置"→已内置 RapidOCR/FunASR；
    风格 3 维→4 维；`HashingEmbedder`→hash/hybrid。
  - **仍未解决**：LICENSE 是专有许可（若真要"开源"必须换）；`_onebot_reply` 对空 content 会抛
    `user_message_required`（生产路径有上游守卫，属防御缺口）；后台跑测试时 TEMP 落在沙箱临时目录
    会让 78 个用 `tempfile` 的用例 ERROR（前台跑正常）——测试应统一 `workdir()`。
- 2026-11-01 **问题总表（四路证据汇总）**：`docs/agent/problem-register-2026-11-01.md`。
  汇入 6 小时现场审计（`data/field_audit_6h_summary.json`，290 采样/4.82h，用户提前停，**health 0 失败**）：
  - **P0**：①模型侧 408 平均 22.2%/最高 35.7%（177/290 采样 >20%）、余额 catpaw/zcode 归零；
    ②QQ 平台"每分钟只能发5条"22 次，回复被拒；③**心脏层生产 0 行**（send_queue/outbox 全空），
    限流感知退避没生效；④图片 OCR 生产 2317/2317 空。
  - **P1**：反馈闭环 0/825、自我生活 92% 低电量/主动 0%、小本子 13/15 同一句、反思 52.8% 空转、
    生命卡七引擎死四个、视觉被核心丢弃、清理留下 25+2 条孤儿、链路断连 4/4/1 次 + pending max 56。
  - 因果：平台 5 条/分钟 + 模型 408 + 写队列堆积 → 4.8h 内 4 段 ≥10 分钟回复空窗（最长 12 分钟）。
  - 建议顺序：发送收回心脏层 → OCR url→bytes → 模型侧降载 → 插件回传 used_ids → 能量经济 → 清理级联。
- 2026-11-01 **运行时深度探测（真跑，297 用例 / 295 PASS / 2 FAIL）**：报告
  `docs/agent/probe-2026-11-01-runtime.md`，脚本 `tools/probe/probe_*.py`（隔离临时库 + FakePool，
  唯一碰生产库的 `probe_db.py` 用 `mode=ro&immutable=1`）。
  - 通过：HTTP 90/90（含鉴权 401 矩阵、越权 403、SSE、61 条路由全可达）、记忆管线 30/30、
    识别 26/26（真实 OCR 1.5s / 真实 FunASR 3/3）、认知 27/27、服务安全 49/49、
    心脏 e2e 10/10、平台模拟 37/37（40 轮）、质量 bench 全绿、召回 2000 事实 p99 2.67/3.81ms。
  - 失败 2 项均为生产库完整性：`fact_link` 孤儿 2 条、`fact_log` 孤儿 25 条，
    **全部来自 10-30/31 手工清理的测试身份**（777/888/99000xxx/learn_probe/sunba_probe…），
    运行时本身从不物理删。建议清理脚本按身份级联，或干脆软删。
  - 运行时复现的缺口：url-only 图片 OCR 仍不生效（R1）、生产反馈闭环 0/825（R2）、
    心脏层生产 5 表全 0 行而 e2e 10/10（R3）、自我生活 92% 时间低电量/93% 休息/主动 0%（R4，
    24h 快进模拟）、图片空文本 2317/2317（R5）。
  - 参数边界：sim `--rounds 24` 时 S4 三断言失败（历史行不足），40 轮 37/37 —— S4 需要 ≥35 轮。
- 2026-11-01 **全量模块审计（只读，未改代码）**：报告 `docs/agent/audit-2026-11-01-modules.md`。
  口径按"代码在 / 已接线 / 生产在用 / 已验证"四档分。硬结论：
  (1) **图片 OCR 生产 100% 空产出**（media image 1823/1823 空文本、conf 0）——OCR 适配器只吃
  `extra.bytes`，QQ/AstrBot 只传 url，ASR 有 url 下载分支而 OCR 没有；
  (2) **反馈闭环从未触发**（injection 750 行 feedback 全 NULL、fact.hit_count>0 = 0），
  插件不回报 `used_ids`，故排序反馈与 cited 纠错只在 sim 里成立；
  (3) **心脏层闲置**（outbox/send_queue/event_log/heart_state/instance 全 0 行），
  发送实际由 AstrBot 插件完成，`proactive.py` 在核心内是死代码。
  验证：Python 3.13 全量 **438/438 OK**（E1）；`.venv`(3.12 空依赖) 跑同一套 1 失败，
  是环境差异不是回归。
  （2026-11-02 更正：LICENSE 已补，但内容是**专有许可**——"All rights reserved，未授权使用/复制/分发"，
  不是开源许可；`dist/` 已重建到 0.8.3 且与源码一致；仍无 git/CI；残留临时目录仍在。）
- 2026-11-01 **自我生活 / 高级逻辑层深剖**（承接上条，报告第六节；excluded #55/#56）：
  - **生命卡七引擎只有三个在链路上**：内分泌（perturb/regress）、离线补偿、`endo_factor`（注入条数）；
    `DeclarativeEngine`/`ProceduralEngine`/`SynapticDiffusion`/`PersonalityKernel.apply_delta`
    **唯一调用方是 tests/test_life.py**；`ContextBudget` 只由 `GET /api/life` 调用；
    `LifeCard.to_dict()` 只存 personality/endo/last_tick。生产证据：200 张卡 `personality.vector`
    全是 `{"identity":0.25}`。
  - **自我生活活着但永远"很累"**：`/being` 实测 energy **0.01**、social 0.01、`proactive_ok=false`；
    24h 839 事件、能量净 **-77**；20 次活动 16 次是"休息/睡觉"（能量恒 <28）。
    因果：每条消息 -0.7 + 白天 -0.8/h，恢复只有睡眠 +3/h、休息 +4/40min。
    连带 ① prompt 每轮注入"现在确实有点累：回复更短"；② `can_proactive` 要 energy≥20 且 social≥15
    → **主动搭话被永久阻断**。
  - **私有小本子退化**：15 条里 13 条同一句"刚才有点被刺到"（`infer_event` 把"笨蛋/滚/垃圾"判 anger，
    孙吧群聊日常触发），且每轮 2 条进 prompt。
  - **反思在跑但一半空转**：1122 行、41% 有 trait 产出、52.8% summary 空洞；今天 980 次反思
    （每次一次模型调用）；trait 786 行、24 条/身份封顶、732 条置信度在 0.5-0.7。
  - 建议优先级：能量经济学重算稳态 → 小本子去重/降权 → 反思加前置闸 → 死引擎二选一。
- 2026-10-31 **AI 核心一致性 / 联网图库 / 数据审计**：新增公开 feed 表情图库
  更新器（公网 URL 校验、图片签名、大小上限、原子写入、并发锁、定时循环），
  AstrBot 插件按 manifest mtime 热加载。核心层修复昵称单值冲突、跨 scope 以 QQ
  号统一最新称呼、昵称/头像仅作账号标签、反思拒收称呼/鱼意象自强化、提示词
  反问与人类身份矛盾；链接识别和歌曲下载增加私网 URL 拦截。数据审计报告：
  `docs/agent/audit-2026-10-31-core-data.md`。
  已备份并清理 24 个测试身份，回填真实 QQ 展示名，迁移历史私聊 media scope；
  新增全局 trait（至少两个独立关系重复）与关系型 trait 分层。
  新增四层“成精体检”和真实模型三探针；IM 角色空间禁止输出可运行代码/执行命令，
  桌面平台保留工具能力。
  验证：主项目 `409` 项完成（`408` 通过、`1` 跳过）；AstrBot 插件 `38/38`
  通过；真实公网图库冒烟因读取超时失败，旧图库不受影响。
- 2026-10-31 **QQ 空间与扩展声线落地**：新增 `aiji/heart/social_service.py`，通过
  NapCat 扩展 OneBot action 执行发说说、评论/自动选最新说说评论、头像、签名、
  网易云音乐分享和 B 站链接分享；`/api/social/*` 受 `AIJI_API_KEY` 保护。
  媒体路径限制在本地社交目录/表情包目录，表情包仍只读本地 `manifest.json`。
  GPT-SoVITS 增加 v2 JSON POST 和相对参考音绝对化；CosyVoice 增加
  `cross_lingual`，zero-shot/cross-lingual 缺参考音时不再误报 ready。
  验证：新增 19 项相关单测；当次全量运行 `378` 项，`377` 通过、`1` 跳过。
- 2026-10-30 **产品收敛（用户拍板）**：不再扩控制台/自写 QQ 框架，AIJiYi 定位为
  “记忆+人格中间件”。控制台砍到 **4 页签**（仪表盘/对话测试/记忆库/日志·死信）；
  工作区/终端默认关闭（`AIJI_ENABLE_WORKSPACE=1` 才开）；`/v1` 支持
  `user=qq_group_<群>_user_<QQ>`、`qq:group:...:user:...`、metadata/aiji 身份对象，
  并兼容 AstrBot 的 `x-astrbot-conversation-id` 作 session 兜底；声音默认 **Edge TTS**
  （`zh-CN-XiaoxiaoNeural`，失败回退 SAPI，`AIJI_TTS=sapi` 可强制离线）。
  验证：全量 285/285；live `/v1` 把 `qq/777/group:424242` 的事实落库并隔离；
  Edge `synthesize_edge` 实测 13824 bytes。**AstrBot 通用 provider 不带 QQ 号**，
  直接填 Base URL 会串号，Task 2 需要 30-50 行插件传身份（下一步）。
- 2026-10-30 **控制台第二阶段（照抄 dsh 插件）**：新增
  **桌宠悬浮层**（whale 精灵帧推进 + 摸头/喂食/亲密度/改名/拖拽，复用 `PetStore` 与
  `data/pet_state.json`）、**对话实时统计**（route / 模型耗时 / 总耗时 / token 估算 /
  tok/s / 注入 id）、**工作区页**（文件树 + 预览/编辑 + 终端，服务端把路径限制在工作区根；
  `AIJI_CONSOLE_SHELL=0` 可关终端）、**记忆星图换 ECharts**（force / roam / 点击详情）。
  新增 `/api/pet`、`/api/pet/pet|feed|rename`、`/api/fs`、`/api/fs/read|raw|write`、
  `/api/shell`；控制台支持 `#view` 深链；新增 `启动控制台.bat` 与 `tools/stop_console.bat`。
  验证：`tests/test_console_workspace.py` 4/4、全量 281/281；真实 Edge 窗口截图
  （工作区 + 桌宠）在 `docs/agent/evidence/console-2026-10-30/`。
- 2026-10-30 **产品接入层落地**：上游只保留用户已有的 Agent2API / WorkBuddy
  积分反代网关；AIJiYi 新增 OpenAI 兼容 `/v1/models` 与
  `/v1/chat/completions`（普通 JSON + SSE），桌宠/IDE/IM 网关不再各写一套。
  控制台由服务端直接托管 `/`，显示路由冷却、队列、记忆和流式对话。
  一问一答改为单个异步 exchange 任务原子落库，修复双 worker 下 user/assistant
  可能乱序的问题。下游 `AIJI_API_KEY` 与上游 `AGENT2API_KEY` 分离。
- 2026-10-30 **开源目标核对**：确认 Octop Gateway、NagaAgent、HippoGraph Pro、
  proactive-gate、Pengy、Pi-Agent-Web、Desktop-pet 等链接与许可证；
  `AutoMind Agent` / `harness-im-bridge` 未找到唯一可信仓库，不硬编码依赖。
  边界见 `docs/agent/design/open-source-assembly.md`。
- 2026-10-28 交付形态 = **Python 包 + CLI + 本地 HTTP 服务**；向量索引默认关闭（用户选定）。
- 2026-10-28 事实冲突用 **supersede**（保留历史 + `superseded_by`），不删除覆盖。
- 2026-10-28 召回硬预算 = 另开线程 + `Future.result(timeout=remaining)`，超时退化纯内存并标 `degraded`。
- 2026-10-28 模型池失败不重试整链路：`PoolExhausted` 立即落规则兜底，写入不丢。
- 2026-10-28 **数据模型改为多平台共用一套表**（用户指定）：`platform`/`scope`/`extra` + 复合身份主键；
  `account` 存身份；媒体只存引用；schema v2 + v1 自动迁移（含 FTS rebuild）。
- 2026-10-29 **schema v3：学习/总结/操控落地**（用户指定）：`fact.kind`(fact|knowledge|relation)、
  `fact.hit_count/last_used_at`（反馈）、`style` 表（风格与人格核心隔离）、`injection` 表（注入可追溯）、
  `media` 增 `text/confidence/status/recognizer/elapsed_ms`（识别结果可追溯）。
- 2026-10-29 **识别器可插拔**：OCR/ASR 由调用方注入；缺引擎时如实 `unsupported`，不编造文本。
- 2026-10-29 **不按类型分叉**：识别层只产出 `Envelope`，之后与纯文本走同一段抽取代码；
  唯一例外是**关掉风格学习**（OCR/ASR 文本是别人的话，不能算用户表达风格）。
- 2026-10-29 **注入只出文本块**：`persona_touched=False`、`generated_reply=None`、`soft=True`，
  硬字符预算 + 来源标注 + 关系标签；有 bench 断言"0 次越权、0 次超预算"。
- 2026-10-29 摘要触发改为**"距上次摘要够 N 轮"**而非 `n % N == 0`（并发跳值会让后者永不触发）。
- 2026-10-29 **v0.4 记忆质量（调研 mem0/Graphiti/A-MEM 后）**：冲突消解采用 **ADD-only 优先**
  （多值累积、单值才 supersede、否定 expire），全部写 `valid_from/valid_to` + `fact_log`，不删历史。
- 2026-10-29 排序改为**多信号**（相关度/重要度/衰减/置信度 + 反馈），重要度写时算，
  半衰期按属性类型（偏好 60d、备注 45d、居住地 240d、职业 365d）。
- 2026-10-29 关联只做 **1 跳**、每事实最多 3 条边（防链接表爆炸），邻居分数按权重打折。
- 2026-10-29 turn 压缩：**先摘要再删原文**（`max_turns_per_session=400 / turn_keep=40`），
  摘要优先模型、失败退本地压缩；删完清召回内存缓冲。
- 2026-10-29 语义索引**启动时从 fact 重建**（只收 active），命中后按身份二次过滤——修复潜在跨平台泄漏。

- 2026-10-29 **桌宠形象改用真立绘**：`蓝发女仆_Tpose.png`（JPEG 误存 .png）确认为插件同款
  大肥鱼形象；抠图后入库 `aiji/pet/assets/dafeiyu.png`（2000² 透明底）。
  `avatar.py` 改为加载真图 + 状态轻量叠加（不再手绘）；`app.py` 重写为豆包式
  （悬浮球 / Alt+Space / 划词 / 截图 / 语音 / 翻译 / 打开 / 桌面），对接 FishBrain 不变。
  验证：compile OK、4 态渲染 OK、orb 角 alpha=0、unittest 180/180
  （evidence `224621-pet-avatar-verify` / `224636-unittest-full`）。

- 2026-10-29 **形象改为"原样搬插件"**（用户否决了手绘版）：发现插件
  `@linxin666/dsh-pet`（`~/.dsh/profiles/web/node_modules/`）内置鲸鱼娘精灵图集
  `spritesheet.webp`(1536×1872, 8列×9行, 192×208) + `pet.json`(9 条轨迹逐帧时长)。
  已拷贝为 `aiji/pet/assets/whale_spritesheet.webp` / `whale_pet.json`，
  新增 `sprite.py` 复刻 `spritesheet.js`+`PetSprite.js` 的帧推进语义
  （trimTrack / loop-hold / 行序 0 idle…8 review），`avatar.py` 只剩 state→track 映射。
  验证：9 轨迹全部帧非空、idle/jumping 时序与 pet.json 一致、
  与插件官方 idle 预览逐帧美术一致、180/180 单测 OK。
- 2026-10-29 **小窗"功能全无"真因**：Tk pack 顺序错误——对话区 `expand=True` 先占满，
  后 pack 的工具行/输入行被挤出窗口（截图证实只有头+对话区）。
  修复：底部控件 `side="bottom"` 先行 pack，对话区最后 pack；
  新增探针 `tools/pet_panel_probe.py`（6 按钮 mapped=1 且在窗内、log 332px 可用 → PASS）。
  端到端：GUI 里输入"你叫什么名字？"由模型池真实作答。

- 2026-10-29 **养成经济照搬插件**（用户选定：搬喂食/好感度）：新增 `aiji/pet/growth.py`，
  逐条复刻 `affinity.js` / `treats.js` / `remarks.js`：
  亲密度 9 档（0/25/50/80/200/500/2000/10000/100000）、摸头 +1 冷却 10s、
  喂食 +5 冷却 30s、每轮对话 +1；小鱼干双来源（每 30 轮 1 条 / 每 5 小时 1 条，上限 20，
  注意原值是 `300*60_000`=5 小时，不是 30 分钟）；台词库 5 槽各 10 句原样照抄。
  UI：悬停面板（名字/亲密度/小鱼干/点数 + 喂食/改名/隐藏）、单击=摸头。
  落盘 `data/pet_state.json`（JSON 原子写，未动 SQLite schema）。
  验证：`tests/test_pet_growth.py` 22/22；全量 202/202；
  GUI 实测单击摸头 +1 落盘、点喂食 20→19 且 +5、30s 内再喂**不白扣**（读 state 文件确认）。

- 2026-10-29 **Alt+Space 没反应的两个真因**（用户报障后定位，都会复发，记下来）：
  1. 旧实现轮询 `GetAsyncKeyState` —— 本机实测读到 **0 次按键跳变**（`tools/hotkey_probe.py`），
     这条路在本环境不可用；改用 `RegisterHotKey` + 独立线程 `GetMessage` 收 `WM_HOTKEY`
     （**不能**在主线程 PeekMessage：Tk/Tcl 的消息泵会先把线程消息取走丢弃，实测收不到）。
  2. 新建的 `Toplevel` 状态就是 `normal`，`toggle()` 若按 `state()` 判断会立刻把它 hide；
     必须按 `winfo_viewable()` 判断。另：RegisterHotKey 不拦按键，Alt+Space 会落到刚显示的
     窗口上把它收成最小化 → 需在 60/250/450ms 多次扶正、且 Alt 松开后再给输入框焦点。
  排查工具：`tools/hotkey_probe.py`（按键读不到）、`tools/hotkey_capability.py`（注册能力）、
  `tools/hotkey_smoketest.py`（WM_HOTKEY 能否收到）、`data/pet.log`（运行时注册/触发日志）。
  验证：日志 `registered=True` + 两次 `fired`；窗口列表 showCmd=1 非最小化；全量 212/212。

## 未决 / 下一步

可选项：
1. 免费模型池**只在本地假 OpenAI 服务上验证过**；需在有出网的机器跑 `tests/smoke_live_pool.py`（阻塞项）。
   —— 但本机两个反代网关（workbuddy/agent2api）已实测连通，LLM 第二层/模型抽取/
   摘要/软嵌入**已全部真机验证**，此项只剩"公网免费池"未验。
2. ~~OCR/ASR 没有真实引擎~~ **已解决**：RapidOCR + FunASR paraformer-zh
   （中文 14/14 全对）+ faster-whisper 回退，e2e 3/3、批量 7/7。
   剩余边界：密集表格短单元格确定性漏检（excluded #48）；OCR/ASR 质量只在
   合成样本（Pillow 图 + SAPI TTS）上验过，**真实用户拍摄/录音质量未验**。
3. ~~口癖/用词风格未做~~ **已解决**：口癖维度 + 4 维归一 + 词表可配
   （`Settings.tics`）。模型摘要质量已做结构化评测。
4. 向量层 `HybridIndex`（hashing + LLM 软嵌入）已实测：同义改写召回 4/5 vs 2/5；
   但换真实 embedding / 近似索引未做（当前仍是暴力余弦）。
5. 冲突消解是确定性规则：未知谓词默认按多值累积；复杂否定、语义矛盾（同一事实换种说法）会漏。
6. `media.expires_at` 只是记录，签名 url 过期后需调用方按平台 API 重新取 ref。
7. `turn` 压缩只在用户轮次 >400 时触发；摘要质量直接决定被删原文的信息保留度。
8. whisper base 专名更准但输出繁体 + 首载 197s（excluded #47），故默认 tiny。
9. 控制台工作区：`/api/fs` 只能把**路径**限制在工作区内，`/api/shell` 的**命令本身**
   无法沙箱化（`cd` 出去就出去了）。它是本地个人控制台，别绑 `0.0.0.0`；
   不想要终端时设 `AIJI_CONSOLE_SHELL=0`。
10. 控制台只在真实 Edge 窗口截图验证过工作区；**聊天统计与 ECharts 交互没有
    自动化浏览器测试**（本机 Edge headless 被沙箱 ACL 拦住，excluded #49）。
11. ~~桌宠状态仍是 `data/pet_state.json`~~ **桌宠已整体删除（2026-11-03 清理残迹）**：
    代码里 `/api/pet*`、`/assets/whale_spritesheet.webp` 全部 404（有回归测试
    `test_console_workspace.py::test_removed_pet_routes_do_not_create_state` 守着），
    没有 `pet_state.json`、没有宠物素材、服务链路里的 presence 改成"供前端同步"。
12. 记忆代谢（**2026-11-02 已实施**，schema v11）：方案与实施状态唯一入口
    `docs/agent/design/borrow-plan-2026-11-02.md`（§0–§8 是决策，**§9 是落地状态与实测数字**）。
    已落：读→强化（`store.bump_usage`，1h 冷却）、冻结层（`fact.frozen`，不删只藏）、
    turn 归档（`turn_archive`，90 天后才物理清）、反思批处理（积压≥2 会话或空闲≥15min）、
    摘要哈希复用、事实键、`rank_mode` RRF 开关、弃答档位、自检 bench、显式遗忘、自我记忆。
    新 CLI：`aiji forget/archive/self-memory`。
    **拆不掉的两条实测**：弃答门默认关（excluded #63）、RRF 单路候选零差异（#64）。
    **主动配额已接线**（`aiji/brain/quota.py`）：额度 = 反代剩余积分 ÷ 单条单价，2026-11-02 续做；
    两个反代都不吐余额（#65），所以余额用 `aiji quota --set N` 手填 + 按单价折算 + 显示"填了多久"。
    `self_memory` 已进注入块（≤2 条、需与查询相关）。
    待办：冻结线 0.5 要等库龄过 90 天用 `bench/bench_decay_baseline.py` 重测再定。
13. 「启动与结束」页：`data/service_state.txt` 为 `stopped` 时保活不会拉起，必须双击
    `启动AIJiYi.bat`；核心停机后页面也无法再下发动作为它自己就是被停的服务——这是设计，不是缺陷。
    「结束整套服务」没有做端到端真按（会把控制台一起停掉），只验到 202 下发与脚本入参。
    保活任务确实存在（`C:\Windows\System32\Tasks\AIJiYi Console Watchdog`，2026-11-01 18:49），
    但沙箱内 `schtasks /Query` 与 `Get-ScheduledTask` 都假阴性，页面靠定义文件二次判定。
14. **群媒体（图片/语音）这一条没查到底**：今天 13:10–20:35 的 4084 条
    `Failed to download file ... HTTP status code: 4xx` 在 AstrBot 重启后归零（2 分钟内十余张图
    正常描述），但没拿到一条带 `rkey` 的原始 URL 做对照，**根因未证实**（代理隧道 vs CDN 拒签）。
    下一步：在 `aiji_memory` 插件里对失败 URL 打一次脱敏日志（保留 query 长度与 `rkey` 前缀），
    或在 `io.py` 里加一次"取到 4xx 时直连重试"的探针。
15. `data/monitors.on` 打开后 `field_monitor.py` 会 15 秒一条 **常驻**写盘（约 1.6 MB/天）；
    不想要就删这个文件。`proactive_audit_1h.py` 是一次性的，别指望它常驻。

阻塞项：无（1 需换有出网的机器才能验）。

## 已知风险（会咬人的）

- **本机出网是"选择性"拦截**（2026-10-29 修订）：pypi 直连通（pip 正常）、
  modelscope.cn 通（FunASR 模型下载）、hf-mirror.com 通（whisper 模型）、本机两个
  反代网关通（LLM 全链路）；**只有公网通用 API 被 SSL 拦**。所以"要真实模型/真实
  引擎就必须有出网机"这条已不成立，只有"公网免费池"那一项还受限。
  另：`download.pytorch.org` 的 r2 CDN 对本机极慢（~440kB/s 且断流），装 torch
  用 pypi 默认源（Windows 上本就是 CPU 版）。
- **机器上有全局代理 `HTTP_PROXY=http://127.0.0.1:7897`（Clash 一类）**：凡是
  `trust_env=True` 的 aiohttp/httpx 都会继承它（AstrBot 的下载就是），QQ 富媒体走隧道
  会拿到 4xx。判定网络问题时**先分清直连还是走代理**：`Invoke-WebRequest` 也吃这个环境变量，
  对照测试要显式清空四个（大/小写各一对）代理变量。
- **`Start-Process -RedirectStandardOutput` 是截断而不是追加**：不先轮转就会把历史日志洗掉
  （已由 `ensure_console.ps1` 的 `Rotate-Log` 修掉）。
- **沙箱里的子进程活不过本次工具调用**：`Start-Process` 起的 python 随调用结束被收掉，
  常驻采集器只能由 `ensure_console.ps1`（计划任务，非沙箱）拉起。
- **DSH 沙箱 + Windows ACL**：首次写盘可能报 `SetNamedSecurityInfoW failed (Win32 5)`，用 `diagnose-windows-sandbox-acl` 修（已修 `D:\AIJiYi`）。
- **`tempfile.mkdtemp()` / `mkdir(mode=0o700)` 在此环境不可用**：子进程不可写且事后删不掉；测试统一 `workdir()`（`.tmp/` + 0o777）。
- **跨平台隔离靠 SQL 过滤**，一次漏写 `platform` 条件就是串号事故；新增查询务必带 `Ident.where()`。
- **风格/摘要都有并发陷阱**：`_turn_counts` 的读改写必须加锁；摘要必须按"距上次够 N 轮"判断并幂等（见 excluded 第 10、11 条）。
- SQLite 写事务全局串行（一把锁）；注入要写留痕，会和 writer 抢锁（实测 p99 仍 <10ms）。
- 内存队列不落盘：进程被 kill 最多丢最后一个未处理事件（`writer.py` 有 `shortcut:` 注释）。
- **SQLite 关联 JOIN 的坏计划**：只给 `fact` 侧加身份条件时，planner 会"扫 fact 再全表扫 fact_link"，
  实测 300ms；必须两侧都带 `Ident.where` 且用 `CROSS JOIN` 固定 `fact_link` 为外循环（见 excluded 15）。
- **`fact_link` 边数必须封顶**（每事实 ≤3）：种子阶段不封顶会到十万级链接（见 excluded 16）。
- **控制台 `/api/shell` 没有认证**：只凭"服务只监听 127.0.0.1"兜底；跨站简单请求因为
  handler 只收 JSON body 而打不进来，但任何本机进程都能直接 POST。不要改绑公网地址。
- **控制台 `/api/fs/write` 可写工作区内任意文件**（含它自己的 `.py`）：这是有意的本地
  编辑能力；删/改代码前先备份，别把这套 API 暴露给别的用户。
- **控制台为 `null` 来源开了 CORS**（2026-11-02）：`file://` 打开页面时浏览器发 `Origin: null`，
  服务端会对这个来源放行（含 `/api/operations/config`、`/api/operations/services`；POST 另外仍限
  127.0.0.1 客户端）。任何本机网页或本地 HTML 因此都能读接口数据——与"任何本地进程本来就能
  curl"同一威胁级别，但**不要**把服务绑到 `0.0.0.0`，也不要给它加公网反代。
- ~~**`data/pet_state.json` 两个写入方**~~：桌宠已于 2026-11-03 整体删除，这条风险随之消失。
