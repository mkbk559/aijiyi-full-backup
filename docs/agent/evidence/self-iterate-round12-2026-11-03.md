# 自我迭代第 12 轮（2026-11-03 22:25–22:40）：给"串号"这个头号风险上机器网

项目的头号已知风险是**跨身份串数据**（"一次漏写 `platform` 条件就是串号事故"）。
这一轮不靠人眼 review，而是做两件事：一个静态体检工具 + 一套行为级隔离回归。

## 1. 静态体检：`tools/audit_identity_sql.py`

用 AST 把 `aiji/**/*.py` 里所有 SQL 字面量取出来，按"碰不碰身份表、
WHERE 里有没有 `platform/platform_user_id/scope`"分类：

```
总计：no_where 20、filtered_other 83、insert_missing 8、by_id 21、ok 63
```

逐个看过 `insert_missing`（8 条）全是**非身份表**，不是漏：

- `being_state` / `being_event` / `being_note` —— 按 `persona` 存，单人格设计
- `instance` —— 进程级心跳表

`no_where` / `filtered_other` 里绝大多数是**有意的全局维护**：
janitor 清理、growth 的成长计数、`stats()` 的全库统计。
但静态分析的天花板在这里：很多查询是
`f"SELECT ... WHERE {w}" + ident.where()`，条件在**运行时**才拼出来，
工具看不见。所以它的定位是"review 清单"，`--strict` 才当门禁。

## 2. 行为级隔离回归：`tests/test_identity_isolation.py`（13 条）

给 A（`wechat/alice/personal`）和 B（`qq/bob/group-42`）在**同一批表**里各灌
一份带独特标记的数据（`紫罗兰记号甲` / `柠檬黄记号乙`），然后**只以 A 的身份**
调用每个读接口，断言结果里绝不出现 B 的标记：

| API | 验到的东西 |
|---|---|
| `search_facts` / `facts_as_of` | 检索不到别人的事实 |
| `recent_turns` / `turn_count` / `session_turn_ids` | 别人的会话连 id 都摸不到 |
| `recent_scope_turns` | 同平台不同 scope 也不串 |
| `profile_snapshot` / `style_snapshot` / `trait_snapshot` | 画像/风格/性格按身份 |
| `latest_summary` / `summary_by_hash` | 摘要按身份 + 会话 |
| `last_injection_with_feedback` | 注入审计按身份 |
| `last_reflection` | 反思按身份 |
| `media_for` | 媒体引用按身份 |
| `links_for` | 顺着别人的 fact_id 也摸不到边 |
| `stats(ident)` | 统计只数自己的 |

再加两条写侧保证：

- `forget_facts(A, fact_id=B 的事实)` → B 那行 `status` 仍是 `active`
- `bump_usage(A, [B 的事实])` → B 的 `hit_count` 不变

**结论：当前实现 13/13 全绿，身份隔离是守住的。**
这套测试的价值在"以后"——任何新查询漏了身份条件，这里会立刻红。

## 顺手记下的坑

- `FactDraft` 没有 `mentions` 字段（只有 `subject/predicate/object/text/confidence/kind/extra`）
- `upsert_profile(conn, ident, key, value)` —— 是"一个键一条"，不是整份 dict
- `log_reflection` 的 deltas 是 `list[dict]`，不是 dict
- `last_injection_with_feedback` 返回 `(id, item_ids)` 元组，不是 dict
- `recent_turns` 返回 dict 列表（不是 dataclass）

## 验证

- `tests/test_identity_isolation.py` 13/13
- `ruff` 全过
- 全量 suite v42 = 920 ok

## 下一轮候选

1. `social_service.py` 77%、`onebot.py` 78%
2. 控制台按钮级 E2E（点动作 → DOM 变化）
3. 用 `sqlite3.set_trace_callback` 做**运行时** SQL 审计：跑一个纯单身份场景，
   把所有执行过的 SQL 抓下来，凡碰身份表又没带 `platform` 的都列出来 ——
   这能覆盖静态分析看不见的 f-string 条件
