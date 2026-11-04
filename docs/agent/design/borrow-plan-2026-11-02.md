# 记忆巩固：全对比与借鉴方案（合并版，2026-11-02）

> **这份文件是"唯一入口"**：把原来的调研报告（16 个开源仓库）、`dsh-memory-brain` 专项、两份手册的
> 内容摘要，与 AIJiYi 的代码基线**合成一份**，读者不需要在几份文件之间跳。
> 三份原始材料保留作证据归档（逐条记录 + hash，不再重复维护结论）：
> `docs/agent/research/sleep-framework-survey-2026-11-02.md`、
> `docs/agent/research/memory-brain-digest-2026-11-02.md`、
> `docs/agent/research/manual-v4-v5-digest-2026-11-02.md`。
>
> **只调研与方案，未改任何代码。**
> 证据等级：**A** = 我读到源码/文档并落盘（§8 有路径与 sha256）；**B** = 项目/作者自述，未复现；
> **C** = 我的推断。所有结论都在 §8 能查到出处。
>
> **2026-11-02 当天已按本文件实施**（阶段 0 + P0 + P1 + P2 全落），实施状态、实测数字与
> 被打回的取舍见 **§9**。§0–§8 保持"决策当时"的样子不追改，按 §9 读现状。

---

## 0. 三句话结论

1. **SLEEP 框架本体用不了**（arXiv 2606.03979 是"改权重+RL 自练"，唯一忠实实现已归档），
   但**它派生出的 harness 级**（不改权重、只做离线巩固）恰好命中 AIJiYi 现在最疼的三处：
   反思 52.8% 空转、turn 原文硬删、只有"排序衰减"没有"记忆代谢"。
2. **真正能落地的只有 4 件 + 3 件锦上添花**（§4），且其中第一件不是写代码，而是**先出三个数字**。
3. **AIJiYi 已经比想象中好**：fact 是软删、有 `hit_count/last_used_at` 字段、有 janitor。
   缺口不是"没有机制"，而是**观测没接上**（`mark_used` 全仓 0 调用方）+ **turn 是真删**。

---

## 1. AIJiYi 基线（代码事实，不是印象）

归档：`C:\Users\22166\.dsh\evidence\2026-11-01\014201-aiji-baseline-facts-087dba\stdout.txt`
（sha256 `f3d12ba5aa94259edf84d514596981d34916f40c1bcb26994d5791cf51f448cf`，exit 0，stderr 空）

| 能力 | 现值 | 出处 |
|---|---|---|
| schema | **v10** | `db.py:34` |
| fact 列 | `kind/confidence/status/importance/mentions/valid_from/valid_to/version/hit_count/last_used_at` | `db.py:487–489, 520–523, 552` |
| 召回打分 | `0.55 相关 + 0.20 重要 + 0.15 新近 + 0.10 置信`（+`mentions` 对数加分） | `recall.py:108` |
| 半衰期 | 名字/职业 365d、偏好/兴趣 60d、备注 45d、默认 **120d**；knowledge 180d、relation 365d、episodic **14d** | `recall.py:37–42,91–98`、`params.py` |
| 召回预算 | **100ms / top_k 8** | `recall.py:126–127` |
| 注入预算 | **700 字 / 12 条** | `config.py:66–67` |
| 摘要节奏 | 每 **20** 轮 | `config.py:60`、`writer.py:402` |
| turn 保留 | **400 / 留 40**，超出 **硬 `DELETE`** | `config.py:63–64`、`store.py:573` |
| 反思 | 每 30 轮 + ≥1800s + 回看 ≤60 条 + 60s 超时 | `params.py`、`reflect.py` |
| 关联 | `LINK_CAP=6`、2 跳、扩展预算 6 | `params.py` |
| fact 清理 | 置信<0.15 删；置信<0.4 且 `hit_count=0` 且 `last_used_at IS NULL` 且 30 天 → `status='deleted'`（**软删**） | `janitor.py:130–146` |
| 纠错 | 地板 0.15、负向 ×0.7、正向 ×1.25、封顶 0.95 | `params.py` |
| **读→强化** | **不存在**：`store.mark_used()`（`store.py:990–996`）**全仓 0 调用方** | 归档 `mark_used callers = 0` |
| 弃答档位 / 事实键 | 无 / 无 | grep 全仓 |

**三条推论**（决定方案形状）：
1. "退役不删"只需改 **`turn`**——`fact` 已经是软删（行还在、可回滚）。
2. 代谢层缺的不是字段，是**输入**：字段早在 v3 就有，但没人写。任何 S/D（稳定性/难度）公式，
   没有 `hit_count/last_used_at` 的更新就是空转。
