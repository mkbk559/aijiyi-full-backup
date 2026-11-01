# SLEEP 框架 / 睡眠期记忆巩固：开源生态调研 + 对 AIJiYi 的可用性判定（2026-11-02）

> **已合并**：结论与逐项目判定已并入唯一入口
> [`docs/agent/design/borrow-plan-2026-11-02.md`](../design/borrow-plan-2026-11-02.md)。
> 本文件保留**逐项目原始记录与证据**（README 原文、sha256、命令归档路径），不再单独维护结论。

> 目的：**只调研与对比，不改代码**。回答两个问题：
> 1) Google「SLEEP 框架」这条线上的开源项目里，有没有 AIJiYi 能用的；
> 2) `y083245/dsh-memory-brain`（下称 **memory-brain**）对 AIJiYi 有没有用。
>
> 证据等级：**A** = 我抓了原始 README/源码并落盘（sha256 见 §7）；**B** = 项目自述、我未复现；
> **C** = 我基于代码/文档的推断。
> 全部数字来自被抓取的原文，不是二手转述。本机出网是选择性拦截：`api.github.com` 直连被限流/SSL 拦，
> 但 README 原文经 py/urllib 可下（§7 有 hash）。

---

## 0. 结论速览

**SLEEP 这条线**：Google 的 SLEEP 论文（`arXiv:2606.03979`，*Language Models Need Sleep*）本身是**改权重**的
（Knowledge Seeding 蒸馏 + RL Dreaming），对 AIJiYi **不适用**；但由它派生出来的 **harness 级** 实现
（不改权重、只做离线巩固）恰恰命中了 AIJiYi 现在最疼的地方：**反思每轮跑、52.8% 空转**、
**淘汰是硬删**、**只有"排序衰减"没有"记忆代谢"**。

| 层 | 项目 | 对 AIJiYi 的判定 | 一句话 |
|---|---|---|---|
| 论文忠实实现 | `EvolvingAgentsLabs/sleep-harness` | **不可用** | 要读 residual stream + LoRA + Colab GPU，且已 ARCHIVED（A） |
| harness 级（技能/插件） | `mocodainteractive-pixel/agent-sleep` | **有用（方法论）** | MIT，6 个模式 + "每个门禁对应一种失败"的表格，最像一份可执行规范（A） |
| harness 级（插件） | `liuqingman/dsh-somni` | **最有用（工程参照）** | 双曲线遗忘 + 间隔重复 + 可抢占 dream + 退役不删，Python sidecar 与 AIJiYi 同构（A） |
| harness 级（技能） | `xiaogege6697/dream-skill`、`stewnight/rem-sleep-skill` | **部分有用（流程）** | 6 阶段/复盘清单可直接改写成巩固作业的 prompt（A） |
| 独立记忆系统 | `ldclabs/anda-brain` | **借机制，不借依赖** | "结算先于模型"、"遗忘是可访问性不是真值"、维护后自检（A） |
| 独立记忆系统 | `qyiun666/MemHop` | **借一点** | 注入体积收敛（DreamCompressMinTopics）+ 每轮恰好 1 次模型调用的成本模型（A） |
| 独立记忆系统 | `Pseudogiant-xr/Pseudolife-MCP` | **借结论（重要）** | 唯一给出 LongMemEval 全量数字：纯事实脊 0.416 vs RAG 0.688，cascade 0.690@73% token（A/B） |
| 独立记忆系统 | `kolegadev/Katra-Agentic-Memory`、`wyckit/mcp-engram-memory` | **不借** | Docker+Mongo+MinIO / C# 5MB，与单文件 SQLite 冲突（A） |
| 研究代码 | `marc-shade/memory-consolidation` | **借结论** | Cohen's d=2.31，但作者自己的 ablation 说"架构 > 特征"（A/B） |
| 旁支 | `microsoft/SkillOpt` | **无关** | 优化的是 skill 自演化，不是记忆巩固（A） |
| 旁支 | `letta-ai/sleep-time-compute` | **只作概念出处** | 是实验脚本，不是可复用实现（A） |

**memory-brain 的判定**：**有用，但只能"抄机制"，不要抄结构**。它与 AIJiYi 是**同题不同栈**的两个近亲
（QQ 陪伴 + 长期记忆），四件东西值得直接搬：**FSRS-5 代谢（含冻结线/解冻）**、**账本建议（候选 + 采用率）**、
**影子模式**、**RAG 总开关 + 失败降级 + 换模型重建**。它的 785KB 单文件 `bridge.js`、Chroma+SQLite 双写、
Linux QQ 版本锁定这些**不要**往 AIJiYi 里搬（§6）。

