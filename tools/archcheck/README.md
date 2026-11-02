# tools/archcheck —— 架构图与整仓的"机器判据"工具箱

这套脚本用来**证明**而不是描述：架构图能不能跑、文字会不会溢出、改造前后行为是否一致、服务端起不起得来。
所有输出落在 `tools/archcheck/results/`（gitignore），代码本身在版本库里，换会话可以直接重跑。

| 脚本 | 判什么 | 怎么跑 | 通过标准 |
|---|---|---|---|
| `extract.js` | 从 `docs/architecture.html` 抽出内联脚本，确认零外链 | `node extract.js` | `script blocks: 1`、`external src attrs: 0` |
| `harness.js` | 用 DOM 桩跑真实内联脚本，24 条断言（68 节点/9 层/88 边/抽屉/搜索/主链路/hash 深链/眼睛跟随等），并导出 `results/nodes.json` | `node harness.js` | `SUMMARY fail=0` |
| `measure.py` | 用真字体（segoeui + msyh）逐节点量标题/副标题宽度，列出越界量 | `python measure.py` | 末行 `no overflow` |
| `cdp_check2.py` | 真 Edge + CDP 量 `getBoundingClientRect`，是文字溢出的**最终真值**；顺带截 `results/cdp-zoom14.png` | 先起 Edge：`msedge --remote-debugging-port=9223 --user-data-dir=.tmp/edge-profile file:///.../architecture.html`，再 `python cdp_check2.py` | `items=0` |
| `run_suite.py` | 全量单测逐用例结果落 JSON（绕开沙箱 tempfile 噪声） | `python run_suite.py results/suite_after.json` | — |
| `diff_suite.py` | 改造前后逐条比对，任何一条状态变化都要解释 | `python diff_suite.py results/suite_before.json results/suite_after.json` | `VERDICT SAME` |
| `http_smoke.py` | 真起 `aiji.senses.cli serve`，直连（绕开 Clash 代理）拉页面与接口 | `python http_smoke.py` | `GET / ok= True`、`/api/operations/services -> 200`、`/v1/models -> 200` |

前置：

- `node` 用 DSH 自带的：`C:\Users\22166\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe`（PATH 里没有 node）。
- Edge 的 headless 在本机被 ACL 拦住，只能起**真的有窗口**的实例 + `--remote-debugging-port`。
- 本机全局代理（`HTTP_PROXY=127.0.0.1:7897`）会影响 `Invoke-WebRequest`，所以 `http_smoke.py` 里显式 `ProxyHandler({})`。

判据分工：**结构/交互**看 harness，**文字越界**看 measure.py 估、CDP 定，**行为一致性**看 suite diff。
