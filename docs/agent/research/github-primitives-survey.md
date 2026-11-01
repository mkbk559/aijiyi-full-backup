# GitHub 开源原语对照研究（抄原语，不抄假设）

日期：2026-10-29。目标：为"多实例 + 崩溃窗口 + 全库单写者 + 44 条不变式"找单点可借鉴实现，
确认组合行为确实稀缺。检索方式：GitHub repo search + README 抓取（本轮，2026-10-29）。
原始证据：本文件内引用的仓库均为当轮 API 实抓，非转述。

## 一、逐单点对照

### 1. 事务性 outbox（我们：send_queue/event_log 同事务写入）

| 仓库 | 原语 | 可抄 | 不抄的假设 |
|---|---|---|---|
| [eventuate-tram/eventuate-tram-core](https://github.com/eventuate-tram/eventuate-tram-core) | outbox 学术原点（Richardson） | "消息插入与业务变更同事务，CDC/轮询器异步读出"的表结构分层 | 假设有 CDC（Debezium）或独立轮询进程；我们单文件 SQLite，轮询器就是 Heart |
| [dtm-labs/dtm](https://github.com/dtm-labs/dtm) | 二阶段消息（2-phase msg）模式 | "prepare→submit 两步确认"对应我们 pending→ready 的门控 | 假设有独立 dtm server 进程与 HTTP 回调；我们进程内闭环 |
| [dotnetcore/CAP](https://github.com/dotnetcore/CAP) | 本地消息表 + 失败重试 + 仪表盘 | outbox 表的 `Attempts/ExpiresAt` 字段语义与"已发布但未确认"状态机 | 假设 RabbitMQ/Kafka 落地端；我们 send_queue 就是终态 |

**要点**：三家对"写入即留痕、消费不丢"用同一套结构（业务事务内 INSERT + 异步转发 + 幂等消费）。
我们的 outbox 已经是这个形状；可借鉴的是 **CAP 的字段语义**（expires_at 已有，attempt 上限→dead 已有）。

### 2. SQLite 任务队列 + 租约/崩溃恢复（我们：Heart claim+lease）

| 仓库 | 原语 | 可抄 | 不抄的假设 |
|---|---|---|---|
| [maragudk/goqite](https://github.com/maragudk/goqite) | SQS 语义于 SQLite：`Receive` 带隐藏超时，超时未删自动重投；`Extend` 续期；max_receive_count 上限 | **租约=可见性超时**（不靠 worker 心跳，靠"没删+超时"单字段判定）；我们 LEASE_SECONDS=120 就是这个模型；`Extend` 续期原语我们没实现——长任务可抄 | 它让每条消息有独立重投计数；我们按 task 级 attempt。它假设多队列同表（我们已同构） |
| [mikestefanello/backlite](https://github.com/mikestefanello/backlite)（Go，SQLite） | 优雅停机（Stop 等待 in-flight）、panic recovery 单条隔离、保留期自动清理、无轮询（WAL hook 通知） | **panic recovery 单条隔离**：一条任务 panic 不死 worker——对应我们 heart pump 的 try/except + traceback（已做）；**保留期清理**对应 janitor purge | 它用 driver 级 hook 免轮询（SQLite 无通用 notify，我们 SCAN 轮询是对的）；假设任务类型注册制 |
| [justplainstuff/plainjob](https://github.com/justplainstuff/plainjob)（TS，better-sqlite3/bun） | "Re-queue jobs if a worker dies"（timeout 机制）；完成/失败分窗口清理（7d/30d） | **崩溃重投与成功清理分开计时**——我们 dead-letter 只留死信，成功行无 TTL 策略，可抄双窗口 | 假设单库纯队列；我们库里有业务表，清理必须带身份过滤 |

**要点**：三个独立实现的 SQLite 队列收敛到同一崩溃语义：**claim 即标记 + TTL 超时重投 + 次数上限 + 死信**。
我们的 heart_state 租约模型与 goqite 等价；**缺的是 Extend 续期**和**成功任务的双窗口清理**。

### 3. 数据库队列的进程崩溃接管（supervisor 模式）

| 仓库 | 原语 | 可抄 | 不抄的假设 |
|---|---|---|---|
| [rails/solid_queue](https://github.com/rails/solid_queue) | `claimed_processes` 注册表：worker 启动注册心跳，supervisor 检测死亡后**释放其名下所有被认领任务**；"Jobs interrupted by non-graceful process death" 章节完整 | **按进程粒度批量接管**：我们崩溃恢复是逐条等 LEASE_TTL 过期（600s 停摆窗口）；solid_queue 的"注册表+死亡即批量释放"把恢复时间从 TTL 压到心跳间隔——这是对"多实例崩溃窗口"最有价值的原语 | 假设专职 supervisor 进程；我们可让 Heart 自己承担（每次 pump 先清"心跳过期进程"的名下任务） |

**要点**：solid_queue 是唯一把"非优雅死亡"当一等公民写文档的：**任务认领挂到进程记录上，进程死=批量释放**。
我们的 lease 是逐条独立的，多实例场景恢复慢 10–100 倍。**这是最值得抄的单点**。

### 4. 记忆置信/时间性（我们：fact + confidence + supersede）

| 仓库 | 原语 | 可抄 | 不抄的假设 |
|---|---|---|---|
| [NORTHTEKDevs/genome](https://github.com/NORTHTEKDevs/genome) | 双时间轴（事件时间 vs 记录时间）fact log；写入路径零 LLM（本地 embed），LLM 只在读取/可选抽取；"plan ≠ durable fact"（想搬 Denver 不落事实） | **bi-temporal 两列**（valid_from/recorded_at——我们 supersede 只留单时间）；**"打算/可能"不落事实**对应我们的 negation/备注分层（已做，可加显式 draft 门槛） | 它假设有本地 embedding 模型（all-MiniLM）；我们 HashingEmbedder 同位替换即可 |
| [getzep/graphiti](https://github.com/getzep/graphiti) | fact 带 validity window：何时成立、何时被 superseded；provenance 回溯到 episode | **supersede 的有效期窗口语义**——我们 superseded 行是"过期"，他们是"截至某刻为真"，可回放历史（S4 archive_replay 场景正好需要） | 假设 Neo4j/图库 + LLM 抽取；我们关系表 + 规则抽取，只抄语义不抄存储 |

**要点**：记忆置信的升降（我们的 ×0.7/×1.25）在开源里没有现成同构——**回腾/地板/封顶这层是我们的原创组合**；
能抄的是**时间语义**（bi-temporal、validity window），它们正好支撑 44 条不变式里"被替换的行可回放"这条。

### 5. LLM 网关故障语义（下一迭代：第二层接入）

| 仓库 | 原语 | 可抄 |
|---|---|---|
| [BerriAI/litellm](https://github.com/BerriAI/litellm) | Router 的 cooldown（连续失败 N 次熔断 X 秒）、fallback 链、retry 策略按异常类型分档 | 熔断参数结构（allowed_fails / cooldown_time）直接进 params.py，与 Heart 的 backoff 同构 |

## 二、组合行为确实稀缺（结论）

检索确认：**没有一个仓库同时做**"SQLite 单文件 + 多实例认领 + 崩溃接管 + 记忆置信升降 + 不变式门禁"。
每个单点都有成熟原语，组合层是我们的工作空间。具体稀缺点：

1. **崩溃窗口收敛速度**：开源队列都靠 TTL 重投（goqite/plainjob，秒到分钟级）或独立 supervisor
   （solid_queue）。**进程注册表 + 心跳 + 批量释放**（solid_queue）搬到 Heart 是最干净的收敛路径，
   能把我们的 600s 停摆窗口压到 pump 间隔。
2. **记忆层**：mem0（LLM-per-message）、genome（零 LLM 写入）、graphiti（时序图）三家写入语义各异，
   但**都没有置信度数值升降机制**——我们的地板/斜率/不对称回升在开源里找不到现成对照，
   因此 44 条不变式里这部分**只能靠我们自己的对抗模拟**维持证据。
3. **不变式门禁**：outbox 三家的正确性都押在 CDC/幂等消费约定上，没有"每轮 pump 后 44 条断言硬校验"
   的做法（gate 是我们的原创，且已闭环）。

## 三、落点建议（按性价比排序）

1. **P0 抄 solid_queue 进程注册表**：heart_state 加 `instance_id + heartbeat_at`，
   pump 开头批量接管心跳过期实例的名下租约 → 崩溃恢复从 ≤600s 压到 ≤SCAN 间隔；
   配一条不变式（租约的持有者必须有心跳或已死）。
2. **P1 抄 goqite Extend**：长任务（LLM 第二层摘要）中途续租，防"任务正常但超 120s 被二次认领"。
3. **P2 抄双时间轴**：fact 加 `valid_from`（supersede 时刻即 valid_until），
   支撑 archive_replay（S4）做"当时视角"回放。
4. **抄字段语义不抄进程模型**：CAP 的 expires_at/Attempts 语义已对齐，无需改动。