**如果只做三件事**（按 ROI 排序）：
1. **把"每轮反思"改成"空闲/夜间巩固作业"**（可抢占、按会话批处理、幂等）；
2. **淘汰改退役**（原文归档而不是 `DELETE`），并给 fact 加"可提取度/冻结"这一层**门禁**（不动现有排序权重）；
3. **巩固后自检 + 面对原文**（防摘要漂移；这是 agent-sleep 全部门禁里最便宜的一条）。

---

## 1. 先把「SLEEP 框架」对齐

- 论文：`arXiv:2606.03979` *Language Models Need Sleep: Learning to Self-Modify and Consolidate Memories*
  （Behrouz, Hashemi, Javanmard, Mirrokni；v1 2026-06-02，v2 2026-07-10；CC BY 4.0）。摘要原文要点（A）：
  - 两阶段："**Memory Consolidation**（Knowledge Seeding，把小 self 的记忆向上蒸馏进更大网络）" +
    "**Dreaming**（用 RL 生成合成课程、自我复练）"；
  - 目标是把"短期脆弱的 in-context 记忆"变成"长期参数里的知识"。
- Google Research 博客同题（`research.google/blog/the-sleep-framework-...`）是这条线的科普版（A，但正文被导航截断）。
- **关键判断（C）**：这条线分两层——
  - **权重层**（改参数）：AIJiYi 是"记忆+人格中间件"，不改模型权重 → 这一层**全部放弃**；
  - **harness 层**（不改权重，只做离线整理/选择/修剪）：**这才是可移植的部分**。下面所有"可用"的项目
    都是 harness 层的，且它们自己也明确这么定位（agent-sleep 原文："What this repo adds is the
    harness-level implementation — token space, platform-agnostic, **no training required**"）。

---

## 2. 生态盘点（我实际读了哪些、各自怎么巩固）

### 2.1 `mocodainteractive-pixel/agent-sleep` —— 把"生命周期"写成门禁表（MIT）
- 定位：编码 agent 的睡眠周期记忆管理，**skill 形态**（`SKILL.md` + bash + prompt 模板），6 个模式：
  `consolidate`（会话末/低峰）、`check`（每日 cron，只读）、`reconcile`（N 次提升或矛盾标记）、
  `prestudy`（空闲预热）、`rebuild`（维护成本超过收益时从源头重建）、`rollback`（漂移告警回滚）。
- 最值钱的是这张表（每个门禁 = 一种已知失败 + 引文）：

| 门禁 | 它防的失败 |
|---|---|
| 对**原文**校验，绝不对摘要校验 | 语义漂移（"微辣" → "爱吃辣"） |
| 每个 skill 有"奠基测试" | 程序性漂移、自演化非单调 |
| 章程（charter）钉在写作用域之外 | 压缩会丢常驻规则（30–59% 违规） |
| TTL + 每日重校验 | 记忆腐烂；保留面越宽违规越多 |
| 每项目隔离 + scope frontmatter | 跨上下文串味/检索碰撞 |
| 只交接结论不交接逐字稿 | 投毒与"洗白"能穿过压缩 |
| **按触发器重写，绝不按日历** | 过度精炼让记忆变通用 |

- 对 AIJiYi（C）：这七条里，**"对原文校验"、"TTL+每日重校验"、"按触发器重写不按日历"** 三条直接可落；
  它的 `rollback` 思路（git 快照 + 回滚）AIJiYi 已有 backups 文化，可对齐。

### 2.2 `liuqingman/dsh-somni` —— 与 AIJiYi 同构度最高的工程参照（MIT）
- 形态：DSH 插件（TypeScript）+ **Python sidecar**，stdio ndjson JSON-RPC；记忆是 Markdown+YAML front-matter，
  SQLite 只做派生向量索引（可删可重建）。→ 与 AIJiYi "SQLite 权威 + 可重建索引"同构。
- 五个 front-matter 数字驱动生命周期：`score / stability / salience / last_used / last_decay`。
- **写入**：episode 走"快曲线"（salience 低 0.4/中 0.6/高 0.8），knowledge/skill 走"慢曲线"；
  **prospective（未了结意图）完全免衰减**（Zeigarnik），只受 3 天到期窗控制。
- **强化（间隔重复）**：`record_usage` 在新的一天 `stability += 1, score += 0.2`；同一天重复只 `+0.05`
  （"massed repetition earns less than spaced repetition"）。
