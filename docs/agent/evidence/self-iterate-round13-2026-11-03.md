# 自我迭代第 13 轮（2026-11-03 22:35–22:45）：401/404 早退导致客户端 RST

## 怎么发现的

跑全量 suite（v41）时，`tests.test_security_contract` 偶发：

```
ConnectionAbortedError: [WinError 10053] 你的主机中的软件中止了一个已建立的连接。
  tests/test_security_contract.py:107 in test_api_key_protects_management_and_turn_endpoints
    status, _ = self.request("POST", "/turns", payload)
```

单独跑就绿 —— 典型"只在负载/时序下出现"的问题。看代码就能定位：

- `_body()` 里有 8 MB 上限分支，注释写得明明白白：
  "**要把 body 读空再回 413**，直接甩手会让客户端还在写 9 MB 时收到 RST（WinError 10053）"
- 但 `_unauthorized()`（401）恰恰**不读** body，直接 `close_connection = True` 就回响应
- 其它早退（404 等）也不读

同一个坑，413 修过了，401/404 没修。

## A/B 实测（证据归档：`2026-11-03\224221-ab-finish-body-rst-82f531`）

同一台本地服务、同样的 120 KB 请求体、同样的错 key，8 次请求：

| 配置 | 结果 |
|---|---|
| `_finish_body` 关掉（复现旧行为） | **401 × 3、连接中断 × 5**（`ConnectionAbortedError: [WinError 10053]`） |
| `_finish_body` 打开（本次修复） | **401 × 8、连接中断 × 0** |

也就是说：旧代码有 **62.5% 的概率**让客户端拿不到状态码，只拿到一个 RST。
这不是测试洁癖问题 —— 真实调用方（AstrBot/QQ 桥、控制台）在"没配 key / key 过期"时
本来应该看到 401 提示"invalid api_key"，实际看到的是"连接被重置"。

## 修法

新增 `_finish_body()`：把没消费过的请求体读空（复用既有 `_drain`，上限 64 MB），
在 `_send()` / `_send_html()` / `_unauthorized()` 里统一调用；
`_body()` 成功读到 body 时打标 `_body_consumed = True`，避免重复读。

## 回归测试：`tests/test_early_exit_body.py`（4 条，真 HTTP 服务）

1. 带 120 KB body 打到受保护端点、错 key → **401 且能读到 `invalid_api_key`**
2. 带 body 打到不存在的路径 → 能拿到状态码，不是连接中断
3. 9 MB body 打进 `/ingest` → 413 且能读到 `too_large`（既有分支没被破坏）
4. 正常 key + body → 正常响应

> 注：`urllib` 的 header 只能放 latin-1，测试里的错 key 必须是 ASCII
> （第一次写成 `Bearer 错的钥匙` 直接 `UnicodeEncodeError`）。

## 验证

- `tests/test_early_exit_body.py` 4/4
- `tests.test_security_contract`、`tests.test_server_errors` 全绿
- A/B 证据：8/8 vs 3/8
- 全量 suite v42 = 920 ok

## 下一轮候选

1. `social_service.py` 77%、`onebot.py` 78%
2. 控制台按钮级 E2E
3. 运行时 SQL 审计（`set_trace_callback`）补静态分析的盲区
