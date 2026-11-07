# 语音逻辑规划（2026-11-05）

> 目标：玛卡巴卡**能发语音**，而且"什么时候该发语音"这件事得像人，不像开关。
> 现状：TTS/RVC 服务在 `127.0.0.1:18601`（`voice-engine/rvc`，声线 `dafeiyu.pth` 已加载 8.8s）；
> QQ 走 OneBot，理论可以用 `record` 发语音条；ASR（FunASR paraformer-zh）已验过中文 14/14。
> **但"发语音"这条业务链路一次都没真机跑过**——下面先定规矩，再实现。

## 一、什么时候发语音（决策表）

人的语音不是随机出现的。按"场景 × 亲密 × 内容"三档判：

| 场景 | 触发条件 | 语音概率 |
|---|---|---|
| 对方刚发来语音 | 收到 `record`/`[语音]` → ASR 转文字后**优先语音回** | 0.9 |
| 睡前/道晚安（21:30–22:30） | 单轮、句子 ≤ 25 字、当天没发过语音 | 0.6 |
| 情绪强（开心/委屈，愿力/激素触发） | `endocrine` 判定情绪强 + 文本 ≤ 30 字 | 0.5 |
| 撒娇/亲密私聊（自己人） | private 且 30 天内互动 ≥ 50 轮 | 0.3 |
| 群聊 | 一律不发（群里发语音很烦），除非被 @ 要求 | 0.05 |
| 上课/睡觉时段（`day.current_block().talk == "none"`） | 硬门闩 | 0 |
| 内容含链接/代码/长列表 | 不适合语音 | 0 |

**硬上限（防刷屏，跟文字一套口径）**：每小时 ≤ 2 条、每天 ≤ 10 条、单条 ≤ 15 秒、
同一个对象 30 分钟内不连发两条语音。超了就用文字。

## 二、生成链路

```
文本(已过 check_identity + 文风改写)
  → edge-tts（zh-CN-XiaoxiaoNeural，先出标准女声）
  → RVC 变声（dafeiyu.pth，18601 /infer）→ wav
  → ffmpeg → 16k 单声道 silk/mp3（QQ 要 silk 或 amr，按客户端实测）
  → 发 record
  → 回读 message_id 记账
```

失败任一步 → **回退发文字**（永远有兜底，不允许"语音失败就哑了"）。
变声服务不在（18601 不通）→ 直接用 edge-tts 原声，别卡住。

## 三、收语音

`gate.py` 已经把 `[CQ:record...]` 识别成媒体事件但**只入了库没转写**。
要补：下载 → FunASR → 用转写文本进正常对话链路，并在 prompt 里标注"（对方是语音说的）"。
这样"对方发语音"和"对方打字"对模型是同一个意思，但我会更倾向用语音回。

## 四、平台能力（先按这个做，不吹）

| 平台 | 发语音 | 依据 |
|---|---|---|
| QQ | **待真机验**：OneBot `record` 字段 | 有 CQ 码通道，但没实测过 |
| 抖音 | 不支持（网页端无语音私信入口） | 与视频上传同一类限制 |
| B站 | 不支持（私信只收文本/图片） | 已有 `dm_rejected` 证据 |

抖音/B站只能"文字 + 表情"，语音是 QQ 独占能力——这点要写进人格自我认知，
别在抖音上说"我给你发个语音"。

## 五、实现顺序（每步都有验收证据）

1. **离线**：`VoicePlanner.decide(text, ctx) -> bool`（纯函数、可测）＋ 单测覆盖决策表每一行
2. **生成**：`tools/make_voice.py` 文本 → wav，人工听一次，确认是玛卡巴卡声线
3. **QQ 真机**：发一条语音到测试号 → 回读 `message_id` + 客户端能播（截图/时长）
4. **收语音**：自己发一条语音给 bot → 库里出现 ASR 文本
5. **接线**：`heart` 发件箱支持 `kind="voice"`，失败自动降级文字
6. **回归**：全量测试 + 夜间体检（`day audit` 里加"语音条数/失败数"）

## 六、明确不做

- 不做实时语音通话（延迟与打断逻辑成本过高，收益低）
- 不做变声"模仿他人"（伦理与封号风险）
- 不在群里发语音
- 不在没有 ASR 转写的情况下"假装听懂语音"

## 七、实施状态（2026-11-06 00:1x 实测）

