# 自我迭代第 7 轮（2026-11-03 17:00–17:25）：并发写"不丢"验证 + 队列积压可观测

## 一、并发写不丢：64/64 落库，无丢无重

8 个进程 × 8 条并发 `/ingest`（ref 唯一：`probe7-p<pid>-<i>`），然后 `/flush` + 只读查库：

```
提交：64 条全 202，提交延迟 p50=15.1 p95=47.7 p99=51.4 max=65.8 ms
flush 后：media 行 64 / 去重 64，failed=0，evicted=0
```

**无丢、无重**。写管道在并发下是对的。

## 二、但吞吐是 4.9s/条，且积压不可见

排空跟踪（每 20s 打一次 `/flush`）：

```
[ 20.1s] depth=56 processed=6
[160.3s] depth=30 processed=32
[314.1s] depth=0  processed=64  flushed=True
```

- 单条 ≈4.9s（大头是每条一次模型抽取），单 worker 吞吐 ≈12 条/分；
- 队列 `queue.Queue()` 无界、纯内存；进程被 kill 丢的是**全部 56 条**，
  不是注释里写的"最多丢最后一个"（那条注释已修正，并有测试守着）；
- 整个积压过程日志里一个字都没有——只有手动 `/flush` 才看得到 depth。

处置：`Settings.writer_backlog_warn`（默认 200）+ `Writer._note_backlog`：
越过阈值打 warning（60s 内不重复），回落一半以下提示"积压已消化"并重置闸门。
`tests/test_writer_backlog.py` 5 条（越过/不刷屏/回落提示+重置/安静区/注释诚实性）。

## 三、回归：改后真机复测

重启服务（加载新 writer），64 条并发再压一遍：64/64 全 202，
30s 落库 1 条 → 60s 落库 5 条（≈4.9s/条，与改前一致），队列在消化。

## 四、验证

- 全量 `run_suite`：v34 = 770 用例 **1 fail**（`test_being_checkup.test_live_probes_capture_replies…`，
  单独跑/重跑全量都过——与 v25 同款偶发）；重跑 v35（加了 traceback 落盘后）= 770 全过
- `ruff check aiji tools tests` 全过
- 新增：`tests/test_writer_backlog.py` 5 条
- 顺手：`run_suite.py` 现在**把失败 traceback 存进 JSON**——v34 那次失败
  日志里只有一句 "fail" 没有任何线索，只能整夜守着重跑赌它复现

## 五、下一轮候选

1. `song_service` 62% / `voice_service` 75% 剩余分支（假 `_run` 验命令拼装）。
2. `admin.py` 70%、`social_service.py` 77%、`onebot.py` 78%。
3. 控制台"点按钮 → DOM 变化"自动化。
4. 批量导入场景（>200 条一次进）要不要给 worker 加"积压时降级为启发式抽取"
   ——现在只有群聊 scope 走 `_ambient_backlog`，私聊导入也会积压。