3. 现有清理**按置信度**清理"低置信猜测"，不按"重要性/使用频率"——所以"90 天没再提的**好**事实"
   既不会被清理，也永远不会被降级。

---

## 2. 来源盘点：26 个候选，逐项判定

### 2.1 权重层（结论：全部不借）

| 来源 | 形态 | 它做什么 | 判定 |
|---|---|---|---|
| Google SLEEP（arXiv 2606.03979） | 论文 | Knowledge Seeding 蒸馏 + RL Dreaming，把 in-context 记忆压进**参数** | **不借**：AIJiYi 不改权重 |
| `EvolvingAgentsLabs/sleep-harness` | Apache-2.0、**ARCHIVED**（A） | 论文忠实实现：Jacobian lens 读 residual stream + LoRA + Colab 微调 | **不可用**（excluded #57） |
| `microsoft/SkillOpt` | 组织项目 | 优化 agent 的 **skill 自演化**（benchmark + 训练） | **无关**（"sleep"指夜间自演化流程，不是记忆） |

### 2.2 harness 级（结论：借方法论与工程常数）

| 来源 | 形态 | 它怎么巩固（关键机制） | 判定 |
|---|---|---|---|
| `mocodainteractive-pixel/agent-sleep` | MIT、skill | 6 模式 `consolidate/check/reconcile/prestudy/rebuild/rollback`；**每个门禁对应一种已知失败**：对原文校验而非摘要、章程钉在写作用域外、TTL+每日重校验、**按触发器重写不按日历**、按 scope 隔离 | **借方法论**（门禁表）、**借 `rebuild` 思想** |
| `liuqingman/dsh-somni` | MIT、DSH 插件 + Python sidecar | 双曲线衰减（semantic 0.1 / episodic 0.25）+ **间隔重复**（跨天 +0.2、当天 +0.05）+ **协作式抢占**（`sleep.cancel` 在工具调用间检查）+ **退役不删**（`score<0.2` → `archived/`）+ idle 15min/最长 6h | **借工程常数与抢占语义**（最值得抄的一份） |
| `xiaogege6697/dream-skill` | MIT、纯文档 | 6 阶段（入睡校准→慢波巩固→REM 关联→稳态瘦身→类淋巴冲洗→髓鞘化+自检）；窄腰接口 `Memory Inventory → Candidate Changes → Reversible Changeset → Evidence Report` | **借输出契约**（巩固作业的产物长什么样） |
| `stewnight/rem-sleep-skill` | 文档 | 复盘清单 | 弱（可忽略） |

### 2.3 独立记忆系统（结论：借机制，不借依赖）

| 来源 | 形态 | 关键机制 | 判定 |
|---|---|---|---|
| `ldclabs/anda-brain` | Apache-2.0、图库 | 三档维护（daydream 21 次 / quick 42 / full 168 且 ≥24h）；**先确定性结算再让模型做认知活**；"**遗忘是可访问性，不是真值**"、"**读从不强化**"；维护后**自检**（拿从没被问过的记忆去搜，搜不到就重编码）；**从不 purge** | **借自检 + "结算先于模型" + 退役不删** |
| `qyiun666/MemHop` | Apache-2.0、Go 单文件 | 六层 L0–L5；Dream pipeline（压缩→索引重建→超图重建→衰减→蒸馏）；**读路径 0 次 LLM/0 embedding**；成本纪律极清楚（写 1 次/轮） | **借成本纪律 + 注入体积收敛**；**不借**它"取消打分检索" |
| `Pseudogiant-xr/Pseudolife-MCP` | Apache-2.0 | 事实皮层 + dream；**LongMemEval 500 题全量**：事实脊 0.416 / naive RAG 0.688 / cascade 0.690@883tok；分题型 multi-session 0.504→**0.474（输）**、temporal 0.526（持平）；作者原话"0.690 vs 0.688 是 1 题，不算赢" | **借结论（重要）**：事实优先≠只留事实 |
| `kolegadev/Katra-Agentic-Memory` | **NOASSERTION**、Docker | Mongo+Redis+MinIO；日/周/月反思生成 insights/自叙事 | **不借依赖**（许可与栈冲突） |
| `wyckit/mcp-engram-memory` | MIT、**C# 5MB** | 图拉普拉斯扩散驱动衰减/巩固/自动连边，63 工具 | **不借**（过重） |
| `smysle/agent-memory` | MIT、SQLite | 写入门禁（语义去重 + 类型化合并 + 冲突检测）+ 语义衰减 + **provenance** + 归档而非删除 + `reflect/reindex` 作业 | **借 provenance 与"归档可恢复"** |
| `EESIZ/clawdreamer` | Apache-2.0 | NREM（聚类→蒸馏→去重→LanceDB）/ REM（冲突三分类 `state_change/different_aspects/unrelated` + 重要性衰减 + 软删）/ Dream Log | **借冲突三分类** |
| `marc-shade/memory-consolidation` | 研究代码 | Cohen's d=2.31（51.6% vs 49.1%），但作者 ablation 结论是"**架构 > 单个巩固特征**" | **借结论**：先有层与时间结构 |
| `letta-ai/letta` | Apache-2.0 | sleep-time compute（后台算记忆）的概念出处；`sleep-time-compute` 仓库只是实验脚本 | **只作概念出处** |

