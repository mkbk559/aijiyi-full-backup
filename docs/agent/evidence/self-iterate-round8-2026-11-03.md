# 自我迭代第 8 轮（2026-11-03 17:35–17:50）：song_service 62% → 94%

这轮把 `heart/song_service.py` 剩下的"真跑 ffmpeg/separator"分支全部用**假 `_run`** 钉住——
不装 ffmpeg、不下载、不跑 separator，但命令拼装、失败转译、下载闸门全都验到。

## 覆盖率

| 模块 | 补测前 | 补测后 | 新增用例 |
|---|---|---|---|
| `heart/song_service.py` | 62% | **94%** | `tests/test_song_commands.py` 30 条 |

未覆盖的 22 行：`_default_separator()` 的路径探测（要真装 audio-separator）、
`_pump` 的 internal_error 兜底、`persist` 失败分支（已有别处覆盖）等。

## 这轮测出的行为（钉住的契约）

1. `_run` 三种失败 → `SongError`：
   - `FileNotFoundError` → `tool_missing`（带上可执行名）
   - `TimeoutExpired` → `tool_timeout`
   - rc≠0 → `tool_failed`，stderr 截尾 1000 字符（3000 字符的 stderr 实测只剩尾部）
2. `_download` 的四道闸：SSRF 校验（不 fetch 就拒绝）、`<4096` 字节、`<html` 开头
   （版权页/登录页）、正常落盘
3. `_http_bytes`：正常分片拼接、超限中止（`download_too_large`）、HTTPError/网络异常
   都包成 `download_failed`
4. `_separate` 只认 `(vocals)` / `(instrumental|other)` 命名的产物，缺人声报
   `separation_failed`；separator 不在 PATH 时用 `shutil.which` 回退
5. `_process` 全流程命令拼装：截取段带 `-ss/-t/pcm_s16le`、成片段带 `-b:a 128k` +
   `afade` 淡入淡出；时长探测 0 → 不跑任何命令直接 `invalid_audio`；
   产物缺失 → `empty_output`
6. `_mix` 的 filter：人声 `volume=1.0`、伴奏 `volume=0.8`、`amix=inputs=2` + `alimiter`
7. `_search_netease`：歌名+歌手拼接、无歌手只留歌名、没结果/非 JSON 都是
   `song_not_found`（不是崩）

## 顺手修的测试坑

- `_download` 第一道闸是**真 DNS 的 SSRF 校验**：断网环境下
  `https://ok.example/...` 直接 `dns_failed`，根本走不到大小/HTML 闸。
  DownloadGuardsTest 的 setUp 把校验 patch 成 no-op，单独的 private_url 用例再关掉
  这个 patch（字面私网 IP 不走 DNS，校验直接拒绝）。
- FakeVoices 没有 `convert_audio`，全流程用例需要一个会写产物的 ConverterVoices。

## 验证

- `tests/test_song_commands.py` 30/30；song 三件套共 61/61
- `ruff` 全过

## 下一轮候选

1. `voice_service.py` 75%（514 语句，最大的一块剩余）
2. `admin.py` 70%、`social_service.py` 77%、`onebot.py` 78%
3. 控制台按钮级 E2E
