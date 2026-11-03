# 自我迭代第 2 轮（2026-11-03 14:43–15:10）

这一轮的靶子是"**没人量过的地方**"：安全边界、入站上限、SQLite 运行时副产物、覆盖率 0% 的模块。
所有数字都来自落盘输出。

## 一、安全边界（归档 `…/144357-security-probe-21d7d1`）

| 探针 | 结果 | 结论 |
|---|---|---|
| `GET /api/fs?path=../../../../Windows/win.ini` | 403 `workspace_disabled` | 工作区默认关闭，读不到 |
| `POST /api/shell {"cmd": "echo ..."}` | 403 `workspace_disabled` | 同上 |
| `POST /api/growth`（坏 JSON） | 404 | 该路由只有 GET |
| 未知路由 / 错方法 | 404 | 稳定 |
| `POST /ingest` 带 1.4 MB 文本 | **202 收下并排队** | **问题：没有上限** |

工作区默认关着，所以"路径限制"那段逻辑平时根本不会被执行到——它的正确性原先只有人眼。
新增 `tests/test_workspace_confinement.py`（4 条）：开工作区后逐个试
`../../../../Windows/win.ini`、`..\..\..\Windows\win.ini`、`sub/../../..`、`/etc/passwd`、
`C:/Windows/win.ini`、`D:\AIJiYi\README.md` —— 全部被拒（400/403/404，错误码区分
`path_outside_root` / `not_a_directory`），正常读/列目录可用。
`/api/shell` 实测是**三道门**：`AIJI_ENABLE_WORKSPACE=1` + 没设 `AIJI_CONSOLE_SHELL=0`
+ 显式 `AIJI_UNSAFE_SHELL=1`；测试把三道门和 kill switch 都钉住了。

## 二、入站上限（新增 `tests/test_inbound_limits.py`，5 条）

- 请求体 > 8 MB → **413** `payload_too_large`（带 `limit_bytes` / `size_bytes`）；
  关键是**先把 body 读空再回**——直接甩手会让客户端还在写 9 MB 时收到 RST
  （实测 `WinError 10053`，连错误码都拿不到），所以加了 `_drain()`（最多 64 MB）。
- `/ingest`、`/remember`、`/turns` 的单条文本 > 32 768 字符 → 截断，并在响应里如实写
  `{"truncated": true, "original_chars": N}`（不静默丢）。
- 正常文本不受影响；截断后队列不卡（后续请求仍 202）。

## 三、SQLite 运行时副产物（真机数字）

- 生产库 `data/aiji.db` 41.02 MB，**WAL 46.18 MB**（比库还大）。
  `PRAGMA wal_autocheckpoint=1000`（4 MB）只重置写指针、**不缩文件**；
  `PASSIVE` 检查点返回 `(0, 617, 617)`（全部落盘）但文件仍是 46.18 MB；
  `TRUNCATE` 之后 → 0.00 MB。
- 处置：`Janitor._truncate_wal()`（阈值 `params.WAL_TRUNCATE_MB = 32`）——
  **必须用独立连接**：`PRAGMA wal_checkpoint` 不能在事务里跑，写在 `db.tx()` 里会静默失败；
  **必须在 `run()` 的最后**：中间任何一步还攥着读游标时 TRUNCATE 会被判 busy
  （实测放在 `_purge_index` 后面 → WAL 24 MB 一动不动）。
  busy 时记 `wal_busy` 而不是装作没事。新增 `tests/test_wal_truncate.py`（2 条）。
- `data/backups/aiji.db.db-wal` **43.19 MB 且没有配套的 .db** —— 单独一个 WAL 恢复不了任何东西，
  已删；`tools/prune_backups.py` 以后不把 `-wal`/`-shm` 当备份算。
- 顺手清 `data/voices` 下两个空目录（`.work`、`aiji-voice-zkwjr9cq`）。
  `data/backups`：158.2 MB → 115.0 MB（3 份，2 份清理前 + 1 份更早的子集）。

## 四、覆盖率补强（`coverage run --source=aiji`，全量 572 条）

最低的几个模块（修复前）：`desktop_sense.py` **0%**、`actions/gui.py` 33%、
`real_engines.py` 41%、`song_service.py` 47%、`persona.py` 50%、`janitor.py` 53%。

- **`persona.py` 50% → 补 9 条用例**（`tests/test_persona_learned.py`），并当场抓到真 bug：
  `learned_examples()` 处理 dict 例句时只认 `assistant`/`reply`，**漏了 `回应`**——
  于是 `{"用户": "干嘛", "回应": "玩呢"}` 会整条变成
  `"{'用户': '干嘛', '回应': '玩呢'}"` 塞进人设提示词。已修（dict 分支补 `or item.get("回应")`）。
  生产数据里 `examples` 都是空的，所以是**潜伏 bug**，没污染过人设。
- **`desktop_sense.py` 0% → 11 条**（`tests/test_desktop_sense.py`）：正常看桌面、截屏失败要如实写
  `截屏失败：OSError`、`do_ocr=False` 不调 OCR、长文本按 `max_text` 截断、
  什么都没读到要写明、OCR 引擎缺位/抛异常都返回 `("", 0.0)`、`window_titles()` 永不抛、
  `render()` 三种形态。全部用假屏幕/假窗口/假 OCR，不碰真桌面。

## 五、验证

- `tests.test_workspace_confinement` 4 条 OK、`tests.test_inbound_limits` 5 条 OK、
  `tests.test_wal_truncate` 2 条 OK（+ janitor 9 条）、`tests.test_persona_learned` 9 条 OK、
  `tests.test_desktop_sense` 11 条 OK
- 全量：见 `tools/archcheck/results/suite_v25.json`；`ruff check` 全过
- 真机：`/api/growth` 仍 `xp 0.0 / primary`；WAL 截断后 `data/aiji.db-wal` 0.00 MB

## 六、下一轮候选

1. `actions/gui.py`（33%）、`real_engines.py`（41%）、`song_service.py`（47%）继续补；
2. `janitor.py` 的 `_dedup_merge` / `_cap_links` 分支还没被覆盖；
3. 控制台浏览器检查目前只验首屏，能不能验"点一下动作按钮后页面更新"；
4. `data/voices/makabaka` 786 MB 的构成（是不是每段语音都留了中间产物）。