### 2.4 `y083245/dsh-memory-brain`（MIT，同题不同栈的近亲）

QQ 陪伴 + 长期记忆，Node/DSH/Linux；主程序 `src/bridge.js` 约 10,400 行；87 个脚本；记忆 sidecar 是
Python（Chroma + SQLite FTS5 双写）。**借 4 件**：

| 借什么 | 具体形态（A） | 对 AIJiYi 的用处 |
|---|---|---|
| **FSRS-5 代谢** | `R=(1+19/81·t/S)^−0.5`；`S∈[0.5,3650]`；`freezeBelow 0.5` → 冻结不删、**查到即解冻**；`reviewCooldownMs=1h`；核心条目/未了结约定**豁免**；只存 ID+数字 | P0-2 的现成规格（Python 重写约 100 行） |
| **影子模式** | `recall.mode / suggest.mode = off/shadow/on`，"shadow 只记录、不注入" | 新打分/新注入的上线方式（贯穿 P0–P1） |
| **账本建议** | `perWake=3 / maxShows=2 / ttlHours=72`，候选 + **采用率**；记不记由她自己决定 | 替代"反思直接写 trait"，治 52.8% 空转 |
| **摘要哈希 + 遗忘语义** | 摘要挂"块编号 + 原文哈希"（同哈希不调模型）；遗忘用**墓碑** + 逐 key 删 + 索引锁；重建只清 L1、保 L2 | 摘要幂等（P1-1）、显式遗忘（P2-2） |

**不借**：10,400 行单文件 `bridge.js`、Chroma+SQLite 双写、DSH preset/bundle patch、Linux QQ 版本锁。
**风险**：自述"记忆服务只在 Linux 测过"、README 无测试数字、RAG 依赖 ≥10GB 显存或云端 API。

### 2.5 两份手册（v5 = 说明书，v4 = 体检报告）

对象是 `D:/QQBot`（Node 216 模块 / 63,454 行），**不是 AIJiYi**；数字不可平移，只借机制与判据。

**v5 可借（机制）**
- **五层记忆**：会话历史 / 事实 / 画像 / **自我记忆（她自己的事，200 条上限，淘汰最久没用）** / 技能经验；
- **三路 RRF（k=60 等权）+ 事实键加权（KEY_W 1.4 / KEY_POW 3）**：同池测试 **Recall@5 27.8%→55.6%**、
  可达项内 71.4%、MRR .271→.460（已回原文核对：`manual_v5.txt:459`，v5 p15）；
- **弃答四档** `sure/vague/stale/none`（`staleDays=90`），35 题校准 **35/35**；原话"解决最伤人的那种错：
  **自信地编一个答案**"；
- **遗忘规则表**：90 天未更新→当"可能过时"问一句；用户说忘→**立刻删且不留备份**；`/reset` 只清最近 10 轮；
  自我记忆超限→淘汰最久没用。

**v4 可借（方法）**
- **三条口径**：① 动作闸门日志只记决定、不记内容；② **直发点不打回复日志** →"日志里没有 ≠ 没发生"；
  ③ 回复被截 70 字 → 统计的是下限。**这就是"证据纪律"的活教材**；
- **50 题探针**：总分 84%，最严重的是 `clause-cut` **从左往右吃分句**，把算术题答案整段砍掉
  （模型层 10/10、可见层 7/10）；还会"用解释句换结论句"；自曝被裁剪**碰巧**救了一次 → "安全靠裁剪运气，不是守卫"；
