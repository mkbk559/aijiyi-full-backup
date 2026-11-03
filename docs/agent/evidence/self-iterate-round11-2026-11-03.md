# 自我迭代第 11 轮（2026-11-03 22:15–22:25）：真机全链路 E2E + 修掉"空回复当成功"

这一轮不再堆单测，而是**用真模型、真 HTTP、真 SQLite 跑完整条链**，
顺带抓出一个会计到线上事故的 bug。

## 环境

- 隔离数据目录 `.tmp/e2e11/`（生产 `data/aiji.db` 一个字没动）
- 真本机网关 `http://127.0.0.1:8788/v1`（workbuddy，glm-5.3-flash）
- 真服务进程：`aiji --db .tmp/e2e11/aiji.db serve --port 8899 --no-presence`

## 链路实测（证据文件：`C:\Users\22166\.dsh\evidence\2026-11-03\`）

| 步骤 | 观测 |
|---|---|
| `/api/capabilities` | 200，6 条路由在册（workbuddy / agent2api / pollinations / zhipu-flash / siliconflow / openrouter-free） |
| 第 1 轮对话 | 200，回复 **"蓝色风车！好贵哦，二十七是很多很多的钱。\n\n它转起来会不会很快呀？放我家窗台上能看好久好久。"**（46 字，孩子口吻） |
| turn 落库 | 6 行（3 用户 + 3 助手），`turn.content` 逐字落盘 |
| 事实抽取 | 8 秒后 `fact#1 = user\|购买记录\|在西湖边花27元买了一个蓝色玻璃风车`，`conf=1.0 status=active src=model`（"二十七块钱"被归一律成"27元"） |
| 第 2 轮召回 | 注入 `injection#1`，`item_ids=[1]`，prompt 里带"用户今天在西湖边买了一个蓝色的玻璃风车，花了二十七块钱" + 画像行；回复 **"蓝色的玻璃风车呀！我还记得的哦，花了二十七块。"** |
| 第 3 轮 | 复用同一条记忆，回复"那个蓝色玻璃风车是全世界最好看的！" |
| 时间线 | turn#1 471.271 → fact#1 479.284 → turn#3 489.299（事实先落库，召回在其后） |

也就是说：**聊天 → 人格回复 → 双写 turn → 模型抽取事实 → 下一轮软注入召回**，
这条主链在真机上整条走通了，不是靠 mock 拼出来的。

## 抓到的 bug：200 + 空正文被当成成功

`brain/pool.py::_post` 原来是 `return str(message.get("content") or ""), calls`。
上游返回 `200` 但 `content` 是空串（网关积分耗尽、`max_tokens` 被推理吃光、
流式截断）时，池子把它记成**成功**：不换路由、不重试、不冷却，
`Head` 拿到空串就生成一条空消息发出去 —— 用户端表现是"机器人突然不说话"，
而且日志里一片绿。

**实测**：直接打网关、`max_tokens=20` 时 `content` 就是 `""`。

### 修法

```python
text = str(message.get("content") or "")
if not text.strip() and not calls:
    raise _RouteError("empty_content", retryable=True)
return text, calls
```

空正文且没有 tool_calls → 当成这条路由坏了：换路由 / 重试 / 进冷却；
全部路由都空 → `PoolExhausted` → `Head` 走既有兜底文案 `FALLBACK_TEXT`
（"刚才没接上，等我一下。"）。带 `tool_calls` 的空正文仍然是**正常**响应
（工具调用本来就没有正文）。

### 真机验证（生产进程，重启后）

`POST /v1/chat/completions` 带 `max_tokens: 1`（逼上游只给推理不给正文）：

```
status=200 len=11
reply='刚才没接上，等我一下。'
is_fallback_text: True
empty: False
```

改之前这里会是一个空字符串。另外用正常预算再问一次，回复 `"玛卡巴卡！"`（5 字），
确认修复没有把正常路径也变成兜底。

## 新增测试

`tests/test_pool_resilience.py`（13 条，真 HTTP 假服务器，不走外网）：

1. 空正文 → 重试下一轮拿到正文，`errors` 里能看到 `empty_content`
2. 全部空 → `PoolExhausted`
3. 只有空白字符也算空
4. `tool_calls` + 空正文 = 成功，工具参数被解析
5. 非 JSON / 缺 `choices` → `bad_response`
6. `400 + tools` → **降级重试**且第二次请求体里确实没有 `tools`
7. `401` → 不重试（不烧配额）
8. `500` → 重试后成功
9. 连不上 → `network` 可重试
10. 两条路由：第一条空、第二条答
11. `max_tokens` 透传、`temperature` 默认 0

## 验证

- `tests/test_pool_resilience.py` 13/13
- 全量 suite **v40 = 903 ok**
- 生产服务重启后 `/api/capabilities` 200、`/v1/chat/completions` 正常

## 下一轮候选

1. `social_service.py` 77%、`onebot.py` 78%
2. 控制台按钮级 E2E（点动作 → DOM 变化）
3. `/api/turns`、`/api/stats` 这两个路径在控制台里不存在（本轮 E2E 里 404），
   确认是否有意为之，还是要补只读接口
