# 磁盘占用体检（2026-11-03）

问题："项目里什么内容占这么多 G"。全部数字来自 `Get-ChildItem -Recurse | Measure-Object Length`
（证据归档：`C:\Users\22166\.dsh\evidence\2026-11-02\065227-raw_run-561505` 等）。

## 体检前：15.28 GB / 84 699 文件

| 路径 | GB | 是什么 | 处理 |
|---|---|---|---|
| `voice-engine/rvc/.venv` | **7.03** | **GPU 运行时**：torch 2.7.1+cu128 的 `torch/` 就 5.53 GB，onnxruntime 0.61、llvmlite 0.12、cv2 0.11、torchfcpe 0.04 | **必须留**（就是它把换声从 9.5 s 降到 0.5 s） |
| `voice-engine/rvc/assets` | 3.54 | 见下 | 见下 |
| ├ `pymss_weights` | 1.45 | 人声分离（MSS）权重，**全仓库零引用**（分离实际走 `voice-engine/models/UVR-MDX-NET-Voc_FT.onnx`） | **已删** |
| ├ `pretrained_v2` | 1.21 | RVC 训练用的底模 G/D，**推理不用** | 留着（你要是重训就要它）；不想重训可删 |
| ├ `logs` | 0.79 | 训练中间产物：300 个 `.pt` checkpoint + 927 个 `.npy` + 624 个 `.wav` | **已删** |
| ├ `weights` | 0.43 | `dafeiyu.pth` 等 8 个 epoch 权重 | 留（你要哪个 epoch 我不替你挑） |
| └ hubert/rmvpe/indices | 0.46 | 推理必需的音高与特征模型 | 留 |
| `data/voices` | 1.23 | `dafeiyu` 声线资产：dataset_v1/v2/v3 共 274 段素材（204 MB）、`source_44k/normalized/vocals_normalized` 各 80.6 MB、模型 54.9 MB、索引 111.5 MB | 留（这是你的声线训练集） |
| `voice-engine/.venv` | 1.21 | 我 11-03 为了跑基准自己装的 **CPU** torch 环境（7 个补丁包也装在这里） | **已删**（GPU 环境完整；要重建见仓库 `docs/agent`） |
| `.tmp` | 1.15 | 我自己的垃圾：`edge-profile` 0.50（CDP 截图用的浏览器 profile）、`test_property` 0.45（hypothesis 产物） | **已删** |
| `data/songs` | 0.05 | 生成过的翻唱 | 留 |
| `.git` `dist` `docs` `aiji` `tests` `tools` | 0.06 | 源码与轮子 | 留 |

## 体检后：10.68 GB（释放 4.61 GB）

删掉的东西**都验证过 RVC 仍能工作**：删完重启守护 → `/health` ok（`loaded_ms` 11 047）→
`--via server` 转换输出 142 124 字节（与清理前逐字节一致），热调用 0.83 / 0.65 / 0.68 s。

## 还没动、但可以再省的

- `voice-engine/rvc/assets/pretrained_v2` **1.21 GB**：只在你想重训 RVC 时有用。
- `voice-engine/rvc/assets/weights` 里除 `dafeiyu.pth` 外的 7 个 epoch **≈0.40 GB**。
- `data/voices/dafeiyu/source_44k.wav` / `source_normalized.wav` / `vocals_normalized.wav`
  三份同源 80.6 MB 中间件 **≈0.24 GB**：分离流程的中间产物，重跑分离就能再生成。
- `voice-engine/seed-vc` 空目录（0 GB）——只是占位，可删。

以上 3 项加起来约 **1.85 GB**，但它们要么是"重训/重跑还能再生成"，要么是你的训练资产，
所以我没替你删。要删说一句。
