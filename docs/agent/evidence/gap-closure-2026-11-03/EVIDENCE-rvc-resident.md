# 缺口⑥：RVC 常驻化（2026-11-03）

## 先纠两个错

1. **"备好的 RVC venv"其实缺 7 个包，RVC 根本跑不起来。**
   `voice-engine/.venv` 里缺 `ffmpeg-python`、`av`、`faiss-cpu`、`praat-parselmouth`、`pyworld`、
   `torchaudio`、`transformers`（`infer/audio.py` / `infer/vc/pipeline.py` / `infer/hubert.py` 全要）。
   装之前每一步都是 `ModuleNotFoundError`，图里"专用 .venv 已就绪"是假的。
   补装（uv，未动主 venv）：`ffmpeg-python 0.2.0`、`av 19.0.2`、`faiss-cpu 1.15.1`、
   `praat-parselmouth 0.4.7`、`pyworld 0.3.5`、`transformers 5.22.1`、`torchaudio 2.11.1`。

2. **"每次 20 s"这个数偏高。** 本机一次性进程实测 **14 064 ms**（1.5 s 素材、CPU、rmvpe）。

## 做了什么

| 文件 | 作用 |
|---|---|
| `tools/rvc_server.py`（新） | 常驻服务：启动时 `VC(config)` + `get_vc()` 载一次模型，之后 `vc_single` 反复推理；HTTP/JSON，只绑 127.0.0.1；`GET /health`、`POST /convert`、`POST /shutdown`；单线程锁（CPU 上并发只会更慢）；记下调用方 cwd，相对路径按它解析（RVC 自己会 `chdir` 到 `voice-engine/rvc`） |
| `tools/rvc_convert.py`（改） | 默认 `--via auto`：服务在跑就走 HTTP，否则回退一次性进程（行为不变）；`--via server` 服务不在就**报错而不是偷偷回退**；`--via inproc` 强制老路径；`AIJI_RVC_SERVER` 换地址，`AIJI_RVC_AUTOSTART=1` 自动拉服务 |
| `tools/archcheck/make_bench_wav.py`（新） | 造 1.5 s 带基频的样本（220 Hz + 两个谐波 + 包络），避免用真人录音做基准 |
| `tools/archcheck/rvc_bench.py`（新） | 一次跑完：起服务 → 3 次转换 → 关服务 → 老路径冷跑，输出对比 JSON |
| `tests/test_rvc_client.py`（新，7 条） | 不装 torch 也能测：健康探测、参数映射（pitch/index-rate/f0/protect/rms/speaker）、auto 走服务、服务报错要冒泡、`--via server` 不许静默回退、auto 在服务关掉时回退、参数不足返回 2 |

## 实测（`tools/archcheck/results/rvc_bench.json`）

| 路径 | 耗时 |
|---|---|
| 服务启动到 `/health` 可用 | 9.17 s（进程内 `loaded_ms` 8.14 s） |
| 服务第 1 次转换（含首次推理预热） | 5 234 ms |
| 服务第 2 次 | 2 768 ms |
| 服务第 3 次 | 2 842 ms |
| **热调用中位数** | **2 892 ms** |
| 一次性 CLI（老路径） | 14 064 ms |
| **加速比** | **×4.86**；**已进 5 s 目标** |

三次输出都是 142 124 字节（同样素材、同样设置），说明走服务的产物与老路径同尺寸。

端到端再验一次（客户端 → 常驻服务，相对路径 `.tmp\rvc_bench_in.wav`）：
`rc=0 wall=5133ms out=142124 bytes`（首次调用含预热）。
修过一个真 bug：服务端 `chdir` 后相对路径解析到了 `voice-engine/rvc`，报 `FileNotFoundError`；
现在客户端发绝对路径、服务端也按启动时 cwd 兜底。

## 还没做（诚实边界）

- **没有守护**：服务要手动起。计划任务/开机自启 + 健康检查重启没做。
- **只测 CPU**：本机 `torch 2.15.0+cpu`、`cuda` 不可用；GPU 上的 F0/合成耗时会不一样。
- **只测 1.5 s 素材**：长音频（30 s 歌）热调用耗时会线性上涨，没测。
- **没进 `voice_service`**：profile.json 的 `postprocess.command` 里加 `--via auto` 才吃到加速，
  仓库里没有默认 profile，所以这是"可用"而不是"默认生效"。
