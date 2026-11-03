# 2026-11-03 审计 10 项：逐条处置与证据

用户原话：「全部修改，顺序你决定，成长绝对不能靠真实时间，事件全部按照真实时间，
还有就是主动性也要按真实时间，成长就按学习阶段来，按照人的小学，初中，高中，大学，
工作的阶段，学习到哪，成长到哪」。

下面是我定的顺序（先止血、再做结构、最后收尾）与每一项的**可复现证据**。

| # | 审计项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 成长靠真实时间（年龄曲线 / `aging_rate`） | ✅ 重写为学分账本 | `docs/agent/design/growth-v2-2026-11-03.md`、`tests/test_growth.py` 35 条 |
| 2 | 换人格失忆（`being_*` 按 persona 分区） | ✅ 新 persona 继承关系状态与最近事件 | `tests/test_being_persona_switch.py` 3 条 |
| 3 | 慢接口：`checkup/live` 32.5 s、`stickers/update` 27 s | ✅ 异步 + 过期缓存先返回 | `tests/test_console_api.py::test_checkup_live_is_cached_and_refreshable`、`tests/test_openai_api.py::test_sticker_library_update_endpoint` |
| 4 | `media` 5 690 行非 ok | ✅ 新增 `aiji media-gc`，删掉 2 008 行无文字的失败行 | 见下方"media 前后对比" |
| 5 | `song_service._persist` 线程崩溃 | ✅ 目录自愈 + 落盘失败不再打死队列 | `tests/test_song_service.py`（含被它掩盖的竞态测试） |
| 6 | `aiji/senses/cli.py` 0% 覆盖 | ✅ 新增 `tests/test_cli.py` 19 条（连 argparse 一起测） | `python -m unittest tests.test_cli` |
| 7 | 仓库体积（备份 300 MB / 临时目录 / 死脚本） | ✅ 回收 ≈ 484 MB，归档 10 个一次性脚本 | 见下方"体积" |
| 8 | 20 份文档还在写"大肥鱼/鱼缸" | ✅ 14 份加"设定已废弃"横幅（证据类文档不动） | `tools/mark_legacy_persona_docs.py` |
| 9 | `mkdtemp` 把目录漏进仓库/`%TEMP%` | ✅ 测试改用 `tests/_tempdir.py`，清理工具扫描 `%TEMP%` | 331 → 0（跑完全量后仍为 0） |
| 10 | 成长边界（`age<=60` 上限、阶段卡时间） | ✅ v2 取消时间上限；工作阶段无下一级 | `tests/test_growth.py::StageThresholdTest` |

## 3. 慢接口：改法与取舍

- `POST /api/being/checkup/live`：克隆整库 + 真模型探测，实测 33.8 s。
  现在**有缓存就先回**（过期也回，带 `"stale": true`），后台起线程刷新；
  只有"从没有过缓存"的第一次会真的等。`{"refresh": true}` 也不阻塞了，
  它只是排一个后台刷新任务（响应里 `"refresh_requested": true`）。
- `POST /api/stickers/update`：默认后台跑、立刻回 `{"started": true}`；
  脚本/自检要同步结果就带 `?wait=1`。

## 4. media 前后对比（`aiji media-gc`，真实库）

```
before: low_confidence 3760 | error 1930 | ok 1870 | timeout 53 | metadata_only 31 | unsupported 25
deletable: 2008   （status ∈ error/timeout/unsupported/empty 且 text 为空）
after:  low_confidence 3760 | ok 1870 | metadata_only 31
```

只删"本来也喂不进抽取层、且没有任何文字"的行；`low_confidence` 有文字，
只是不可信，一行不动。

## 7. 体积（都是机器输出，不是估计）

| 项目 | 前 | 后 |
|---|---|---|
| `data/backups` | 13 份 / 300.8 MB | 3 份 / 115.0 MB（保留最全的清理前快照） |
| `%TEMP%/aiji-*` 测试残留 | 331 个目录 / 82.3 MB | 0 |
| `tools/` 顶层脚本 | 74 个 | 64 个（10 个一次性脚本进 `tools/archive/`，带 README） |
| 合计回收 | | **≈ 484 MB** |

备份保留策略写进 `tools/prune_backups.py`：人格清理的 5 份只留**最早**那份
（最全的全量状态，后面几份都是它的子集）。

## 9. 临时目录：真正的原因不是"忘了删"

`test_life_loop` / `test_style_rewrite` 本来就有 `tearDown` 里 `rmtree(ignore_errors=True)`，
但 Windows 上 SQLite 句柄还没放开，`rmtree` **静默失败**，于是每次跑测试都留一份
`style.db`/`life.db`。现在统一走 `tests/_tempdir.py::make_tmp()`：
重试 8 次（每次 gc + 0.2 s），仍失败就 `atexit` 兜底。
验证：跑完 22 个用例，`%TEMP%/aiji-*` 计数 333 → 333（一个没新增）。

## 顺带发现的"靠 bug 过"的测试

`song_service.test_completed_job_survives_service_restart` 以前能过，是因为
`_persist` 抛 `FileNotFoundError` 把工作线程打死了，测试对 job 的改动没被线程覆盖。
修好 `_persist` 后它才暴露成竞态——现在改成直接造完成态快照，只验"重启能读回成品"。
