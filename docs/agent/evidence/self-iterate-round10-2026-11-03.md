# 自我迭代第 10 轮（2026-11-03 21:50–22:15）：voice_service 75% → 99%

声线服务是"给 QQ 发语音条"那条链路的核心。它 75% 的时候，没被覆盖的全是
**适配器**：真跑 `powershell.exe -File sapi_tts.ps1`、真跑 ffmpeg、真发 HTTP。
这轮全用假 `subprocess.run` / 假 `_fetch` / 假 `net.urlopen` 钉住，一条外网都不用。

## 覆盖率

| 模块 | 补测前 | 补测后 | 新增用例 |
|---|---|---|---|
| `heart/voice_service.py` | 75% | **99%** | `tests/test_voice_adapters.py` 78 条 |

剩 1 行（`synthesize` 里 `else: raise unsupported_provider`）是**不可达**的防御分支：
provider 在 `VoiceProfile.from_dict` 就校验过，进不到这里。

## 钉住的契约（每条都是真出过或会出事故的点）

1. **provider 不可用要说清是"没装"还是"挂了"**：
   edge-tts 没装 → `provider_unavailable`；`save()` 抛 → `provider_failed` 且带原始
   异常文本；`save()` 没写文件 → `empty_audio`（用户不该收到 0 字节音频）。
2. **SAPI 命令形状**：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File
   tools/sapi_tts.ps1 -Text … -Output …`；rc≠0 时把 stderr 塞进错误信息
   （否则只剩一个数字，没法诊断）；rc=0 但没产物也算失败。
3. **`_fetch` 的三种失败**：HTTP 4xx/5xx → `provider_http_error` 带响应体前 500 字；
   连接异常 → `provider_unreachable`；>24 MB → `audio_too_large`
   （防止一个坏 endpoint 把内存吃穿）。
4. **换声超预算就跳过**：`mode="speech"` 且时长 > `max_seconds`（默认
   `params.VOICE_MAX_CONVERT_SECONDS`）→ **不跑换声命令**、返回原音、把
   `last_skipped` 写进 `/v1/voices`。`mode="song"` 永不跳过（离线任务该等）。
   `max_seconds` 是坏值 → 回默认；cap<=0 → 等于不设限。
5. **换声协议是纯字节**：source 与 reference 都由本进程读好再 base64 塞进 JSON
   （跨完整性级别时引擎进程 stat 会被 WinError 5 拒，那条路会静默退回 edge 音色，
   表现就是"定制语音不生效"）。返回 <100 字节 → `postprocess_failed`。
6. **削波保护**：换声器 float 输出常超 ±1.0，写 PCM16 会削波。`_limit_peak` 只在
   峰值 > 0.98 时把整段按比例压到 0.95；8-bit / 空帧 / 非 wav 一律原样返回
   （后处理绝不能让合成失败）。
7. **OpenAI 端点归一**：`…/audio/speech` 原样、`…/v1` 补 `/audio/speech`、
   其它补 `/v1/audio/speech`；有 key 才发 `Authorization`。
8. **`http` provider 必须带 `{text}` 占位符**且文本要 URL 编码（空格不能出现在 URL 里）。
9. **edge 二次失败要抛最初那个错误**：先换 `zh-CN-XiaoxiaoNeural` 重试，再退 SAPI，
   三条都挂时抛**最初的** `VoiceError`（别让 SAPI 的报错盖掉真实原因）。
   非 edge provider 失败不重试、不碰 SAPI。
10. **时长读取宁可返回 None**：读不出时长就不跳过（`rate<=0`、坏文件都返回 None 并打
    warning），否则一次误判会把换声永久关掉。

## 与已有测试的分工

`tests/test_voice_service.py`（17 条，上一轮已有）测"声线发现 / 回退 / 各 provider 端到端"；
新文件专测**适配器自身**（命令拼装、编码、上限、异常转译），不重复。

## 验证

- `tests/test_voice_adapters.py` 78/78；voice 两件套 91/91
- `voice_service.py` 覆盖率 99%
- `ruff` 全过
- 全量 suite v39 = 890 ok

## 下一轮候选

1. `social_service.py` 77%、`onebot.py` 78%
2. 控制台按钮级 E2E（点动作 → DOM 变化）
3. 全链路真实 E2E：`/v1/chat/completions` → 人格回复 → 记忆写入 → 召回
