# PROJECT_STATE —— AIJiYi

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
- 入口：`aiji/__init__.py`（门面 `Memory`）、`aiji/cli.py`、`aiji/server.py`
- 核心模块：`db.py`（schema v4 + 迁移）、`store.py`（身份/事实/画像/风格/媒体/摘要/注入日志/检索/冲突消解/关联/淘汰）、
  `recognize.py`（识别层）、`extract.py`（反思：事实/知识/关系/风格/摘要 + 启发式兜底）、
  `recall.py`（硬预算召回）、`inject.py`（软引导注入）、`writer.py`（异步写）、`pool.py`（模型池）、
  `vectors.py`（可选向量）
- 对外说明：`README.md`；示例配置：`config/aiji.example.json`

## 怎么验证它是好的

```bash
cd D:\AIJiYi
python -m unittest discover -s tests -t .                       # 期望：Ran 357 tests, OK
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
  - `aiji/quota.py`：`allowed_24h = min(8, floor(剩余积分×0.8 ÷ 单条单价))`；积分取不到就退回 3/天。
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
- 2026-10-31 **QQ 空间与扩展声线落地**：新增 `aiji/social_service.py`，通过
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
10. 控制台只在真实 Edge 窗口截图验证过工作区/桌宠；**聊天统计与 ECharts 交互没有
    自动化浏览器测试**（本机 Edge headless 被沙箱 ACL 拦住，excluded #49）。
11. 桌宠状态仍是 `data/pet_state.json`：Tk 桌宠 + 控制台同时跑是"后写覆盖"；
    要做多宠物/多进程再迁 SQLite（见 growth.py `shortcut:`）。
12. 记忆代谢（**2026-11-02 已实施**，schema v11）：方案与实施状态唯一入口
    `docs/agent/design/borrow-plan-2026-11-02.md`（§0–§8 是决策，**§9 是落地状态与实测数字**）。
    已落：读→强化（`store.bump_usage`，1h 冷却）、冻结层（`fact.frozen`，不删只藏）、
    turn 归档（`turn_archive`，90 天后才物理清）、反思批处理（积压≥2 会话或空闲≥15min）、
    摘要哈希复用、事实键、`rank_mode` RRF 开关、弃答档位、自检 bench、显式遗忘、自我记忆。
    新 CLI：`aiji forget/archive/self-memory`。
    **拆不掉的两条实测**：弃答门默认关（excluded #63）、RRF 单路候选零差异（#64）。
    **主动配额已接线**（`aiji/quota.py`）：额度 = 反代剩余积分 ÷ 单条单价，2026-11-02 续做；
    两个反代都不吐余额（#65），所以余额用 `aiji quota --set N` 手填 + 按单价折算 + 显示"填了多久"。
    `self_memory` 已进注入块（≤2 条、需与查询相关）。
    待办：冻结线 0.5 要等库龄过 90 天用 `bench/bench_decay_baseline.py` 重测再定。

阻塞项：无（1 需换有出网的机器才能验）。

## 已知风险（会咬人的）

- **本机出网是"选择性"拦截**（2026-10-29 修订）：pypi 直连通（pip 正常）、
  modelscope.cn 通（FunASR 模型下载）、hf-mirror.com 通（whisper 模型）、本机两个
  反代网关通（LLM 全链路）；**只有公网通用 API 被 SSL 拦**。所以"要真实模型/真实
  引擎就必须有出网机"这条已不成立，只有"公网免费池"那一项还受限。
  另：`download.pytorch.org` 的 r2 CDN 对本机极慢（~440kB/s 且断流），装 torch
  用 pypi 默认源（Windows 上本就是 CPU 版）。
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
- **`data/pet_state.json` 两个写入方**：Tk 桌宠与控制台各持一把进程内锁，文件级是
  tmp+replace 原子写，但跨进程仍是后写覆盖；同时操作时以最后一次落盘为准。
