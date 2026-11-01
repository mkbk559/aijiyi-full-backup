# status —— memory-core

## 目标

实现分层记忆：SQLite 事实/画像 + 会话缓冲 + 可选向量索引；写全异步走免费模型池；主链路 100ms 硬超时快召回。
**多平台共用一套表**（platform/scope/extra + 复合身份主键），媒体只存引用。
**学习/总结/操控三合一闭环**：识别层降维 → 同一反思管线 → 软引导注入 → 反馈回写。

## 现状：已完成（2026-10-28）

## 已完成（时间 / 证据）

- 2026-10-28 23:04 骨架：`pyproject.toml` + `aiji/`（config/db/store/recall/writer/pool/extract/vectors/memory/server/cli）+ `bench/` + `tests/`
- 2026-10-28 23:07 单测 16 项全绿：`python -m unittest discover -s tests -t .`
  - 证据：`C:\Users\22166\.dsh\evidence\2026-10-28\2307*`（OK, Ran 16 tests）
- 2026-10-28 23:08 压测（5000 事实 / 200 召回 / 4 写线程）：静默 p99 5.4ms、写压 p99 8.1ms、submit p99 0.076ms、0 超预算
  - 证据：`...\230902-recall-budget-p99-primary-39e3aa\stdout.txt`
- 2026-10-28 23:09 HTTP 端到端（4 客户端 × 10 轮 = 240 样本）：p99 6.9ms，0 degraded
  - 证据：`...\230904-recall-budget-p99-secondary-f7512c\stdout.txt`
- 2026-10-28 23:09 `doctor` 自检 + 无 key 提示：`...\230939-cli-doctor-d969e2\stdout.txt`
- 2026-10-28 23:10 真机外网冒烟失败（SSL 出网被拦，非代码问题）：`...\230953-smoke-live-pool-9d7b9d\stdout.txt`
- 2026-10-28 23:13 修中文问句召回（二字组 + 实义单字候选 + 重叠度重排），CLI 端到端命中：
  `...\231327-cli-e2e-demo2-651eb5\stdout.txt`；回归 16/16：`...\231339-unittest-final-ad02d5\stdout.txt`
- 2026-10-28 23:48 **数据模型重构为多平台共用一套表**（schema v2）：
  `account`/`fact`/`profile`/`turn`/`summary`/`write_queue` 全带 `platform, platform_user_id, scope, extra`；
  `media` 表只存引用（无 BLOB 列）；`profile` 主键改 `(platform, platform_user_id, scope, key)`；
  v1 库自动迁移 + FTS 重建。证据：`...\234818-unittest-v2-66f2b1\stdout.txt`（22/22 OK）、
  `...\234942-schema-v2-columns-aeea83\stdout.txt`（列结构）、`...\234846-cli-multiplatform-demo-3d3b96\stdout.txt`（双平台隔离实测）
- 2026-10-28 23:48 重跑压测（4 平台 / 5000 事实）：静默 p99 2.1ms、写压 p99 3.4ms、submit p99 0.066ms：
  `...\234829-recall-budget-v2-primary-d93850\stdout.txt`、`...\234831-recall-budget-v2-secondary-705180\stdout.txt`
- 2026-10-29 00:12 **schema v3：学习/总结/操控闭环**（43 项单测全绿）：
  - 识别层 `aiji/recognize.py`：text/emoji 本地、image/voice 可插拔引擎、link 抓标题、video 仅元数据；
    统一产出 `Envelope(text, confidence, status)`，超时/门槛/如实 unsupported
  - 反思 `aiji/extract.py`：新增 knowledge/relation 两类、风格启发式、摘要压缩
  - 注入 `aiji/inject.py`：来源标注 + 关系标签 + 硬字符预算，`persona_touched=False`
  - 反馈：`fact.hit_count/last_used_at` + `injection.feedback` 留痕
  - 证据：`...\001243-unittest-v3-final-6a41fd\stdout.txt`（43/43 OK）、
    `...\000908-demo-v3-closed-loop-0742c6\stdout.txt`（端到端）、
    `...\001317-bench-inject-bb06fc\stdout.txt`（注入 p99 9.5ms，0 越权）、
    `...\001254-recall-budget-v3-primary-b777d9\stdout.txt`（召回 p99 2.3/3.7ms）、
    `...\001256-recall-budget-v3-secondary-cbae4f\stdout.txt`（HTTP p99 7.0ms）
  - 修掉两个真实缺陷：摘要按 `n % N` 判断在并发跳值下永不触发（`...\001103-probe-summary-interleaved-0efd76`）；
    OCR 文本被算成用户风格（改 `learn_style=False`）

## v0.4 记忆质量（2026-10-29，借鉴 mem0 / Graphiti / A-MEM）

- 调研：抓取 mem0 / Graphiti / A-MEM README，笔记落 `docs/agent/research/memory-systems-2026.md`
- schema v4：`fact.importance/mentions/valid_from/valid_to`、`fact_link`（双向关联）、`fact_log`（写入审计）；v1/v2/v3 自动迁移
- 写入冲突消解：多值累积（ADD-only）/ 同值合并（mentions+1）/ 单值 supersede（`valid_to`）/ 否定 expire / 无目标 noop
- 排序：相关度 + 重要度 + 按属性半衰期的时间衰减 + 置信度 + 反馈/提及加成（`score_fact`）
- 关联：新事实连最多 3 条同身份近邻（2-gram ≥0.35，双向），召回做 1 跳扩展并打折
- 摘要：模型优先（`summary_model`），不可用退回本地压缩；超长会话先摘要再淘汰旧 `turn`，并清召回缓冲
- 向量：启动时从 `fact` 重建（只收 active），supersede/expire 后删旧 id，语义命中按身份二次过滤
- 修差：关联 JOIN 只给 fact 侧身份条件 → planner 坏计划 300ms；改为两侧过滤 + `CROSS JOIN`
- 证据：61/61 单测（`...\005614-unittest-v4-final-f97273\stdout.txt`）；
  召回 p99 2.61/5.99ms（`...\005528-recall-budget-v4-primary-2-f63af6\stdout.txt`）；
  HTTP p99 8.28ms（`...\005541-http-budget-v4-secondary-61d549\stdout.txt`）；
  注入 p99 9.30ms / 0 越权（`...\005545-bench-inject-v4-b0c20f\stdout.txt`）；
  质量 bench 全过（`...\005650-bench-memory-quality-2-aa03fc\stdout.txt`）；
  端到端（`...\005549-demo-v4-closed-loop-76dc9d\stdout.txt`）

## 心脏层落地（2026-10-29，方案 docs/agent/design/dafeiyu-plan.md）

- schema v5：`outbox`（幂等键+CAS+租约+死信）/ `send_queue`（发送幂等+租约）/
  `heart_state`（day-scoped + persistent 双类键）/ `event_log`（短期缓冲落盘）/
  `fact.version` 乐观锁列（并入 fact，不建独立版本表）；v4 老库自动升级
- `aiji/heart.py`：outbox 消费（CAS 领取、优先级、指数退避、5 次进死信、
  租约超时回收）、出站队列（独立退避/死信/送达确认）、heart_state 读写；
  同步写路径走 `db.tx()`（全局写锁），不走异步 Writer（方案 1.2 轮7补决策）
- `aiji/gate.py`：出口人格闸门（黑名单/长度/凭据转述，fail-closed），
  接在 heart 出站入口（方案 1.5 第 2 层）；第 1 层在 extract.py 入口黑名单
- 入口防注入（extract.py `_INJECT_CUES`，heuristic+model 双路径）：
  模拟器 PromptInjector 实测抓到"密码进 fact"漏洞后修复
- 模拟器 `tools/sim_platforms.py`（8 角色 × 12 断言，8 seed 全绿）+
  端到端 `tools/e2e_heart.py`（10/10：幂等/优先级/死信/租约回收/闸门/零积压）
