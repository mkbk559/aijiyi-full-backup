# 缺口①：回复长度硬上限（2026-11-03）

目标清单里的"回复长度硬上限"。**改前是"超长整条丢弃"**：`gate.check_reply` 只在 `len > 500` 时返回
`ok=False / reason=too_long`，调用方一律换成一句兜底话；`params.QQ_MAX_REPLY_CHARS = 200` 声明了群聊上限
**但全仓没有任何地方用它**，模型侧也没有 `max_tokens` 上限——模型想吐多长就吐多长，付完钱再被丢。

## 改成了什么

双层硬上限，各管一头：

| 层 | 位置 | 行为 |
|---|---|---|
| 生成侧 | `aiji/brain/head.py` | 调模型时 `max_tokens` 一律夹到 `params.MAX_REPLY_TOKENS = 400`；调用方传更大也只取 400。池子签名不收 `max_tokens` 就不塞（免得打崩测试假池子） |
| 出口侧 | `aiji/brain/gate.py::fit_reply` | 按平台裁字符：IM（qq/onebot/wechat/wecom/lark/telegram/discord）**200**，其它 **500**。优先切在句末（。！？；…），不劈开 emoji 的 ZWJ 序列、不劈开 `[CQ:…]` / `【…】`，末尾补 `…` |
| 接线 | `head.py` / `heart.py` / `proactive.py` | 三条出口路径统一 `fit_reply(...)` → `check_reply(...)`：**超长改裁，不再整条丢** |

`params.py`：新增 `MAX_REPLY_TOKENS`、`IM_REPLY_CHAR_LIMIT`；`QQ_MAX_REPLY_CHARS` 保留为别名（指向同一个值，避免两处各写一份）。

## 证据

| 检查 | 结果 |
|---|---|
| 新增单测 | `tests/test_reply_limit.py` **12 条全过**（裁剪不越界、句末落点、无标点硬切、emoji ZWJ、CQ 码、平台上限、裁完能过闸门、Head 三条、Heart 一条） |
| 全量单测 | **464/464 ok**（`Counter({'ok': 464})`，`tools/archcheck/results/suite_after.json`） |
| 端到端 | QQ 身份 + 900 字回复 → `send_queue.payload` 里 `text` 长度 ≤ 200 且以 `…` 结尾；`max_tokens` 实测被夹到 400 |
| 架构图 | `gate` 节点补上"双保险"说明；DOM 断言 23/23、文字溢出 0 处 |

## 踩到的坑（记下来，别再踩）

1. **第一版直接无条件塞 `max_tokens`** → 6 个用例炸在 `SequencePool.chat() got an unexpected keyword argument`。
   测试里的假池子签名很窄。改成先 `inspect.signature` 探支持度（和已有的 `supports_tools` 同一套写法）。
2. **`send_queue` 没有 `text` 列**，正文在 `payload` 的 JSON 里（`{"text": ...}`）。断言要 `json.loads` 后量长度，
   别拿整条 payload 长度当回复长度（会多出 11 字节 JSON 开销）。
