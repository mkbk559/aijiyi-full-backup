# GitHub 记忆系统调研与落地映射（2026-10-29）

> 目的：为 AIJiYi v0.4「记忆质量」找可借鉴的机制，并记录**只借鉴、不拷贝**。
> 证据来源：直接抓取 GitHub 仓库 README 原文（web_fetch），不依赖二手转述。
> 注意：本机 Python 出网被 SSL 拦；以下都是"设计可借鉴"，不是"已在真实服务上验证"。

## 1. 看了什么

| 项目 | 抓取到的关键机制 | 原文 |
|---|---|---|
| **mem0** | 2026-04 新算法：**单遍 ADD-only 抽取**（不再 UPDATE/DELETE，记忆累积、不覆盖）、**实体链接**跨记忆提升检索、**多信号检索**（semantic + BM25 + entity 融合打分）、**时间感知检索**（当前状态/过去事件/计划分别排序）。旧版是 LLM 二次判断 ADD/UPDATE/DELETE/NOOP。 | [mem0ai/mem0 README](https://github.com/mem0ai/mem0) |
| **Graphiti / Zep** | **双时态事实**：每条事实有 validity window（何时为真、何时被取代）；信息变化时**失效旧事实而不是删除**，可查询任意时点的真相；**episode 溯源**（每个派生事实能回到原始输入）；**混合检索**（embedding + BM25 + 图遍历）；边去重/矛盾消解用 LLM 决策模型。 | [getzep/graphiti README](https://github.com/getzep/graphiti) |
| **A-MEM** | Zettelkasten 式记忆：新记忆生成结构化属性（tags/context/keywords），**自动与历史记忆建立关联**，并做**记忆演化**（用邻居信息更新自己的上下文）；检索时沿关联网络扩散。 | [agiresearch/A-mem README](https://github.com/agiresearch/A-mem) |
| LangMem / Letta | 语义/情景/程序性记忆的分类，以及"热路径 vs 后台"的分工（本次抓取失败，只作背景，不据此下结论）。 | [langchain-ai/langmem](https://github.com/langchain-ai/langmem)、[letta-ai/letta](https://github.com/letta-ai/letta) |

## 2. 选了什么、拒了什么

**采纳（v0.4 已落地）**

1. **ADD-only 优先**：多值属性（偏好/兴趣/备注）累积，不互相覆盖；只有互斥的单值属性（居住地/职业/年龄/邮箱/电话/名字）换值才 supersede。
   - 对应：`aiji/store.py::plan_reconciliation`、`add_fact`。
   - 理由：mem0 新版明确"nothing is overwritten"，对长期记忆更安全——误覆盖比多一条更难发现。
2. **同值合并**：同一个值重复出现 → 置信度取大、`mentions+1`、文本取长，不新增行。
   - 对应：同上 `merge` 分支。
3. **否定失效**：`negated` 草案（"我不再喜欢猫"）把命中的旧事实标 `expired`，不新增正面事实。
   - 对应：`aiji/extract.py::heuristic` 的前缀否定词判断 + `add_fact` 的 `expire` 分支。
4. **双时态有效期**：`valid_from/valid_to`；失效只打时间戳，行不删；`fact_log` 记录每次 add/merge/supersede/expire/noop 的决策与理由。
   - 对应：`aiji/db.py` schema v4、`fact_log`。
5. **多信号排序**：相关度 + 重要度 + 时间衰减（按属性类型给半衰期）+ 置信度 + 反馈/重复提及加成。
   - 对应：`aiji/recall.py::score_fact`。
6. **关联记忆 + 1 跳扩展**：新事实与同身份近邻连边（最多 3 条双向），召回从 top-3 事实扩展邻居并按权重打折。
   - 对应：`aiji/store.py::link_facts/links_for`、`aiji/recall.py::_search_db`。
7. **后台压缩**：超长会话先摘要再淘汰原文（`max_turns_per_session` / `turn_keep`），摘要优先模型、失败退回本地压缩。
   - 对应：`aiji/writer.py::_maybe_summarize/_compact_session/_summarize`。
8. **索引可恢复 + 身份过滤**：启动时从 `fact` 重建语义索引（只收 active），语义命中后再按身份过滤一次。
   - 对应：`aiji/memory.py::_rebuild_index`、`aiji/recall.py::_search_db`。

**没采纳（及原因）**

- **两阶段 LLM 冲突消解（mem0 旧版 ADD/UPDATE/DELETE/NOOP）**：每次写入多一次模型调用；本机无法验证；而且新版 mem0 自己已转向 ADD-only。保留确定性规则，需要更强语义时可在抽取层产出 `negated`/显式操作。
- **图数据库 / 实体归一化管线（Graphiti）**：引入 Neo4j 与实体消歧成本过高，单机 SQLite 的 `fact_link` 已能覆盖"1 跳联想"这一层需求。
- **用 LLM 做边去重与矛盾判定**：同上，离线不可验证；改为"值相同/包含 + 单值谓词表"的确定性判定。
- **跨会话全局向量库（Chroma 等）**：保持零依赖，`HashingEmbedder` 只是弱语义占位；上量再换。

## 3. 与现有约束的冲突处理

- **跨平台隔离**：所有新查询（`links_for`、`semantic` 命中、`evict_turns`）都带 `Ident.where()`；`fact_link` 两侧各过滤一次。
- **100ms 硬预算**：关联扩展放在 worker 线程内、失败静默；实测静默 p99 2.6ms、写压 p99 6.0ms。
- **不阻塞主链路**：新增的模型摘要、关联建边、压缩全在 writer 线程；提交 p99 仍 0.06ms。
- **不覆盖历史**：supersede/expire 只改 `status/valid_to`；`fact_log` 保留决策链。

## 4. 取证

- 单测：61/61（`C:\Users\22166\.dsh\evidence\2026-10-28\005614-unittest-v4-final-f97273\stdout.txt`）
- 召回延迟：静默 p99 2.61ms / 写压 p99 5.99ms（`...\005528-recall-budget-v4-primary-2-f63af6\stdout.txt`）
- HTTP：p99 8.28ms（`...\005541-http-budget-v4-secondary-61d549\stdout.txt`）
- 注入：p99 9.30ms，0 超预算、0 越权（`...\005545-bench-inject-v4-b0c20f\stdout.txt`）
- 记忆质量：冲突消解 6/6、排序 top-1 1.0、关联召回 30/30（`...\005650-bench-memory-quality-2-aa03fc\stdout.txt`）
- 端到端：`...\005549-demo-v4-closed-loop-76dc9d\stdout.txt`

## 5. 还未证的

- 模型摘要、模型抽取在**真实免费池**上的质量与延迟（本机 SSL 出网被拦）。
- `HashingEmbedder` 的"语义"很弱；关联扩展只对字面相近的事实有效。
- 否定识别依赖规则前缀与模型字段，复杂否定会漏。
- `fact_link` 只有 1 跳、每事实最多 3 条边；大规模下的关联质量未评测。
