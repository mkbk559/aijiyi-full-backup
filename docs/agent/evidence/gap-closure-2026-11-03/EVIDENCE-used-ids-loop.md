# 缺口②：插件回传 used_ids 的反馈闭环（2026-11-03）

**结论先说**：这条闭环**代码里本来就是通的**——本轮的产出不是"实现"，而是**把它端到端证出来**，
并把"哪里留痕、哪里不留"讲清楚（之前没人说得准，容易误判成"没实现"）。

## 链路与落点

```
AstrBot 插件 main.py:2670  _feedback_payload(data)  ← 读响应里的 aiji.injection_id / aiji.used_ids
        └─ POST {AIJI_CORE}/feedback
              └─ server.py:915  → memory.feedback(ident, injection_id, used_ids, helpful)
                    └─ store.apply_feedback()
                         ├─ helpful=True ：hit_count+1、last_used_at=now；
                         │                 若该 fact 曾被 downgrade → confidence ×POS_FACTOR、封顶 POS_CAP
                         ├─ helpful=False：confidence ×NEG_FACTOR、地板 FEEDBACK_FLOOR + fact_log('downgrade')
                         └─ injection.feedback = {"helpful":…, "used":[…], "note":…}
```

**留痕口径（容易踩的判断错误）**：正向反馈且该事实没被下调过时，**不写 `fact_log`**——只 `hit_count+1`。
所以"断言 fact_log 变多"是错的，正确断言是 `fact.hit_count` 增长 + `injection.feedback` 非空。

## 证据

新增 `tests/test_feedback_loop.py`（3 条，真 HTTP + 真 SQLite，上游用假 OpenAI 兼容服务顶替）：

| 用例 | 断言 | 结果 |
|---|---|---|
| `test_response_carries_injection_and_used_ids` | `/v1/chat/completions` 响应里 `aiji.injection_id > 0`、`aiji.used_ids` 非空且都是正整数 | ok |
| `test_feedback_updates_store` | `POST /feedback` 返回 `updated ≥ 1`；被引用 fact 的 `hit_count` 增长；`injection.feedback` 被写上 | ok |
| `test_feedback_on_unused_ids_is_safe` | 回传一个不存在的 fact id（999999）不报错（幂等安全） | ok |

全量：**467/467 ok**（`Counter({'ok': 467})`，`tools/archcheck/results/suite_after.json`）。

## 仍然没证的那一段（诚实边界）

插件侧 `main.py:2670` 只是**读到**了字段并 POST；真正"用户夸了一句 → 插件调用 /feedback"的触发点
在插件的对话流程里，需要真实 QQ 消息才会走。**本轮没有真 IM 账号可用**，所以这一段仍是纸面推理，
不算实测。要真验，需要：发一条消息 → 看 `data/aiji.db` 的 `injection.feedback` 是否出现新时间戳。