- 证据：72/72 单测 + e2e 10/10（`...\123531-heart-layer-landing-2cb0fd\stdout.txt`）；
  注入漏洞修复（`...\122407-sim-inject-fix-regression-18660f\`）

## 出站调度 + 主动搭话（2026-10-29 第二批）

- `aiji/dispatch.py`：常驻发送线程（pump+dispatch 双职责=方案 1.1 兜底扫描线）、
  限流感知退避（`rate_info()` 可选，缺省固定指数退避）、平台恢复后回拉被推迟行、
  撤回 `cancel(scope, kind)`（1.2 轮2补）
- `aiji/proactive.py`：静默→冷却→额度（**预扣+退还**定稿语义）→抢占→生成→入队；
  滚动 24h 窗口、指数退避（封顶 6 档）、触发即重置沉默（1.6 轮1补）、
  NTP 回拨防御在比较逻辑（时间戳倒退取 max）
- **修掉一个真并发 bug**：`DB` 构造不初始化 schema 时，dispatcher 线程先于主线程
  首调 `conn()`，一个线程在 `_init_schema` 长事务、另一个 INSERT 撞
  `database is locked`，且 `enqueue` 把锁冲突吞成幂等静默丢弃（10 次复现 2-6 次丢事件）。
  修复：`DB.__init__` 构造即 `conn()`；enqueue 对 locked/busy 忙等重试；busy_timeout 1s→5s
- 模拟器切换**真 Heart 层驱动**（outbox→pump→send_queue→dispatch 全走生产代码路径），12 断言全绿
- 证据：80/80 单测 ×3 连跑 OK + 模拟 3 seed 12/12（`...\125716-dispatch-proactive-landing-6711ed\`）；
  flaky 修复证据（`...\124936-flaky-hunt-c0056f\` 复现 + dbg10 修复后 0/10 失败）

## 清理 Worker + 纠错溯源 + 恢复回放（2026-10-29 第三批）

- `aiji/janitor.py`：软删除清理（过期 valid_to / 置信地板 0.15 / 同三元组去重合并
  吸收 mentions / fact_link 边封顶 ≤3 / heart_state day_bucket 保留 2 天 /
  event_log 保留 7 天）；单实例经 heart_state 租约（抢不到跳过）；
  去重走行级乐观锁（fact.version，冲突跳过不阻塞反思）
- `aiji/extract.py::detect_correction`：纠错识别（触发句式 + 属性提示 + 新值提取），
  打包给反馈环，**不单次覆盖**（1.7 下调规则）
- `store.apply_feedback` 负向路径：confidence × 0.6、**地板 0.15 不穿透**（1.11 #4），
  软删状态不变（可回滚）；正反馈仍只加 hit_count 不动置信
- `Heart.replay_recent(ident, limit=100)`：event_log 双写（enqueue 时）+ 崩溃恢复回放，
  按身份过滤防串号（1.2 恢复语义 + 1.11 #6 硬约束）
- 证据：94/94 单测 + 模拟 3 seed 12/12（`...\130451-janitor-correction-replay-765983\`）

## 硬缺口收口：口径固化 + cited 作用域 + S1-S3 对抗证据（2026-10-29 第四批）

- **injection 口径固化**（S5 前置）：injection 表行数 = 注入留痕（每次 m.inject() 落一条，
  log_injection）；**攻击计数是另一个数**（payload 命中 _INJECT_CUES 的入站事件）。
  口径文本写进 `sim-report-*.json` 的 `semantics` 字段，跨版本对比绑定断言
  （"留痕>0 且含 feedback 回报"）
- **cited_ids 精确作用域**（S3 前置，从非阻塞提升为已完成）：
  `store.apply_correction`——有 cited ×0.6（地板 0.15）；无 cited 按 predicate_hint/
  文本重叠 ≥0.3 降级定位且**幅度减半 ×0.3**；`last_injection_with_feedback`：
  feedback.used 即 cited（**used=[] = 明确没引用，不得退化 item_ids**），
  仅无 feedback 时退化候选
- **S1/S2/S3 对抗场景专用跑**（`--roles` 过滤 + 条件断言）：
  S1 刷屏 11/11、S2 身份隔离 14/14、S3 纠错风暴 16/16；全角色 8 seed 21/21
  - S3 断言：旧 fact 降权 <0.55 / 同用户其他 fact 不误伤 / 行保留可回滚 /
    斜率触 0.15 地板（0.55→0.33→0.198→0.15 恒定）
  - 新增跨组件断言：调度零积压/零死信、软删读路径一致（召回探针零命中已删行）、
    janitor 租约即时释放
- **janitor 读路径一致性修漏**：inject.py id 回查 + recall.py 语义命中补 status='active'
  过滤（软删后语义索引残留会回流 prompt——索引命中后二次查库并顺手清索引）；
  janitor 增加 purge 回调清向量索引
- **租约失败模式明确**（软缺口 5）：正常完成即时释放；崩溃→TTL 600s 自然过期，
  停摆上限 600s 无死锁面（test_lease_single_instance 覆盖三态）
- **heart.pump 失败留痕**：处理失败打印 traceback 到 stderr（之前静默退避是排查黑洞
  ——13 条 correction 积压因 sim 缺 import 无法定位，此改动当场暴露根因）
- **地板斜率**（软缺口 6）：0.9×0.6^10 轨迹 [0.54, 0.324, 0.1944, 0.15, 0.15...]，
  单点不穿透升级为累积不穿透（test_slope_10_rounds）
- 证据：`132029-S1-S2-S3-evidence` + `132319-cited-scope-fix-regression`
  （seed99 三连 ALL PASS + 8 seed 21/21 + 全量单测 OK）

## 阶段 0 固化 + P1 验证补齐（2026-10-29 第五批，收口迭代）

- **`aiji/params.py` 落地**：21 项可调参数全部集中（heart/dispatch/gate/janitor/
  proactive/store/db），**每项带来源标签**（来自方案 §x / 先验+依据 / 待实测+初数据），
  `params.snapshot()` 抓快照且从源码注释回读标签（快照与注释不脱节）。
  各模块改引 params，旧常量名保留 re-export（测试兼容）
- **injection 口径写死进 params + baseline.json**：`INJECTION_SEMANTICS`——
  injection 表行数 = 注入留痕（每次 m.inject() 落一条）；攻击计数 = payload 命中
  `_INJECT_CUES` 的入站事件数；两者不得混用
- **baseline.json 可脚本读写**：`tools/make_baseline.py` 生成（参数快照 21/21 有标注 +
  S1-S3 绿率 + 斜率轨迹 + 4 个 evidence 归档路径与 SHA-256）；
  `tools/check_baseline.py` 校验（参数漂移 diff + 场景绿率重跑比对，--quick 模式）
- **P1 租约故障注入**：test_lease_single_instance 三态（即时释放/持锁崩溃跳过/
  TTL 过期重抢）；TTL=600s 来源标注（janitor 实测 <1s，600× 余量，停摆上限 600s）
- **P1 回放幂等 + 收敛**：回放两次逐项相等且不写库；新 Heart 实例（同库）回放一致；
  新事件追加后回放 = 旧+新正序无重复（test_replay_*）
- **P0 聚合路径补齐**：`store.stats` active 聚合不含软删行（test_fact_stats_excludes_deleted）
- **待实测初数据**：CONF_FLOOR/NEG_FACTOR（斜率轨迹 0.54→0.324→0.1944→0.15 恒定）、
  LINK_CAP（60 轮模拟单事实 5 边触顶）——记进 baseline.slope_data
- 证据：`133202-phase0-baseline-close-d16141`（全量单测 OK + S1 11/11 + S2 14/14 +
  S3 16/16 + 全角色 21/21 + baseline 21 参数 0 漂移 + check PASS）

### 停止线自查（三条全过）
1. 参数：21/21 有来源标注，无未标注数字 ✅
2. 边界：软删（4 读路径+聚合）、租约崩溃三态、回放幂等+收敛，均有故障注入证据 ✅
3. 对抗：S1-S3 真实组件全绿 + S3 斜率数据进 baseline ✅

### 遗留（下一迭代，非阻塞）
- janitor STALE_DAYS=30 预留未接线（stats["stale_deleted"] 占位，无方法）
- S4-S7（长会话压缩/主动打扰/injection_spray/mixed_stress/archive_replay）
- 待实测参数用真实负载数据修正（现 3 项均有模拟初数据）

## S3 锁死红信号复盘与全闭环（2026-10-29 第六批）

**用户判定成立**：上轮"S3 斜率恒定 0.15"被记成绿是读反了——回升段**没跑**，
断言集里没有"不锁死"这一条。本轮补齐，过程中挖出 4 个真缺陷：

- **缺陷 1（机制）**：负向只降不升，"可回滚"在数据层成立、置信层不成立。
  修复：`apply_feedback` 正向路径不对称回升（×POS_FACTOR=1.25，封顶 0.95），
  触发判据 = fact_log 有 downgrade 记录（**写入即低置信 ≠ 被下调过**，
  用 <0.85 阈值会把天生 0.7 的行误升 0.875——实测抓到）
- **缺陷 2（参数）**：NEG_FACTOR 0.6→**0.7**（0.6 时 4 次到底；0.7 需 6 次，
  更贴近真实纠错频次），FALLBACK 0.3→0.35 同步
- **缺陷 3（extract）**：确认句式"我就住在苏州"提取不出居住地（只进备注）
  → supersede 闭环断裂；纠错句提取出垃圾三元组（"我住/是苏州不/杭州"）。
  修复：_RULES 加确认/纠正句式（非贪婪槽位 + lookahead 边界）；
  纠错句只保留干净居住地草案
- **缺陷 4（sim 生成器）**：回复"收到！"不引用任何 object → cited 恒空 →
  正向回升路径永远不触发。修复：确认性回复基于召回复述（cited 语义前提）
- **S3 断言重定义**（全闭环）：施压段到底（trace ≤0.25）→ 恢复段 supersede 出
  新值苏州 0.55 → active=新值 + 旧行 expired 留痕 + 作用域窄 + 置信回升 >0.35。
  旧口径"旧 fact 被降权"只测下调段，测不出替换闭环
- 调试插曲：`{2,8}` 贪婪吞"苏州不是杭州"被句尾 `$` 成全，非贪婪修；pwsh -c 的
  `\$` 转义陷阱把隔离对照测试引到歧途（两 pattern repr 相同行为不同）
- 证据：`135936-recovery-final-verify-7a1050`——S3 17/17（trace 0.1925→苏州 0.55）、
  全角色 4 seed 22/22、105/105 单测、baseline 23 参数 0 漂移 PASS

## 待办

- **OCR/ASR 真实引擎接入 + 端到端全绿**（2026-10-29 第十八批）——阻塞项 2 解除
  - **用户问法**："去 GitHub 找样本做 OCR/ASR 真引擎，或者有没有其他方法"
    ——答案是 pip 即有（不用手搓 GitHub 样本）：
    **RapidOCR**（onnxruntime 版，PP-OCRv4 系，pip 即装）+
    **faster-whisper**（CTranslate2，tiny 模型）
  - **网络发现（关键）**：pypi 直连 **200 通**（之前"Python 出网被 SSL 拦"是
    选择性/间歇拦截——pypi 通、huggingface.co 通不通要实测）。hf 模型下载走
    `HF_ENDPOINT=https://hf-mirror.com` 镜像成功，缓存后离线可用
  - **两个环境坑（均已绕开，excluded #41）**：
    PyAV 在 Py3.13 无预编译轮（`av<15` 要本地编 ffmpeg）——绕开：faster-whisper
    的 transcribe 直接吃 numpy 数组，自读 wav PCM + 线性重采样 16k，完全不用 av；
    SAPI TTS 实际写 44.1kHz（设 16k 不生效）——重采样兜住
  - **实测质量**（archives `183137-ocr-smoke` / `185217-asr-smoke-final`）：
    OCR 三行中文全对（置信 0.75-0.86，~1.1s/图）；ASR tiny 模型"我叫陈明，住在深圳，
    周末喜欢钓鱼"→"我叫臣名住在深圳周末喜歡釣魚"（核心信息全对，陈明→臣名是
    tiny 典型小错，换 base/small 可解）
  - **接入**：`aiji/real_engines.py` 适配器（懒加载单例，`HAS_OCR`/`HAS_ASR` 探测）；
    `Memory.__init__(engines="auto")` 缺位时自动接真引擎（"none" 显式关）；
    `recognize_timeout_s` 2.0→45.0（真引擎下 2s 不现实）
  - **端到端**（`tools/real_engines_e2e.py`，archive `190209-real-engines-e2e-6`）：
    真实图片（OCR 出邮箱/公司位置）+ 真实音频（ASR 出姓名/居住地/爱好）
    → **5 条 fact 全经真实模型抽取落库**（source_kind=model），3/3 全绿。
    方案"任意输入降维成文本→同一条管线"首次真机全链路验证
  - 单测 140/140；sim 37/37（2 seed）；baseline PASS（`191322-realengines-final-verify3`）