- **遗忘（两条曲线）**：`daily_decay = 1 − base/(1+stability×0.5)`，`score *= daily_decay^天数`；
  semantic `base=0.1`，episodic `base=0.25`（从未被召回的情景 1–2 周淡出）；高显著度 episodic `base×0.4`（闪光灯效应）。
- **退役不删除**：`score < 0.2` → 移到 `archived/`，文件仍可 grep，后续 dream 可"重新提升"。
- **检索**：`0.6×cos + 0.4×关键词` + 字面命中 +0.2；cos 噪声地板 0.40；邻居 1 跳按 0.6 折。
- **dream 触发与抢占**：`idleMs=15min` / `maxIntervalMs=6h` / `minPendingLogs=1`；`sleep.cancel` 是
  **协作式**的（dreamer 在每次工具调用之间检查标志），"用户永远不等巩固"；`dream_now` 工具可手动触发。
- 对 AIJiYi（C）：这是**最有价值的一份参考实现**——因为它把"衰减/强化/退役/抢占"全部写成可抄的常数，
  且宿主模型可复用（`dream.llm: host` 用宿主 LLM，零额外 key），与 AIJiYi 的模型池处境类似。

### 2.3 `ldclabs/anda-brain` —— "结算先于模型" + 断言/证据模型
- 三层：业务 agent → Brain（Formation/Recall/**Maintenance**）→ 图库（Concept/Proposition/**Assertion**/Evidence/Activity）。
- **Maintenance 就是 sleep**，三档深度（A）：

| 档 | 触发 | 做什么 |
|---|---|---|
| `daydream` | 每 21 次成形对话 / 手动 | 显著度打分 + 近期材料微巩固 |
| `quick` | 每 42 次 | 评估 + 紧急 SleepTask |
| `full` | 每 168 次，且 ≥24h 一次 | 全量：谓词普查 + 保留期到期处理 |

- 每轮先做**确定性 settlement**（"the host, not the model, records new corrections…and hands the model a
  factual `assessment`"），模型只做认知活：合并成派生断言（带 lineage）、身份去重（**非破坏性合并**）、
  修订依赖（前提被改 → 标 stale）、把重复成败编译成 skill 候选（保持 unproven）、**自检**（拿从没被问过的
  近期记忆去搜，搜不到就排队重编码）。
- 两句可直接引用的设计原则（A）："**Forgetting is about accessibility, never truth**"、
  "**Reading never reinforces**"。
- 对 AIJiYi（C）：图库不借（AIJiYi 用 SQLite + `fact_link` 已够），但**"先结算、后模型"**和**"维护后自检"**
  两条是 AIJiYi 现在没有的，且成本很低。

### 2.4 `qyiun666/MemHop` —— 六层 + 零打分的读路径（Apache-2.0，Go 单文件 `.meh`）
- L0 Profile → L1 Engram（场景超图）→ L2 Context → L3 Knowledge → L4 Archive → L5 Plan；**Dream pipeline**：
  L2 压缩 → 索引重建 → L1 节点/超边重建 → L1 衰减 → L0 蒸馏，外加 `l4_prune`/`l5_prune`（保留窗默认 7 天）。
- 三个可抄的点（A）：
  1. **读路径零 LLM/零 embedding**（只读内存缓存），写路径每轮**恰好一次** LLM（关键词蒸馏）；
  2. **注入体积靠巩固收敛**（`DreamCompressMinTopics=20` 是目标而非硬上限）；
  3. 成本模型写得极清楚：阅读 0 次调用、写入 1 次/轮、Dream 每场景 1 次（输出 cap 8192/2048）。
- 它明确**放弃**了三路 RRF、扩散激活、topic centroid、embedding 依赖（"Removed along with retrieval"）——
  这和阿木（AIJiYi）的方向相反，**不要照搬**；只借"注入体积收敛"和"每轮一次调用"的成本纪律。

### 2.5 `Pseudogiant-xr/Pseudolife-MCP` —— 唯一给出全量基准的项目（Apache-2.0）
- 关联记忆库 + 规范化事实皮层（cortex）+ 睡眠式 dream（把未巩固记忆抽成 `(entity, attribute, value)`）。
- **LongMemEval 500 题全量、oracle 变体、单遍、本地判题**（B，项目自述）：

| 方案 | 准确率 | 每题上下文 token |
|---|---|---|
| naive RAG（top-6 turn） | 0.688 | ~1210 |
| 只给 cortex 事实 | **0.416** | **~158** |
| hybrid（事实 + top-3 turn） | 0.664 | ~842 |
| **commit-gated cascade** | **0.690** | ~883 |

