# docs/architecture.html 体检（2026-11-03）

对象：`docs/architecture.html`（人体架构图：9 个人体系统 / 68 节点 / 88 边 / 抽屉 + 儿童行为动画），83,329 B，mtime 2026-11-03 00:09。

## 怎么复现

```powershell
# 1) 抽出内联脚本 + 语法校验
node .tmp\archcheck\extract.js          # 生成 arch0.js，报 script 块数 / 外链
node --check .tmp\archcheck\arch0.js    # 期望 exit 0

# 2) DOM 桩里跑真脚本，24 条断言
node .tmp\archcheck\harness.js          # 期望 SUMMARY pass>=23；唯一 fail 是文字宽度粗估

# 3) 真 Edge + CDP 量文字溢出（headless 在本沙箱不可用）
#    先带 --remote-debugging-port=9223 --user-data-dir=.tmp\archcheck\edge-profile 起 Edge，
#    文件地址 file:///D:/AIJiYi/docs/architecture.html
python .tmp\archcheck\cdp_check2.py     # 输出 cdp_cover.json + cdp-zoom14.png

# 4) 事实核对
python .tmp\archcheck\db_facts.py       # 表数 / 每表行数（只读 mode=ro）
python .tmp\archcheck\facts_check.py    # 68 个节点文件 + 156 个符号 + 端口
python .tmp\archcheck\test_counts.py    # 逐测试文件用例数
python .tmp\archcheck\measure.py        # PIL + 真实字体量文字宽度
```

原始机器输出（stdout/stderr/sha256）：
- 语法：`C:\Users\22166\.dsh\evidence\2026-11-02\001359-archcheck-extract-and-node-check-2-0b2418\`
- DOM 断言：`...\001753-archcheck-text-measure-2-354e73\`
- CDP 量宽 + 截图：`...\001834-archcheck-cdp-measure-9b2ec9\`、`...\002145-archcheck-cdp-cover-2-70544d\`
- 库表：`...\001436-archcheck-db-facts-c45a4c\`
- 文件/符号/端口：`...\002354-archcheck-facts-files-symbols-f1ee00\`
- 测试收集：`...\002453-archcheck-test-count-per-file-2-7686e5\`

图片与 JSON 产物（`docs/` 在本沙箱由子进程不可写，故留在工作区 `.tmp`）：
`.tmp\archcheck\cdp-fit.png`、`.tmp\archcheck\cdp-zoom14.png`、`.tmp\archcheck\cdp_cover.json`、`.tmp\archcheck\cdp_measure.json`、`.tmp\archcheck\nodes.json`

## 结论（一句话）

**能打开、能用、数据来源基本属实**；需要修的是：9 个节点第二行小字溢出方框（3 个被邻框盖住，出现"…B站可"这种截字）、顶栏「单测 478」应为 449、库计数与 `heart_state` 那行已过期、2 个符号挂错文件。

## 明细

| 项 | 期望/图中写 | 实测 | 判定 |
|---|---|---|---|
| 内联脚本语法 | 可运行 | `node --check` exit 0 | PASS |
| 外部依赖 | 零依赖 | script 块 1 个、`src=` 0 个、外链只有正文里的 `127.0.0.1:8787` | PASS |
| 层/节点/边 | 9 / 68 / 88 | 9 / 68 / 88 | PASS |
| 节点落位 | 不重叠不越界 | 68 个全有落位、两两交集 0、全在 1900×2400 内 | PASS |
| 边 | 都画得出 | 88/88 有路径，无 NaN，无自环 | PASS |
| 交互 | 点选/搜索/分层/主链路/深链/抽屉 | 6 项全对（主链路 18 节点） | PASS |
| 真渲染 | 打开无白屏无报错 | 真 Edge 窗口 + CDP：68 节点 88 边 | PASS |
| 文字排版 | 不超框 | **9 处副标题溢框 ≈1–20 SVG px**，3 处被邻框盖住 | **FAIL（小）** |
| 节点文件 | 都存在 | 68/68 存在 | PASS |
| 关键符号 | 都在对应文件 | 156 中 153 命中，2 处挂错文件 + 1 处限定名 | **FAIL（小）** |
| 顶栏数字 | 55 模块 / v11 / 24 表 / 478 单测 / 43 测试文件 / 9 bench | 55 ✓ v11 ✓ 24 ✓ **449 ✗** 43 ✓ 9 ✓ | 1 项过期 |
| 库计数 | 事实 21,365 等（2026-11-02） | 21,431 / 24,966 / 23,542 / 7,660 / 1,923 / 1,533 / 1,850 / 1,521 / 723 | 全部上漂 |
| "heart 全 0 行" | 全 0 | `heart_state` **8 行**（其余三张仍 0） | 过期 |

### 排版缺陷的具体位置（CDP 实测，zoom=0.259）

| 节点 | 溢出 | 被谁盖住 |
|---|---|---|
| qq | +5.2 px 屏幕（≈20 SVG px） | napcat，2.1 px |
| song | +5.2 px | —（右侧空白） |
| server | +5.1 px | onebot，2.0 px |
| selfhood | +4.6 px | quota，1.0 px |
| voice | +2.6 px | — |
| recognize | +2.3 px | — |
| pool | +1.9 px | — |
| t_mcp | +1.3 px | — |
| recall | +0.3 px | — |

根因：`.node .s`（10px）没有折行/省略号，文字可用宽只有 `节点宽 − 15 − 8`（113–125 px），而文案实宽 120–150 px。修法二选一：给 `s` 加短文案（≤11 个汉字）或把副标题折两行/用 `textLength` + `lengthAdjust`；也可把盒子加宽 6–10 px。

### 环境边界

- Edge **headless 仍不可用**（`--headless=new` 只吐 crashpad `OpenProcess 拒绝访问`，不出 PNG）→ 像素证据只能来自真窗口 + CDP。
- 视觉后端本轮中途 402（siliconflow 余额不足），只有前两张截图有视觉判读。
- 后台跑全量单测时 77 个用例因 `tempfile` 落在沙箱 TEMP 目录 `PermissionError`（已知问题）；`Ran 449` 与逐文件 `countTestCases` 的 449 一致，说明 449 是当前真实用例数。
