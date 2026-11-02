# 语音链路真机重测（2026-11-03 第二轮）

第一轮我拿 **CPU 环境**量出"RTF 1.6~2.1、必须上 GPU"，结论是错的（见
`voice-engine-decision-2026-11-03.md` 顶部更正）。这一轮全部在真机上重来一遍：
`voice-engine/rvc/.venv`（Python 3.12 + torch 2.7.1+cu128）+ **RTX 3050 Laptop 4 GB**。

原始机器输出：`tools/archcheck/results/{rvc_gpu_bench,rvc_half_bench,rvc_leg_split,voice_chain,voice_http_e2e}.json`。

## 一、模型推理本身（`rvc_half_bench.py`，同一颗素材、GPU 空闲）

| 素材 | fp32 耗时 | fp32 RTF | fp32 显存 | **fp16 耗时** | **fp16 RTF** | **fp16 显存** |
|---|---|---|---|---|---|---|
| 5 s | 588 ms | 0.118 | 1 928 MiB | **444 ms** | **0.089** | **1 202 MiB** |
| 13 s | 993 ms | 0.076 | 3 696 MiB | **682 ms** | **0.052** | **2 262 MiB** |
| 30 s | **59 983 ms** | 1.999 | 3 948 MiB | **23 799 ms** | 0.793 | 3 954 MiB |

两条结论：
1. **半精度必须开**：1.3~2.5 倍速度、显存少 40%（短素材）。已在 `rvc_server.py` 里
   cuda 默认 `is_half=True`，`/health` 会回报 `"is_half": true`。
2. **30 s 是断崖**，不是线性外推：13 s → 682 ms，30 s → 23.8 s（fp32 更是 60 s）。
   两个模式都吃满 4 GB 显存（3 948 / 3 954 MiB）→ 触发换页/回退。
   所以**语音条不该超过 15 s** 才换声（这条我又改回了 15 s，前面按错误数据放开到 60 s 是错的）。

## 二、真机链路（`voice_chain_bench.py`：VoiceRegistry → Edge TTS → rvc_convert → 常驻服务）

同文本跑两遍（原 profile / 清空 postprocess），差值即换声段：

| 用例 | 文本 | 音频时长 | 整链 | TTS | 换声段 |
|---|---|---|---|---|---|
| short | 24 字 | 5.1 s | 10 666 ms | 2 692 ms | 7 974 ms（**含开机后首次调用的预热**） |
| medium | 64 字 | 13.0 s | 5 553 ms | 3 298 ms | **2 255 ms** |
| long | 165 字 | 30.8 s | **2 346 ms** | 2 542 ms | **跳过**（`last_skipped.seconds=30.82, limit=15.0`） |

- 长文本用例证明**护栏真的生效**：30.8 s 素材不换声，直接返回 TTS 原声，
  `last_skipped` 里留了证据，整链从 95 s 降到 2.3 s。
- 开机后第一次调用要 7~8 s（rmvpe + faiss 索引预热），之后 0.7~2.3 s。

## 三、换声段内部拆解（`rvc_leg_split.py`，13 s 素材）

| 环节 | 耗时 |
|---|---|
| 服务端 `/convert`（纯推理，含 faiss 索引重载） | **826 ms** |
| 完整客户端（进程启动 + health + convert） | **1 371 ms** |
| 其中 python 启动 + import | 215 ms |
| 客户端带来的额外开销 | **546 ms** |

→ 换声段里**约 40% 是进程/HTTP 开销**，不是推理。再优化要动两处：客户端常驻化、
`infer/vc/pipeline.py:279` 每次调用都 `faiss.read_index`（111 MB 索引）改成进程内缓存。

## 四、HTTP 接口真机验收（`voice_http_e2e.py`）

```
GET  /v1/voices              -> 200，2 个声线：dafeiyu / edge-default
POST /v1/audio/speech        -> 200，audio/wav，470 444 字节，4.9 s @48 kHz，
                                RIFF/WAVE 头正确，整链 2 539 ms（另一次 6 282 ms，网络抖动）
GET  /v1/voices (after)      -> last_skipped 为 null（说明这条确实换了声）
```

## 五、这轮暴露并修掉的 5 个真问题

1. **我上一步的清理打坏了点歌链路**：`song_service` 的 separator 默认路径
   `voice-engine/.venv/Scripts/audio-separator.exe` 被我删了。
   → 新建 `voice-engine/sep-venv`（874 MB，只装 audio-separator）+ `_default_separator()`
   按 `sep-venv → .venv` 顺序探测。
2. **我之前的安装尝试污染了 GPU 环境**：`uv pip install audio-separator` 在
   `rvc/.venv` 里删掉了 numpy 的 dist-info（`import numpy` 正常但
   `importlib.metadata.version('numpy')` 返回 None），导致 `transformers` 版本检查抛
   `ValueError: … need=1.17 found=None`，服务起不来。
   → `uv pip install --reinstall numpy==1.26.4` 修好。**教训：不要往 rvc/.venv 装东西**。
3. **`rvc_daemon.ps1 -Stop` 停不住**：`/shutdown` 后进程仍活着占显存（两次复现）。
   → 加 pid 文件 + 等 7.5 s + 兜底 `Stop-Process -Force`。
4. **守护脚本会挂住调用方**：`Start-Process` 起的孙进程握住父进程 stdout，
   python `subprocess.run(capture_output=True)` 会一直等到超时（实测 185 s / 600 s）。
   → 改用 `Invoke-CimMethod Win32_Process Create`（不继承句柄），实测 15.4 s 正常返回。
5. **4 GB 卡上不能同时跑两个服务**：并行起两个服务时后者直接 OOM，
   表现为 `ConnectionReset` / `URLError 10061`。→ 基准脚本先 `-Stop` 现存服务。

## 六、还没解决的问题（下一步）

- **预热成本**：开机后第一次换声 7~8 s。可以在 daemon 起来后主动跑一次 1 s 空转预热。
- **长音频（>15 s）**：现在只能跳过。要做就得分块（每块 ≤10 s）转换再拼接，
  否则 30 s 素材 24 s 不可接受。
- **点歌/翻唱链路**：只修好了 separator 路径，**没有端到端跑过一次真歌**（需要联网取伴奏/音乐源）。
- **CPU 兜底没了**：`voice-engine/.venv` 已删，GPU 驱动出问题就只剩"不换声"这一条路。
- `infer/vc/pipeline.py:279` 每次调用重载 faiss 索引（未改上游代码）。
