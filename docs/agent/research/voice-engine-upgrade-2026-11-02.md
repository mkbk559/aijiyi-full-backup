# 语音引擎"升级便利化"调研（2026-11-02）

> 目的：不是找"评分最高的 TTS"，而是找**换引擎/升引擎这件事本身有多省事**——
> 一条命令装、一条命令升、坏了能回滚、AIJiYi 侧不用改代码。
> 本文只做调研与选型建议，**没有安装任何一个新引擎，也没有本机实测数据**（见 §7 未验证项）。

## 0. 结论先行（三句话）

1. **AIJiYi 侧的接口早就够用了**：`aiji/voice_service.py` 已支持 `edge / gptsovits / cosyvoice / openai / http`
   五种 provider，其中 `_openai()`（`voice_service.py:583-606`）就是一个完整的 OpenAI TTS 客户端
   （`model / voice / input / speed / response_format=wav / Bearer`）。
   **任何**暴露 `POST /v1/audio/speech` 的引擎，加一个 profile JSON 就能用，**零代码改动**。
2. **真正不便利的是"装和升"**：现在是 vendored Seed-VC（GPL-3.0）+ 手搓两个 venv（9 GB，
   其中 CUDA torch 5.5 GB）+ 手动往 `checkpoints/` 塞模型 + `ensure_voice_engine.ps1` 只负责"起进程"。
   没有版本锁、没有镜像源策略、没有就绪探针、没有回滚路径。