- **34 步真跑**：合并转发段没进上下文 → 模型编 27KB PDF；写文档路径只喂 185 tok 裸 prompt →
  "你记得我什么"被写成"我看不到你任何记录"。→ **"绕过主链路的旁路 = 记忆黑洞"**；
- **建议清单格式**：`P0–P3 × 举证（时间戳+日志原文）× 改哪一侧（文件:行）× 判据`，可直接当我们 audit 的模板。

---

## 3. 全对比矩阵（12 维）

| # | 维度 | AIJiYi 现状 | 最佳来源 | 判定 |
|---|---|---|---|---|
| 1 | 巩固触发 | 每 30 轮 + ≥30min，5s 轮询、逐会话 | somni（idle 15m/最长 6h）· 手册 v5（每 6h） | **借**：批处理 |
| 2 | 可抢占 | 单 worker，无让路 | somni（`sleep.cancel`）· agent-sleep（串行化） | **借**（轻量）：有新 turn 跳会话 |
| 3 | 遗忘模型 | 打分半衰期 + 按置信软删 | memory-brain FSRS-5 · somni 双曲线 | **借**：加 S/D + 冻结（不动排序） |
| 4 | 读→强化 | **0 调用方** | somni（跨天 +0.2/当天 +0.05/1h 冷却） | **借**：先只观测 |
| 5 | 淘汰 | turn **硬删**；fact 软删 | somni `archived/` · anda-brain 从不 purge | **借**：只改 turn |
| 6 | 摘要幂等 | 只有 `up_to_turn` | memory-brain 原文哈希 | **借** |
| 7 | 检索融合 | 手工 0.55/0.20/0.15/0.10 | 手册 v5 三路 RRF | **条件借**：shadow + A/B |
| 8 | 事实键 | 无 | 手册 v5 `fact-keys.js` | **借**（P1） |
| 9 | 弃答档位 | 无 | 手册 v5 四档 | **借**（P1，不碰应答） |
| 10 | 裁剪安全 | 500/200 字硬校验，无护栏 | v4 手册 `clause-cut` 事故 | **借**（P0，低成本） |
| 11 | 自检 | 有 bench，无巩固后抽查 | anda-brain self-test | **借**（P2） |
| 12 | 上线方式 | 直接生效 | memory-brain `shadow` | **借**（贯穿） |
| — | 成本/主动/工程实践 | 980 次调用/天；`proactive` 0 行；有 evidence | MemHop 成本纪律 · lifeLoop 配额 · `tests[]/probe-*` | **借**（P2 + 工程习惯） |
| — | 权重层 | — | SLEEP 本体 · sleep-harness · SkillOpt | **不借** |

---

## 4. 借鉴方案（可直接排期）

> 硬顺序：**先能观测 → 再能代谢 → 最后才动排序**。排序是已取证部分（top-1 1.0、p99 2.6ms），
> 和代谢混改就分不清是谁的锅。

### 阶段 0：只出三个数字（不改行为，1 天）

| 项 | 做什么 | 产出 |
|---|---|---|
| 0.1 | `bench/bench_decay_baseline.py`：**90 天未提事实占比**、`hit_count/last_used_at` 分布、按谓词分组 | 定冻结线（0.3 还是 0.5） |
| 0.2 | `bench/bench_paraphrase.py`：自建 **18 条换说法查询**，量现召回（hash/hybrid）的 Recall@5 / MRR | 事实键值不值得做的依据 |
| 0.3 | `bench/probe_mark_used.py`：断言"一次注入后 `hit_count` 不变"（预期：不变） | 把缺口钉成回归 |

验收：三份 stdout 落 `.dsh/evidence/`，数字写进 `PROJECT_STATE.md`。

### P0：代谢与退役（schema v11，不对外改行为）

1. **P0-1 读→强化接上**：`inject` 成功后异步调 `store.mark_used()`（现成实现），**只更新计数，不参与打分**；
   1 小时内重复只算一次。验收：probe 三例（首 +1 / 同小时不 +1 / 跨天 +1）；`bench_recall` p99 不变。
2. **P0-2 稳定性/冻结层**：`fact` 加 `stability/difficulty/frozen`（`last_review` 复用 `last_used_at`，不新加列）；
   `janitor._stale_delete` 从 `status='deleted'` 改为 **`frozen=1`**；`recall` 默认排除 frozen，显式查询可见、
   **查到即解冻**；核心条目/未了结约定豁免。验收：6 例单测 + `EXPLAIN QUERY PLAN` 仍走 `idx_fact_ident`。
