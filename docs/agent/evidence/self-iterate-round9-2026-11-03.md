# 自我迭代第 9 轮（2026-11-03 17:50–18:05）：控制台数据面 70% → 100%

`right_hand/admin.py` 是"把内核表拼成 UI JSON"的只读层。它没有复杂逻辑，
但错的地方都很贵：**身份串号**（A 的快照带出 B 的记忆）和**坏数据把整页打崩**。

## 覆盖率

| 模块 | 补测前 | 补测后 | 新增用例 |
|---|---|---|---|
| `right_hand/admin.py` | 70% | **100%** | `tests/test_admin_snapshots.py` 12 条 |

## 钉住的契约

1. **身份隔离**：`memory_library` 只返回本身份 active 事实；
   用另一个身份插一条"串号检测"，确认它不出现。
2. **坏 JSON 不崩**：`fact.extra` / `send_queue.payload` 是坏字符串时回
   `{}`，不是抛 `JSONDecodeError`。
3. **边必须闭合**：`memory_graph` 只保留两端都在返回节点集里的边——
   否则前端会画出悬空连线（limit=2 时把第三条边裁掉）。
4. **会话过滤**：`recent_turns_snapshot(session=...)` 与不带 session 的
   "全部会话"两种语义都验，且 `extra` 为空串时回 `{}`。
5. **`scheduler_snapshot(ident=None)`** 表示"看全部身份"，与按身份过滤分开测。
6. **`models_snapshot`** 的 active/cooldown 计数：缺 `cooldown_s` 字段视为可用，
   `route` 缺省用 `name` 兜底。
7. **`capabilities_snapshot`** 的字段形状：路由列表只暴露
   `name/model/base_url/key_configured`（**不泄露 api_key 本身**），
   onebot 快照与 settings 合并。

## 测试里踩到的 schema 事实（写进了用例注释）

`outbox.idem_key`、`send_queue.send_idem_key`、`outbox.created_at/updated_at`、
`fact.importance/mentions/hit_count` 都是 **NOT NULL 无默认**，
raw INSERT 少一列直接 `IntegrityError`；`_fact_row` 里的 `or 0` / `or 1`
是给老库和外部行留的防御，测试里只能用"坏 JSON + 空 kind + 0 值"来触达。

## 验证

- `tests/test_admin_snapshots.py` 12/12
- `admin.py` 覆盖率 100%
- `ruff` 全过
- 全量 suite v38 = 812 ok

## 下一轮候选

1. `voice_service.py` 75%（514 语句，剩余最大的一块）
2. `social_service.py` 77%、`onebot.py` 78%
3. 控制台按钮级 E2E（点动作 → DOM 变化）