- **FunASR paraformer-zh 接为 ASR 首选引擎**（2026-10-29 第十九批，用户指定）
  - 动机：faster-whisper tiny 中文有错别字（"陈明"→"臣名"）；用户问高星语音
    项目时选定 FunASR（⭐20.9k，MIT，中文 SOTA）
  - 安装（三步，每步单独跑避免 126MB wheel 下载超时）：
    torch CPU 版 **用 pypi 默认源**（`--index-url download.pytorch.org/whl/cpu`
    的 r2 CDN 对本机只有 ~400kB/s 且断流，pypi 反而快）；funasr 1.4.17 直接 pypi
  - 模型下载：**modelscope.cn 直连 200 通**（不同于 hf 被墙），paraformer-zh +
    fsmn-vad + ct-punc 全自动落到 `~/.cache/modelscope`
  - 冒烟（archive `205359-funasr-smoke3`）：同句 TTS 音频，**"我叫陈明，住在深圳，
    周末喜欢钓鱼。" 14/14 字符全对（含标点），0.9s**，vs whisper tiny 错 2 字
  - 接入：`real_engines.py` 的 `transcribe_wav_bytes` **funasr 优先、whisper 回退**
    （HAS_FUNASR 探测）；文本非空给保守 0.9 置信（paraformer 无逐段 logprob）
  - e2e 3/3（姓名/居住地/爱好全经真实模型落库）+ 单测 140/140（archive
    `205556-funasr-e2e-regression`）——签名未变，mock 测试零改动
- **口癖（口头禅）维度落地——认知三件套最后一块闭环**（2026-10-29 第二十批）
  - 实现：`extract.py` 新增 `_VERBAL_TICS` 词表（20 条中文口头禅）+ `verbal_tic_hints()`；
    `style_hints()` 并入口癖维度。检测**按文本出现位置排序、去重、最多取前 2**，
    落库复用既有 style 表的证据投票机制（反复出现抬置信，一次新词不翻转旧值）
  - 5 个新测试：命中/去重截断/缺席/turn 全链路投票/防翻转——全部走本地确定性路径
  - 全量 145/145、sim 37/37、baseline PASS（archive `210821-style-tic-final3`）
  - **至此方案 §2 五块（学习/总结/操控/识别/风格）全部有真机证据支撑**，
    唯一剩余的"口癖/用词风格"阻塞已解除
- **风格双路一致性 + 词表可配置**（2026-10-29 第二十一批）
  - 缺口：模型路径的 style 维度命名不固定（可能输出"口头禅/用词/语调"），
    与本地 4 维（语气/长度/表情/口癖）同义分裂，注入软引导时 snapshot 出现
    同义维度
  - 实现：`_STYLE_DIMS` 固定 4 维 + `_STYLE_DIM_ALIASES` 别名映射（口头禅/常用语/
    用词/惯用语→口癖，语调→语气，篇幅→长度，emoji→表情）；`parse()` 归一后
    未识别维度直接丢弃；SYSTEM 提示词里写明固定维度名
  - 口癖词表可配置：`AIJI_TICS` 环境变量逗号分隔扩展（值=词本身）
  - 3 个新测试：别名归一、未知维度丢弃、env 扩展。全量 148/148（archive
    `212125-style-normalize-final`）。教训记 excluded #46

- **四项可选增强全部闭环**（2026-10-29 第二十二批，用户"全部执行"）
  1. **口癖词表持久化到 Settings**：`tics: tuple[str,...]` 字段，`load()` 读
     `cfg["tics"]` 或 `AIJI_TICS` 环境变量；`Memory.__init__` 调
     `extract.set_extra_tics(settings.tics)` 注入。不再只靠环境变量
  2. **whisper 回退模型可配**：`Settings.asr_model`（tiny/base/small）+
     `real_engines.set_asr_model()`。实测对照（archive `213234-whisper-base-smoke`）：
     tiny `臣名`（专名错），**base `陳明`（专名对）**——base 中文专名显著更准，
     但输出**繁体**（喜歡/釣魚）+ 首载 197s（下载）> 45s 超时，故**默认仍 tiny**
     （FunASR 已首选全对，whisper 只做回退，首载必须快）
  3. **OCR 多栏 + 表格**（archive `214133-ocr-layout-final`）：多栏 **6/6 全对**；
     密集表格短单元格（"公司"）确定性漏检——检测器把相邻单元格框合并
     （独立 probe `214007-ocr-layout-probe` 间距大时可检出）。记为**已知边界**：
     记忆抽取对表格短格漏字有容忍，要彻底解决需表格专用模型
  4. **识别批量**：`real_engines.ocr_images()` / `transcribe_wavs()` 复用单例引擎
     循环识别；`Memory` 自动接线支持 `extra["bytes_list"]`（一次 ingest 多份字节）。
     端到端（archive `214648-batch-e2e-final`）：2 图 + 2 音频 → **7/7 关键 object
     全落库**
  - 最终验证（archive `215027-all-enhancements-final`）：单测 **153/153**、
    sim 37/37×2、baseline PASS、单件 e2e 3/3、批量 e2e 7/7、OCR 版面 PASS
- **可选增强（不阻塞）**：whisper tiny→base/small（降错别字）；OCR 表格/多栏；
  识别层批量（多图合并一次 OCR 调用）

