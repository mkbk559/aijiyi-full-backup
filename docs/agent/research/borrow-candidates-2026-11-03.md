# 可借鉴的开源项目 / 插件清单（2026-11-03）

目标：每个"图里有、代码里弱"的地方，都指一个能直接借的现成实现，并写清**借什么、怎么接、代价**。
不是"调研报告"，是待办清单。

## 1. 心跳层的发送/重试 —— 借 `tenacity` 的重试策略 + OneBot v11 错误码

- **现状**：`aiji/heart/heart.py` 负责 `outbox → pump → gate → send_queue`，`heart_state` 只有 8 行，
  `outbox/send_queue` 全 0 行——**队列的"发出去"这段实际由 AstrBot 插件完成**，本仓库没有真正投递。
- **借什么**：`tenacity`（`retry_if_exception_type` + `wait_exponential_jitter` + `stop_after_delay`）把
  "重试几次、退避多久、什么错不该重试"写成声明式；OneBot 11 的 retcode 语义决定"哪类错误可重试"
  （网络类重试、权限类直接判死），别自己造轮子。
- **怎么接**：`dispatch.py` 的发送函数外面套一层 `tenacity.retry`，把 `SendThrottled` 单独拎出来（它是限流不是故障）。
- **代价**：小。`tenacity` 纯 Python、无依赖。

## 2. 能量经济学 / 配额 —— 借 `litellm` 的 Router（budget + fallback + cooldown）

- **现状**：`aiji/brain/quota.py` 已有"额度 = 反代剩余积分 ÷ 单条单价"，但两个反代都不吐余额（excluded #65），
  余额只能靠人工填；`brain/pool.py` 的冷却/故障转移是自制。