3. **P0-3 turn 归档**：`store.py:573` 的 `DELETE FROM turn` 前先写 `turn_archive`（带三列身份条件）+90 天清理。
   验收：压缩后 archive 有行、摘要能指回、CLI `--archived` 可取。
4. **P0-4 巩固批处理**：触发条件改"积压 ≥2 会话 或 空闲 ≥15min"，一次跑多会话、每会话一次调用、有新 turn 让路；
   保留 `up_to_turn` 游标与 `_summary_lock` 原子性。验收：反思写行数/天 与空转率双降；`kill -9` 重跑幂等。

### P1：召回质量（动打分，先 shadow）

| 项 | 借 | 做法 | 验收 |
|---|---|---|---|
| P1-1 | memory-brain | `summary` 加 `source_hash/source_turn_ids`；同哈希不调模型 | 探针计数=0 |
| P1-2 | 手册 v5 | `extract` 出 `keys`（实体/属性/时刻，存 `extra` JSON，不建表）；`recall` 扩展查询 | 18 条换说法 Recall@5 对比阶段 0 |
| P1-3 | 手册 v5 | `settings.rank_mode=weighted|rrf`；shadow 只记录"RRF 会选哪 8 条" | 影子可对齐；开之前 top-1 不掉 |
| P1-4 | 手册 v5 | 注入块每条带 `tier`（`stale`＝`now-last_review>90d`）+ 四句措辞模板 | 四档单测；出现"可能过时"措辞 |

### P2：闭合回路与"敢开"

- **P2-1 巩固后自检**（anda-brain）：20 条间接问法测 top-5，低于阈值触发重编码；
- **P2-2 显式遗忘**（memory-brain 墓碑 + 手册"你说忘就删"）：只对**显式请求**真删 + 审计；
- **P2-3 自我记忆层**（手册 v5 第五层）：`self_memory` ≤200 条、淘汰最久没用，与 `trait` 分开；
- **P2-4 主动配额**（lifeLoop）：`recallPerDay=2`、7 天去重、`quiet[3,8]`、素材白名单，再解决 `proactive.py` 无调用方。

### 明确不做

改权重路线（SLEEP 本体/sleep-harness/SkillOpt）；把 Katra/MemHop/mcp-engram 当依赖；MemHop 的无打分检索；
memory-brain 的巨石 `bridge.js`/Chroma 双写/DSH preset；手册的"90 天真删"与"忘就删不留备份"；手册四档权限。

---

## 5. 冲突检查

| 红线 | 是否触碰 | 处理 |
|---|---|---|
| `submit_turn` p99 0.066ms | P0-1 加一次 UPDATE | 写操作全进 writer 线程 |
| recall p99 2.6ms / 100ms | P0-2 加 WHERE、P1-3 多路 | `EXPLAIN QUERY PLAN` 验收；RRF 只 shadow |
| 注入 0 越权 | 否 | `frozen` 是减法；事实键不跨身份 |
| 跨平台隔离 | **是**（新表） | `turn_archive` 必带三列 + 单测 |
| writer 单线程抢锁 | P0-4 | 模型调用不持锁、按会话让路 |
| schema 迁移 | **是** | 只 `ADD COLUMN`，FTS 不动，`user_version` 递增 |

**排期**：0 → P0-1 → P0-2 → P0-4 → P0-3 → P1(1→4) → P2。
工作量（相对）：阶段 0 新增 3 个 bench；P0 改 8 文件 +12 测试；P1 改 4 文件 +10 测试；P2 +8 测试。

---

## 6. 如果只做一件

**阶段 0**。不改一行业务逻辑，却能把整个方案里最大的未知变成三个数：
90 天未提占比决定冻结线取 0.3 还是 0.5；换说法 Recall@5 决定事实键值不值得做；`mark_used` 探针
证明"读→强化"确实断着。现在这三个数字**一个都没有**。

---

## 7. 被推翻并排除的假设（新增 3 条，已同步 `excluded.md`）

| # | 假设 | 推翻依据 |
|---|---|---|
| 57 | "SLEEP 的开源实现可以直接拿来用" | 论文=改权重；唯一忠实实现要 LoRA+Colab 且已 ARCHIVED |
| 58 | "记忆就该只留事实、原文可以删" | Pseudolife LongMemEval：事实脊 0.416 vs RAG 0.688；multi-session 0.504→0.474 |
| 59 | "换成 MemHop 那种无打分检索更先进" | 它删掉了多信号/embedding；我们已有取证（top-1 1.0），不能拿已证的换未证的 |

---