- 分题型（B）：事实脊在 knowledge-update（0.859→0.936）和单会话题上赢，在 **temporal-reasoning（0.526 持平）**
  和 **multi-session（0.504→0.474）** 上**输**——"per-fact consolidation 恰好丢掉了跨会话聚合与时间顺序"。
- 作者自己的结论（A）："0.690 vs 0.688 是 500 题里的 1 题，**nobody should read it as a win**"。
- 对 AIJiYi（C）：**这是本次调研最重要的一条负面证据**——AIJiYi 的注入是"事实优先"，如果某天要评质量，
  必须**分题型**看（尤其是"跨会话聚合"和"时间推理"），否则会被总体分数骗。可抄的是 **cascade 策略**
  （事实能答就答，不能答回退原文片段），而不是"只保留事实"。

### 2.6 其余（判定：不借依赖 / 只借结论）
- `kolegadev/Katra-Agentic-Memory`：episodic/semantic/KG/working + 每日/每周/每月反思 + 66 个 MCP 工具；
  但栈是 **MongoDB + Redis + MinIO 的 docker compose**（~384MB RAM）、许可 `NOASSERTION`（BSL 系）。
  → 机制（"反思分日/周/月三档"）可看，依赖**不碰**。
- `wyckit/mcp-engram-memory`：图谱拉普拉斯扩散驱动衰减/巩固/自动连边，63 个 MCP 工具，**C# 5MB** → 过重，不借。
- `smysle/agent-memory`：SQLite + 写入门禁（语义去重/类型化合并/冲突检测）+ 语义衰减 + provenance + 归档
  而非删除 + `reflect/reindex` 作业。→ 与 AIJiYi 最接近的"纯 SQLite"方案，可借 **provenance（observed_at/
  source）** 与 **"eviction 走 archive 可恢复"**。
- `marc-shade/memory-consolidation`：论文级实验代码，报告 **Cohen's d=2.31, p=0.007、51.6% vs 49.1%**；
  但作者 ablation 的结论是"**架构（多层 + 时间结构）> 单个巩固特征**"（memory replay / concept updates /
  failure learning 贡献都边际）。→ 对 AIJiYi 的含义（C）：**先有"层与时间结构"，再谈花哨的巩固算子**。
- `xiaogege6697/dream-skill`：纯文档 skill，6 阶段（入睡校准→慢波巩固→REM 关联→稳态瘦身→类淋巴冲洗→
  髓鞘化+自检），"保守修剪、可回滚、绝不编造记忆"，中间接口
  `Memory Inventory → Candidate Changes → Reversible Changeset → Evidence Report`。→ 这段接口定义可以直接
  拿来当 AIJiYi 巩固作业的输出契约。
- `EESIZ/clawdreamer`：NREM（聚类→蒸馏→去重→LanceDB）/ REM（冲突三分类 `state_change`/`different_aspects`/
  `unrelated` + 重要性衰减 + 软删除）/ Dream Log（审计报告）。→ **冲突三分类**值得抄进 AIJiYi 的冲突消解
  （现在只有"多值累积/单值 supersede/否定 expire"）。
- `EvolvingAgentsLabs/sleep-harness`：论文忠实实现，要用 Jacobian lens 读 residual stream + 堆 LoRA + Colab
  微调，README 标 **[ARCHIVED]**。→ **排除**（见 §5 #1）。

---

## 3. 横向机制对照（AIJiYi 视角）

| 维度 | AIJiYi 现状（代码事实） | somni | memory-brain | anda-brain | MemHop | agent-sleep |
|---|---|---|---|---|---|---|
| 巩固触发 | **每 5s 扫描 + 攒够轮次就反思**（`reflect.py`） | idle 15m / 最长 6h | 每次唤醒后提炼话题摘要 | 21/42/168 次成形对话 + 24h | 场景 topic 数 > 24 | 会话末 + 每日 check |
| 可抢占 | ✗（单 worker 串行） | ✓ 协作式 cancel | ✗ | 宿主调度 | 域锁 + ctx 取消 | ✓ 串行化 |
| 遗忘模型 | **只有排序衰减**（按谓词半衰期） | 双曲线 + stability | **FSRS-5（S/D/R）+ 冻结线** | 衰减算在读取时 | L1 重要性/边权衰减 | TTL + 每日重校验 |
| 强化 | `hit_count` 存在但**生产为 0** | 跨天 +0.2 / 当天 +0.05 | 顺利/费劲两档 + 冷却 1h | 只有显式信号才强化 | 边权只在端点变化时变 | 无（靠触发重写） |
| 淘汰 | **硬 DELETE**（`evict_turns`） | 移到 `archived/` | 冻结不删（可查即解冻） | **从不 purge** | L4 超 7 天丢正文留关键词 | dry-run + git 回滚 |
| 巩固产物 | reflection 行 + trait + 摘要 | episodes/knowledge/skills + profile delta | 话题摘要 + 实体关系 + 账本候选 | 派生断言 + skill 候选 | L2 融合主题 + L0 人格 | MEMORY.md + 技能 |
| 事后自检 | ✗ | ✗ | 报表（只统计不校验） | ✓ 自检 + 重编码 | 一致性测试 | ✓ 每日 check |
| 成本纪律 | 每会话每 N 轮 1 次调用（实测 980 次/天） | 1 次/次 dream 迭代 | 1 次/话题块 | 每档多次 | **写 1 次/轮、读 0 次** | 1 次/会话 |

