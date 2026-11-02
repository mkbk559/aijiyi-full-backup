# 语音引擎决策：换什么、多久（2026-11-03）

用户问"这个引擎延迟太高了，换什么"。这里给实测数字 + 结论 + 已经落地的改动。
**结论先说：不换 SVC 引擎；把"TTS + RVC"两段式改成按场景分开走。**

## 一、现在到底多慢（实测，`tools/archcheck/results/{rvc_bench,rvc_f0_bench,tts_bench}.json`）

| 路径 | 素材/文本 | 耗时 | 备注 |
|---|---|---|---|
| RVC 一次性进程（老路径） | 1.5 s 音频 | **14 064 ms** | 每次重载模型 |
| RVC 常驻服务 第 1 次 | 1.5 s | 5 234 ms | 含首次推理预热 |
| RVC 常驻服务 第 2/3 次 | 1.5 s | 2 768 / 2 842 ms | 热调用中位数 **2 892 ms** |
| RVC 常驻服务（6 s 音频） | 6.0 s | **9 519 ms** | RTF **1.60** |
| 同上，换 F0 方法 pm | 6.0 s | 10 278 ms | 没更快 |
| 同上，1.5 s 用 pm | 1.5 s | 3 129 ms | 比 rmvpe 快 4% |
| **纯 TTS：Edge（在线）** | 24 字/约 5 s 语音 | **2 420 ms** | `provider=edge` 真走了在线 |
| **纯 TTS：SAPI（本机离线）** | 同上 | **704 ms** | 零网络 |
| 服务冷启动 → `/health` 可用 | — | **8~18 s** | 模型载入 8.1/9.1/18.1 s 三次实测 |
| 守护脚本冷启动（含轮询粒度） | — | **40.8 s** | `tools/rvc_daemon.ps1` |
| 守护脚本探活（已在跑） | — | **0.8 s** | |

两条从数字里直接读出来的事实：

1. **换 F0 算法没用**：rmvpe vs parselmouth 在 1.5 s 素材上只差 4%，在 6 s 上 pm 反而更慢。
   瓶颈是 Hubert 特征 + 合成器本身，不是音高提取。
2. **CPU 上 RTF 1.6~2.1**：6 s 音频要 9.5 s。这不是"实现不好"，是这颗 CPU 的算力上限。
   任何 SVC（RVC / So-VITS-SVC / Seed-VC）在 CPU 上都是这个量级——**换 SVC 引擎买不到延迟**。

## 二、所以换什么

| 场景 | 现在怎么走 | 决定 |
|---|---|---|
| **聊天语音条** | 代码里本来就没接 RVC（`voice_service` 只被 `song_service` 和 HTTP 用） | **不换引擎**：走单段 TTS。在线 Edge 2.4 s、离线 SAPI 0.7 s；再加时长护栏（见下），超 15 s 不换声 |
| **点歌/翻唱** | RVC（`convert_audio(mode="song")`） | **保留 RVC**，常驻服务已把冷启动消掉；离线任务，9.5 s/6 s 可以等 |
| **要"大肥鱼音色"的语音条** | 现在是"TTS → RVC"两段 | **换架构不换同类引擎**：用**单段零样本克隆 TTS**（GPT-SoVITS 或 CosyVoice），一次出音色，代码里 `provider="gptsovits"/"cosyvoice"` 已支持 |
| **真·实时对话（<500 ms）** | — | CPU 做不到；要上 GPU 再谈，届时换的也是单段克隆 TTS |

为什么推荐 GPT-SoVITS/CosyVoice 而不是另一个 SVC：
- 两段式（TTS 2.4 s + RVC 9.5 s≈**12 s**）比单段克隆 TTS 多一次模型前向；
- 单段克隆 TTS 直接吃 5~10 s 参考音就能复刻音色，不需要"先合成再变声"；
- 代码已经写好 HTTP 适配（`_gptsovits` 打 `api_v2.py`、`_cosyvoice` 打 `/inference_zero_shot`），
  只差模型和端点——**没装模型，所以我没有实测它们的延迟，不编数字**。

**没做的原因**：这两个要下 1.5~3 GB 模型（modelscope 本机可达），CPU 上跑起来也不一定比 RVC 快；
在拿到你的取舍（要不要这个音色 / 值不值得几个 G 的磁盘）之前，不动仓库体积。

## 三、这轮已落地的改动

1. `aiji/right_hand/params.py`：`VOICE_MAX_CONVERT_SECONDS = 15.0`
2. `aiji/heart/voice_service.py`：`_postprocess` 前加时长护栏——**语音条模式**（`mode="speech"`）
   超过预算直接返回 TTS 原声、不跑换声器，并记 `last_skipped`（进 `/v1/voices` 可查）；
   profile 里可用 `postprocess.max_seconds` 覆盖；`mode="song"`（点歌）不受限。
   → 依据就是上面那张表：一次换声能把工作线程占十几秒，而语音条的收益只是音色。
3. `tools/rvc_server.py`：`--log` 自己做日志（不再让启动方做重定向），
   并把"该 F0 方法缺依赖"从 500+traceback 改成 **400 + 一行原因 + 能照做的 hint**
   （实测 `fcpe` → `400 … No module named 'torchfcpe'` + `uv pip install …` 提示）。
4. `tools/rvc_daemon.ps1`：探活 → 没活就起 → 等 `/health`；`-Stop` 停服；
   日志轮转（`Start-Process` 的重定向是截断不是追加）。**没替你注册计划任务**，要开机自启自己跑
   `schtasks /Create /SC ONLOGON /TN AIJiYi-RVC /TR "powershell -NoProfile -File D:\AIJiYi\tools\rvc_daemon.ps1"`。
5. 顺手删掉三个从未被调用的引擎（`DeclarativeEngine`/`ProceduralEngine`/`SynapticDiffusion`）——
   依据 `tools/archcheck/wiring_audit.py`：组合进 LifeCard 但无任何调用方，扩散连组合都没进。

## 四、踩到的坑（写下来省事）

- `Start-Process -RedirectStandardOutput` 起的子进程会握住父进程句柄：`powershell -File` 里调用时，
  被管道捕获的上层会**等十几分钟不返回**。改成"服务自己写日志 + 父进程只 launch"。
- `cmd /c start /b "" "exe" ...` 在 PowerShell 里拼参数时，第一个引号会被当成窗口标题吃掉，
  结果执行的是别的解释器（表现为莫名其妙 `ModuleNotFoundError: ffmpeg`）。别用这招。
- Windows PowerShell 5.1 的 `Invoke-WebRequest` 没有 `-Proxy $null` 这种写法，会卡住；
  探活用"清空四个代理环境变量"。
