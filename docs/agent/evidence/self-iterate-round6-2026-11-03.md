# 自我迭代第 6 轮（2026-11-03 16:35–17:05）：并发下的 p99 卡在 500ms

这一轮不再看覆盖率，改**量并发**。之前所有"快"的结论都是单发测的：
`route_smoke` 一次一发、点击一次量一次。并发一上，问题立刻露出来。

## 一、现象：三个端点 p99 都卡在 ~516ms

8 线程 × 10 发，混合打 `/api/self` 与 `/api/memories`：

```
read_self:     n=120 p50=10.4 p95=196.7 p99=513.1 max=1035.1 ms
read_memories: n= 80 p50= 7.3 p95= 51.6 p99=515.8 max= 516.8 ms
write_ingest:  n= 40 p50=10.1 p95= 24.3 p99=516.0 max= 516.0 ms
错误数: 0
```

三个完全不同的端点 p99 一模一样（≈516），说明**不是端点自己慢**，是共享资源被独占。
再单线程顺序打 200 发：`p50=26.3 p95=30.2 p99=33.5 max=42.0 ms`，一次都没超 150ms。
→ 单发没事、一并发就整段 500ms 台阶。

## 二、定位：socketserver 的 backlog 只有 5

并发梯度（每线程 5 发，改之前）：

```
threads=  1  p50= 21.6 p95=  26.0 max=  42.0 ms
threads=  2  p50= 13.7 p95=  27.2
threads=  4  p50=  9.2 p95=  29.5
threads=  8  p50=  4.7 p95= 522.1   ← 突然跳
threads= 16  p50=  7.1 p95= 531.1 max=1028.8
threads= 32  p50= 18.4 p95=1514.4 max=2023.6
```

4 线程还好、8 线程整段 522ms，而且平台是 ~500ms 的整数倍（1035、2023）——
这正是 **Windows 的 SYN 重传间隔**。`socketserver.TCPServer.request_queue_size`
默认 **5**，突发超过 5 个连接时 listen backlog 溢出，多出来的连接要等客户端重传。

## 三、修法与复测

`AijiHTTPServer.request_queue_size = 128`（控制台一屏会并发拉好几个接口）。

改之后同口径：

```
threads=  8  p50=  6.4 p95=  15.7 max=  16.3 ms   （原 522.1）
threads= 16  p50=208.1 p95=1080.9                 ← 见下
threads= 32  p50= 75.8 p95=1505.4
```

8 线程的台阶没了。16/32 还慢，但那次测量用的**是同一个 Python 进程开 16 个线程**
当客户端——客户端自己在抢 GIL。换成**多进程客户端**（每进程 5 发）重测：

```
procs=  4  n= 20  p50= 14.3 p95=  67.7 max=  67.8 ms
procs=  8  n= 40  p50= 25.3 p95=  56.8 max=  79.0
procs= 16  n= 80  p50= 11.9 p95=  92.1 max= 101.2
procs= 32  n=160  p50= 72.5 p95= 293.9 max= 424.7
```

16 并发 p95 = 92ms、32 并发 p95 = 294ms，是**平滑退化**，没有 500ms 台阶了。
结论要写成两条：服务端修的是 backlog；16/32 那组数字里有一半是**测量工具**造成的。

## 四、顺手：GitHub 非 JSON 的报错

`GitHubClient._get_json` 直接 `json.loads(body)`。限流页/代理错误页是 200 + HTML，
抛出来是 `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`，
看不出是限流还是代码错。改成 `RuntimeError("GitHub 返回的不是 JSON（'<html>...'）")`，
把响应开头 80 字符带出来。`tests/test_mcp_backoff.py` 加 2 条钉住
（非 JSON / JSON 但不是对象）。

## 五、验证

- 全量 `run_suite` → **765/765 ok**（`suite_v33.json`）
- `ruff check aiji tools tests` 全过
- 新增回归：`tests/test_server_errors.py::ListenBacklogTest`（backlog 必须 ≥64）
- 真机：改后重启服务，`/api/self` 单发 p50 15–26ms；8 线程 p95 15.7ms

## 六、下一轮候选

1. 写路径并发：这次 write 只 40 发（p99 516 全是 backlog 台阶），现在要重测
   真正的写并发 p99（SQLite 串行是设计如此，得知道数字）。
2. `song_service` 62% / `voice_service` 75% 剩余分支（假 `_run` 验命令拼装）。
3. `admin.py` 70%、`social_service.py` 77%、`onebot.py` 78%。
4. 控制台"点按钮 → DOM 变化"的自动化还没有。
