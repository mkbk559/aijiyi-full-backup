# 缺口⑤：生命卡引擎接线 / 能量经济学（2026-11-03）

结论先说：**能源经济学是真的（有账本、有闸门）；"七引擎"里 2 个是死代码、1 个没接线。**
并且我顺手改掉了图里一处**错误**说法（原写"PersonalityKernel.apply_delta 的调用方只有测试"）。

## 机器判据（可复现）

`python tools/archcheck/wiring_audit.py` → `tools/archcheck/results/wiring_audit.txt`
（AST 级，三条判据：LifeCard 字段组合 / LifeCard 方法内真调用 / life.py 之外的外部调用点；
连模块级 helper（`save_life_card`/`load_life_card`）都算上，避免"只匹配类名"的假阴性）

| 引擎 | 组合进 LifeCard | LifeCard 内调用 | 外部调用点 | 判决 |
|---|---|---|---|---|
| `LifeCard` | — | — | **8**（`memory.py:708` 起） | 在链路上 |
| `EndocrineEngine` | ✔ | `perturb`/`regress`/`endo_factor` | **3**（`being_checkup.py`） | 在链路上 |
| `PersonalityKernel` | ✔ | `apply_delta` | 0 | 仅 LifeCard 内部 |
| `OfflineCompensation` | ✔ | `compress_days` | 0 | 仅 LifeCard 内部 |
| `ContextBudget` | — | `context_plan()` 里直接构造 | 0 | 仅 LifeCard 内部（只经 `/api/life/context` 暴露） |
| `DeclarativeEngine` | ✔ | **无** | 0 | 组合了但从没被调用 |
| `ProceduralEngine` | ✔ | **无** | 0 | 组合了但从没被调用 |
| `SynapticDiffusion` | ✘ | **无** | 0 | 完全未接线 |

## 生命卡真的会影响回复（不止"存着好看"）

`aiji/brain/head.py:240-250`：

```python
base_items = int(self.memory.settings.top_k)
if life_snapshot:
    factor = float(life_snapshot.get("endo_factor", 1.0))
    base_items = max(3, min(12, round(base_items * max(0.65, factor))))
injection = self.memory.inject(query, ident, session=session, max_items=base_items, mode="chat")
```

→ 激素状态（皮质醇高 → `endo_factor` 低）会**直接减少注入的记忆条数**。
所以"生命卡 → 提示词"这条线是通的，不是纸面承诺。

已有回归测试：`tests/test_life.py::test_event_inference_and_memory_integration`
（一次 `comfort` 事件后 `hormones.oxy` 与 `personality.warmth` 都必须增长）＋
`test_head_uses_life_card_event_and_exposes_snapshot`（Head 会把 life 快照带进回复对象）。

## 能量经济学：账本、闸门、生理都在

`aiji/endocrine/being.py`：

| 机制 | 实现 |
|---|---|
| 睡眠/清醒收支 | 睡（<8 点或 ≥23 点或 `asleep_until`）：`energy += 3.0/h`；醒着：`energy -= 0.8/h` |
| 每条消息的成本 | `energy -= 0.02 + 0.03*强度`（注释记录了历史教训：早期 0.5~1.3 会让普通群聊两小时内把一整天体力耗光、主动搭话永久瘫痪） |
| 力竭强制休息 | `energy <= 8` → `asleep_until = now + 8h` |
| 主动搭话闸门 | `can_proactive()`：`energy>=20 and social>=15 and 8<=h<23 and now>=asleep_until` |
| 活动选择 | 体力 <28 直接改成睡觉/静息；压力 ≥60 只允许发呆/喝茶/听歌类活动 |
| 影响行为 | 体力进 `HumanPacer`（打字延迟）、进 `endo_factor`（注入条数） |

→ 结论：**"能量经济学"不需要新建，需要的是别再把它当缺口。** 真正缺的是"睡眠/清醒的判定为了可测性用了固定钟点（<8 点 / ≥23 点），没有跟随用户时区或作息"——这是产品级偏差，不是代码缺陷，记在这里。

## 这轮实际改了什么

1. 新增 `tools/archcheck/wiring_audit.py`（可复现的接线审计，三条判据都写在脚本 docstring 里）。
2. 架构图 `lifecard` 节点改成上述实测结论；**改掉一处错误**：原文说 `PersonalityKernel.apply_delta` 只被测试调用，
   实际它在 `LifeCard.apply_event` 里每次情绪事件都跑（`memory.life_event` → `card.apply_event`）。
3. 图里 `lifeloop` 节点保持原样（我一开始把引擎结论误塞进去了，已撤回）。

## 没做的（明确记为待办）

- `DeclarativeEngine` / `ProceduralEngine`（约 100 行）：只在测试里活着。要不要删由产品决定——
  它们描述的是"记忆调度/技能熟练度"的一套独立模型，而 hippocampus 里已有真实现，
  如果要删，应该连 `tests/test_life.py` 的 `TestDeclarative`/`TestProcedural`/`TestSynaptic` 一起删。
- `SynapticDiffusion`：三跳扩散，完全没接。若要用，落点应该是 `recall` 的 2 跳联想（现在就 2 跳且预算封顶 6）。
