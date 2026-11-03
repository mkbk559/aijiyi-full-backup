> **历史文档（设定已废弃）**：这里的「大肥鱼 / 鱼缸」是 2026-11-03 之前的人设，
> 那一天已整体替换为「玛卡巴卡」（约 6 岁半的小孩，见
> `docs/agent/design/persona-child-2026-11-03.md`），相关记忆与素材均已清空。
> 本文只作当时的决策/研究记录，不要当作现行设定。

# 换音色（SVC）开源项目调研（2026-11-02）

> 只调研，**没有安装、没有下载、没有跑任何候选**（上一轮我越界动手装 GPT-SoVITS 被叫停，这轮不再重复）。
> 所有"许可 / 安装命令 / 能力"都摘自仓库 README 原文；标 **未核对** 的项没有一手证据。

## 0. 结论（先说能替什么、不能替什么）

1. **"换音色"其实有两件事，别混在一起**：
   - **给合成语音套音色**（我们现在的用法：edge 合成 → Seed-VC 换音色）→ 这件事**不需要 SVC**，
     换成**克隆式 TTS**（GPT-SoVITS / CosyVoice2，自带 zero-shot 克隆）就直接省掉一整层。
   - **给唱歌套音色**（`song_service.py:306-315` 分离人声后转音色）→ 这才是真 SVC。克隆式 TTS 干不了，
     必须留一个 SVC。
2. **SVC 里"更好"的不是又一个零样本模型，而是 RVC 生态**：RVC / Applio 是"每个音色训一个模型"，
   相似度、稳定性、唱歌表现都强于零样本的 Seed-VC；代价是**要先训一次**（我们有 154 段大肥鱼音频 + 转录，正好够）。
3. **"升级便利化"上 Applio 明显最强**：Windows 双击 `run-install.bat` 装、`run-applio.bat` 跑、
   **`run-updater.bat` 原地升级且保留模型/日志/音频/预设**——正好解决"换引擎要重装一遍环境"的痛点。
   若要"零训练 + 极简"，则用 **kNN-VC**（只有 torch/torchaudio/numpy 三个依赖，`torch.hub.load` 一行）。

## 1. 候选总表

| 项目 | 许可 | 要训练吗 | 安装方式（README 原文） | 目标用途 | 唱歌 | 中文 | 备注 |
|---|---|---|---|---|---|---|---|
| **Seed-VC**（现状） | GPL-3.0 | 不要（zero-shot） | 无 install 脚本，手工 venv + 手放 checkpoints（本机 9 GB） | 通用换音色 | 支持（在用） | 支持 | 已跑通，但没有升级路径 |
| **RVC 官方**（Retrieval-based-Voice-Conversion-WebUI） | **MIT** | **要**（推荐 ≥10 分钟低底噪语音） | Python 3.12 x64 + `requirments_cu128_py312.txt`（**requirements 里已内置国内镜像** `mirrors.pku.edu.cn`、`mirrors.nju.edu.cn/pytorch/whl/cu128`）；权重 `hf download lj1995/VoiceConversionWebUI …`；`python webui.py`（:7865） | 换音色/变声 | **强（AI 歌手事实标准）** | 中文界面/中文教程 | 有 CPU / AMD / DirectML 方案；170ms 实时变声 |
| **Applio**（RVC 生态的 UX 分支） | **MIT** | **要** | Windows 双击 `run-install.bat` → `run-applio.bat`；官方出**编译版**；`run-updater.bat` **原地升级并保留模型** | 换音色/唱歌 | 强 | 英文界面，中文模型可用 | README 明说"不再频繁更新，已成熟稳定"；有插件体系 |
| **OpenVoice V2** | **MIT**（V1/V2 自 2024-04 起均 MIT） | 不要（tone color 克隆） | `pip install -e .` + 下 `checkpoints_v2_0417.zip` 解到 `checkpoints_v2/`；Windows 只有社区指南 | 给任意语音换音色（TTS 侧） | 不面向唱歌 | **原生支持中文**（6 语） | 定位是"音色克隆 + 风格控制"，不是 SVC 工具链 |
| **kNN-VC** | 未核对 | 不要（kNN 无参数，只需预训练 WavLM+HiFiGAN） | 依赖只有 `torch/torchaudio/numpy`；`torch.hub.load('bshall/knn-vc','knn_vc',prematched=True,pretrained=True)` 一行 | 任意→任意换音色 | 未声明 | 未验 | 论文 EN WER 6.29%；**16 kHz**输出；参考音频越多越好（>5 min 收益递减） |
| **DDSP-SVC 6.0** | 未核对 | 要（但比 so-vits 轻很多） | `pip install -r requirements.txt` + 手动下 ContentVec/HubertSoft、NSF-HiFiGAN、RMVPE；作者测试基线是 **Python 3.8 + torch 2.0** | 唱歌专用 | **强** | 中文作者/文档 | 有实时 GUI；纯 DDSP 轻量，浅扩散版质量接近 RVC |
| **so-vits-svc 4.1** | 未核对 | 要（重） | 重，未细查 | 唱歌 | 强 | 中文社区 | 老项目，工程化不如上面两个 |