## 8. 证据清单

| 内容 | 位置 | 校验 |
|---|---|---|
| AIJiYi 基线（常量/字段/调用方计数） | `C:\Users\22166\.dsh\evidence\2026-11-01\014201-aiji-baseline-facts-087dba\stdout.txt` | sha256 `f3d12ba5aa94259edf84d514596981d34916f40c1bcb26994d5791cf51f448cf` |
| 16 个仓库 README 原文 | `.tmp/research/raw/`（16 个文件 + `memory-decay.js` + `design.md`） | 清单与各文件 sha256 见调研报告 §7 |
| 16 个仓库抓取日志 | `C:\Users\22166\.dsh\evidence\2026-11-01\011610-sleep-ecosystem-readmes-441817\stdout.txt` | sha256 `0580cb6c483dded2…` |
| 仓库元数据（stars/许可/语言） | `...\2026-11-01\011658-sleep-repo-meta-0eb07f\stdout.txt` | 仅取到 6 个，其余被匿名限流 → 未取到的不下结论 |
| memory-brain 文档/config | `.tmp/research/mb/`（8 个文件） | 见 memory-brain digest §8 |
| 两份手册原文抽取 | `.tmp/pdf/manual_v4.txt`（1,323 行）/ `manual_v5.txt`（952 行） | 页码对照见 manual digest §5 |

**边界**：没有运行任何候选项目；Pseudolife 基准、手册的 27.8→55.6、Cohen's d 均为 B 级（作者自述）。

---

## 9. 实施状态（2026-11-02 落地，全部已跑通）

> **2026-11-04 修订（第 28 轮审计）**：本节的"已跑通"指**函数与 schema 级**；
> janitor 运行时（周期性执行者）当时**没接线**——`freeze_facts` 唯一调用方在
> janitor.py 里，而 Janitor 类生产零引用（tests 33 处）。已于 2026-11-04 补上：
> server 后台线程（`AIJI_JANITOR` 门，默认开，租约单实例）+ `aiji janitor`
> 手动命令；生产首轮实测 dedup_merged=6 / links_capped=93（CLI 拷贝库与
> 生产线程两条路径数字一致）。教训见 excluded #173。

**结论先行：阶段 0 + P0（4 项）+ P1（4 项）+ P2 的四项主体全部落地；
全量单测 464 条 0 失败**（原 454 条 + 新增 10 条 `tests/test_metabolism.py`）。
两处**按实测数据打回的方案细节**（弃答门、RRF 默认值）见 §9.3。

### 9.1 落地清单

| 方案项 | 落点（文件/行级） | 状态 |
|---|---|---|
| 阶段 0-① 90 天未提占比 | `bench/bench_decay_baseline.py` | ✅ 可跑（生产库 8,173 条事实） |
| 阶段 0-② 换说法 Recall@5 | `bench/bench_paraphrase.py` | ✅ 基线 R@1 0.417 / R@5 0.500 / MRR 0.458 |
| 阶段 0-③ `mark_used` 探针 | `bench/probe_mark_used.py` | ✅ 7/7 PASS（读→强化已闭环） |
| P0-1 读→强化 | `store.bump_usage` + `inject.build(learn=)`（注入事务内） | ✅ 1h 冷却、同日 +5%、跨天 +20% |
| P0-2 稳定性/冻结层 | schema **v11**：`fact.stability/difficulty/frozen`；`store.retrievability/should_freeze/thaw_fact`；`janitor._stale_freeze`；`search_facts(include_frozen=False)` 默认排除 | ✅ 冻结不删、查到即解冻 |
| P0-3 turn 原文归档 | schema v11 `turn_archive` + `store.archive_turns/archived_turns/prune_archive`；`writer._compact_session` 先归档再删；90 天后 janitor 才物理清 | ✅ 退役不删 |
| P0-4 反思批处理 | `reflect.ReflectionWorker._pending/_should_run`（积压 ≥2 会话 或 空闲 ≥15min） | ✅ 单测覆盖 |
| P1-1 摘要哈希复用 | `summary.source_hash/source_turn_ids` + `store.summary_by_hash` + `writer._summary_for` | ✅ 同哈希 0 次模型调用 |
| P1-2 事实键 | `store.fact_keys`（写路径自动补，≤14 键）+ `recall._search_db` 二次匹配 | ✅ |
| P1-3 RRF 开关 | `Settings.rank_mode` = `weighted`(默认)/`rrf`/`rrf_shadow`；`Recaller._fuse_rrf`（k=60） | ✅ 影子先跑 |
| P1-4 弃答档位 | `InjectItem.tier` = sure/vague/stale，进注入标签；`Settings.inject_min_rel` 为整块弃答门（默认关，见 §9.3） | ✅ |
| P2-1 巩固后自检 | `bench/bench_self_test.py`（27 条探针，hit@5/abstain/stale/leak/semantic/short 六项） | ✅ 全过门槛 |
| P2-2 显式遗忘 | `Memory.forget` / `store.forget_facts` / `aiji forget --id/--like` | ✅ 不可见 + `fact_log` 留痕 |
| P2-3 自我记忆 | schema v11 `self_memory` + `store.add_self_memory/list/trim` + `aiji self-memory`，200 条上限 | ✅ |
| P2-4 主动配额 | **已接线**（2026-11-02 续做）：`aiji/brain/quota.py`（`CreditProvider`）+ `Memory.proactive_quota_ok/spend/quota_state` + `head.py` 主动路径闸门 + `Proactive.quota_provider` + `aiji quota [--set N]` | ✅ 见 §9.5 |

