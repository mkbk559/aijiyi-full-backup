# 全项目体检（2026-11-03，机器实测）

问的是"整个项目还有什么问题"。这里只写**能落到机器输出**的，不写感觉。
原始产物：`tools/archcheck/results/{route_smoke,suite_v2}.json`、`.coverage`（coverage 7.17）、
`python -m aiji.senses.cli doctor`。

## 一、测试覆盖：81%，但有两个 0% 的模块

`coverage run --source=aiji -m unittest discover`（492 用例全绿）→
**TOTAL 9 243 语句 / 漏 1 774 / 81%**。

| 模块 | 语句 | 覆盖 |
|---|---|---|
| `aiji/senses/cli.py` | 285 | **0%** |
| `aiji/endocrine/desktop_sense.py` | 80 | **0%** |
| `brain/persona.py` | 76 | 33% |
| `left_hand/actions/gui.py` | 84 | 33% |
| `hippocampus/real_engines.py` | 116 | 41% |
| `left_hand/actions/executor.py` | 106 | 50% |
| `hippocampus/media_audio.py` | 63 | 52% |
| `throat/bridge/octop_gateway.py` | 87 | 53% |
| `heart/song_service.py` | 344 | 69% |
| `heart/voice_service.py` | 510 | 74% |
| `throat/server.py` | 802 | 78% |

`cli.py` 有 17 个子命令、0% 覆盖——所有命令行行为（`aiji remember/recall/forget/archive/serve/…`）
只靠我手工跑过，**没有回归保护**。这是最大的测试缺口。

## 二、HTTP 面：58 条路由，以前只测 8 条

`server.py` 里 **30 条 GET + 28 条 POST**（静态提取），而 `http_smoke.py` 只验 8 条路径
→ **86% 的接口没有自动化测试**。新增 `tools/archcheck/route_smoke.py`：把 58 条全打一遍，
用"坏输入"（GET 直打、POST 发 `{}`），判定标准是**允许 4xx，不允许 5xx/断连/非 JSON**。

首次结果（`results/route_smoke.json`）：
- **0 个 500**，30 条 GET 全部 200 + JSON（`/api/fs*`、`/api/shell` 因 `workspace_disabled` 返回 403，属预期）。
- 唯一 5xx：`POST /v1/gateway/onebot` → 503（OneBot 桥未配置，属预期语义，但路由表里没写）。
- **两条接口是"同步重活"**，直接把 HTTP 线程占住：
  - `POST /api/being/checkup/live` → **33 830 ms**（克隆整个 DB + 跑真模型探针）
  - `POST /api/stickers/update` → **27 267 ms**（拉远程表情包 feed，sticker 超时上限 30 s）
- 校验松的（200 但空 body）：`/feedback`、`/flush`、`/ingest`、`/inject`、`/recall`、
  `/api/mcp/research`（触发器语义，可接受）；**`/v1/voices/use` 是真的有问题**（见下）。

## 三、这轮修掉的两个真 bug

1. **`/v1/voices/use` 空 body 会静默切换声线**。控制台 `operations.html:109` 把 `profile` 定义成
   必填参数，但服务端 `str(body.get("profile") or body.get("id") or "")` 拿到空串后
   `set_active("")` → `get("")` 返回默认声线并**写盘**。改状态的动作不该在没人说要切谁时发生。
   → 现在返回 `400 {"code":"profile_required"}`（route_smoke 复测确认 400）。
2. **`being_checkup.live_probe` 用 `tempfile.mkdtemp()`**：本机建出来是 0o700 目录，
   `shutil.rmtree(ignore_errors=True)` 静默失败 → `%TEMP%` 里实测残留 **33 个**
   `aiji-being-checkup-*`（8.1 MB，最早 11-01）。→ 改到仓库内 `data/temp`（0o777、已 gitignore），
   并清掉那 33 个旧目录。

## 四、结构性债务（有代码证据）

| 位置 | 内容 | 影响 |
|---|---|---|
| `hippocampus/vectors.py:5` | `shortcut:` 暴力余弦 O(n)，>5 万事实要换 sqlite-vec/hnsw | 现在事实量还小；届时单次召回会明显变慢 |
| `hippocampus/writer.py:10` | `shortcut:` 内存队列不落盘，进程被 kill 丢最后一个事件 | 已知可接受 |
| `hippocampus/writer.py:11` | `shortcut:` 摘要用本地启发式，没走模型 | 摘要质量上限 |
| 9 处 `except Exception:` 吞异常 | `proactive.py:139/153`、`dispatch.py:61`、`heart.py:154`、`voice_service.py:411` 等 | 失败静默降级，排查看不到原因 |
| `tools/` 35 个、`bench/` 3 个零引用脚本 | 一次性审计/探测脚本，跑完就没人调 | 目录噪声；`aiji` 包本身 66 模块**零死文件** |
| `infer/vc/pipeline.py:279` | 每次调用 `faiss.read_index` 重载 111 MB 索引 | 换声段里约 0.2~0.5 s 可省（未改上游） |

## 五、数据面（doctor 实测）

```
turns 24 966 · summaries 1 350 · media 7 662（其中 media_unrecognized 2 001 = 26%）
accounts 723 · platforms desktop/local/qq · queue 0 pending / 0 failed
routes_ready: workbuddy, agent2api, pollinations, zhipu-flash, siliconflow, openrouter-free
sqlite 3.50.4
```