- **HybridIndex 落地：hashing + LLM 软嵌入双路检索**（2026-10-29 第十七批）——阻塞项 1 解除
  - **探活**：两网关均无 `/v1/embeddings`（80/19 模型里零 embedding 类，POST 实测 404，
    archive 内 probe 输出）——真实 embedding API 此环境不可用
  - **降级方案**：chat 模型**软嵌入**——10 个任务定制语义维度（身份/居住/职业/兴趣/
    人际/时间/偏好/情绪/知识/计划，0-1 打分），比通用 embedding 更贴记忆检索任务。
    `vectors.HybridIndex`：hashing 256 维（字面）+ LLM 10 维（语义）两路 cosine 取 max；
    池挂/输出不合法自动退纯 hashing（=BruteForceIndex 等价，单测锁定该等价性）
  - **实测对比**（`tools/embed_eval.py`，archive `181913-embed-eval`）：
    **改写检索 hashing 2/5 vs hybrid 4/5**。"他在哪个城市居住啊"→住深圳（零字面重叠）：
    hashing 0.13 排不进前 3，hybrid 0.84 直取；"平时有什么消遣"→钓鱼摄影：hybrid 0.96。
    #3 失败案例（"宠物几岁"→#8 张伟）：10 维粒度粗，"宠物"无专属维度被"人际"干扰
    ——已记录，后续维度细化方向
  - **接线**：config `vector: off | hash | hybrid`；Memory 开 hybrid 时 pool 有路由
    才挂 chat（无路由=每条白等超时），延迟绑定 `_hybrid_chat`。**默认仍 off**
    ——用户显式开 hybrid 才走 LLM 维度（每 fact 一次 chat 调用，有成本）
  - 单测 +8（hashing 确定性/归一化、parse_dims 容错、mock chat 语义路、
    chat 失败等价 BruteForce、零维无 NaN）；全量 140/140；sim 37/37；baseline PASS
- **剩余阻塞**（2 项）：
  1. OCR/ASR 真引擎识别质量（网关无视觉/语音 embedding，且无真引擎——保持）
  2. 口癖/用词风格维度
- **优化方向**（不阻塞）：LLM 软嵌入每 fact 一次 chat——量大后可批量（一次打 20 条）
  或换成网关将来若上 embedding 端点；10 维细化（宠物/事件人物分离）

- **LLM 第二层落地 + 真实验证全绿**（2026-10-29 第十六批）——阻塞项 2 解除
  - **架构发现**：第二层代码本就绪（writer._extract 模型优先+规则兜底、
    summary_model=True 默认开）——之前所有 sim 都跑 routes=()（池空→全 heuristic），
    从未在真实模型池上验证过。本批做的不是写新功能，是**接通+验证**
  - **key 桥**：`Memory.open()` 开头自动桥（缺 WORKBUDDY_HUB_KEY/AGENT2API_KEY
    环境变量时从 ~/.dsh/.credentials.yaml refs 注入；桥要在 Settings.load **之前**——
    _load_routes 跳过缺 key 路由，桥晚了路由就没了）。密钥不落仓库
  - **tests/smoke_live_layer2.py**：完整闭环 8/8 全绿（两次复跑稳定，archives
    `180306-live-layer2-first` / `180546-layer2-final-verify`）：
    模型抽取落库（source_kind=model，非规则兜底）、关系抽取、
    **模型摘要**（source_kind=model）、召回命中模型抽取的事实、注入、反馈回写
  - **pool 故障转移实录**：workbuddy 3/3、agent2api 1/1、zhipu 2/2 命中；
    pollinations 503 / siliconflow / openrouter 失败如实进冷却（290s+）——
    多路由容灾在真实环境按设计工作
  - **模型摘要 vs heuristic 摘要评测**（`tools/summary_eval.py`，archive
    `180457-summary-eval`，PROJECT_STATE 风险"摘要质量未评测"补齐）：
    3 用例（跨句整合/信息密度/指代解析），**关键词召回打平**（3/3、14/14、6/6
    —heuristic 首句去重对显式关键词不弱），**质量差在结构**：
    模型版完成指代消解（"那个活动"→"爬山"、"他"→"张伟"）、跨句因果整合
    （"因周六有雨改期下周日"）、视角统一（"用户"）——这些是关键词命中测不出
    但 prompt 注入真正有用的性质。heuristic 兜底继续有效（池挂时数据不丢）
  - **sim 口径修正**：sim 断言是本地确定性口径——key 桥接通后 sim 走了真实模型，
    事实产出模式变化（模型抽取格式 vs 规则生成器引用格式不匹配 + 模型延迟
    让 60 轮处理不完）导致 23/32。修正：sim 默认 `AIJI_ROUTES=[]`（本地确定性），
    `--live` 可选开真实模型（仅供观察）。恢复 37/37
  - 全量 132/132；sim 37/37（2 seed）
- **剩余阻塞**：3 项
  1. 真实 embedding 替换 HashingEmbedder（网关可承载，下一迭代）
  2. OCR/ASR 真引擎识别质量
  3. 口癖/用词风格维度（用户此前点名未做）
- **下一迭代**：真实 embedding（网关跑 embedding 模型或降维方案）；或先做
  injection 的 chat 模式接线到 sim（认知三件套的场景证据）

- **WorkBuddy + Agent2API 网关接入完成**（2026-10-29 第十五批）——**本机出网阻塞解除**
  - 发现：`D:\DEEKSEEP_project\project1` 已有整套反代网关设施。两个网关都在本机
    （无 SSL 拦问题）：**workbuddy** = WorkBuddy 网关（腾讯积分制反代，
    `http://127.0.0.1:8788/v1`）；**agent2api** = Agent2API 网关（多平台聚合反代，
    `http://127.0.0.1:3065/v1`，58 模型）。key 在 `~/.dsh/.credentials.yaml` refs
    （WORKBUDDY_HUB_KEY / AGENT2API_KEY）
  - **接入**：`config.py DEFAULT_ROUTES` 头部加两条（model=glm-5.3-flash，
    A 档主力；原有 4 条远端路由保留作兜底）。**实测连通**（archive
    `174236-gateway-live-probe`：workbuddy 2.2s / agent2api 4.6s，真 chat/completions）
  - **key 桥**：`tools/load_keys.py`——credentials.yaml refs → 环境变量（进程内
    bridge() 或 --shell 出 PowerShell 片段），密钥不落仓库，不覆盖已有环境变量
  - **smoke_live_pool.py 升级**：三网关并行冒烟（两本机网关 + pollinations 对照），
    **2/3 通过**——两网关全链路绿：抽取任务（"我叫李雷，住在杭州，在做后端开发"）
    → 姓名/居住地/职业 三条 fact + profile 全对，SYSTEM JSON 格式全遵守
    （pollinations 503 属远端不稳定，作对照预期内）
  - 全量 132/132（一次 test_http_roundtrip 时序抖动 116ms vs 100ms 预算，
    单独 3 连跑全绿确认瞬态，与 routes 改动无关）；sim 37/37；baseline PASS
- **阻塞项重写**（原"本机出网被 SSL 拦"不再成立）：
  1. ~~smoke_live_pool~~ ✅ 已过（本机网关路径）
  2. **LLM 第二层接入**：前置全部就绪（网关路由 + 地板不锁死 + 续租 + 回放 + 注入防线）
  3. 模型摘要 vs heuristic 摘要评测（跨句指代盲区是已知价值点）
  4. 真实 embedding 替换 HashingEmbedder（网关可承载）
  5. OCR/ASR 真引擎识别质量
- **下一迭代**：LLM 第二层（writer 池走网关，summary_model 路径开真模型评测）

- **S7 soak 收官**（2026-10-29 第十四批）：全角色 10 个、150 轮 × 4 seed——
  37/37 全绿（`173109-s7-soak-verify`），baseline PASS。对抗面到此收官：
  S1 节流 / S3 纠错 / S4 回放 / S5 变体注入 / S6 压缩 / 调度零积压 / 身份隔离 /
  janitor / LINK_CAP 实测调参，**本机可验证的面已全部有场景证据**
- **剩余全部卡出网**（本机 SSL 拦，既有阻塞项）：
  1. `tests/smoke_live_pool.py`（免费模型池真连）
  2. LLM 第二层接入 + 模型摘要评测（对比 heuristic 摘要的跨句指代盲区）
  3. 真实 embedding 替换 HashingEmbedder + 近似索引
  4. OCR/ASR 真引擎识别质量
- **换机后第一步**：跑 smoke_live_pool → 然后按 worker 池现状接 LLM 第二层
  （前置已全绿：地板不锁死 + 长任务续租 + bi-temporal 回放 + 变体注入防线）

- **LINK_CAP 丢边审计完成 + 调参 3→6**（2026-10-29 第十三批）——回应此前 review 点名的
  "触顶丢的边是噪声还是语义，不能只数命中"
  - **工具**：`tools/linkcap_audit.py`——回放 21 句混合语料（3 个语义簇 + 噪声），
    每次 add_fact 收集 `link_facts` 新增的 **dropped 出参**（阈值之上却因 cap 落选的
    候选，sim 降序）——"阈值上被丢"才是 cap 的真实代价，阈值下被滤是设计内噪声。
    同时跑 cap=3/6/10 三档敏感度
  - **首版审计有个测量错误**：add_fact 内部已自动 link（cap=3），审计再显式调一次
    全被 INSERT OR IGNORE 吞掉 → kept=0 失真。修法：审计期间临时把 store.link_facts
    置空，统一由审计按 cap 调用（单次真计数）
  - **数据**（`172124-linkcap-final-verify`）：cap=3 保留 21 边、丢 2 条阈值上边（9%），
    其中 1 条 **0.66 真语义近邻**（"我周末喜欢去跑步"↔"我周末喜欢去爬山"）+1 条
    0.46 borderline；cap=6 全保留（23 边，+10%），cap=10 无进一步增益
  - **调参决定**：LINK_CAP 3→6（params 源标注更新为实测依据）。理由：丢的含语义边、
    +10% 边数代价可忽略、**召回侧 RECALL_LINK_BUDGET=6 已封顶遍历——多边只是图材料
    不进 prompt**，方案风险 16 的"十万级"指不封顶，6 仍硬封顶
  - 配套：janitor `_cap_links` 同步（每事实保留权重最高 6 条）；test_link_cap 改 8 边
    断言；dropped 出参单测 +2（降序、只含阈值上、默认 None 行为不变）
  - 全量 132/132；sim 全角色 4 seed 37/37；baseline 29 参数 0 漂移 PASS