### 9.2 实测数字（全部来自归档的机器输出）

| 指标 | 值 | 归档 |
|---|---|---|
| 全量单测 | 464 条 **OK**，225s | `...\2026-11-01\024358-aiji-suite-final-d91db7\stdout.txt`（sha256 `74f0427a…`） |
| **生产库迁移演练** | v10→v11 成功；fact 8,264 / turn 14,012 / summary 575 / injection 985 **行数完全不变**；迁移后仍能召回 | `...\2026-11-01\024357-aiji-migration-drill-40c42b\stdout.txt`（sha256 `e42c26f6…`） |
| 生产库事实 | 8,173 活跃（fact 2800 / episodic 4872 / knowledge 340 / relation 161） | `...\024032-aiji-probe-decay-baseline-6f8ca5\stdout.txt`（sha256 `be39a0b9…`） |
| `never_used` | **100%（8,173/8,173）** —— 印证"读→强化从来没接上" | 同上 |
| 90 天未提占比 | **0%** —— 库龄不足 90 天，**这个数现在测不出来**，冻结线暂用 `FREEZE_BELOW=0.5`（临期约 90 天） | 同上 |
| 换说法召回 | weighted R@1 0.417 / R@5 0.500 / MRR 0.458；RRF **完全相同** | `...\024031-aiji-paraphrase-baseline-b8c1db\stdout.txt`（sha256 `d8f72e1b…`） |
| 自检六项 | hit@5 **1.0** / abstain 0.5 / stale 1.0 / leak **0** / semantic 0.25 / short 0.667 | `...\024019-aiji-selftest-gate-off-e094e6\stdout.txt`（sha256 `19bf72b6…`） |

### 9.3 按实测打回的两处（写进 `excluded.md` #63/#64）

1. **弃答门默认关**（`inject_min_rel=0.0`）。实测开 0.15：abstain 0.5→1.0（好），
   但同时 `short` 0.667→0.333 —— "我住哪"这种只能靠单字命中的真问题被一起吞掉。
   **结论**：词面门槛分不清"噪声"和"短问句"，真正的解法是语义层（hashing 索引不够），
   故保留机制、默认关，把选择留给数据。
2. **RRF 在这套语料上没有差异**（R@1/R@5/MRR 与手工权重完全一致）。
   原因诚实可解释：候选几乎只有 `fact` 一路来源，跨来源融合无从发挥。
   **结论**：机制保留、默认 `weighted`，等接入向量层出现多路候选再开 `rrf`（先 `rrf_shadow` 对比）。

### 9.4 已知未做 / 剩余风险

- **生产库还没迁移**：`data/aiji.db` 仍是 v10，下次 `Memory()` 打开时自动升 v11；
  升级前的演练见 §9.2（行数不变、迁移后可召回）。要回滚就还原备份——v11 只加列/加表，
  旧代码读新库不会报错（会忽略新列）。
- **冻结线仍是先验 0.5**：等库龄过 90 天，用 `bench_decay_baseline.py` 重测"90 天未提占比"再定。
- **`decay_enabled` 只关"读→强化"**，不影响 janitor 冻结（后者由 `params` 决定）。
- **弃答门 / RRF / 自我记忆进块都只在合成语料上验过**，真实流量下未测。

### 9.5 主动配额 = 反代积分（2026-11-02 续做，已接线）

**用户口径**：主动搭话的额度不是本地拍的次数，而是**反代那边剩下的积分**。