> AIJiYi 列的全部是**代码事实**（`aiji/reflect.py`、`aiji/store.py::evict_turns`、`aiji/recall.py::score_fact`、
> `aiji/writer.py::_compact_session`），不是推测。

---

## 4. 对 AIJiYi 的具体可用清单

> 原则：**不动已经取证的指标**（提交 p99 0.06ms、召回 p99 2.6ms、注入 0 越权、top-1 1.0）。
> 下面每条都写明"落在哪 / 怎么验收 / 会不会碰红线"。

### P0-1 把"每轮反思"改成"空闲/夜间巩固作业"
- 机制来源：somni `dream.idleMs/maxIntervalMs/minPendingLogs` + 协作式 `sleep.cancel`；agent-sleep
  "会话末/低峰 consolidate"；手册 v5"每 6 小时事件巩固"（B）。
- 落到：新增 `aiji/consolidate.py`（单例后台线程）。触发 = **max(攒够 2 个会话, 空闲 ≥15min)** 且距上次 ≥1h，
  上限 6h 兜底；一次跑**多个会话**，每个会话**一次**模型调用；写 `reflection`+`summary`+trait 在同一事务；
  会话有新 turn 到来时**跳过该会话**（协作式让路，不抢写锁、不持锁做模型调用——沿用 `_compact_session` 的写法）。
- 验收：① 反思写行数/天 从 ~980 降到 ≤ 会话数；② summary 空洞率（audit 口径 52.8%）下降；③ `kill -9` 后重跑
  不重复写（`up_to_turn` 游标）；④ 全量单测不回归。
- 红线：**不要把模型调用搬进 `submit_turn`**（现有点对点指标会崩）。

### P0-2 给 fact 加"可提取度 + 冻结"（门禁层，不改排序）
- 机制来源：memory-brain `memory-decay.js` 的 FSRS-5——`R=(1+F·t/S)^−0.5`（F=19/81，t=S 时 R=0.9）、
  `S'=S(1+e^{w8}(11−D)S^{−w9}(e^{w10(1−R)}−1))`、`freezeBelow=0.5`、`reviewCooldown=1h`、
  **核心条目/未了结约定不参与**；somni 的双曲线（semantic 0.1 / episodic 0.25）。
- 落到：`fact` 增 `stability / difficulty / last_review / frozen`（schema v8 + 迁移，照 v6→v7 的写法）；
  注入时 `frozen=1` 不进块（但 CLI/`recall` 显式查得到，查到即解冻——这是"遗忘 ≠ 删除"）；
  `fact_log` 增 action `review`/`freeze`/`thaw`。
- 验收：① 单测"90 天未提 → frozen；显式召回一次 → 解冻且 stability 增大"；② 5000 事实 bench p99 不退化；
  ③ 跨平台隔离用例全绿（新增查询必须带 `Ident.where()`）。
- 注意：AIJiYi 现在的衰减是**打分用的**（`recall.py` 半衰期），这两个概念要**并存**，不要合并成一个数。

### P0-3 淘汰改"退役"：turn 原文归档
- 机制来源：somni "Retirement, not deletion"；anda-brain "forgetting is accessibility, never truth"；
  agent-sleep "rebuild from source"。
- 落到：`store.evict_turns` → `archive_turns`：先把待删行写 `turn_archive`（原 schema + `archived_at` +
  `summary_id`），再 `DELETE FROM turn`；或加 `archived` 列软删（更省事但表会涨）。归档保留 90 天后按策略清理。