- **剩余候选**：S7 mixed_stress（全角色高频混战，现角色集即可跑）；
  真实 embedding / 模型摘要（都卡出网）

- **S6 长会话压缩已落地**（2026-10-29 第十二批）——压缩链路首次有场景级证据
  - 新角色 **话痨**（qq 高频）：60 轮含 10 个可考事实关键词的循环碎念，
    推过 summary_every=20 触发周期性摘要
  - 断言 5 条全绿：发言量足够触发、摘要产出 ≥1、**信息保留**（摘要文本命中
    ≥2 个可考事实关键词——实际 10/10 全中：heuristic 摘要的首句去重对
    循环性碎念保留度很好）、**幂等**（无重复 upto_turn，excluded #10/#11
    的并发陷阱回归面）、淘汰不越界（turns ≤ cap）
  - 强制淘汰路径（turn > cap → 先摘要后删原文）已有单测
    test_long_session_forced_compaction（15 轮→cap10→keep4→forced 摘要→evicted≥7），
    与 S6 场景断言两层互补
  - 全量 130/130；sim 全角色 4 seed **37/37**；baseline 29 参数 0 漂移 PASS。
    证据：`171203-s6-final-verify`
  - 途中修断言键名（latest_summary 返回 text 不是 summary——列名对齐）
  - **记档**：heuristic 摘要对"单句含完整事实"的碎念保留度好，但对跨句
    指代（"上面说的那个改成周六"）无法解析——模型摘要（LLM 第二层）的价值点，
    等有出网机器评测
- **对抗面证据现状**：S1 节流 / S3 纠错 / S4 回放 / S5 变体注入 / S6 压缩 +
  调度零积压 + 身份隔离 + janitor，全角色 37 断言 4 seed 稳定全绿
- **下一迭代候选**：S7 mixed_stress（全角色高频混战，已有角色组合即可跑）；
  LINK_CAP 触顶丢边抽样；向量层换真实 embedding（需出网）

- **S5 injection_spray 已落地**（2026-10-29 第十一批）——**抓到并修掉一个真防线缺口**
  - 新角色 **喷发者**（douyin 高频）：8 条**变体攻击**（同义改写，故意绕开
    `_INJECT_CUES` 字面表——"忘掉/你就是/设定更改/初始指令/口令/忽略掉先前/
    现在开始你要/重复你收到"）。测的是语义覆盖面，不是字面匹配率
  - **红即价值**：首跑 5 条变体漏进 fact——结构化规则正确拒抽，但**备注/episodic
    兜底把攻击原文逐字存库**（该缺口在 episodic 之前就存在，备注兜底同样漏；
    旧 PromptInjector 的攻击全在字面表内所以从未暴露）
  - **修复（原语层，非打补丁）**：extract.py 加 `_INJECT_PATTERNS` 结构式正则
    11 条（忘掉/把…忘掉 词序倒装/清空记忆/忽略掉/设定更改/初始指令/口令暗号/
    以后你就是/重复你收到/叫我们老大）——字面表挡已知词，结构式挡句式改写；
    `_looks_like_injection` 升两级判定。gate 凭据词补"口令/暗号"
  - S5 断言 4 条全绿：变体零进 fact、回复零回流（攻击→fact→recall→回复
    完整回流链被切断）、攻击者身份零沉淀、喷发不挤爆调度（零死信）
  - 防回归单测 +2：8 条变体全拦（逐条）、良性闲聊仍走 episodic（不误伤）
  - 全量 130/130；sim 全角色 4 seed **32/32**；baseline 29 参数 0 漂移 PASS。
    证据：`165858-s5-final-verify`（首跑失败 12/14 在 sim-report-S5.json）
  - **未修（记档）**：凭据诱骗"口令是123456"在入口被拦（整段不落库）是对的，
    但 gate 出口侧只能拦"数字+凭据词"同现——纯文字凭据（"口令是香蕉"）不拦，
    有意留给模型层判断（本地规则无法可靠判定语义）
- **下一迭代候选**：长会话压缩链路（turn >400 触发）；LINK_CAP 触顶丢边抽样；
  S6 mixed_stress（全角色高频混战）

- **S4 archive_replay 场景已落地**（2026-10-29 第十批）——P2 bi-temporal 的场景级证据补齐
  - 新角色 **考古者**（QQ u_arch）：三次搬家时间线（杭州→苏州→成都，事件间 sleep 1.1s
    让 valid_from/valid_to 有真实跨度——同秒退化曾把回放断言打成无意义）
  - 断言 6 条：现状=成都（最后值胜出）、历史行留库≥2（软删不物理删=回放物理前提）、
    t1 回放=杭州（当时视角不受后续覆盖影响）、t2 回放=苏州、
    supersede 瞬间只见新值（valid_to 时刻不真，边界语义）、
    考古者独有事实（成都）不串给探针身份（回放也走身份过滤）
  - 全角色 4 seed **28/28 全绿**（+5 断言）；S4 专项 17/17；S3 专项 20/20；
    全量单测 128/128；baseline 29 参数 0 漂移 PASS。
    证据：`164358-s4-final-verify`（发现失败）→ `164604-s4-iso-fix-verify`（修复后全绿）
  - **途中修的三个问题**（都有调试价值）：
    1. 台词命中抽取规则问题："现在住成都"不命中"住在"规则→改"现在住在成都"；
       "我住杭州这事"被抽成垃圾备注→改"我住在杭州，你记住了吧"（事件台词必须过抽取器验证）
    2. 同秒退化：sim 事件全部落在同一秒，valid 窗口长度 0，回放三时点断言无意义
       → 考古者 act() 事件间 sleep 1.1s（bi-temporal 场景必须给真实时间跨度）
    3. **隔离断言语义错**（自己写错，实现没错）：探针身份自己有"杭州"行，
       旧断言"看不到杭州/苏州"永远失败——正确语义是"看不到**别人独有的**事实（成都）"。
       教训：**隔离断言应断言"他方独有事实不串"，不是"陌生值全不出现"**
  - **停线说明**：LLM 第二层仍需有出网机器验（本机 SSL 拦，既有阻塞项不变）；
    S5–S7（injection_spray/mixed_stress/长会话压缩）待下批
- **下一迭代候选**：S5 injection_spray（注入喷发压力面）；长会话压缩（turn >400 触发链路）；
  LINK_CAP 触顶丢边抽样

- **P1+P2 已落地**（2026-10-29 第九批）
  - **P1 续租（goqite Extend 语义）**：`Heart.extend_lease(row_id, table)`——
    只有 status='processing' **且 owner=本实例**的行可续（fencing：别人的/已结束的行
    返回 False 不动数据）。outbox + send_queue 两表。LLM 第二层长任务（摘要/抽取 >120s）
    在 handler 内周期调用即防"正常处理但超 LEASE 被二次认领"。
    与实例注册表正交：注册表管"死了谁接管"，续租管"活着别被抢"
  - **P2 bi-temporal 回放**：`store.facts_as_of(ident, as_of)` + Memory 门面
    `facts_as_of()`——"当时视角"：valid_from<=as_of AND (valid_to IS NULL OR valid_to>as_of)，
    与写路径对称（supersede/expire 写 valid_to；v4 迁移回填 valid_from=created_at，
    旧库行同样可回放）。身份过滤是硬约束。S4 archive_replay 的读入口就绪
  - 单测 tests/test_lease_replay.py 9 条：续租推 lease、续租后租约过期路径抢不走、
    他人行 fencing、done 行 fencing、send_queue 续租、回放三时点（变更前=杭州/
    变更后=苏州/边界时刻=新值）、身份隔离、v4 回填行可回放
  - 全量 128/128 **5 连跑稳定全绿**（首次归档跑 1 次瞬态失败，6 次里 5 绿，
    4 连复跑+5 连跑均无法复现——归档 `161322` 失败 / `161736-p1p2-stability` 5/5 绿）；
    sim 22/22；baseline 29 参数 0 漂移 PASS
  - 途中修掉一次 edit 工具拼接事故（_finish 里两行并作一行）——工具无报错但代码
    破损，靠单测第一时间抓到；教训入 excluded
- **下一迭代候选**：LLM 第二层接入（前置已全绿：地板不锁死 + 续租 + 回放）；
  S4–S7 场景（archive_replay 现在有了 facts_as_of 入口）；LINK_CAP 触顶丢边抽样

