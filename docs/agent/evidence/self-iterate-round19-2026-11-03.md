# 自我迭代第 19 轮（2026-11-04 00:00–00:10）：线上服务其实是旧代码——重启 + A/B 证据

## 起因

第 17 轮修掉了"空白消息也算一条对话"。代码改了、测试绿了、suite 也过了——
但**线上 8787 那个进程还是启动时的那份代码**。控制台是计划任务拉起的常驻进程，
不会因为源码变更自动重载；`ensure_console.ps1` 只在进程消失时补拉。

所以"修好了"这句话在动手查之前是不成立的。先探：

```powershell
Get-NetTCPConnection -LocalPort 8787 -State Listen   # OwningProcess = 444
```

## A/B 证据（两次原始输出都已归档）

新增 `tools/live_probe_turns.py`：读 `/health`、POST 一条纯空白、POST 一条正常文本、
读 `/api/operations/services` 与 `/api/capabilities`。

**重启前**（归档 `C:\Users\22166\.dsh\evidence\2026-11-03\000136-live-probe-before-46e988\stdout.txt`）：

```
GET  /health            -> 200 version=0.8.3
POST /turns  (空白)     -> 202 {"queued": true, "queue_depth": 1}      ← 旧代码：空白照样入队
POST /turns  (正常)     -> 202 {"queued": true, "queue_depth": 1}
GET  /api/operations/services -> 200 ready=4/5 actions_available=True
GET  /api/capabilities  -> 200 bytes=2427
```

重启（`taskkill /PID 444 /F` → `Start-ScheduledTask -TaskName 'AIJiYi Console Watchdog'`，
6 秒后 `/health` 恢复 200）。

**重启后**（归档 `…\000200-live-probe-after-44e27f\stdout.txt` 与 `…\000304-live-probe-after-clean-f62624\stdout.txt`）：

```
GET  /health            -> 200 version=0.8.3
POST /turns  (空白)     -> 400 {"error": "content_required"}           ← 修复生效
POST /turns  (正常)     -> 202 {"queued": true, "queue_depth": 1}
GET  /api/operations/services -> 200 ready=4/5 actions_available=True
GET  /api/capabilities  -> 200 bytes=2427
```

同一个进程、同一个 URL、同一条请求，行为从 202 变 400 —— 这条缺陷在线上确实存在过，
也确实是这次重启才消失的。

## 顺手确认的两件事

- `/api/operations/services` 重启后仍 200，`ready=4/5`、`actions_available=True`：
  第 18 轮那处 `_established()` 挪进 `try` 的改动没有把服务面板弄坏
  （`ready=4/5` 缺的是 onebot——本机没有 QQ 连上来，这是真实状态，不是缺陷）
- `/api/capabilities` 字节数与重启前一致（2427），说明没有配置漂移

## 文档同步

- `docs/agent/PROJECT_STATE.md`：把"怎么验证"里的 `452 tests` 改成当前数字，
  末尾新增"当前数字"一节（1128 条 / 整包 93% / 100% 模块清单 / **重启规程**）
- `docs/agent/work-items/memory-core/status.md`：追加第 12–19 轮汇总
- `excluded.md` #146：改完代码线上不生效这件事写进"勿重走"

## 下一步

线上与代码现在一致了。下一轮继续清剩下的低覆盖模块（`sticker_service.py` 86% 起），
以及控制台按钮级 E2E。