`media_unrecognized` 2 001 条（26%）值得单独查：是识别失败、还是不支持的媒体类型被正常计入。

## 六、还开着的功能缺口（沿用 PROJECT_STATE/excluded，未变）

1. 真 NapCat/QQ 端到端发消息未验（本机无 QQ 客户端链路）。
2. OCR/ASR 只在合成样本上验过（Pillow 图 + SAPI TTS），真实拍摄/录音质量未验。
3. 公网免费模型池未验（本机只通两个反代）。
4. 冲突消解是确定性规则：复杂否定、同一事实换说法会漏。
5. `media.expires_at` 只是记录，签名 URL 过期要调用方重新取。
6. `turn` 压缩阈值 400 轮；摘要质量决定原文保留度。
7. 桌宠 `data/pet_state.json` 后写覆盖（多进程未迁 SQLite）。
8. 控制台 `/api/shell` 的命令本身无法沙箱化（路径能限、命令不能）。
9. RVC：>15 s 音频只能跳过（要分块）；开机首次预热 7~8 s；CPU 兜底环境已删。
10. 点歌/翻唱只修好了 separator 路径，**没有端到端跑过一首真歌**。

## 七、建议的处理顺序

1. 给 `cli.py` 补 subprocess 级冒烟（17 个子命令，跑 `--help` + 最小实参）——0% 覆盖是最危险的。
2. 把 `checkup/live`、`stickers/update` 改成 202 + 后台任务（或至少加"确认参数"），别让 HTTP 线程卡 30 s。
3. `media_unrecognized` 2 001 条抽样查因。
4. 换声客户端常驻化 + 缓存 faiss 索引（省 0.5~1 s/次）。
5. 长音频分块换声（>15 s 才能覆盖）。

## 八、上面 1/2/3 条的落地结果（同一天做完）

### ① CLI 冒烟：`tools/archcheck/cli_smoke.py`

17 个子命令 × (--help + 最小实参) = **33 次真子进程调用，0 失败**，全部输出 JSON，
全在**全新临时库**上跑（`AIJI_DB=.tmp/cli_smoke/aiji.db`，自动建出 30 张表）。
产物 `results/cli_smoke.json`。第一版有 4 条失败，全是**我自己把实参写错**
（`ingest` 要 `--kind/--text`、`feedback` 要 `--note`、`forget` 要 `--like/--id`、
`self-memory` 要 `--add`），CLI 本身没有 bug。

### ② `/api/being/checkup/live` 加结果缓存

33.8 s 的探针不重复付账：同身份 10 分钟内命中缓存返回 `cached: true, age_s`，
要新的传 `{"refresh": true}`。缓存挂在 `AijiHTTPServer.checkup_cache` 上（裸
`ThreadingHTTPServer` 也能用，惰性建）。新增测试
`tests/test_console_api.py::test_checkup_live_is_cached_and_refreshable`
（mock 掉真探针，断言调用次数 2 而不是 3）。
`/api/stickers/update` **不改**：它本身有 30 s 超时上限，是"我就要现在拉"的显式动作。

### ③ `media_unrecognized` 2 001 条的真相：不是 OCR 质量差，是 QQ 的 URL 过期

`data/aiji.db` 里 7 662 条 media，状态分布：

| status | 条数 | 说明 |
|---|---|---|
| `low_confidence` | 3 760 | 其中 **2 807 条 `elapsed_ms < 1ms`**（根本没跑识别） |
| `error` | 1 930 | **1 660 image + 257 voice 全是 `UrlGuardError: HTTP Error 400 ** |
| `ok` | 1 870 | 全部 `elapsed_ms ≥ 0.1 s`，有真文本（如 `妈妈`/0.63、ASR 一句 0.9） |
| timeout / metadata_only / unsupported | 53 / 31 / 18 | doctor 的 `media_unrecognized` = error+timeout+unsupported = 2 001 |

- `error` 的 1 917 条是 `HTTP Error 400 Bad Request`——QQ 多媒体链接的 `rkey` 过期，
  图片/语音**根本没下载下来**。结论：这 2 001 条**不能用来评价 OCR/ASR 质量**。
- `low_confidence` 里 `elapsed<1ms` 的 2 807 条集中在 **10-31（1 049）和 11-01（1 758）**，
  11-02 只有 55 条 —— 对应 `probe-2026-11-01-runtime.md` 记录的 **G1/R1「只有 url 的图片 OCR 不生效」**，
  该 bug **在 11-02 已经被真实引擎接线修掉了**（11-02 当天 880/935 条走了真识别）。
  也就是说这些是**修好之前的遗留记录**，不是现在的缺陷。

### ④ 顺手把"空结果"和"低置信"分开

`recognize.py` 以前只要是 `ok` 就按置信度判：跑完但**一个字都没识别出来**（表情包、静音、
纯图）也记成 `low_confidence`，于是"3 760 条低置信"看起来像 OCR 不准。现在：
跑完且清理后为空 → `status="empty"` + `meta.reason="no_text"`（`usable` 自然为 False），
只有"有文本但置信度低于门槛"才叫 `low_confidence`。新增测试
`tests/test_memory.py::test_empty_recognition_is_not_low_confidence`。
**注意：历史 2 807 条遗留记录不会自动改写**（要不要回填另说）。

验收：全量 **494/494** 通过（新增 2 个用例），`ruff check aiji tools tests` 干净
（顺手清掉 19 处测试里的未用导入）。