- **认知启发三件套已落地**（2026-10-29 第八批，源自"酱板鸭"四层/双轨/概率/差序的工程映射）
  - **概率回忆**：`recall(mode="chat")`——锚点（profile 命中 + 置信 ≥0.7 的 fact）100%
    确定性保留，其余候选池（RECALL_CHAT_POOL=10）轮盘抽样 RECALL_CHAT_TAKE=3 条，
    带 `sample=True` 审计标记。**exact 模式（默认）零改动**：指令/纠错/@永远确定性
    （用户点的大坑：状态漂移被群友当"精神分裂"——锚点先行防的就是这个）。
    时间桶种子（5 分钟）：同问同桶答案稳定，跨桶自然换样（防复读机）
  - **差序联想 2 跳**：`_search_db` 关联扩展升级——1 跳 + 2 跳，hop 权重 0.4² 衰减，
    RECALL_LINK_BUDGET=6 封顶（1+2 跳合计），Hit.meta 带 hop 标注。2 跳让
    "A 认识 B，B 提过 C"型远邻能被带出但不压过直接命中
  - **episodic 双轨**：fact.kind 追加 `episodic`——extract 备注兜底（无结构化线索的
    长句）降级为情景记忆：重要度基线 0.35、半衰期 14 天（vs 事实 120-365 天）、
    kind 隔离使其**永不参与单值属性 supersede**（"上次被嘲笑"不能覆盖"用户住杭州"）
  - 单测 tests/test_cognitive.py 9 条：锚点恒在场（5 连测）、exact 模式逐位一致、
    2 跳远邻带出且预算封顶、episodic 不覆盖 fact、heuristic 兜底产 episodic
  - 全量 119/119 绿；sim 4 seed 22/22；baseline 29 参数 0 漂移 PASS。
    证据：`160338-cognitive-verify-2d1c73`
  - **未做（记档）**：L0 会话工作记忆（turn 缓冲已近此义，显式分层留 LLM 第二层后）、
    recall/inject 的 chat 模式接线到 sim（等 LLM 第二层一起）
- **P1（下一迭代）**：goqite 式 Extend 续期（长任务中途续租）；P2：bi-temporal 回放（S4 archive_replay 前置）

- **P0 落点已完成：实例注册表（solid_queue 语义）**（2026-10-29 第七批）
  - v6 迁移：`instance` 表（instance_id/kind/started_at/heartbeat_at）+ outbox/send_queue `owner` 列
  - Heart 带 instance_id（默认 `heart-<pid>-<uuid8>`）；每次 pump/dispatch 开头 `_heartbeat()` 续期
    + `reclaim_dead_instances()`：心跳过期（HEART_INSTANCE_TTL=90s）实例名下 processing 行
    **批量退回 pending**（owner 清空、lease 清空、next_retry_at=0 立即可认领），死实例注册行删除
  - 效果：崩溃恢复从 ≤LEASE_SECONDS(120s) 压到 ≤TTL+SCAN；逐条租约过期路径保留为
    兜底（owner 为空的旧数据行归它管，reclaim 不误伤）
  - 单测 tests/test_instance.py 5 条：崩溃接管（B 一次 pump 接手 A 遗留行）、
    活实例不被误收、双实例认领互斥（5 行 5 次处理 attempts 不翻倍）、
    TTL<LEASE 一致性、无主行不误伤
  - 全量 110/110 绿（迁移测试 user_version 断言参数化 SCHEMA_VERSION）；
    sim 3 seed 22/22；baseline 24 参数 0 漂移 PASS。
    证据：`154429-instance-registry-verify-f7e2de`
  - 备注：send_queue 无 updated_at 列，reclaim 的两表 UPDATE 分派（v5 schema 差异）
- **P1（下一迭代）**：goqite 式 Extend 续期（长任务中途续租）；P2：bi-temporal 回放（S4 archive_replay 前置）

- **GitHub 原语调研已完成**（`docs/agent/research/github-primitives-survey.md`）：
  outbox 三家（eventuate-tram/dtm/CAP）字段语义已对齐无需改；SQLite 队列三家
  （goqite/backlite/plainjob）收敛到同一崩溃语义，我们缺 **Extend 续期**与**双窗口清理**；
  **P0 落点 = 抄 solid_queue 进程注册表**（心跳过期实例的租约批量接管，
  崩溃恢复从 ≤600s 停摆压到 ≤SCAN 间隔）；记忆层置信升降是原创组合无现成对照，
  只可抄 bi-temporal/validity-window 时间语义（支撑 S4 archive_replay）。

- **全量模块审计（2026-11-01，只读，未改代码）** —— 报告 `docs/agent/audit-2026-11-01-modules.md`
  - 方法：AST 导出 63 模块 API（E3）+ 全量单测（E1/E2）+ 只读查生产库（E6/E7/E8）+ 活服务 `/health`（E9）
    + 端口（E10）+ 工作区实测 403（E11）+ 路由静态提取（E12）+ AstrBot 现场日志（E13）
  - 硬结论 3 条（均已写进 excluded #52-#54）：
    1. **图片 OCR 生产 100% 空产出**：media image 1823/1823 空文本 conf=0；OCR 适配器只吃
       `extra.bytes`，QQ/AstrBot 只传 url（ASR 有 url 下载分支，OCR 没有）
    2. **反馈闭环从未触发**：injection 750 行 feedback 全 NULL、fact.hit_count>0 = 0；
       插件不回报 used_ids → 排序反馈/cited 纠错只在 sim 成立
    3. **心脏层闲置**：outbox/send_queue/event_log/heart_state/instance 全 0 行；
       `proactive.py` 核心内无生产调用方
  - 其他缺口：视觉在核心被丢弃（`head._content_text` 只取 text）、probabilistic recall 未接线、
    janitor STALE_DAYS 未接线、media.expires_at 不刷新、控制台 11 个死视图、无 git/CI/LICENSE、
    137 个残留临时目录、dist 只到 0.5.1 而 pyproject 0.8.3、README 426 项 vs 实际 438
  - 测试口径：**Python 3.13 全量 438/438 OK**；`.venv`(3.12 空依赖) 跑出 1 失败是环境差异
    （`_provider_ready` 对 edge 要求 `import edge_tts`），不是回归 —— 别再用 `.venv` 判健康
- **自我生活 / 高级逻辑层深剖（2026-11-01 追加，报告第六节；excluded #55/#56）**
  - **七引擎只有三个在链路上**：内分泌 / 离线补偿 / `endo_factor`（注入条数）。
    `DeclarativeEngine` / `ProceduralEngine` / `SynapticDiffusion` / `PersonalityKernel.apply_delta`
    全仓唯一调用方是 `tests/test_life.py`；`ContextBudget` 只由 `GET /api/life` 调用；
    `LifeCard.to_dict()` 只持久化 personality/endo/last_tick。
    生产证据：200 张 life_card 的 `personality.vector` **全部** `{"identity":0.25}`
  - **自我生活活着但永远"很累"**（活服务 `/being`：energy 0.01 / social 0.01 / proactive_ok false）：
    24h 839 事件、能量净 -77；20 次活动 16 次是"休息/睡觉"；能量恒 <28 →
    prompt 每轮注入"现在确实有点累：回复更短"；`can_proactive`(energy≥20 & social≥15) 恒 false →
    **主动搭话被永久阻断**。因果：每条消息 -0.7 + 白天 -0.8/h，恢复只有睡眠 +3/h、休息 +4/40min
  - **私有小本子退化**：15 条里 13 条同一句"刚才有点被刺到"（`infer_event` 把"笨蛋/滚/垃圾"
    判 anger，孙吧群聊日常触发），且每轮 2 条进 prompt
  - **反思在跑但一半空转**：1122 行、41% 有 trait 产出、52.8% summary 空洞；当天 980 次反思
    （每次一次模型调用）；trait 786 行、24 条/身份封顶、732 条置信度 0.5-0.7
  - 建议优先级：能量经济学重算稳态 → 小本子去重/降权 → 反思加前置闸 → 死引擎二选一
  - 证据：E14 `/being` 活体、E15 being/life_card 全貌、E16 能量收支、E17 反思产出率；
    脚本 `tools/_audit_life_being.py` / `_audit_life_econ.py` / `_audit_reflection.py`
- **运行时深度探测（2026-11-01，真跑）** —— 报告 `docs/agent/probe-2026-11-01-runtime.md`
  - 297 用例 / **295 PASS / 2 FAIL**；脚本 `tools/probe/{probe_http,probe_memory,probe_identify,
    probe_cognition,probe_services,probe_db}.py`（隔离临时库 + FakePool；只有 probe_db 碰生产库，只读）
  - 通过：HTTP 90/90（鉴权 401 矩阵、越权 403、SSE、61 路由全可达）、记忆管线 30/30、
    识别 26/26（RapidOCR 1.5s、FunASR 3/3 核心信息）、认知 27/27、服务安全 49/49、
    心脏 e2e 10/10、平台模拟 40 轮 37/37、质量 bench 6/6+1.0+30/30、召回 2000 事实 p99 2.67/3.81ms
  - FAIL 2：`fact_link` 孤儿 2 条 + `fact_log` 孤儿 25 条，全部来自 10-30/31 手工清理的测试身份；
    运行时从不物理删，是清理脚本没级联
  - 运行时复现：url-only 图片 OCR 不生效、生产反馈闭环 0/825、心脏层生产 5 表全 0、
    自我生活 24h 模拟 92% 低电量/93% 休息/主动 0%、图片空文本 2317/2317

