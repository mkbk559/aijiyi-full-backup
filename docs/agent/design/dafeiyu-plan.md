> **历史文档（设定已废弃）**：这里的「大肥鱼 / 鱼缸」是 2026-11-03 之前的人设，
> 那一天已整体替换为「玛卡巴卡」（约 6 岁半的小孩，见
> `docs/agent/design/persona-child-2026-11-03.md`），相关记忆与素材均已清空。
> 本文只作当时的决策/研究记录，不要当作现行设定。

# 大肥鱼·AIJiYi 落地方案 v1

> 2026-10-29。依据：DeepSeek 分享对话评审存档（`docs/agent/research/dafeiyu-architecture-notes.md`）。
> 原则：AIJiYi 已有记忆层不推倒；心脏/出站/人格闸门/纠错溯源/清理五块**增量**落地。
> 铁律沿用：全库单写者、读写分离预算、软删除不物理删、失败可观测、变更可回滚。

## 1. 总体结构

```
外界(QQ/B站/抖音/面板)
   │
   ├─[响应事件]──▶ ①outbox ──▶ ❤️心脏 ──▶ 头(召回→约束→生成→校验→闸门) ──▶ 出站队列 ──▶ 嘴
   │
   └─[采集/日志]────────────────────────────────────────▶ ingest ──▶ ⑤单写者 ──▶ 记忆

反馈环：记忆+事件日志+纠正事件 → 反思Worker → ④审核 → ⑤单写者 → 写回记忆
清理环：记忆 → 清理Worker(低频+水位触发) → ⑤单写者 → 写回记忆（软删除）
```

### 1.1 触发三线（分离，不共用定时器）

| 线 | 实现 | 延迟 | 随机性 |
|---|---|---|---|
| 唤醒 | 事件到达→内存优先级队列→condition 唤醒心脏 | 毫秒级确定 | 无 |
| 兜底扫描 | 独立线程，1–2s 固定 + 抖动，扫 outbox pending 超时 | 秒级 | 抖动仅防多实例同步 |
| 主动计时 | 独立沉默计时器（分钟级），带退避 | 分钟级 | 无 |

重任务（反思/清理/采集）独立低频定时器 + 水位触发，绝不跟心跳。

### 1.2 outbox（心脏入口）

- 表：`outbox(id, scope_type, scope_key, kind, priority, payload, idem_key UNIQUE, status, next_retry_at, lease_until, created_at, updated_at)`
- 状态机：`pending → processing(CAS+租约) → done / failed(可重试) / dead(死信)`
- 索引：`(status, next_retry_at)`；心跳扫描只走索引。
- 优先级：`@/私聊/纠错(0) > 主动(1) > 采集(2) > 清理(3)`；同 scope 合并窗口 3s；同 scope 保序串行；老化：低优先级等待 >10min 提一级，**上限提到优先级 1（不高于主动线），防止低优任务淹没 @/私聊**。
- **同步写路径（关键决策）**：outbox/heart_state/send_queue 的领取、CAS、预扣是**同步语义**操作，不能走异步 Writer（提交即返回、无法知道结果）。方案：Writer 增加 `submit_sync(job, timeout)` 接口（内部 Event 等待完成标志），或为调度层开**独立的同步写连接**（仍受全局写锁串行）。心跳兜底扫描的批量领取走批处理事务，减少锁竞争。二选一在 heart.py 实现时定，默认前者。
- 冷启动：pending 限速回放（≤10/s）。
- **合并语义（轮1补）**：合并只发生在内存队列消费侧——窗口内同 scope 事件折成一个组合事件；outbox 逐条落盘但共享 `group_id`，done/failed 按 group 批量标。
- **恢复语义（轮1补）**：崩溃重启后短期缓冲从 `event_log` 回放最近 ≤100 条，不为空。
- **（轮2补）同 scope 生成互斥**：同 scope 头生成串行；等待中的事件若在合并窗口内则并入当前生成输入，否则等生成完基于结果重新召回再生成。
- **（轮2补）主动可撤回**：send_queue 支持按 scope+kind 撤回未发送消息（status=cancelled，进看板）。
- **（轮2补）留痕不阻塞发送**：injection 留痕走异步低优写；崩溃丢失的留痕从 send_queue payload 重建。
- **（轮2补）召回降级**：预算内等召回；超时只用短期缓冲+画像（degraded 机制），不阻塞生成。
- **（轮2补）额度口径**：主动"每日上限"用**滚动 24h**（存最近 3 次送达时间戳）；冷却/静默才用 day_bucket。时间戳用 wall clock 持久化（跨重启必需）；防 NTP 回拨在**比较逻辑**里做（时间戳倒退时取 max，不产生负窗口），不依赖单调钟——单调钟不跨进程重启，与持久化需求冲突。
- **（轮2补）额度计数语义（定稿）**：**预扣 + 失败退还**——触发即预扣写 heart_state，发送失败/被拦截时退还。（v1 的"成功才计一次"与"失败退还"矛盾，已统一为此语义。）
- **（轮2补）乐观锁上限**：纠错/反思/清理写冲突重试 ≤3 次，超限进死信看板。
- **（轮2补）清理单实例**：清理 Worker 经 heart_state 租约（process_id+lease_until）抢锁，抢不到跳过本轮。

