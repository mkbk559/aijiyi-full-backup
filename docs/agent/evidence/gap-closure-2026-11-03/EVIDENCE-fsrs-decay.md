# 缺口④（DSH 借鉴 #1）：回忆排序改用 FSRS-5 曲线（2026-11-03）

## 先说一个发现：FSRS-5 **早就实现了**，缺的是"排序没接它"

`store.py` 里已经有完整的 FSRS-5 状态：
- `fact.stability` / `fact.difficulty`（schema v11 就有，初始值按 kind 分档：fact 7 天、episodic 14 天、relation 30 天）
- `store.retrievability()`：`R=(1+F·t/S)^-0.5`，`F=19/81`（`params.FSRS_F`），管**门禁/冻结**
- `bump_usage()`：每被引用一次把 stability 涨一截（封顶 `STABILITY_MAX_DAYS=3650`），解冻再 ×1.2

但 `recall.score_fact` 的"排序新近度"一直用的是**按事实类型写死的半衰期**（`exp(-t/half_life)`），
跟"这条被想起过几次"完全无关。也就是说：**同一套 FSRS 状态量，门禁在用，排序没用**。

我第一版写了个 `aiji/hippocampus/fsrs.py` 想"移植 FSRS"——写完之后发现 `store.retrievability` 就是它，
于是**把新模块删了**，改成让 `recall` 直接读 `fact.stability`（单一曲线常数，不重复实现）。

## 改了什么

| 位置 | 改动 |
|---|---|
| `aiji/right_hand/params.py` | 新增 `RECALL_DECAY_MODE = "stability"`（可选 `half_life` 回退） |
| `aiji/hippocampus/recall.py` | 新增 `_recency_for_fact()`：`stability` 口径走 `(1+F·t/S)^-0.5`，S=`fact.stability`；`score_fact` 增加可选 `decay_mode` 参数，默认跟 params |

## 证据（真库 + 合成对照）

`tools/archcheck/fsrs_ab.py` → `tools/archcheck/results/fsrs_ab.json`：

不变量（全部成立）：

| 断言 | 结果 |
|---|---|
| `R(t=0) = 1` | 1.0 |
| `R(t=S) = 0.9` | 0.9 |
| 新的赢旧的（1 天 vs 365 天） | true |
| **365 天旧事实：被引用过 > 从没被引用** | **true**（stability 口径） |
| 旧指数口径对"用没用过"无感 | **true**（两条新近度完全相同——这就是缺口本身） |

合成对照（365 天龄）：

| 事实 | half_life 新近度 | stability 新近度 | half_life 总分 | stability 总分 |
|---|---|---|---|---|
| 从没被引用（S=7） | 0.0147 | 0.4895 | 0.6362 | 0.5812 |
| 被引用 10 次（S=28） | 0.0147 | **0.4964** | 0.6362 | **0.7215** |

**真库抽样（`data/aiji.db` 最近 400 条 active 事实）**：stability 中位数 **7.0 天**、最小值 7.0、最大值 14.0；
400 条里只有 **1 条** `hit_count>0`。所以两种口径的分差中位数只有 **0.0073**，`|Δ|>0.01` 的条数为 **0**。

→ 诚实结论：**这个改动在数学上是决定性的，但在当前数据上几乎不改变排序**，因为系统还没被真正用起来
（几乎没有事实被引用过，stability 全是初始值）。它的价值在于"用起来之后排序不会继续无视使用频率"。

配套：`bench/bench_memory_quality.py` 六项判据仍 `ok=true`（reconcile 6/6、排序 top1 1.0、联想召回 hit_rate 1.0）；
新增 `tests/test_decay_mode.py` 6 条（曲线形状、单调、核心效应、旧口径反证、默认口径、分数单调）。
全量单测 **484/484 ok**。

## 待办（记进 excluded，免得下次重走）

真正的 FSRS-5 还要 `S' = f(S, D, R)` 迭代 + `D` 更新（现在 stability 是"乘一个增益"，difficulty 存着但没人用）。
这需要把 `bump_usage` 换成完整的 FSRS 更新式，并把 `difficulty` 接进公式——本轮**没做**，
而且当前数据量下做了也测不出差别。