- 验收：压缩后原文可回查（CLI 增加 `--archived`）；单测断言"删 turn 后 archive 有行、摘要可指回"。
- 为什么值得：agent-sleep 引用的漂移研究说得很直白——**一旦只剩摘要，摘要的错误就再也无法被发现**。

### P1-1 巩固后自检（"醒来抽查"）
- 来源：anda-brain self-test；agent-sleep 的 `check`（只读）。
- 落到：`bench/bench_memory_quality.py` 增一节：抽最近 20 条 fact，用**间接问法**测 top-5 命中；命中率低 →
  触发重编码（重跑 extract 或重建索引）。
- 验收：报告里出现 `recall@5` 基线数字；后续改动可对比。

### P1-2 巩固面对原文，摘要只做"指针"
- 来源：agent-sleep 第一条门禁（verify vs raw source）；Pseudolife 的分题型结论。
- 落到：巩固作业读**未归档的 turn 原文**；摘要写入时带 `source_turn_ids`；摘要与原文冲突以原文为准
  （可先做成"抽查 20 条、人工/模型判是否有原文不支持的内容"）。
- 验收：抽查报告；摘要命中率。

### P1-3 事实键扩展 + 弃答四档（来自手册 v5，不是开源项目）
- 来源：手册 v5 §5.2/5.3（B，作者自测）：把问题拆成 **实体/属性/时刻** 三类键再放大召回，
  同一批 18 条换说法查询 **Recall@5 27.8% → 55.6%**（可达项内 71.4%，MRR .271→.460）；弃答四档
  `sure/vague/stale/none`，35 题校准 35/35。
- 落到：`extract.py` 产出 `fact.keys`（JSON）；`recall.py` 用键做查询扩展；`inject.py` 给每条注入标档位
  （`stale` 就写"上次你说的是 X，现在还是吗"）。
- 为什么排 P1：它正好补 AIJiYi 已知的两个短板（**换说法召回**、**语义矛盾/否定会漏**），但需要改打分与注入，
  风险高于前三条；先把 P0 做完再动。

### P2（有明确收益但可以等）
- **cascade 注入**（Pseudolife）：事实能答就答，不能答回退原文片段——与 P1-3 的档位天然配套。
- **注入体积收敛**（MemHop `DreamCompressMinTopics`）：给"每会话注入事实条数"设目标值，靠巩固收敛而不是靠截断。
- **冲突三分类**（clawdreamer：`state_change` / `different_aspects` / `unrelated`）：补 AIJiYi 现在只有
  "多值累积/单值 supersede/否定 expire" 的盲区。
- **provenance**（smysle `observed_at` / `source_session`）：`fact` 记录"何时观察到"而不是"何时写入"，
  对"过时"判定（手册里的 stale 档）是前提。
- **账本建议 + 影子模式**（memory-brain，见 §6）。

---

## 5. 明确排除（别重走）

1. **`sleep-harness` 这类"论文忠实实现"**：需要读 residual stream（Jacobian lens）、堆 LoRA、Colab 微调，
   仓库已 **[ARCHIVED]**。AIJiYi 不改权重，**不可用**（A）。
2. **`microsoft/SkillOpt`**：优化的是 agent 的 **skill 自演化**（benchmark + 后端 + 训练），"sleep" 指的是
   夜间自演化流程，与记忆巩固无关（A）。
3. **Letta 的 `sleep-time-compute`**：是论文配套实验脚本（stateful GSM 等），不是可复用库；"后台算记忆"
   这个概念已经被 somni / 其它 harness 级项目落地了，**抄概念即可**（A）。
4. **把 Katra / MemHop / mcp-engram 当依赖引入**：Docker+Mongo+MinIO / Go 自定义 `.meh` 二进制 / C# 5MB，
   与 AIJiYi「Python 包 + 单文件 SQLite + 零外部服务」的定位冲突；**只借机制**（A）。
5. **MemHop 的"取消打分检索"**：它删掉了 RRF/扩散/embedding，改成"场景=会话、只读关键词轨"。AIJiYi 的
   多信号排序是有 bench 取证的（top-1 1.0），**不要照搬**。
6. **"只保留事实、丢掉原文"**：Pseudolife 的数字已经把代价摆出来了（multi-session 0.504→0.474，
   temporal 0.526 持平）。AIJiYi 已朝"事实优先"走，**必须保留回退原文的能力**（P0-3 + P2 cascade）。

---

## 6. `y083245/dsh-memory-brain` 专项：有什么用、怎么用