### 1.3 会话短期缓冲（修“上下文缺失”洞）

- 每个 scope 维护**最近事件窗口**（内存 deque + 落盘 `event_log` 双写）。
- 头组装上下文 = 短期缓冲（当前对话）∥ 记忆召回（持久），**并行**取，两者都过预算。

### 1.4 出站发送队列（修“done≠送达”洞）

- 表：`send_queue(id, scope, priority, payload, send_idem_key UNIQUE, status, next_retry_at, lease_until, created_at)`
- outbox 在**送达确认**后才 done；发送失败重试是独立触发线；限流感知退避；流式/秒回可选。
- **（轮1补）send_queue 同样有 processing 租约**（lease_until），超时回收 pending。
- **（轮1补）限流接口**：适配器实现 `rate_info() -> {remaining, reset_at}`，send_queue 据此算退避。

### 1.5 人格闸门（三层）

1. 入口：prompt 软注入（已有 inject）。
2. 出口：黑名单/长度/敏感词硬校验，fail-closed；拦截→看板。
3. 闸门：适配器发送前查外发权限；静默期**只拦主动**不拦@。

### 1.6 主动搭话

- 触发前检查顺序：静默→冷却→额度→**再查抢占**（@在场则取消）→生成→发送。
- 计数规则：**预扣 + 失败退还**（语义定稿见 1.2 轮2补）。
- 无人回应指数退避；scope 三层（群/用户/全局）键落 heart_state。
- **（轮1补）主动发送成功 → 重置该 scope 沉默计时器**，防止立刻再触发。

### 1.7 纠错溯源（修“无法定位下调”洞）

- 生成时把**引用的记忆 ID** 写进 injection 留痕（已有 item_ids）。
- 独立纠错提取器：识别“你错了”→ 打包(原回复, 纠正内容, 引用记忆ID) → 反馈环。
- 下调规则：来源加权 × 多次确认 × 时间衰减，不单次覆盖；产物过 ④ 审核。
- **（轮1补）区分 injected_ids 与 cited_ids**：纠错只下调 cited（调用方回报）；无 cited 时按文本相似降级定位，且下调幅度减半。落点：`injection` 表加 `cited_ids` 列（feedback 回执时写入）；降级定位的比对对象 = injection 留痕的 prompt + item_ids 原文（生成时快照存 send_queue payload，防原文被淘汰后无法比对）。

### 1.8 清理 Worker

- 独立低频（每小时）+ 水位触发（L1 条数超阈值）。
- 职责：降层归档、过期、去重合并、confidence 衰减、软删除。
- 与反思并发冲突：按记忆行乐观锁（version 列）；召回过滤 `deleted/expired/superseded`。
- **（轮1补）采集可带 `flag:mentionable`**：满足条件时由心脏生成一条低优先级响应事件进 outbox，而不是直接走头——采集/响应不是绝对二分。

### 1.9 表结构（新增，全部走 ⑤ 单写者）

```
outbox(id, scope_type, scope_key, kind, priority, payload, idem_key UNIQUE,
       status, next_retry_at, lease_until, created_at, updated_at)
send_queue(id, scope_type, scope_key, priority, payload, send_idem_key UNIQUE,
           status, next_retry_at, lease_until, created_at)   -- lease_until 必须有：1.4 轮1补的租约回收依赖它
heart_state(scope_type, scope_key, state_key, value, day_bucket, updated_at,
            PRIMARY KEY(scope_type, scope_key, state_key, day_bucket))
  -- state_key 两类：day-scoped 键（冷却/静默，带 day_bucket，保留 2 天）与
  -- persistent 键（如 recent_send_ts 滚动窗口，day_bucket 固定 0，跨天不清）
event_log(id, scope_type, scope_key, kind, payload, ts)          -- 短期缓冲落盘；按天清理，回放必须带 Ident.where
fact(+version INTEGER DEFAULT 1)                                  -- 乐观锁并入 fact 表加列；UPDATE ... WHERE id=? AND version=?
                                                                  -- 不建独立 memory_version 表（跨表无法原子 CAS）
```

### 1.10 验收总则

触发不丢 · 状态不归零 · 约束有牙齿 · 产物先审后生效 · 写库不打架 · 主动不刷屏 · 失败可观测 · 变更可回滚。
+ **（轮4补）外部内容不可信**：采集入库默认 untrusted，注入层隔离在参考块，永不出现在指令位。

## 1.11 轮4/轮5 模拟补丁（对抗面 + 失败经济学面）

**安全与数据面（轮4）**