另有一批 **2025–2026 的零样本 SVC 论文/权重**（只见到论文与 HF 页面，**没有看到成熟的一键工程，也未验证**）：
[YingMusic-SVC](https://export.arxiv.org/pdf/2512.04793)（Flow-GRPO 零样本歌唱转换）、
[REF-VC](https://ieeexplore.ieee.org/document/11434632)（扩散 Transformer，鲁棒/快速零样本）、
[HQ-SVC](https://dl.acm.org/doi/10.1609/aaai.v40i36.40249)（AAAI-40）、
[MeanVC2](https://huggingface.co/mrfakename/MeanVC2)。这些**不适合当主方案**（工程化风险），但值得一年后再看。

## 1.5 说话也能用 RVC 吗？（能，且有仓库一手证据）

结论：**能**。RVC 本来就是"变声器"，说话是它的原始场景，唱歌只是后来的主流用法。证据（RVC 仓库原文）：

| 证据 | 出处 | 含义 |
|---|---|---|
| "简单易用的 **语音音色转换/变声器** 框架" | README 首行 | 定位就是给**说话**换音色 |
| 实时变声 GUI：`go-realtime_gui.bat`，端到端 **170 ms**（ASIO 可到 90 ms） | README 界面说明 | 说话场景实时可用 |
| "解锁16系限制。**4G显存GPU给到4G的推理设置**" | `docs/cn/Changelog_CN.md` 20230428 | **4 GB 显卡官方支持推理**（我们这张 3050 正好） |
| "支持**非f0模型**推理"、"呼吸、清辅音、齿音保护模式" | Changelog 20230618 / 20230528 | 都有针对"说话"而非唱歌的处理 |
| "人声与伴奏分离后端由 UVR5 更换为 **PyMSS**"（2026-07-23） | Changelog 顶部 | 内置人声分离，可替掉我们现在 `audio-separator` 那 982 MB venv |

但要注意**架构没变**：RVC 是"转换器"，说话仍要有一段源语音 → 所以是**两段式**（和现在用 Seed-VC 一模一样）：

```
edge / SAPI / 任意 TTS  ──►  RVC(大肥鱼模型)  ──► 输出
```

因此"说话"有两条等价路线，区别只在要几段：

| 路线 | 链路 | 优点 | 代价 |
|---|---|---|---|
| **一段式** | 克隆式 TTS（GPT-SoVITS / CosyVoice2）直接说 | 一层、少一次转换延迟、少装一个引擎 | 只解决说话，唱歌还得另找 |
| **两段式** | 任意 TTS + **RVC** | **一个引擎同时解决说话和唱歌**；MIT；有 `run-updater.bat` 原地升级；4 GB 可推理；内置人声分离 | 要**先训一次**大肥鱼模型；训练显存/时长未验证；音色上限受源音频影响 |

一句话：**RVC 能替 Seed-VC 干"说话"这件事，而且比它多干"唱歌"，许可还从 GPL-3.0 变成 MIT。**

## 2. 按用途选

| 我们的用途 | 最佳选择 | 理由 |
|---|---|---|
| **说话用大肥鱼音色** | **不用 SVC**，用克隆式 TTS（GPT-SoVITS / CosyVoice2） | 少一层、少 8 GB、少一次转换延迟 |
| **唱歌用大肥鱼音色** | **Applio 或 RVC 官方**（用现有 `data/voices/dafeiyu/dataset_v2/clips/` 154 段训一次） | 相似度和稳定性明显强于零样本；训完 `.pth`+`.index` 与代码解耦，**升级引擎不动模型** |
| **零训练、要最省事/最轻** | **kNN-VC** | 3 个依赖、无训练、`torch.hub` 一行；质量不如 RVC，可作兜底/对比 |
| **给任意 TTS 输出换音色（不想训）** | **OpenVoice V2** | MIT、原生中文、checkpoint 一个 zip |

## 3. "升级便利化"对比（这才是我们真正的痛点）

| 维度 | Seed-VC（现状） | RVC 官方 | Applio | OpenVoice V2 | kNN-VC |
|---|---|---|---|---|---|
| 一条命令装 | ❌ 手工 | ⚠️ 手动选 requirements + 下权重 | ✅ `run-install.bat` | ⚠️ `pip install -e .` + zip | ✅ 3 个依赖 |
| 一键升级 | ❌ | ⚠️ 重新拉代码装依赖 | ✅ **`run-updater.bat` 原地升级、保留模型** | ❌ | ✅ 无状态（torch.hub） |
| 国内镜像 | ❌ | ✅ requirements 内置 pku/nju 镜像 | ⚠️ 未见 | ❌ | ➖ 走 pypi |
| 模型与代码解耦 | ⚠️ checkpoints 在仓库里 | ✅ `assets/weights/*.pth` | ✅ 同左 | ⚠️ `checkpoints_v2/` | ✅ hub 缓存 |
| 显存 | 5.5 GB CUDA torch（推理约 2–3 GB） | 训练 4–6 GB / 推理低 | 同左 | 很低（CPU 可跑） | 低（WavLM-large 约 1.2 GB 权重） |

## 4. 本机相关实测（网络/硬件，本轮新测）

| 项 | 结果 |
|---|---|
| GPU | RTX 3050 Laptop **4096 MiB**，`torch.cuda.is_available()=True` |
| huggingface.co 直连 | **0.96 MB/s**（`s2G2333k.pth` 100 MB 实测） |
| hf-mirror.com | 0.45 MB/s |
| www.modelscope.cn | **SSL UNEXPECTED_EOF**（下载失败） |
| download.pytorch.org | **0.06 MB/s**（2.5 GB CUDA torch ≈ 11 小时，不可用） |
| mirrors.pku.edu.cn/pypi | 0.59 MB/s（可达） |
| mirrors.nju.edu.cn/pytorch/whl | 可达（返回 206），大文件速度未验 |
| `git clone` github.com | 默认 schannel 报 `SEC_E_NO_CREDENTIALS`；**加 `-c http.sslBackend=openssl` 成功** |

→ 结论：**装 RVC/Applio 时 CUDA torch 必须走 nju 镜像**，否则 download.pytorch.org 会卡死；模型权重走 huggingface 直连（1 MB/s 可接受）。

## 5. 未验证项（诚实清单）

1. 六个候选**一个都没装、没跑**；显存占用、RTF、相似度全部无本机数据。
2. **kNN-VC / DDSP-SVC / so-vits-svc 的许可证没有核对**（只核了 README 里的用法，没读 LICENSE 文件）。
3. 唱歌相似度对比（Seed-VC zero-shot vs RVC 训练后）**没有量化**；RVC 需要训练这一环本身也没试。
4. `mirrors.nju.edu.cn` 的大文件（2.5 GB cu128 wheel）下载**没有实测完成时间**。
5. 2025–2026 那批零样本 SVC 论文只有摘要级信息，**质量与可用性都未验证**。

## 6. 建议（不是承诺，等你选）

- **若还想要"唱歌用大肥鱼"**：训一个 RVC/Applio 模型（154 段数据够，训练在 4 GB 卡上可跑低显存模式），
  Seed-VC 降级为"临时对比用"，验证后删掉，省 8 GB。
- **若不在乎唱歌**：一句话——**SVC 整个不要**，说话交给克隆式 TTS，`voice-engine/seed-vc` 直接删。
- **若想先零成本验证链路**：用 kNN-VC（3 个依赖）跑一段"TTS 输出 → 换音色"对比听感，再决定要不要上 RVC 训练。

## 7. 来源

- RVC 官方：https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI （README：环境、镜像、权重路径、:7865）
- Applio：https://github.com/IAHispano/Applio （README：run-install / run-applio / run-updater、MIT、维护状态）
- OpenVoice：https://github.com/myshell-ai/OpenVoice （README + docs/USAGE.md：V2 MIT、checkpoints_v2、中文支持）
- kNN-VC：https://github.com/bshall/knn-vc （README：3 依赖、torch.hub、WER 表、16 kHz）
- DDSP-SVC：https://github.com/yxlllc/DDSP-SVC （README：6.0 reflow、依赖与预训练模型、Python 3.8 基线）
- Seed-VC（现状）：https://github.com/Plachtaa/seed-vc ；本仓库 `voice-engine/seed-vc/AIJI_INTEGRATION.md`
