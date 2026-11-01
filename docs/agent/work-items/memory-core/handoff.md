# handoff —— memory-core

## 现在什么状态

功能闭环 + 记忆质量（v0.4.0）：**多平台共用一套表**（schema v4）、识别层（text/emoji/可插拔 OCR+ASR/链接/文件/视频仅元数据）、
反思（事实/知识/关系/画像/风格/摘要）、**ADD-only 冲突消解（多值累积/同值合并/单值覆盖/否定失效，带 valid_from/valid_to + fact_log）**、
**多信号排序（相关度/重要度/时间衰减/反馈）**、**1 跳关联记忆（fact_link）**、100ms 硬预算召回、软引导注入、反馈回写、
**超长会话先摘要后淘汰**、**模型摘要优先 + 本地兜底**、**启动时从 fact 重建向量索引**、HTTP 服务、CLI、四个压测脚本、v1/v2/v3→v4 自动迁移。
61 项单测全绿；p99 分别 2.6 / 8.3 / 9.3ms（召回 / HTTP / 注入）；质量 bench 全过（6/6、top-1 1.0、关联 30/30）。

控制台（2026-10-30）：`python -m aiji.cli serve` 在 8787 托管 `/`；新增桌宠悬浮层
（whale 精灵 + 摸头/喂食/亲密度）、对话实时统计、工作区（文件树/预览/编辑/终端）、
ECharts 记忆星图；`启动控制台.bat` 常驻。全量 **281/281** 通过；截图在
`docs/agent/evidence/console-2026-10-30/`。

## 下一步

1. 有出网的机器上跑 `python tests/smoke_live_pool.py`，确认免费池真实可用（唯一没被证实的核心假设）。
2. 接真实 OCR/ASR：`Memory.open(ocr=..., asr=...)`，签名为 `(Item) -> (text, confidence)`。
3. 评测模型摘要的真实质量；补口癖/用词风格维度。
4. 向量层换真实 embedding（现在 `HashingEmbedder` 弱语义）+ 近似索引；关联扩到多跳/语义矛盾判定。
5. 用真实流量调重要度权重与半衰期（当前值是按经验定的，质量 bench 只证明了信号方向正确）。
6. 控制台：给聊天统计 / ECharts 交互补一个自动化浏览器测试（本机 Edge headless 被
   沙箱 ACL 拦住，见 excluded #49；可换有出网/无沙箱的机器跑 Playwright）。
7. 控制台：工作区终端目前是单命令 + 15s 超时，没有 PTY；需要真实终端再加 ConPTY。
8. 桌宠：控制台与 Tk 桌宠共用 `data/pet_state.json`，跨进程是后写覆盖；多宠物再迁 SQLite。

## 坑（踩过一次就别再踩）

- **别用 `tempfile.mkdtemp()` / `TemporaryDirectory()` 建测试目录**：本机沙箱下 0o700 目录子进程不可写、事后无法删除。用 `tests/test_memory.py::workdir()`。
- **别用系统临时目录**：SQLite 在当前沙箱里打不开 `%TEMP%`。统一落 `.tmp/`。
- **新增任何查询都必须带 `Ident.where()`**：跨平台隔离只靠 SQL 过滤（`test_cross_platform_does_not_leak`、`test_cross_platform_not_injected` 兜着）。
- **FTS 的 MATCH 在 2 字查询上永远空**：trigram 要 ≥3 字；验证索引用 `用户住` 这种 3 字片段。
- **外部内容表 `fact_fts` 的 `COUNT(*)` 读的是 `fact` 表**，不能判断索引状态；要看 `MATCH`。
- **改 schema 一定升 `PRAGMA user_version` 并写迁移**：ALTER 加列后必须 `INSERT INTO fact_fts(fact_fts) VALUES('rebuild')`。
- **并发计数必须加锁**：`_turn_counts` 的 read-modify-write 被覆盖会让摘要静默失效（n 序列跳过倍数）。
- **摘要写入要幂等 + 加锁**：否则两个 worker 会写两条同 `upto_turn` 的摘要。
- **识别产物不能学风格**：`_ingest(..., learn_style=False)`，OCR/ASR 文本是别人的话。
- **规则兜底改正则要连带跑 `TestExtract` 和 `TestPersistence`**（见 excluded 第 4 条）。
- **`Memory.close()` 必须调**：worker 是 daemon 线程，不 close 会丢未处理队列。
- **关联 JOIN 必须两侧都带身份条件 + `CROSS JOIN`**：只给 `fact` 侧过滤时 SQLite 会选
  “扫 fact 再全表扫 fact_link”的计划，5000 事实下 300ms（超预算）；`links_for` 里那两条注释别删。
- **`fact_link` 每事实最多 3 条边**：不封顶时 5000 条种子会写出十万级链接，种子时间和库体积都炸。
- **语义索引命中后要再按身份过滤**：索引用的是全局内存，不二次过滤就是跨平台串号（`test_semantic_hits_are_identity_filtered`）。
- **淘汰 turn 后要 `recaller.forget_session`**：否则内存缓冲还能召回已删除的原文。
- **多值谓词表（`_STABLE_PREDICATES`）决定覆盖还是累积**：加新槽位时先想清楚它是单值还是多值，未知谓词默认累积（宁多勿删）。

- **`python -m aiji` 不存在**（包没有 `__main__.py`）→ 起服务用 `python -m aiji.cli serve`。
- **Edge headless / `--screenshot` / CDP 在本机沙箱起不来**：network sandbox 授权被拒 +
  mojo `platform_channel` FATAL；只能截真实桌面窗口（excluded #49）。别把时间花在这上面。
- **别用 GUI 自动化去点用户正在用的 Edge 窗口**：会抢焦点/刷新用户的页面；验证 UI 优先
  用真实窗口截图 + HTTP 接口，必要时先问用户。
- **`docs/` 下用 pwsh 的 `Set-Content` / `New-Item` 会被沙箱拒绝**（即使 ACL 已修）；
  改文档用 fs 的 write/edit 工具（本会话实测可行）。

## 影响面 / 不要做什么

- 不要把模型调用搬进 `submit_turn` —— 会破坏"提交 p99 0.116ms"这条已取证的指标。
- 不要把 `recall()` / `inject()` 里的慢查询挪回主线程 —— 硬预算靠线程 + timeout 兜底。
- 不要删除 `fact` 行来"更新"事实 —— 用 supersede，历史要能回溯。
- 不要给 `media` 加 BLOB 列存文件 —— 只存引用 + 识别结果（单测断言无 BLOB）。
- 不要按平台分表/分库 —— 单套表 + `platform` 列。
- 不要给风格加"你就是……"这类人格描述，也不要让注入生成回复 —— `persona_touched` / `generated_reply` 有 bench 断言。