1. **提示注入隔离**：`fact` 增加 `origin`（user|external）+ `untrusted` 标记。采集入库默认 external/untrusted；注入层把 untrusted 内容**限制在"参考资料"块**，绝不进指令位/人格位；入口剥离指令式句式（"忽略…"、"你必须…"类）；出口闸门兜底。
2. **scope 响应节流**：同 scope 滚动 60s 最多 N 次生成，超出合并为一次批量回复（合并窗口 + 节流双层）。
3. **幂等粒度统一**：send_idem_key = (scope, group_id)；未合并单条 group_id = event_id。
4. **纠错下限与熔断**：confidence 地板 0.15；同来源连续下调 3 次 → 熔断进人工审核。
5. **状态表增长治理**：heart_state 保留 2 天 day_bucket（清理 Worker 顺带删）；event_log 按天分桶保留 7 天，蒸馏后可提前归档。
6. **scope 编码**：`scope_type='ident'`，`scope_key='platform|platform_user_id|scope'` 三段编码；**所有新表查询必须带 Ident.where**（硬约束，写进 excluded 防重走）。

**失败经济学（轮5）**

7. **生成熔断**：连续 5 次模型失败 → 熔断暂停生成，降级话术（"我暂时掉线"）；熔断状态进看板；指数退避避免雪崩。
8. **互斥超时**：同 scope 头生成互斥锁带租约（如 120s），超时释放、任务进死信，不阻塞后续。
9. **秒回独立**：先秒回后补发时，补发不依赖秒回成功（各自幂等）；秒回被限流不影响补发内容。
10. **mentionable 升级条件**：仅当采集内容命中已有记忆关键词 **且** 该 scope 用户近 10 分钟活跃，才降级生成低优先级响应事件。
11. **（轮6补）origin 继承**：反思蒸馏产物继承源数据 origin，蒸馏链不可洗白 untrusted。
12. **（轮6补）熔断抑制主动**：生成熔断期间，主动计时器暂停触发（不生成也不消耗额度），恢复后计时器从当前时刻重新计时。
13. **（轮6补）节流批量回复的 group_id 取首条触发事件的 id**，幂等稳定。
14. **（轮7补，模拟验证）出口去重按事件 ID + TTL 缓存**：不能只比"上一条文本"——中间夹一条不同回复即可绕过窗口（模拟器实测抓到）。采用 hermes-agent 模式：`_seen(text_hash|event_id) → ts` 缓存，TTL 300s、上限 2000 条，拦截位置在出站队列入队前。
15. **（轮7补，模拟验证）写后立刻读的会话轮必须走短期缓冲**：turn 是异步写，提交后立即 inject 召回不到刚说的话（模拟器实测：用户自报姓名后马上问"我叫什么"，答不出）。现有 `writer.submit_turn` 已调 `recaller.note_turn` 进内存缓冲，此洞在真实链路中已闭；心脏实现时**禁止**绕过 note_turn 直写 DB。
16. **（轮7补，上游教训）WAL 模式下读连接可能困在过期快照**：若某次 BEGIN 的 ROLLBACK 被异常路径吞掉，同连接后续读全是旧快照（[TencentDB-Agent-Memory #987](https://github.com/TencentCloud/TencentDB-Agent-Memory/pull/997) 的修法是每次读前发一个无害 ROLLBACK 兜底）。`db.py` 的 `conn()` 读路径若出现"写后读恒空"可按此排查；暂不加防御代码，先记录模式。
17. **（轮8补，模拟实战）防注入必须在写入口拦截**：出口闸门拦不住"已落库的污染"——被污染的 fact 每次召回都会进 prompt。已实现双层：`extract.py::_INJECT_CUES` 入口整段不抽取（heuristic + model 双路径）+ `gate.py` 出口黑名单。模拟器 `tools/sim_platforms.py` 的 `PromptInjector` 角色为常驻回归对抗面（12 断言含注入零进 fact / 回复零越权）。

## 2. 与 AIJiYi 现状的接入点

| 新组件 | 挂在哪里 |
|---|---|
| outbox/heart_state/send_queue/event_log | 现有 `aiji/db.py` 迁移到 schema v5（现状是 v4） |
| 心脏调度 | 新 `aiji/heart.py`；同步语义写走 Writer.submit_sync（见 1.2） |
| 出站队列 | 新 `aiji/dispatch.py` |
| 人格闸门 | 新 `gate.py`（出口硬校验/发送前闸门，职责独立于 inject 的 prompt 软注入） |
| 纠错溯源 | 已落：`extract.detect_correction` 识别 + `store.apply_correction`（cited ×0.6 / 无 cited 降级 ×0.3，地板 0.15）；cited = feedback.used（used=[] 即明确没引用） |
| 清理 Worker | 已落 `aiji/janitor.py`（软删+租约+乐观锁去重）；复用 `store.evict_turns` 思路 |
| 会话缓冲 | 扩展 `recall.py`，`recent_window(scope)` 与 `_search_db` 并行 |
