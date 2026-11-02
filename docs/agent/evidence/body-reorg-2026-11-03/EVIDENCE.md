# aiji/ 按九个人体系统重排（2026-11-03）

依据：`docs/architecture.html` 的 9 层人体框架。改造前的基线提交：`ceb7752 checkpoint: before body-reorg`；
改造提交：`90eeaf7 reorg: aiji/ 按九个人体系统重排`。144 个文件、+901/−591。

## 一句话结论

**55 个模块搬进 9 个子包，449 个用例的逐条结果与改造前完全一致（372 ok + 77 沙箱 tempfile 环境错误，差集为空），CLI / HTTP / wheel 三条入口都实测可用，图里 68 个节点的路径已同步到新位置，文字溢出从 9 处降到 0 处。**

## 新布局

| 层 | 包 | 内容 |
|---|---|---|
| ① 感官 | `aiji/senses/` | `cli.py` |
| ② 咽喉 | `aiji/throat/` | `server.py`、`net_guard.py`、`bridge/{onebot,octop_gateway}.py` |
| ③ 大脑 | `aiji/brain/` | head、debounce、pool、gate、humanize、role_boundary、persona、selfhood、quota |
| ④ 海马体 | `aiji/hippocampus/` | memory、recognize、real_engines、media_audio、extract、reflect、recall、inject、store、vectors、writer、janitor、db |
| ⑤ 内分泌 | `aiji/endocrine/` | being、life、life_loop、being_checkup、proactive、presence、desktop_sense |
| ⑥ 心脏 | `aiji/heart/` | heart、dispatch、social_service、song_service、sticker_service、voice_service |
| ⑦ 左手 | `aiji/left_hand/` | `actions/*`、mcp_client、mcp_research |
| ⑧ 右手 | `aiji/right_hand/` | admin、operations、service_control、params、config、`web/operations.html` |
| ⑨ 骨架 | `aiji/skeleton/` | `paths.py`（ROOT/DATA/CONFIG/TOOLS/BENCH/WEB_DIR 唯一来源） |

## 机器证据

| 检查 | 结果 | 原始输出 |
|---|---|---|
| 语法 | `python -m compileall -q aiji tests tools bench` 无 Error | `~/.dsh/evidence/2026-11-02/004739-reorg-compile-and-import-607f7b/` |
| 导入 | `import aiji` → `Memory in aiji.hippocampus.memory`，版本 0.8.3 | 同上 |
| 全量单测（改造前） | 449 例：372 ok / 77 error（沙箱 tempfile） | `.tmp/archcheck/suite_before.json` |
| 全量单测（改造后） | 449 例：372 ok / 77 error，**差集 0，状态变化 0** | `.tmp/archcheck/suite_after2.json`（比对脚本 `diff_suite.py` → `VERDICT SAME`） |
| CLI | `python -m aiji.senses.cli --help` 列全 17 个子命令 | `~/.dsh/evidence/2026-11-02/005357-reorg-cli-smoke-b9bb56/` |
| HTTP | 起服务后 `GET /` 44,777 B 且含 4 个视图、`/api/operations/services` 200、`/v1/models` 200 | `~/.dsh/evidence/2026-11-02/005830-reorg-http-smoke-96b047/` |
| wheel | `uv build --wheel` 成功；72 个文件含 `aiji/right_hand/web/operations.html`、console 入口 `aiji = aiji.senses.cli:main` | `.tmp/wheel/aiji-0.8.3-py3-none-any.whl` |
| 图-码一致 | 68 个节点 `f` 路径全部指向新位置；CDP 实测文字越界 **0 处**（改造前 9 处） | `~/.dsh/evidence/2026-11-02/010228-archcheck-cdp-after-sync-29073b/` |

## 已知残差

- 77 个 `tempfile` 用例在本沙箱必失败（`chmod 0o700` / 沙箱 TEMP 被拒），改前改后同集合，属环境而非代码。
- `python -m aiji.cli` 旧入口已不再存在（包级模块被搬走）。仓库内 `ensure_console.ps1`、`restart_aiji_field.py`、README 已改；若仓库外还有脚本按旧路径调用，需要同步。
- 外部 AstrBot 插件走 HTTP（`AIJI_URL`），不 import `aiji`，未受影响。

## 复现

```powershell
python tools/reorg_body.py --check     # 只看计划
python tools/reorg_body.py --apply     # 搬 + 改 import + 改路径表达式
python -m compileall -q aiji tests tools bench
python .tmp/archcheck/run_suite.py .tmp/archcheck/suite_after.json
python .tmp/archcheck/diff_suite.py    # 期望 VERDICT SAME
python tools/arch_sync.py              # 架构图路径/数字/副标题同步
python .tmp/archcheck/http_smoke.py    # 起服务 + 直连冒烟
```