#### 实现（`aiji/brain/quota.py`）

```
allowed_24h = min( max_cap, floor(remaining * (1 - reserve) / cost_per_msg) )
```

| 项 | 取法 | 实测 |
|---|---|---|
| `remaining` 剩余积分 | ① `credits_url`（任意网关 JSON，`credits_json_path` 指到数字）→ ② 响应头 `x-credits-remaining` 等 → ③ `data/credits.json`（`aiji quota --set N` 手填，带时间戳）→ ④ `AIJI_CREDITS`/`credits_static` | 两个网关都不吐余额（见 §9.7 excluded #65）→ 实际用 ③ |
| `cost_per_msg` 单条单价 | 网关 `/v1/models` 里该模型的 `credits` 字段（`"x0.5 credits"` 这种写法也认） | **agent2api 实测 0.08/条**（workbuddy 未进入配额链路） |
| `reserve` | 给"用户回话"留 20% | 先验，可配 |
| `max_cap` | `params.PROACTIVE_QUOTA_MAX = 8`/24h（防充值后刷屏） | 先验 |
| 取不到任何数字 | 退回 `daily_cap = 3`（= 今天为止的老行为） | 单测覆盖 |

接线点：`head.py` 主动路径开头闸门（额度不够直接 `finish_reason="skip", reason="proactive_quota"`），
发送成功后 `Memory.proactive_spend()` 记滚动 24h 窗口；`Proactive` 类同时接受 `quota_provider`。

#### 实测（真网关，非 mock）

`python -m aiji.cli quota`（归档 `...\2026-11-01\030055-aiji-suite-with-quota-4c1252` 同批）：

```json
{"remaining": 40.0, "cost": 0.08, "reserve": 0.2, "daily_cap": 3, "max_cap": 8,
 "allowed_24h": 8, "source": "credits:file(0.0h)/cost:models",
 "note": "可用积分 32.00 ÷ 单条 0.08 = 399，再按防刷屏上限 8 截断",
 "used_24h": 0, "left_now": 8, "ok": true}
```

单测：`tests/test_quota.py` **14 条全过**（解析/单价/缓存/网关挂掉/0 积分/文件来源/窗口/自我记忆）。

#### 怎么用（人只需要做一步）

```powershell
python -m aiji.cli quota --set 40     # 把反代 dashboard 上看到的积分填一次
python -m aiji.cli quota              # 看"还能主动几次、依据是什么"
```

填一次之后，系统按单价折算成每日次数；积分降低时次数自动变少，0 积分直接不再主动。
**这是"能落地且不假装"的做法**：没有余额接口就不硬编一个，而是留一条手填通道 + 明确的过期提示（`file(3.2h)`）。

### 9.6 自我记忆进注入块（原先"只进库不进 prompt"，2026-11-02 决定做）

决定：**做，但要克制**——`inject.py` 里加 `kind="self"` 层：
最多 2 条、每条 ≤60 字、**必须与本次查询有 2-gram 重叠**、分数 0.45（低于普通事实）。
不做"人设倾泻"：`我不喜欢被叫老师` 会出现在"你还记得我不喜欢什么称呼吗"，但不会出现在"成都天气如何"。
单测：`test_related_self_memory_enters_block` / `test_unrelated_self_memory_stays_out`。

### 9.7 新增的否定性证据（excluded #65）

**"反代会把剩余积分放在响应头或一个 JSON 接口里，直接读就行"——本机实测推翻。**

| 探测 | 结果 |
|---|---|
| workbuddy `8788` 的 `/v1/models` | 200，模型表带 `credits` 字段（有单价，无余额） |
| workbuddy `/health` | 200，只有账号数/就绪数，**无余额** |
| workbuddy `/dashboard` | 200，但**纯 SPA 壳**：HTML 里 `/api/*` 字面量 0 个（`tools/probe_gateway_credits.py` 实测） |
| workbuddy 真实 chat 响应头 | 仅 `Content-Type/Length/Date/Server/ACAO`，**无 quota 头** |
| agent2api `3065` 的 `/v1/models` | 200，`credits` 字段可读（**单价可用**） |
| agent2api 其它余额路径 | 无（agent2api 侧的 /api/me、/v1/usage、/balance 等全部 404/401） |

→ 结论：**单价能自动读，余额不能**。所以做成"手填 + 自动折算 + 过期可见"，而不是编一个自动余额接口。
探测脚本留在 `tools/probe_gateway_credits.py` / `tools/probe_gateway_headers.py`，换网关时先跑一遍。