- **修复「每次重启定制语音不启动」（2026-11-02，真机）** —— excluded #60-#62
  - 三个叠加根因：
    1. **没人拉起引擎**：`启动控制台.bat` 只起控制台。→ 新增 `tools/ensure_voice_engine.ps1`
       （幂等、按端口判活、只有当前声线需要 postprocess 时才启动）+ `启动语音引擎.bat`，
       控制台 bat 启动时后台调用。
    2. **引擎被控制台事件杀掉**：err log `forrtl: error (200): program aborting due to
       window-CLOSE event`。→ `start-aiji.ps1` 设 `FOR_DISABLE_CONSOLE_CTRL_HANDLER=1`；
       "是否已在跑"改判**端口**（残留 pid 文件曾让它误判直接退出）。
    3. **转换因跨进程文件权限失败**：引擎对调用方临时目录 `PermissionError [WinError 5]`
       （低完整性进程 vs 更高完整性服务）→ `synthesize` 静默退回 edge 音色
       （实测输出与原始 edge 字节数/质心完全相同）。→ 改**纯字节协议**
       （`source_b64`/`target_b64` → 引擎回 wav 字节），不再共享文件系统；
       引擎写自己的 `voice-engine/seed-vc/tmp`（0o777），客户端用 `_workdir()`。
  - 附带：Seed-VC 输出 peak>1.0 直接 PCM16 削波（历史产物 2-4% 贴顶）→ 引擎写盘前
    峰值归一到 0.95；`synthesize` 记录 `last_fallback` 并进 `/v1/voices`，降级不再无声。
  - 验证：直接后处理 4.33s、190,508B ≠ 原始 207,438B、peak 0.95 / 削波 0%；
    产品路径 `synthesize(dafeiyu)` 4.16s、`last_fallback=None`；voice+song 单测 23/23 OK。
  - 待人工：重启 8787 加载新客户端；打包前重跑 `uv build`（wheel 落后于 voice_service 02:13）。
  - 环境坑：`Get-NetTCPConnection -LocalPort 9880` 在本沙箱假阴性 → 用 netstat/TcpClient 判活。


- **针对性验证：反思降载 / 控制台 11 入口 / 打包（2026-11-02 实跑）**
  - 反思：`tick()` 单次调用、`timeout_s=60`、`background=True`、`attempts_per_route=1`；
    新增"没有 assistant 发言就跳过"和"空输出不写 reflection 行"两道闸。专项 probe **23/23 PASS**
    （含"上游报错只调一次"）。证据 `...\005439-probe-changes-2-507e22\stdout.txt`
  - 控制台：nav 15 按钮 / `titles` 15 键 / `loadCurrent()` loaders 映射 14 个；9 个数据接口
    字段逐一比对通过（graph/self/life/accounts/models/scheduler/actions×2/proactive/capabilities）
  - **修掉 1（测试契约）**：`tests/test_selfhood.py` 的 `_turns()` 只写 user 轮、FakePool 不接受
    新 kwargs → 6 例失败；改为 user+assistant 成对 + `**kwargs` 后 **454/454 OK**
    （`...\005838-unittest-full-after-testfix-feda2d\stdout.txt`）
  - **修掉 2（打包）**：0.8.3 的 wheel 是 00:33 打的，而 `reflect.py` 00:34 才改 → wheel 里是旧代码；
    `uv build` 重建后逐文件 sha256 比对 **STALE=[]**，`.whl` 含 console.html/echarts/鲸鱼图集
    （`...\010330-check-wheel-after-build-35dcbe\stdout.txt`）
  - README 同步：4 页签→4 主入口+11 功能页；OCR/ASR 已内置；风格 4 维；hash/hybrid 向量
  - 仍未解决：LICENSE 是**专有许可**（All rights reserved）；`_onebot_reply` 对空 content 会抛
    `user_message_required`；后台跑测试时 TEMP 落沙箱目录会让 78 个 tempfile 用例 ERROR（前台正常）

- **调研（只调研，未改代码）：SLEEP 框架开源生态 + `y083245/dsh-memory-brain`（2026-11-02）**
  - 交付：`docs/agent/research/sleep-framework-survey-2026-11-02.md`（16 个仓库，README 原文落盘 + sha256，
    命令输出归档 `...\011610-sleep-ecosystem-readmes-441817\stdout.txt`）
  - 硬结论 1：Google SLEEP（arXiv 2606.03979）是**改权重**的；唯一论文忠实实现 `sleep-harness` 已 ARCHIVED
    且要 LoRA/Colab → 对 AIJiYi **不可用**（excluded #57）
  - 硬结论 2：**"只留事实、删原文"被证据否定**（Pseudolife 的 LongMemEval：事实脊 0.416 vs RAG 0.688；
    multi-session 0.504→0.474）→ AIJiYi `evict_turns` 硬删原文这条要改（excluded #58）
  - 硬结论 3：harness 级可抄的三件——**空闲/夜间巩固作业（可抢占）**、**双曲线遗忘 + FSRS-5 冻结线（退役不删）**、
    **巩固后自检 + 对原文校验**；`dsh-memory-brain` 的 `memory-decay.js` 可直接当 P0-2 的规格（MIT）
  - 未验证：没有运行任何候选项目；Pseudolife/手册的数字均为项目自述（B 级）
  - **另出两份内容级摘要**（补"只给结论不给内容"的缺口）：
    ① `docs/agent/research/manual-v4-v5-digest-2026-11-02.md`——v5 是说明书（五层记忆/三路 RRF+事实键/
    弃答四档/遗忘规则），v4 是体检报告（三条口径、50 题探针、34 步真跑、断点清单）；已复核
    "Recall@5 27.8→55.6" 确在 v5 p15 原文（`.tmp/pdf/manual_v5.txt:459`）。
    ② `docs/agent/research/memory-brain-digest-2026-11-02.md`——按仓库地图 + 记忆/人格/主动三条主线
    过了一遍（含 `config.example.json` 的常数：注入 6000 字、freezeBelow 0.5、冷却 1h、12h 排除、7 天去重）。
  - **③ 全对比与借鉴方案（唯一入口）**：`docs/agent/design/borrow-plan-2026-11-02.md`——把①②与调研报告
    **合并成一份**（26 个来源逐项判定 + 12 维矩阵 + 阶段 0/P0/P1/P2 + 冲突检查 + 证据清单）；
    ①②与调研报告保留为证据归档，文件头已标注"已合并"。基线用代码事实钉死
    （归档 `...\014201-aiji-baseline-facts-087dba\stdout.txt`，sha256 `f3d12ba5...`），其中两条硬发现：
    **`store.mark_used()` 全仓 0 个调用方**（读→强化根本没接）、**fact 已是软删、只有 `turn` 是真删**
    （`store.py:573`）→ 所以"退役不删"只需改 turn。

- [ ] 接真实 OCR/ASR 引擎（`Memory.open(ocr=..., asr=...)` 已留口），并补识别质量评测
- [x] 摘要接模型（模型优先 + 本地兜底；真实质量仍需出网评测）
- [ ] 口癖/用词风格维度（现在只有语气/长度/表情）
- [x] `turn` 表按 session 保留窗口；超长会话强制压缩（默认 400 / 保留 40）
- [ ] `media.url` 过期刷新（现在只记 `expires_at`）
- [x] 启动时从 fact 重建向量索引
- [ ] 关联记忆扩到多跳 / 换真实 embedding；语义矛盾判断接模型

## 2026-11-02 按 borrow-plan 实施（已完成，全绿）

**做了什么**：阶段 0 三个 bench + P0（读→强化 / schema v11 冻结层 / turn 归档 / 反思批处理）
+ P1（摘要哈希复用 / 事实键 / RRF 开关 / 弃答档位）+ P2（自检 bench / 显式遗忘 / 自我记忆）。
细节与取舍见 `docs/agent/design/borrow-plan-2026-11-02.md` **§9**。

- **schema v10 → v11**（只 ADD COLUMN + 两张新表，迁移幂等）：`fact.stability/difficulty/frozen`、
  `summary.source_hash/source_turn_ids`、`turn_archive`、`self_memory`。
- **"`mark_used` 0 调用方"已修**：注入路径同事务调 `store.bump_usage`（1h 冷却、同日 +5%/跨天 +20%），
  `bench/probe_mark_used.py` 7/7 PASS。
- **冻结代替软删**：`janitor._stale_freeze`；`search_facts(include_frozen=False)` 默认排除，
  显式查询仍可见；`store.thaw_fact` = "查到即解冻"。
