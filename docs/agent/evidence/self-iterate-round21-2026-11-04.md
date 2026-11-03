# 自我迭代第 21 轮（2026-11-04 00:20–00:50）：表情包服务 100% + 成长账本并发写（真 bug，A/B 117→0）

本轮按"影响排序"挑了三件事：一处**并发写文件**的真实缺陷、一个**死代码背后的产品行为**、
以及一套可复跑的审计工具。

## 1. 成长账本 `growth.save()` 并发写（最高影响）

### 怎么发现的

不是靠读代码，是靠**审计的不稳定测试**：`test_being_checkup` 在 v49、v51 两次全量跑里
都失败，v50 通过。第 20 轮我给它加了诊断（把整个 `report` 塞进断言消息），这轮一跑就看见了：

```
"reply": "PermissionError: [WinError 32] 另一个程序正在使用此文件，进程无法访问。:
          '…\\data\\temp\\being-checkup-cugrhdmk\\persona\\growth.tmp' -> '…\\growth.json'"
"route": "error", "degraded": true
```

### 根因

`aiji/brain/growth.py::Growth.save()` 是**固定临时名 + 直接 replace + 无锁无重试**：

```python
tmp = self.path.with_suffix(".tmp")      # 所有人共用一个 growth.tmp
tmp.write_text(...)
tmp.replace(self.path)
```

而 `POST /api/being/checkup/live` 会开 **3 个线程**，各自克隆一份库、各跑一次 `Head.reply`，
每条线都会结算成长账本。Windows 上并发替换同一个目标文件直接
`WinError 32`/`Errno 13`。更糟的是 `Head.reply` 把异常当**回复文本**返回（`degraded=True`），
于是体检报告里三个探针有两个"答非所问"——**这是产品级误报**，不是测试问题。

### 修法

- 临时名带 pid + uuid：`.<name>.<pid>.<8hex>.tmp`（跨进程不撞）
- 读改写整体加进程内锁 `_SAVE_LOCK`
- `os.replace` 遇占用重试 6 次（20ms 递增），最终失败则删临时文件并抛出——
  **不吞异常**：账本写不进去必须让上层知道
- 失败时原账本保持完好（旧内容不被截断）

### A/B 原始输出（已归档）

`tools/growth_save_ab.py`：同一份负载（8 线程 × 15 次保存）分别打旧写法与新写法。

归档 `C:\Users\22166\.dsh\evidence\2026-11-03\003126-growth-save-ab-a59cfc\stdout.txt`：

```
[old] 固定临时名 + 无锁: errors=117
   PermissionError: [WinError 32] 另一个程序正在使用此文件…: '…\growth.tmp' -> '…\growth.json'
[new] 唯一临时名 + 锁 + 重试: errors=0
new 落盘完整 JSON: True
残留临时文件 old=[] new=[]
```

120 次保存里旧写法失败 117 次、新写法 0 次。

### 回归用例

`tests/test_growth_save_race.py`（5 条）：8 线程并行保存不抛错且落盘是完整 JSON、
`os.replace` 前两次抛 `PermissionError` 时第 3 次成功、始终占用时清理临时文件并抛、
临时名每次不同且带 pid、旧 schema 迁移一次。
另外连跑 5 次 `tests/test_being_checkup`：5/5 OK（原来是 3 次里红 2 次）。

## 2. `sticker_service.py` 86% → 100%，并修掉一个"死代码背后"的行为

新增 `tests/test_sticker_edges.py`（44 条）：URL 守卫（私网/环回/链路本地/保留/凭据/DNS 失败）、
下载上限（`Content-Length` 与 `read(max+1)` 双保险、重定向目标**二次**校验）、
魔术字节判类型、失败快照落盘、`import` 同名不同字节加哈希后缀、
更新循环（首轮延迟里叫停、失败计数、没有 `snapshot` 的 service）。

写用例时撞出一个真问题：feed 里写 `{"file": "图"}`（没有扩展名）时，
`_safe_filename` **无条件**要求图片后缀 → 整轮更新直接抛 `invalid_sticker_filename`；
而后面那段"按 `_image_suffix` 补后缀"的代码**永远走不到**（死代码）。
修法：`_safe_filename(..., require_suffix=False)` 给 feed 路径用，
拿到字节后用魔术字节补后缀，那段补正逻辑这才活了。用例
`test_missing_suffix_is_replaced_by_detected_format` 钉住它。

顺带确认的语义（写进用例）：feed 没配置时**不写**失败快照（配置错误 ≠ 运行失败）。

## 3. 审计工具：吞异常与遗留标记

新增 `tools/audit_swallowed.py`（AST 扫描）：
- 找 `except ...: pass / continue / return 空值` 这类**静默吞异常**
- 区分 `裸 except Exception`（危险）与窄类型（多数是有意的兜底）
- handler 内任何注释都算"写了理由"
- 顺手修掉第一版的误报：`group:xxx` 里的 `xxx` 曾被算成 `XXX` 待办

当前基线（归档 `…\003246-audit-swallowed-e2e5a9\stdout.txt`，脚本升级后重跑同口径）：

```
静默吞异常 52 处；没写理由的 36 处（其中裸 except Exception 的 0 处）
TODO/FIXME/XXX/HACK/占位 标记 0 处
```

裸 except 全部带理由（0 处无理由），剩下 36 处都是窄类型兜底（如"JSON 解析失败就当没有"）。
逐条看过一遍，只有两处会**悄悄降级质量**，已改：

- `heart/dispatch.py::_platform_backoff`：`rate_info()` 抛错时**静默不退避**（等于关掉限流保护），
  现在进程内告警一次
- `hippocampus/writer.py`：模型摘要失败退回启发式压缩，以前纯 `pass`，
  现在计入 `stats.summary_fallback`（`snapshot` 里能看见降级次数）

## 验证（两条独立路径）

| 路径 | 结果 |
|---|---|
| `run_suite.py`（覆盖率壳内） | v52：**1203 ok / 0 errors / 0 failures**；整包 93%（10015 语句 / 685 未覆盖） |
| `unittest`（直接跑，不经 run_suite） | 本轮新增/改动用例：sticker 49、growth race 5、being_checkup 5 连跑、writer/dispatch 相关全绿 |
| 覆盖率 | `sticker_service.py` 100%（294 语句） |

## 本轮留下的边界

- 36 处窄类型静默兜底没有逐一加注释：审计的 `--strict` 只对**裸 except** 设门禁（现为 0）
- `test_being_checkup` 单跑 ~9 秒（3 个探针各克隆一份库），属于可接受的慢用例
- 下一轮候选：`extract.py` 86%、`real_engines.py` 88%、`server.py` 85%；控制台按钮级 E2E