### 6.1 它是什么（A，README + FORK_FEATURES）
- 一句话：**QQ 陪伴 AI 的桥**——`SnowLuma`（OneBot）↔ 本项目（:3100 控制台）↔ **DSH**（DeepSeek Harness，:3080）
  ↔ DeepSeek API；可选 `sidecars/memory-rag`（Python，:8093，Chroma + SQLite FTS5）。
- 出身：fork 自 `Derpyu520/qq-bridge`（MIT），保留上游历史；**MIT**；Node ≥22（推荐 24）+ Python 3.12（uv）；
  Linux 主线（Windows 有 `windows-native` 分支）。
- 记忆相关模块：`long-memory.js`（按天 JSONL 存档 + 人物账本）、`memory-decay.js`（FSRS-5 代谢）、
  `memory-rag-client.js` / `rag-control.js`（RAG 开关与降级）、`ledger-suggest.js`（账本建议）、
  `life-loop.js`（到期约定/忽然想起）、`persona*.js`（人格判读，先影子模式）、`daily-report.js`（零模型日报）。

### 6.2 与 AIJiYi 的重叠 / 差异
| 维度 | AIJiYi | memory-brain |
|---|---|---|
| 栈 | Python 包 + CLI + HTTP（:8787），SQLite schema v7 | Node 桥 + DSH 宿主 + Python RAG sidecar |
| 身份 | `(platform, platform_user_id)` + `scope`，多平台共用一套表 | 按 QQ 号的人物账本（单平台） |
| 原文 | `turn` 表，超 400 轮**硬删** | 按天 JSONL **永久存档**（`state/long-memory/archive/`） |
| 检索 | FTS5 trigram + 多信号打分 + 1 跳关联 + 可选软嵌入 | Chroma 向量 + SQLite FTS5（jieba/BM25）两路 RRF |
| 代谢 | 无（只有排序半衰期） | **FSRS-5 S/D/R + 冻结线 + 解冻** |
| 主动 | `proactive.py`（审计说生产 0 行） | `life-loop.js` 到期提醒 + own-life 忽然想起（有每日/去重配额） |
| 许可 | **专有许可**（PROJECT_STATE 已记） | **MIT** |

### 6.3 值得直接搬的四件
1. **FSRS-5 代谢**（`memory-decay.js`，A，我读了源码 120 行）：
   - `R=(1+F·t/S)^−0.5`，`F=19/81`（t=S 时 R=0.9）；
   - `S0` 按类别定：约定/经历 14 天、事实/性格 7 天、备注 3 天，**主人条目 ×1.5**；消息按显著度+情绪强度 3–30 天；
   - 强化分"顺利/费劲"两档，**1 小时冷却**（重复想起不重复加分）；
   - `R<0.5` 冻结：不再自动冒出来、不注入，但**原文不删**，查到一次解冻；
   - **核心条目与未了结约定不参与冻结**；
   - 实现约束也值得学：只依赖 Node 内置模块、状态只存 ID+数字（不存原文）、无复习记录不写盘、原子写、出错只 log。
   → 这是 AIJiYi P0-2 的**现成规格**（Python 重写约 100 行）。
2. **账本建议（candidate + 采用率）**：提炼话题摘要时顺带抽"关于在场某人、值得长期记的事"，存成候选；
   下次唤醒去掉已有/过期/出现太多次的，按重要性给几条，**记不记由她自己用工具决定**。→ 正好治 AIJiYi
   "反思 52.8% 空转"——把"硬写 trait"换成"给候选 + 记录采用率"。
3. **影子模式**：人格判读先只记录不改回复，跑两三天看判读准不准，再勾"注入"。→ AIJiYi 的 `trait` /
   `reflection` 可以直接沿用这个开关，避免一上线就影响人格。
4. **RAG 总开关 + 失败降级 + 换模型重建**：默认关；关着时桥接根本不去连；连不上自动退回关键词；换 embedding
   模型先"测试连接拿维度"→提示全量重建→重建期间只走关键词、摘要不重新提炼。→ AIJiYi 的 `vectors.py`
   （hash/hybrid + 幂等重建）可以补上"换模型"这条路径。

### 6.4 不要照搬的
- `src/bridge.js` **785KB 单文件**（其余模块 5–95KB）：AIJiYi 已经模块化，别把桥接逻辑塞进核心。
- **Chroma + SQLite 双写**：AIJiYi 的 FTS5 + 内存索引已经够用，引入 Chroma 等于多一个进程和一个一致性面。
- **Linux QQ 3.2.32 版本锁定 / `setcap` / hostpatch**：平台特定坑，与 AIJiYi 无关。
- **依赖 DSH 宿主**：memory-brain 的记忆"醒时"能力大量挂在 DSH 的 hook 上；AIJiYi 是独立中间件，
  只能借它在"宿主内"的分工思想（capture / inject / associate / tools / scheduler 五件套），不能借代码。