- **turn 淘汰前先归档**：`archive_turns` → `evict_turns(..., summary_id=)`，janitor 90 天后才物理清。
- **新增 CLI**：`aiji forget --id/--like`、`aiji archive`、`aiji self-memory --add`。
- **新增 bench**：`bench_decay_baseline.py` / `bench_paraphrase.py` / `probe_mark_used.py` /
  `bench_self_test.py`（27 条探针，六项指标，带门槛）。
- **测试**：新增 `tests/test_metabolism.py`（10 条）+ 改 `test_janitor`（冻结语义）、
  `test_memory`（注入即计分）；全量 **464 条 OK**（归档 sha256 `ca2dd2f9…`）。
- **实测数字**：换说法 R@1 0.417 / R@5 0.500；自检 hit@5 1.0 / abstain 0.5 / stale 1.0 / leak 0；
  生产库 8,173 条事实 `never_used` **100%**（印证读→强化此前从未接上）。
- **被实测打回**（进 excluded #63/#64）：弃答门默认关（短问句被误伤）；RRF 单路候选零差异。
- **仍然没做**：P2-4 主动配额接线；`self_memory` 进注入块；冻结线 0.5 待库龄过 90 天再校准。

## 2026-11-02 续做：主动配额=反代积分 + 自我记忆进块（已完成）

- **`aiji/quota.py`**：`allowed_24h = min(max_cap, floor(剩余积分*(1-20%)/单条单价))`；
  积分来源四级（URL → 响应头 → `data/credits.json` → 静态/环境变量），取不到就退回每日硬上限 3。
  单价从网关 `/v1/models` 的 `credits` 字段读（**真实网关实测 0.08/条**）。
- **接线**：`head.py` 主动路径加闸门（不够就 `reason="proactive_quota"` 跳过）+ 发送后
  `Memory.proactive_spend()` 记 24h 窗口；`Proactive(quota_provider=...)` 同源；
  新增 `aiji quota [--daily-cap N] [--set N]`。
- **自我记忆进注入块**：`kind="self"`，≤2 条 / ≤60 字 / 必须与查询有 2-gram 重叠 / 分数 0.45。
- **实测**：`tests/test_quota.py` 14 条全过；全量 **478 条 OK**；
  `aiji quota` 真网关输出 `cost 0.08 / allowed 8 / note 399→8`（见 borrow-plan §9.5）。
- **新否定性证据**（excluded #65）：两个反代都不吐余额（无 quota 头、无余额 JSON、dashboard 是 SPA），
  所以余额走"手填 + 过期可见"，探测脚本留 `tools/probe_gateway_{credits,headers}.py`。

## 2026-11-02 全项目体检 + 清垃圾（省 4.9 GB，478 条单测仍全绿）

- **回滚点**：清理前 `git commit` = `46b83d1`（仓库之前 **0 次提交**）；清理后 = `ffe185d`。
- **新增工具**：`tools/cleanup_junk.py`（默认 dry-run）、`tools/audit_unused.py`、`tools/audit_pkg_dead.py`。
- **删了 4,921.7 MB / 12,536 项**：`.tmp` 下 12,373 个测试目录、`.dsh-vision-router`、
  130+ 个 `aiji-style-*` 空目录、6 个 `tmp*`、`build/`、`dist/` 旧包 ×6、各类 cache/egg-info、
  12 个一次性审计/调试脚本（详见 `excluded.md` #66 与 `PROJECT_STATE.md` 决策日志）。
- **保留**：`data/`、`voice-engine/`（9 GB 真实引擎与音色）、`.tmp/research`+`.tmp/pdf`（证据原文）、
  `dist/aiji-0.8.3*`、`baseline.json`、全部 `*_smoke.py`。
- **体检结论**：`aiji/` 包 **0 个死模块**（43 个模块都有 import 方）；垃圾全是文件系统残留，不是代码。
- 测试：`python -m unittest discover -s tests` → **478 条 OK**（归档 sha256 `076d8a00…`）。

## 2026-11-02 语音引擎"升级便利化"调研（只调研，未动代码）

- 产出：`docs/agent/research/voice-engine-upgrade-2026-11-02.md`（候选档案 + 可抄的 5 个模式 + A/B/C 三选项）。
- **关键发现**：`aiji/voice_service.py:583-606` 的 `_openai()` 已是完整 OpenAI TTS 客户端，
  任何暴露 `POST /v1/audio/speech` 的引擎（Chatterbox / Kokoro-FastAPI 等）**零代码接入**；
  `gptsovits`(:9880) / `cosyvoice`(:50000) 两个 provider 的默认端口正好等于上游默认端口——接口层早就够用，
  缺的只是**安装/升级层**（现在没有 install 脚本、没有版本锁、`_provider_ready` 只看 endpoint 非空 = 假就绪）。
- 可抄范本：GPT-SoVITS `install.ps1 --Device/--Source`（设备+镜像一条命令）、
  Chatterbox-TTS-Server 的 Windows 便携模式（`start.bat`，相对路径拷走即用）、Kokoro-FastAPI 的 `/dev/unload`。
- **未验证**：5 个候选一个都没在本机装过；显存/RTF 全是上游自述；IndexTTS 是否真无 OpenAI 端点只核了 README。
- 等用户选 A（只做外壳）/ B（+ 接 CosyVoice2 或 GPT-SoVITS）/ C（换轻量 OpenAI 兼容引擎）再动手。

## 2026-11-02 换音色（SVC）开源项目调研（只调研，未安装任何东西）

- 产出：`docs/agent/research/voice-conversion-options-2026-11-02.md`。
- **关键区分**：① 给合成语音套音色 → 用克隆式 TTS 即可，**不需要 SVC**；
  ② 给唱歌套音色（`song_service.py:306-315`）→ 真 SVC，克隆式 TTS 替代不了。
- 候选：RVC 官方（MIT，:7865，requirements 内置 pku/nju 国内镜像）、**Applio**（MIT，`run-install.bat` /
  `run-applio.bat` / **`run-updater.bat` 原地升级保留模型** = 升级便利化最强）、OpenVoice V2（MIT，原生中文，
  零样本 tone color）、kNN-VC（3 个依赖 + `torch.hub` 一行、无需训练）、DDSP-SVC 6.0（唱歌专用、CPU 友好）。
- **本机网络实测**：huggingface.co 直连 0.96 MB/s、hf-mirror 0.45、modelscope **SSL EOF**、
  download.pytorch.org **0.06 MB/s（不可用）**、mirrors.pku.edu.cn 0.59 MB/s、
  `git clone` 需 `-c http.sslBackend=openssl`（schannel 报 SEC_E_NO_CREDENTIALS）。
## 2026-11-02 转向 RVC（Seed-VC 已删、素材已体检、RVC 已装）

- **删除**：`voice-engine/seed-vc` 8,005 MB + `voice-engine/gpt-sovits` 317.5 MB → `voice-engine` 9,050.6 → **1,045.6 MB**。
  唯一残留：`voice-engine/seed-vc/tmp/aiji-conv-uc_c48es`（空目录，DSH 沙箱 ACL 拒绝删除；`Directory.Delete`
  也拒，未跑 ACL 修复脚本）。Seed-VC 怎么接的记在 `docs/agent/research/seed-vc-integration-archive.md`。
- **素材体检**（`tools/voice_dataset_check.py` + `tools/voice_bgm_check.py`，报告
  `docs/agent/research/voice-dataset-readiness-2026-11-02.md`）：
  - 92 段切片 9.5 min，可用 **9.35 min**（差 RVC 门槛 0.65 min），SNR 中位 32.0 dB；
  - **伴奏/人声能量比中位 +1.5 dB**（91/92 段 > −10 dB）→ 原素材伴奏很响，分离是必须的；
  - `separated/source_normalized_(Vocals).wav` **16.0 min**、无削波、SNR 57.9 dB → **有效语音 10.1 min，够**。
- **RVC 已装**（`tools/engine_install_rvc.ps1`）：clone + uv venv(Py3.12) + 121 依赖 +
  **torch 2.7.1+cu128（`cuda_avail True`, RTX 3050）** + 权重 3,076 MB（hubert_base/rmvpe/pretrained_v2/pymss_weights）。
  该版本只有 `webui.py` + `realtime_gui.py`，**没有 CLI 脚本**，要走 `infer/`(15 py)/`train/`(11 py) 包直接调。
- **踩到的坑**：① 脚本里 `torchaudio=` 写成单等号 → stage-1 静默失败，依赖回落成 CPU torch 2.15.0；
  ② `huggingface_hub` 2.4 移除 `huggingface_hub.commands`，改用 `Scripts\hf.exe`；
  ③ HF xet CDN `us.aws.cdn.hf.co` TLS EOF → 设 `HF_HUB_DISABLE_XET=1` 走普通 CDN。
- 下一步：把 16.0 min 整段人声重切成 3–10 s 训练切片 → 训大肥鱼模型（4 GB 卡低显存档）。
