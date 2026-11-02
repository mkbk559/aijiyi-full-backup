# 缺口③：心脏层接管发送（2026-11-03）

## 先纠正一条我自己的错账

上一轮我在架构图 `dispatch` 节点写了"本仓库只把消息落到 send_queue，真正投递在 AstrBot 插件"。
**这句话是错的**。核对代码后：`Memory.__init__` 就给 `Heart` 注入了 `sender=self._heart_send`，
而 `_heart_send` 会真的调 `OneBotClient.send()` 打 `POST {onebot_api}/send_private_msg`。
也就是说"心脏层接管发送"**本来就已经接线**——`send_queue` 里 0 行只是因为当时没有 QQ 流量。
架构图那句已改正（见本轮 diff）。

## 真正的缺口：失败**不分类**

`Heart.dispatch` 只会 `ok: bool`：

```
失败 → attempts+1 → 指数退避 2^n → 到 MAX_ATTEMPTS 进 dead
```

于是 `retcode=1401`（token 失效）、`1404`（目标不存在）、HTTP 401/403 这类**再试一万次也没用**的失败，
要白试 5 次、耗时按 2+4+8+16s 累积，期间还占着这个平台的队头。这是本轮补的东西。

## 改了什么

| 文件 | 改动 |
|---|---|
| `aiji/throat/bridge/onebot.py` | 新增 `SendOutcome(ok, retcode, http_status, message)` 与 `send_ex()`：把 HTTPError / URLError / 非法 JSON / 正常响应**结构化**返回，不再压成 bool。`send()` 保留为 `send_ex().ok` 的兼容壳 |
| `aiji/heart/sender.py`（新） | `classify()` 判决表 + `raise_for()`：OK→True、THROTTLE→`SendThrottled(now+PLATFORM_BLOCK_DELAY)`、FATAL→`SendFatal(...)`、RETRY→False |
| `aiji/heart/heart.py` | 新增 `SendFatal`；`dispatch` 捕获它 → 立刻 `status='dead'`，并往 `event_log` 写一条 `send_fatal`（带 reason/retcode，供人工补发），`stats` 增加 `fatal` |
| `aiji/hippocampus/memory.py` | `_heart_send` 改用 `send_ex` + `raise_for`，分类逻辑统一在一处 |
| `aiji/heart/dispatch.py` | 顺手修掉一个**真 bug**：`_tick` 原来把"任何被推迟的行"都当"平台已恢复"回拉，导致单条消息的 `SendThrottled` 推迟被下一轮抹掉 → 每秒重发一次 429 热循环。改成 `_blocked` 实例记忆：只有上一轮**真的**被平台级限流压住、现在恢复的平台才回拉 |

判决表（`classify`）：

| 输入 | 判决 |
|---|---|
| `ok=True` | OK |
| HTTP 429 | THROTTLE |
| retcode 1200 | THROTTLE |
| HTTP 400/401/403/404 | FATAL |
| retcode 100/102/103/104/1400/1401/1403/1404 | FATAL |
| HTTP 5xx、status=None（连不上）、其它 retcode（含 -1） | RETRY |

判据来自 OneBot 11 错误码约定 + HTTP 语义，**没有真机逐个验证**（本机无可用 QQ 账号），
真机上线后要拿 `send_queue.status='dead'` 的行反查校准。

## 证据

`tests/test_send_classify.py` 10 条全过（真 HTTP + 真 SQLite + 真常驻 Dispatcher 线程）：

| 用例 | 断言 | 结果 |
|---|---|---|
| 判决表 7 条 | ok/429/1200/1401 等各归其类；`raise_for` 抛对异常类型、限流带 `retry_at>now` | ok |
| `test_success_marks_sent_and_hits_onebot` | 假 OneBot 收到 `POST /send_private_msg`，`user_id` 正确，行变 `sent` | ok |
| `test_token_failure_goes_dead_without_retry_storm` | retcode 1401 → `status='dead'`、`attempts==1`、`event_log` 有 `send_fatal` | ok |
| `test_rate_limit_keeps_pending_and_backs_off` | HTTP 429 → `pending`、`next_retry_at` 在未来、`attempts==0`（不消耗重试） | ok |
| `test_single_message_throttle_survives_new_dispatcher`（加在 test_dispatch） | 单条限流推迟不会被新 Dispatcher 抹掉 | ok |

全量：**478/478 ok**（`Counter({'ok': 478})`，`tools/archcheck/results/suite_after.json`）。

## 仍然没证

真实 NapCat / 真实 QQ 号的一次发送（含群聊 4 条/分钟限流）。本轮所有"发送"都发给了假 OneBot，
验的是**分类与落点**，不是"消息真到了 QQ"。