3. **可抄的范本有两个，都在"安装便利化"上做得比我们好**：
   [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 的 `install.ps1 --Device CU128 --Source ModelScope`
   （设备+镜像一条命令、模型自动下），以及 [Chatterbox-TTS-Server](https://github.com/devnen/Chatterbox-TTS-Server)
   的 **Windows 便携模式**（`start.bat`，自带 Python、整个文件夹拷走即用、相对路径不炸）。

## 1. 现状证据（本机实测，非转述）

| 项 | 数值 | 来源 |
|---|---|---|
| `voice-engine/` 总计 | 9,050.6 MB / 61,403 文件 | 本次 `Get-ChildItem -Recurse` 统计 |
| `seed-vc/.venv` | 6,153 MB，其中 **torch 2.7.1+cu128 = 5,525 MB** | 同上 + 逐包统计 |
| `seed-vc/checkpoints` | 1,797 MB（whisper-small 922 + bigvgan 428 + DiT 420 + campplus 27） | 同上 |
| `voice-engine/.venv` | 982 MB（torch **CPU** 483 MB + onnxruntime，audio-separator 用） | 同上，`song_service.py:88` 调用 |
| GPU / CUDA | RTX 3050 Laptop **4,096 MiB**；`torch.cuda.is_available()=True`，CUDA 12.8 | `voice-engine/seed-vc/.venv/Scripts/python.exe -c "import torch..."` |
| 引擎在线状态 | `127.0.0.1:9880` 正在监听 | `Test-NetConnection` |
| 已有 provider | edge / gptsovits / cosyvoice / openai / http | `voice_service.py:112` |
| 配置样例 | `config/voices/gptsovits.example.json`（:9880 v2）、`cosyvoice.example.json`（:50000 zero_shot） | 仓库文件 |

**关键点**：`gptsovits` 样例端口写的是 9880、`cosyvoice` 写的是 50000——这两个端口正好是
这两个上游项目的**默认端口**。说明当初设计时就已经"预留"了换引擎的位置，只是没做安装层。

## 2. 候选档案（事实层，均可点开核对）

| 项目 | 许可 | 安装方式（官方 README 原话/原文命令） | 模型获取 | HTTP 接口 | 中文 | 与本项目契合 |
|---|---|---|---|---|---|---|
| [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) | MIT | Windows 集成包 `.7z` 双击 `go-webui.bat`；源码装：`pwsh -F install.ps1 --Device <CU126\|CU128\|CPU> --Source <HF\|HF-Mirror\|ModelScope> [--DownloadUVR5]`；或 `docker compose run --service-ports GPT-SoVITS-CU128-Lite` | `install.sh/ps1` 自动下；手装则放 `GPT_SoVITS/pretrained_models` | `api_v2.py`（**默认 9880**），`api_style: v2` | 母语级（5s zero-shot / 1min few-shot，v1–v5 五个档） | **已有 `gptsovits` provider 直连**，端口也对得上 |
| [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) | Apache-2.0（但 README 末尾 Disclaimer 写 "for academic purposes only"，**商用需自己确认**） | conda 建环境 → `pip install -r requirements.txt` → `snapshot_download(...)` 拉 `CosyVoice2-0.5B` 等 | ModelScope / HF SDK，一段 python 即可 | `runtime/python/fastapi/server.py --port 50000`（也有 grpc / docker / vLLM / TensorRT-LLM） | 9 语种 + 18 种中文方言，0.5B，150ms 首包 | **已有 `cosyvoice` provider 直连**，端口也对得上 |
| [IndexTTS-2.5](https://github.com/index-tts/index-tts) | bilibili 模型使用许可协议（**非标准开源许可**） | `pip install -U uv` → `uv sync --all-extras`（可加 `--default-index` 走阿里/清华镜像）；Windows 建议去掉 deepspeed | `uv tool install huggingface-hub` → `hf download IndexTeam/IndexTTS-2.5 --local-dir=checkpoints`（或 modelscope） | 官方只给 `webui.py`（:7860）与 vLLM 部署，**没看到 OpenAI 兼容端点** | 0.8B，SS(zh) 77.10 / RL 77.92，4090 bf16 RTF≈0.20 | 需要薄封装成 OpenAI 端点才能零改代码接入 |
| [Chatterbox-TTS-Server](https://github.com/devnen/Chatterbox-TTS-Server) | MIT（★1492 / fork 364，push 2026-05-26） | Windows：`start.bat`（**便携模式，自带 Python，整个文件夹可拷走**）；`python start.py --portable --nvidia-cu128`；`docker compose up -d` | 首启自动从 HF 下（~2 GB+）；可 `model_cache` 指到项目内 | **`/v1/audio/speech` + `/v1/audio/voices`（OpenAI 兼容）**，Web UI :8004，还有 `/api/unload` 释放显存 | 多语版 23 语种；中文非其强项（英文向） | `provider: "openai"` 直接吃，零改代码 |
| [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) | Apache-2.0（权重也 Apache-2.0） | `docker run -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:latest`（gpu/cu128/rocm 同款；也可 `uv` 直跑 `start-cpu.ps1`） | 镜像内置 / `download_model.py` | `/v1/audio/speech`、`/v1/audio/voices`、`/dev/unload`、词级时间戳 | 有 `zf_` 中文音色，但**上游自评**单句 CER 0.143，且**不是克隆器**（`/dev/tune` 明说 "a tuner, not a cloner"） | 适合"固定音色、要极轻"，不适合"复刻大肥鱼" |
| [seed-vc](https://github.com/Plachtaa/seed-vc)（**当前**） | GPL-3.0 | 无 `install.ps1`，靠人手工 venv + 手放 checkpoints | 手动 | 我们自己的 `aiji_seed_vc_server.py` :9880（**非** OpenAI 兼容） | 它是**转换器**（音色迁移），不是 TTS | 现在的实现 |

补充：社区已经出现大量"一键壳"，可作为"便利化"确实被需要的旁证——
`IndexTTS-2.5-Launcher`（Windows 启动器）、`IndexTTS-2.5-Easy-GUI`、`IndexTTS-2.5-Pinokio`（Pinokio 一键）。

> ★ 星数只取到了 Chatterbox-TTS-Server 一个（GitHub REST 匿名限流 60 次/小时，本机 IP 已被封，
> `api.github.com` 全部 403）。其余项目的**许可与安装命令都来自 README/LICENSE 原文**，结论不受星数影响。

## 3. 从这些项目里能抄的 5 个"便利化"模式

| 模式 | 谁在做 | 我们的缺口 | 抄过来长什么样 |
|---|---|---|---|
| **① 设备+镜像自动探测，一条命令装** | GPT-SoVITS `install.ps1 --Device/--Source`；Chatterbox `start.py --cpu/--nvidia-cu128/--portable` | 现在没有安装脚本，venv 是手工建的 | `tools/engine_install.ps1 -Device auto -Source modelscope`：`nvidia-smi` 探到 3050 → 装 cu128；探不到 → CPU 轮子 |
| **② 便携化整个运行时**（相对路径，拷走即用） | Chatterbox 便携模式 | 我们两个 venv 都是绝对路径 venv，换个目录就废 | 每个引擎一个 `engines/<name>/`，venv 用 `--copies` + 相对激活脚本 |
| **③ 依赖锁 + 可回滚** | IndexTTS 用 `uv sync`（uv.lock）；GPT-SoVITS 按版本分 v1–v5 目录 | 现在是"vendored 一个 commit"，但 venv 里装了什么没人知道 | `engines/<name>/engine.lock.json`：上游 commit + 关键包版本 + 模型文件 sha256；升级 = 改 lock 再 sync；回滚 = `git checkout <lock>` + 重 sync |
| **④ 模型走镜像、断点续传、可校验** | GPT-SoVITS `--Source ModelScope/HF-Mirror`；IndexTTS 支持 `HF_ENDPOINT=https://hf-mirror.com` | 现在是手动放文件，没有 sha 校验 | 统一用 `modelscope` 优先（本机可达），失败退 `hf-mirror`，落盘后校验 sha256 写进 lock |
| **⑤ 运行期可运维**（健康/语音列表/卸载） | Kokoro-FastAPI `/dev/unload`；Chatterbox `/api/unload` + `/v1/audio/voices` | `voice_service._provider_ready()` 只看"endpoint 非空"，根本没探活 | provider 就绪 = `GET /health` 或 `/v1/audio/voices` 成功；再加"空闲 N 分钟自动 unload"（4 GB 卡刚需） |

## 4. 如果要把"便利化"落到本项目（最小改造清单，未实施）

1. **不新增 provider**。`openai` / `gptsovits` / `cosyvoice` 三条已经覆盖全部候选；
   只在 `config/voices/*.json` 里加 profile。
2. **新增 `engines/` 目录结构**，每个引擎一套：`engine.lock.json` + `install.ps1`（或复用一份通用脚本）+ `start.ps1`。
   引擎源码仍在 `voice-engine/`（或各自 vendored 子目录），但**装法统一**。
3. **`tools/engine_install.ps1`**：设备探测 → 建 venv（uv 或 python -m venv）→ 装依赖（镜像可切）→
   拉模型（modelscope→hf-mirror 兜底）→ 写 lock → 起进程 → 打 `/health` 自检 → 失败自动回滚到上一个 lock。
4. **`_provider_ready()` 改真探活**（`voice_service.py:284-304`），并给 `ensure_voice_engine.ps1` 复用同一个探针，
   避免"端口起来了但模型没加载"这种假就绪。
5. **保留 Seed-VC 作为可选转换器**（`convert_audio`），但把它从"主链路必装"降级为"要用大肥鱼音色才装"。

## 5. 选型建议（按"便利 × 本机 4 GB 显存 × 中文"排序）

| 排名 | 方案 | 为什么 | 代价 |
|---|---|---|---|
| 1 | **CosyVoice2-0.5B** | 已有 `cosyvoice` provider；0.5B 塞得进 4 GB；官方带 FastAPI server + docker + vLLM；9 语种 18 方言 | 装法还是 conda + 手工 snapshot；README 有 "academic only" 声明，商用要确认 |
| 2 | **GPT-SoVITS** | 已有 `gptsovits` provider；**`install.ps1` 就是便利化范本**；MIT；有 Windows 集成包 | 权重目录结构琐碎（UVR5/G2PW/ASR 各自位置）；默认下模型体积大 |
| 3 | **IndexTTS-2.5** | 中文 SS 最高，`uv sync` 一条命令；情感/语速/拼音控制最细 | **没有 OpenAI 端点**，得封一层；许可是 bilibili 协议不是标准开源 |
| 4 | **Chatterbox-TTS-Server** | 安装便利度第一（便携 start.bat）；原生 OpenAI 兼容；MIT | 中文弱；适合英文角色，不适合大肥鱼 |
| 5 | **Kokoro-FastAPI** | docker 一行；CPU 就能跑；Apache | 不能克隆音色，只能"选音色/微调" |

**不推荐动 Seed-VC 的理由**：它做的是"任意 TTS → 大肥鱼音色"的转换，别的项目都不替代这件事。
真要省空间，走"CPU 版 torch"（省 ~5 GB）而不是删它。

## 6. 明确"不抄"的

- **不引入 Docker 作为主路径**：本机 Docker Desktop + WSL2 是额外一层依赖，4 GB 显存还要和 WSL 抢；
  Docker 只留作"备选/对比"。
- **不做 Pinokio / 第三方一键壳**：那是别人的分发层，我们要的是自己仓库里可审计的 `install.ps1`。
- **不为了"新"而换引擎**：现有 Seed-VC 链路已实测可用（`data/voices/dafeiyu` 477 MB 参考音频在跑），
  换引擎必须以"便利性收益 > 迁移风险"为前提。

## 7. 未验证项（诚实清单）

1. 上述 5 个候选**一个都没在本机安装过**；显存占用、RTF、中文效果全部是**上游 README 自述**，不是本机实测。
2. 4 GB 显存能否跑 IndexTTS-2.5 bf16 / GPT-SoVITS v2Pro，**未验证**（上游给的 RTF 是 4090/4060Ti）。
3. IndexTTS 是否真的完全没有 OpenAI 兼容端点：只核了 `README.md` 与 `docs/README_zh.md`，
   **没有逐个翻仓库目录**（GitHub API 限流，无法列目录）。
4. CosyVoice 的许可歧义（LICENSE=Apache-2.0 vs README "academic purposes only"）**未查证官方说明**。
5. 升级脚本的设计（§4）是提案，**没有写成代码，也没有跑过**。

## 8. 下一步可选项（等你选一个我再动手）

- **A（最小）**：只做"便利化外壳"——`tools/engine_install.ps1` + `engine.lock.json` + 真探活 + `/dev/unload`，
  引擎仍是 Seed-VC。**收益**：以后换/升引擎有标准流程；**风险**：几乎为零。
- **B（推荐）**：A + 接一个真 TTS（CosyVoice2 或 GPT-SoVITS），走已有 provider，
  Seed-VC 降级为可选转换器。**收益**：中文质量与速度上一档，且能真跑起"TTS→音色转换"两段式；**风险**：多装 2–4 GB。
- **C（激进）**：主 TTS 换成 OpenAI 兼容的 Chatterbox/Kokoro（轻、装得快），只在要"大肥鱼"时才启 Seed-VC。
  **收益**：常态内存/显存最省；**风险**：中文表现不如 B。

## 9. 来源

- GPT-SoVITS：https://github.com/RVC-Boss/GPT-SoVITS （README 安装段、`install.ps1`、docker compose、v1–v5 升级说明）
- CosyVoice：https://github.com/FunAudioLLM/CosyVoice （README 安装/FastAPI/docker/vLLM 段；LICENSE）
- IndexTTS：https://github.com/index-tts/index-tts （`README.md`、`docs/README_zh.md` 的 uv 安装与评测表）
- Chatterbox-TTS-Server：https://github.com/devnen/Chatterbox-TTS-Server （README 便携模式/端点/卸载；GitHub API 元数据）
- Kokoro-FastAPI：https://github.com/remsky/Kokoro-FastAPI （README 一键 docker/unload/克隆能力说明）
- seed-vc（当前）：https://github.com/Plachtaa/seed-vc ；本仓库 `voice-engine/seed-vc/AIJI_INTEGRATION.md`
