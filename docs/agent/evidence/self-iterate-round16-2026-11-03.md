# 自我迭代第 16 轮（2026-11-03 23:10–23:30）：全库覆盖率盘点 + Octop 桥 100% + 参数表自检

## 0. 先量化家底（全量 suite 的整包覆盖率）

`coverage run --source=aiji tools/archcheck/run_suite.py`（一次跑完 1001 条）：

```
TOTAL  9974 语句  850 未覆盖  91%
```

按覆盖率升序，最薄的前几名：

| 模块 | 覆盖 | 未覆盖行 |
|---|---|---|
| `throat/bridge/octop_gateway.py` | 69% | 60-66, 113-127, 131-145, 149 |
| `right_hand/params.py` | 79% | 115-125, 130-134 |
| `throat/server.py` | 80% | 189 行（大量 handler 分支） |
| `hippocampus/recognize.py` | 82% | … |
| `left_hand/mcp_client.py` | 83% | … |

（注：`coverage report --include='aiji*'` 在 Windows 上匹配不到，路径被规范化成
`aiji\...`，得用 `--include='aiji\*'` 或干脆不加。）

## 1. `octop_gateway.py`：69% → **100%**（`tests/test_octop_gateway.py`，20 条）

这层是**所有非 OneBot 平台**（QQ 官方机器人/飞书/微信）的唯一入口，
第三方包 `octop_gateway` 本机没装——所以用假包注入 `sys.modules`，
只测"包装层自己的行为"：

- `inbound_payload`：群/频道按 `subject_id` 归到 `group:<id>`、私聊归 `default`；
  sender 三级回退（`sender_id` → `user_openid` → `subject_id` → `unknown`）；
  session 三级回退；只有媒体的消息补 `[用户发送了媒体消息]`；文本 strip
- `AijiGatewayClient.chat`：端点 `/v1/gateway/messages`、有 key 才带 `Bearer`
- `_default_post`：用假 `aiohttp`（`ClientTimeout`/`ClientSession`/响应 async CM）
  验证 timeout 透传、`raise_for_status()` 被调用、非对象 JSON 返回 `{}`
- `make_processor`：正常文本、空文本→"（挠头）这次没接上话"、
  异常→"本地 AIJiYi 暂时没连上：RuntimeError"、没文字时**不打网关**
- `run_qq`：缺 `QQ_APP_ID/SECRET` → `SystemExit`；有则 start→add_qq_channel→finally stop，
  并核对 `workers_per_channel=4`、`sandbox` 透传
- `main`：argparse 参数、环境变量默认值、`KeyboardInterrupt` → 返回 0；
  用 `runpy.run_path(..., run_name="__main__")` 覆盖 `raise SystemExit(main())`

坑：`from octop_gateway.channels.qq import QQConfig` 要求假模块是**包**，
必须设 `module.__path__ = []` 并把 `octop_gateway.channels`、`.channels.qq`
都塞进 `sys.modules`，否则 `ModuleNotFoundError: 'octop_gateway' is not a package`。

## 2. `params.py`：79% → **100%**，并且**抓出两个真问题**

`params.snapshot()` 会进 `baseline.json` 做漂移检测，它的 `source` 字段是从
**源码注释**正则抓的（`^NAME\s*=.*?# (.+)$`）。跑一遍快照，58 个数值参数里有 2 个是
`(无来源标注！)`：

- `WAL_TRUNCATE_MB = 32` —— 注释在**上一行**，正则抓不到
- `VOICE_MAX_CONVERT_SECONDS = 15.0` —— 注释在上面 6 行，同理

还有一处**孤儿注释**：`RECALL_DECAY_MODE` 下方孤立着一行
`# 默认 fsrs：bench/bench_memory_quality.py 的 6 条排序不变量…`，
而真实默认是 `stability` —— 这行是编辑时留下的残渣，写着错误结论，
还会被 `_source_of` 误抓给下一个参数。

三处都已修：把来源注释折回同一行、孤儿注释归位到 `RECALL_DECAY_MODE` 上方。

新增 `tests/test_params.py`（8 条）把这件事变成硬约束：

- 快照只含数值、跳过 `snapshot`/`annotations`/`INJECTION_SEMANTICS`/下划线
- 每个条目的 `value` 与模块属性一致
- **每个数值参数都必须有真来源**（`(无来源标注！)` 直接红）
- `_source_of` 正常与找不到两种情况
- `INJECTION_SEMANTICS` 必须写明"不是 prompt 注入攻击计数"
- 回归：不许再出现"默认 fsrs"这种孤儿注释块
- `py_compile` 能单文件编译

## 验证

- `tests/test_octop_gateway.py` 20/20、`tests/test_params.py` 8/8
- 覆盖率：`octop_gateway.py` 100%、`params.py` 100%
- ruff 全过
- 全量 suite：v45

## 下一轮候选

1. `throat/server.py` 80%（189 行没覆盖）——handler 分支最多，也最可能是"点了没反应"的来源
2. `hippocampus/recognize.py` 82%、`left_hand/mcp_client.py` 83%
3. 整包 91% → 目标 93%+，把"低覆盖模块"清单清空