### 6.5 对它的风险提示（C）
- 项目自述"记忆服务和一键脚本只在 Linux 上测过"；README 里没有测试数字（只有 `npm run test:*` 脚本清单），
  与 AIJiYi"每项能力都有 evidence 路径"的标准不同——**引用它的结论时要标 B**。
- 它的 RAG 依赖"本机 ≥10GB 显存跑 Qwen3-Embedding-8B"或云端 API；AIJiYi 本机是 CPU 路线，**别指望同款召回质量**。

---

## 7. 证据清单（可复核）

采集时间：2026-11-02 01:16（Asia/Shanghai）。原始 README 全部落盘在
`D:\AIJiYi\.tmp\research\raw\`（工作区内，可直接 grep）；命令输出归档在
`C:\Users\22166\.dsh\evidence\2026-11-01\011610-sleep-ecosystem-readmes-441817\stdout.txt`
（stdout sha256 `0580cb6c483dded21f9adbaa9c99378d44e3194bc958c87ed52a965a1614d99a`）。

| 项目 | 抓到的文件 | 字节 | sha256(前 16) |
|---|---|---:|---|
| liuqingman/dsh-somni | README.md / docs/design.md | 15044 / 14609 | `d87b420d523c812b` |
| xiaogege6697/dream-skill | README.md | 7928 | `27ddbe1914105bee` |
| EvolvingAgentsLabs/sleep-harness | README.md | 6245 | `4cc847f3a6dea79f` |
| ldclabs/anda-brain | README.md | 25044 | `67ff515ab74b22ce` |
| qyiun666/MemHop | README.md | 52442 | `56aadf0965847cca` |
| kolegadev/Katra-Agentic-Memory | README.md | 16024 | `80b737aabf21054f` |
| wyckit/mcp-engram-memory | README.md | 25999 | `e602c67e33e85061` |
| Pseudogiant-xr/Pseudolife-MCP | README.md | 98315 | `be60e8a24b243ee8` |
| mocodainteractive-pixel/agent-sleep | README.md | 4823 | `08ca12465b9aca93` |
| EESIZ/clawdreamer | README.md | 11799 | `dc18a5ac7f3558c9` |
| marc-shade/memory-consolidation | README.md | 3447 | `1a1e6a3e785752e2` |
| stewnight/rem-sleep-skill | README.md | 3985 | `9da5e5c12a2fc7db` |
| smysle/agent-memory | README.md | 14170 | `207453f2280ac19e` |
| microsoft/SkillOpt | README.md | 8734 | `90636bb83040aa9a` |
| letta-ai/letta | README.md | 1477 | `67ec50661bb95db1` |
| y083245/dsh-memory-brain | README.md / docs/FORK_FEATURES.md / src/memory-decay.js | 16354 / 8972 / 12570 | `f3dab586c1468ae8` |

元数据（stars/许可/语言/最后推送）取自 GitHub API，归档在
`C:\Users\22166\.dsh\evidence\2026-11-01\011658-sleep-repo-meta-0eb07f\stdout.txt`
（stdout sha256 `5cb296d0d16501c23a3ce4cf51cfdb087fb62ec291e5903ac6ba6a818f319ad8`；
该次仅取到前 6 个仓库，其余被未认证限流，故本报告不对其余仓库的 star 数下结论）。

论文/博客：
- arXiv `2606.03979`（A，摘要原文）· Google Research 博客同题（A，正文截断）
- `research.google/blog/the-sleep-framework-…` / `letta.com/blog/sleep-time-compute`（C，经 agent-sleep 转引）

---

## 8. 还没验的（别当结论用）

- 我**没有运行**任何候选项目；所有"它怎么工作"都是读 README/源码得到的（A 级是"我读到了"，不是"我跑通了"）。
- Pseudolife 的 LongMemEval、marc-shade 的 Cohen's d、手册 v5 的 Recall@5 27.8→55.6，全部是**项目/作者自述**（B）。
- FSRS-5 参数是否适合"中文 QQ 闲聊事实"未验；AIJiYi 的真流量里 90 天未提的事实占比也未知（先量再定冻结线）。
- 手册 v4/v5 描述的是另一套私有代码（`D:/QQBot`，Node 216 模块），**不是** AIJiYi，数字不可直接平移。