- **借什么**：[LiteLLM Router](https://docs.litellm.ai/docs/proxy/error_reference)
  的 `router_settings`：`routing_strategy`、`cooldown_time`、`fallbacks`、`allowed_fails`、预算字段。
  它的"失败计数 → 冷却 → 换路 → 恢复"四段式已经打磨过很多生产问题，比自己写的冷却表更细（它还区分
  `context_window` 类错误不参与降级）。
- **怎么接**：**不引入 litellm 依赖**（太重），只照抄它的状态机：给 `pool.py` 加 `allowed_fails/cooldown_time/
  fallbacks` 三个参数，行为对齐；`quota.py` 的余额探测改成"能拿到就用，拿不到按调用次数衰减估算"。
- **代价**：中（要写状态机测试）。**收益**：路由诊断口径能直接对齐业界。

## 3. 记忆强度衰减 —— 借 FSRS-5（`fsrs` / `py-fsrs`）

- **现状**：`hippocampus/store.py` 的衰减是"新近度 × 访问次数"的启发式（`bump_usage` + 1h 冷却）。
- **借什么**：FSRS-5 的 DSR 模型（Difficulty / Stability / Retrievability），
  PyPI 包 `fsrs==2.1.0`（[pypi](https://pypi.org/pypi/fsrs/2.1.0/json)、
  [open-spaced-repetition/py-fsrs](https://raw.githubusercontent.com/open-spaced-repetition/py-fsrs/main/README.md)）。
  它把"什么时候该复习"变成可算的 retrievability，而不是拍一个新近度公式。
- **怎么接**：事实表加 `stability/difficulty/last_review`，`recall.py` 排序时用 `R = fsrs.retrievability(...)`；
  写入时用 `S' = fsrs.next_stability(...)`。**只借算法不借调度**（我们不做主动复习推送）。
- **代价**：中。风险：FSRS 是为"卡片"设计的，事实可能被提及但没被"使用"——需要自定义 `review` 的判定（建议用"被注入且被反馈 helpful"）。

## 4. 向量层 —— 借 `sqlite-vec` 换掉暴力余弦

- **现状**：`hippocampus/vectors.py` 的 `HybridIndex` 是 hashing + LLM 软嵌入，**检索是暴力余弦**（excluded 记载）。
  事实量 2.1 万条时暴力还能扛，10 万条就废了。
- **借什么**：[sqlite-vec](https://pypi.org/project/sqlite-vec-win-arm64/)（SQLite 扩展，纯 C，Windows 有轮子）。
  直接在我们的 `aiji.db` 里建 `vec0` 虚表，和现有 FTS5 一起用，**不引入第二个数据库进程**。
- **怎么接**：`vectors.py` 加 `index="vec0"` 分支；建表在 `db.py` 的迁移里（schema v12）。
- **代价**：中。风险：sqlite-vec 的 Windows 轮子还带 `a` 后缀（预发布），要验 `quick_check` 与备份一致性。

## 5. 记忆分层/图 —— 借 `mem0` 的"提取 → 更新 → 去重"三段式与 `Graphiti` 的时间边

- **现状**：冲突消解是确定性规则（未知谓词按多值累积；复杂否定、同义改写会漏），见 excluded。
- **借什么**：[mem0](https://github.com/mem0ai/mem0) 的 `ADD/UPDATE/DELETE/NOOP` 四选一决策（它把
  "这条新事实该不该覆盖旧事实"变成一次结构化输出），以及 Graphiti（Zep）**带时间窗口的边**思路
  （事实有 `valid_from/valid_to`，而不是简单 `active`）。
- **怎么接**：`extract.py` 的反思输出里加一个 `op: add|update|delete|noop` + `target_fact_key`，
  落库前由 `store.py` 做一次确定性校验（保留现有规则做兜底）。
- **代价**：中。风险：多一次 LLM 调用的成本与延迟——建议只对"同 subject+predicate 已存在"的冲突路径启用。

## 6. 生命卡/体检 —— 借 `inspect_ai` / `openai/evals` 的评测骨架

- **现状**：`endocrine/being.py` 的"成精体检"是自写的一组断言，没有数据集与回归基线。
- **借什么**：[inspect_ai](https://inspect.aisi.org.uk/) 的 task/solver/scorer 三件套（或 `openai/evals` 的 yaml 用例），
  把"体检"从"跑一次看看"变成"每次提交都能对比的分数"。
- **怎么接**：把现有断言改写成 `sample → score` 的用例表（人设一致性、记忆召回正确率、口癖命中率），
  `bench/` 里已有 9 个基线脚本可以直接喂进去。
- **代价**：小-中，收益大（有分数才谈得上"像不像"）。

## 7. 睡眠/巩固 —— 借 Generative Agents 的 reflection 循环

- **现状**：`reflect.py` + `janitor.py` 的批处理是工程化的（积压≥2 会话或空闲≥15min 触发），但没有"分层摘要 + 重要性打分"。
- **借什么**：Stanford [Generative Agents](https://arxiv.org/abs/2304.03442) 的三段式：记录 → 反思（importance 打分）
  → 检索（recency × importance × relevance 加权）。它和我们已有的 inject/recall 结构几乎一一对应，只差 importance 维度。
- **怎么接**：`fact` 加 `importance`（反思时打分），`recall.py` 的排序里加一档权重。
- **代价**：小。

## 8. 属性测试 —— 借 `hypothesis` 补"边界不漏"

- **现状**：449 个用例全是手写例子，跨平台串号、预算超时这类**并发/边界**问题只能靠事故发现。
- **借什么**：`hypothesis` 的 `@given(st.text())` + stateful testing，专打 `Ident.where()` 平台过滤、
  `recall` 的 100ms 预算、`check_reply` 的长度裁剪这些"参数空间大"的地方。
- **怎么接**：新目录 `tests/property/`，只覆盖 3 个模块（store 的平台过滤、recall 预算、gate 裁剪），别铺开。
- **代价**：小。风险：hypothesis 会生成 CJK/表情等怪输入，正好是我们要的。

---

**排序建议**（按"收益/代价"）：⑥ 体检评测骨架 → ⑧ 属性测试 → ① 重试策略 → ③ FSRS 衰减 → ④ sqlite-vec → ⑤ mem0 冲突决策 → ② litellm 状态机 → ⑦ importance。