| 步骤 | 状态 | 证据 |
|---|---|---|
| 决策函数 `VoicePlanner.decide` | ✅ 已实现 | `aiji/heart/voice_plan.py`，10 个单测全绿（决策表逐行） |
| TTS 生成 | ✅ 真机跑通 | `tools/make_voice.py "晚安啦，明天见，我先睡啦" --no-rvc` → `data/temp/voice.mp3` **19,584 字节**（约 2.2 秒） |
| RVC 变声（玛卡巴卡声线） | ❌ 未通 | 18601 端口当前无服务；**入口脚本没确认**（`voice-engine/rvc` 下只有 `realtime_gui.py`/`webui.py`，11-03 那次启动命令没记） |
| QQ `record` 真机发语音 | ⬜ 未做 | 卡在上一行 |
| 收语音转写 | ⬜ 未做 | `gate.py` 只识别 `[CQ:record]` 入库，没送 ASR |
| 名字注入（语音/文字共用） | ✅ 已实现 | `aiji/brain/names.py` + `data/persona/names.json`，3 个单测 |

**环境坑（记下来省得重踩）**：本机 `HTTP_PROXY=http://127.0.0.1:7897`（Clash）现在是**关的**，
`uv pip install` 会因为继承代理直接 `tcp connect error 10061`。装包前先清四个代理变量，
`edge-tts` 7.3.1 就是这么装上的（直连 pypi 137ms）。

### 追加（00:2x–00:3x）：变声打通了

踩了两个坑，都修了：

1. **权重改名**：`assets/weights/dafeiyu.pth` 已不存在，实际是 `makabaka.pth`（还有 e60–e120 七个 epoch 权重）。
   `tools/rvc_server.py` 里 `DEFAULT_MODEL/DEFAULT_INDEX` 硬编码旧名 → 改成按存在性挑（优先 makabaka）。
2. **接口不是 /infer**：服务端只有 `POST /convert {"in": 源, "out": 目标}`，
   `tools/make_voice.py` 原来打 /infer 得到 404，已改。

实测：

```
pwsh -File tools/rvc_daemon.ps1
→ 已启动：http://127.0.0.1:18601  model=makabaka.pth  loaded_ms=7430.7

python tools/make_voice.py "晚安啦，明天见，我先睡啦" -o data/temp/voice_makabaka.wav
→ {"ok": true, "bytes": 311084}   # 2.2 秒，已变声
```

还有一条：`voice-engine/rvc/.venv` 缺 `ffmpeg` 模块（`infer/audio.py` 要 import），已补 `ffmpeg-python`。
这也解释了 11-03 之后 18601 一直起不来的原因。

## 八、收语音闭环（2026-11-06 01:5x 打通）

**卡了两轮的根因找到了，不是网络也不是模型，是"没有超时的同步链路"**：

- `aiji media --kind voice --url …` 走 `memory.add_media()`，那条链同步下载 + 同步转写，
  任何一步不顺就**静默挂住**（实测 5 分钟无输出、无栈、无报错）。
- 更隐蔽的一层：`real_engines.HAS_FUNASR` 只检查 `import funasr` 成不成功，
  但 FunASR 真正跑起来还要 `import torch`；本机 `.venv` 没有 torch，
  于是"看起来有引擎"，实际一调用就抛，且被上层吞掉。

**修的三处**：

1. `HAS_FUNASR = _probe("funasr") and _probe("torch")` —— 缺 torch 就当场判定不可用（实测现在 `HAS_FUNASR: False`）；
2. FunASR 分支整段包 try/except，崩了**回退 whisper**，不把链路带崩；
3. 新增 HTTP 路由 `POST /api/voice/transcribe`（`aiji/throat/server.py`），
   入参 `{"path": 本地文件}` 或 `{"url": http(s) 地址}`，内部走 `voice_inbox.transcribe_record`，
   下载带 15s 超时、8MB 上限。**这条链有界、可观测、失败会回错误码。**

实测（服务进程内，不是子进程）：

```
POST /api/voice/transcribe {"path":"D:\\AIJiYi\\data\\temp\\voice_makabaka.wav"}
→ 34s（冷启模型）  {"ok":true,"text":"晚安了，明天见，我先睡了。","conf":0.9}
→ 热调用见下方记录
```

注意转写结果是**准的**（"我先睡了"听对了），比之前命令行里 whisper tiny 的"我稀睡了"好——
因为服务进程里 FunASR 不可用后走的是同一条 whisper，但这次音频是 RVC 输出的干净 wav。

**结论**：收语音现在有一条**自己能跑、有时间上界、有明确错误码**的路。
插件侧 `Comp.Record → {"kind":"voice","url":…}` 推过来之后，只需让接收端调这条路由即可。
