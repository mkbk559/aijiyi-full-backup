# 运行时深度探测报告（2026-11-01）

> 上一份 `audit-2026-11-01-modules.md` 是**静态+生产库**审计；这一份是**真跑**：
> 每个子系统在隔离环境里用真 HTTP / 真识别引擎 / 真 SQLite / 真桌宠资产跑一遍，
> 全部用例机器判定 PASS/FAIL，原始输出已归档。
>
> **总计 297 个用例，295 PASS，2 FAIL**（FAIL 全是生产库完整性，见 §5）。

## 0. 怎么跑（全部可复现）

```powershell
python tools\probe\probe_http.py        # 90 用例：全端点矩阵 + 鉴权 + 越权 + Openai 兼容 + SSE
python tools\probe\probe_memory.py      # 30 用例：写入决策/排序/召回/注入/反馈/纠错/双时态/压缩/janitor/隔离/向量/死信
python tools\probe\probe_identify.py    # 26 用例：7 种输入状态机 + 真实 OCR/ASR + 落库闭环
python tools\probe\probe_cognition.py   # 27 用例：24h 快进模拟/生命卡/反思/自我层/成精体检
python tools\probe\probe_services.py    # 49 用例：声线/点歌/社交/图库/MCP/受控操作/闸门/节奏/桌宠
python tools\probe\probe_db.py          # 28 用例：生产库完整性（只读 immutable）
python tools\e2e_heart.py               # 10 断言：outbox→dispatch 全链路 + 闸门
python tools\sim_platforms.py --rounds 40   # 37 断言：8 角色全场景闭环
python bench\bench_memory_quality.py    # 冲突 6/6、top-1 1.0、关联 30/30
python bench\bench_recall.py --facts 2000 --platforms 2
```

隔离原则：探测全部用 `.tmp/probe-*/` 下的临时库 + `FakePool`（确定性、零成本）；
只有 `probe_db.py` 碰生产库，且用 `mode=ro&immutable=1` 只读打开。

## 1. 证据索引

| 编号 | 内容 | 归档（`C:\Users\22166\.dsh\evidence\2026-11-01\`） |
|---|---|---|
| P1 | HTTP 全端点 90/90 | `210630-probe-http-3-f92790\stdout.txt` |
| P2 | 记忆管线 30/30 | `210604-probe-memory-5-8075c3\stdout.txt` |
| P3 | 识别层 26/26（真 OCR/ASR） | `211124-probe-identify-3-a8ac69\stdout.txt` |
| P4 | 认知/自我生活 27/27 | `211626-probe-cognition-4-073253\stdout.txt` |
| P5 | 服务/安全层 49/49 | `213037-probe-services-11-59bb23\stdout.txt` |
| P6 | 生产库完整性 26/28 | `213225-probe-db-27ce06\stdout.txt` |
| P7 | 孤儿行定位 | `213358-dbg-orphans-2-15052e\stdout.txt` |
| P8 | 心脏层 e2e 10/10 | `211246-probe-e2e-heart-687a6e\stdout.txt` |
| P9 | 平台模拟 37/37（40 轮） | `211328-probe-sim-40-4f2d57\stdout.txt` |
| P10 | 平台模拟 31/34（24 轮，S4 前置不足） | `211300-probe-sim-d3928e\stdout.txt` |
| P11 | 质量 bench 全绿 | `213301-bench-quality-bb0a03\stdout.txt` |
| P12 | 召回 bench 2000 事实 | `213332-bench-recall-2000-6a390c\stdout.txt` |

## 2. 分项结果

### 2.1 HTTP 全端点（90/90 PASS）

- 公开：`/`、`/console`、`/health`、4 个资产 → 200；`/health` 字段齐全。
- 只读 API 26 条（`/v1/models`、`/stats`、`/api/memories`…）→ 全部 200。
- 写入链路：`/turns`→`/flush`→`/recall`（命中"南京"）→`/inject`（284 字、`persona_touched=false`）→`/feedback`（hit_count+1、injection.feedback 落库）→ 200/202 全通。
- OpenAI 兼容：`/v1/chat/completions` 结构完整；`stream=1` 返回 SSE；`/v1/gateway/messages` 归一化入口可用。
- 鉴权矩阵：设 `AIJI_API_KEY` 后 5 条受保护 GET + POST `/turns` 全 401；错 token 401；对 token 200；`/health`、`/`、资产保持公开。
- 越权：`../aiji.db`、`..\..\Windows\win.ini`、`C:/Windows/win.ini`、`%2e%2e/...` 全部 403 `path_outside_root`；写越权 400。
- 负面输入：空 query → 200 + `degraded=["empty_query"]`（优雅降级，不是 500）；空 content / 空 messages / 无 user 消息 → 400；未知路径 → 404；`limit=abc` → 400。
- **实测确认已知边界**：开启工作区后 `POST /api/shell {"cmd":"cd /d C:\\ && dir"}` 能列 C 盘（命令本身无沙箱）——与 PROJECT_STATE 风险条目一致。
- 外部依赖：`/api/stickers/update` 真联网 41s 后 200（150 条保留，有 <120s 上限）；社交/点歌/语音在未配置时返回明确 code（`social_not_configured` / `voice_converter_not_configured` / `profile_not_found`），**没有一个 500**。

### 2.2 记忆管线（30/30 PASS）

多值累积 / 同值合并（mentions+1）/ 单值 supersede（旧行留痕 + `superseded_by`）/ 否定 expire / 无目标 noop，五种决策 + `fact_log` 审计字段齐全；
排序（身份事实压过低置信备注）、召回（"我住哪"命中"南京"）、注入（≤700 字、带 kind/来源/置信标注、无越权）、
反馈（hit_count+1 + 留痕）、纠错（有 cited 只打 cited ×0.7；无 cited 降级 ×0.3；12 次后地板 0.15 不穿透）、
双时态回放（当时视角只见当时值）、跨身份隔离（B 的成都进不了 A）、
摘要（够 N 轮自动写、`upto_turn` 不重复）、强制压缩（forced=1、原文剩余 5 条）、
janitor（低置信软删 `status=deleted`、无重复边）、hash 向量索引（n=1）、语义命中身份二次过滤（0 泄漏）、
抽取失败进 `write_queue`（`kind=retry, state=pending`）、`media` 无 BLOB 列、风格四维 + 口癖命中。

### 2.3 识别层（26/26 PASS，含真实引擎）

- 状态机：无引擎 `unsupported`（带 `no_ocr_engine`/`no_asr_engine`）、text/emoji 正常、低置信→`low_confidence`、超时→`timeout`（0.4s 引擎）、抛错→`error`（不炸 worker）、link 抓 `<title>`、link 私网→`error:port_not_allowed`（SSRF 防线）、file 无提取器→`unsupported`、video→`metadata_only`、未知 kind→`unsupported`。
- **真实 OCR**（RapidOCR）：合成中文图 → `我叫陈明，1 住在深圳 周末喜欢钓鱼`，conf 0.74，1.5s；批量接口正常。
- **真实 ASR**（FunASR paraformer-zh + SAPI 合成参考音）：`我叫陈明，住在深圳，周末喜欢钓鱼。` 核心信息 3/3，conf 0.90，首载 33.6s、后续 0.6s。
- **落库闭环**：带 `bytes` 的图片 → `status=ok` 且文本落库；**只有 url 的图片 → `low_confidence` 且文本为空**（G1 复现，见 §3）。

### 2.4 认知 / 自我生活（27/27 PASS，含量化复现）

- **24h 快进模拟**（15 分钟步长、96 步）：
  - 空载：能量回到 84.2，活动轮换 7 种（听歌/看书/发呆/泡茶/整理表情包/刷视频/写小本子）。
  - 生产量级（每步 9 条消息 ≈839 条/天）：**最低 0.00、92% 时间能量 <28、93% 活动是休息/睡觉、`proactive_ok` 占比 0%**。
- 提示词：低电量必带"累 + 回复更短"；高电量不再说累；始终带"不是用户事实"隔离声明与"小本子不能照念"约束。
- 生命卡：愤怒→皮质醇 0.20→0.36；6 小时后回落 0.34；安慰→催产素 0.50→0.60；持久化往返一致；**DB 里只存 `personality/endo/last_tick`**（声明性/程序性不落库）。
- 反思：FakePool 输出被落成 trait（evidence=4）；无新对话再跑返回 None（幂等）；最小间隔内跳过；强制反思只累加不新建。
- 自我层：底线自检通过；往底线塞"温柔"→ `FloorViolation`；自我块 = 底线 + 稳定底色 + 局部适应 + 说话规则；空白状态如实说"白纸"。
- 成精体检：四层 `surface/continuity/defense/emergence`（partial/pass/pass/unverified），overall 0.688，`is_human_like_certified=false`。

### 2.5 服务 / 安全 / 桌宠（49/49 PASS）

声线注册表（内置 edge-default、ffmpeg 可用、未知声线 `profile_not_found`）、点歌（未知声线 / 无换声器都给明确 code）、
社交未配置 → `configured=false` + `social_not_configured`、图库环回地址导入被拒、
MCP 三服务加载（ddg/fetch/github）、默认只读、GitHub 默认停用、非白名单工具被拒、
操作策略（越界路径 4 种全拒、敏感文件 `aiji.db`/`id_rsa` 拒、危险命令 3 条全拒、普通命令放行）、
执行器（读成功；写未审批被拒且不静默直写；批准后写入成功）、
出口闸门（凭据拦截、超长拦截、文字表情剔除）、角色边界（写代码识别、代码块检测、提示词不含鱼缸角色扮演）、
拟人节奏（IM 有延迟 ≤9s、非 IM 为 0）、连发合并（并发同会话只生成 1 次）、
存在感广播、精灵图集 9 轨迹全有帧（6/8/8/4/5/8/6/6/6）、RGBA 帧、形象层渲染、
养成（摸头 +1 且 10s 冷却、无粮喂食被拒且不扣、改名生效）、
桌面窗口枚举 12 个、SAPI 2 个音色、FunASR + whisper 均在。

### 2.6 生产库完整性（26/28）

通过项：schema v10、主表非空、关联边**不跨身份**、turn 都有 account、状态一致性（active 无 valid_to / superseded 有 superseded_by / expired 有 valid_to / 窗口不倒挂）、单值谓词无重复 active、摘要幂等、style/trait/media 置信度都在 [0,1]、WAL 4.5MB、库 16.2MB。

失败项（见 §5）：`fact_link` 孤儿 2 条、`fact_log` 孤儿 25 条。

### 2.7 性能（复跑）

- 质量 bench：冲突消解 **6/6**、排序 top-1 **1.0**（重要度/衰减/反馈各 1.0）、关联召回 **30/30**，`ok=true`。
- 召回 bench（2000 事实 / 2 平台）：静默 p50 0.97 / p95 2.02 / **p99 2.67ms**；写压 p99 **3.81ms**；`degraded=0`、`over_budget=0`；**submit p99 0.077ms**。

### 2.8 心脏层与平台模拟

- `e2e_heart.py`：pump 3 条、采集不回复、优先级 0<2、幂等（重复 enqueue 返回 None 且只 1 行）、gate 拦截坏回复、dispatch 送达、多用户出站、零积压、enqueued==processed → **10/10**。
- `sim_platforms.py --rounds 40`：**37/37**（含 S3 全闭环、S4 三时点回放、S5 变体注入零进 fact、S6 摘要幂等、janitor 租约、调度零死信）。
- **参数边界**：`--rounds 24` 时 S4 三条失败（历史行只有 1 条，前置不足）→ **31/34**。即 S4 需要 ≥约 35 轮才凑够三次搬家，短跑不代表缺陷。

## 3. 运行时复现/确认的缺口

| # | 缺口 | 运行时证据 |
|---|---|---|
| R1 | **只有 url 的图片 OCR 不生效**（G1 复现） | P3：`img-e2e`（有字节）→ ok + "陈明"；`img-url-only` → low_confidence + text 空 |
| R2 | **生产库反馈闭环 0** | P6：`injection=825`、`feedback_null=825/825` |
| R3 | **心脏层生产闲置** | P6：outbox/send_queue/write_queue/event_log/heart_state 全 0 行（e2e 里 10/10 正常） |
| R4 | **自我生活能量经济失衡** | P4：生产量级负载下 92% 时间低电量、93% 活动是休息、主动搭话 0% |
| R5 | **图片识别 100% 空文本** | P6：image=2317 / empty=2317 |
| R6 | **清理测试身份留下孤儿** | P7：25 条 fact_log + 2 条 fact_link 指向已删除的 777/888/99000xxx/probe 系列身份；运行时从不物理删，是 10-30/31 手工清理没清关联表 |

## 4. 上一轮静态审计被"真跑"校正的地方

1. "HTTP 65 条路由" → 实测 90 个用例覆盖约 62 条路由，**全部可达**，含 SSE。
2. "记忆内核在跑" → 不只是"有数据"，**五类写入决策/纠错地板/双时态/压缩/janitor 全部真跑通过**。
3. "识别层能插拔" → 真实 OCR 1.5s、真实 ASR 3/3，**引擎确实可用**；但 url-only 路径确认不生效。
4. "自我生活永远累" → 从"看快照"升级为**可复现模拟**：空载 84.2 能量，生产负载 0.00。
5. "心脏层闲置" → e2e 10/10 与生产 0 行并存，说明**不是坏，是没接**。
6. "数据库不物理删" → 运行时成立，但**手工清理破坏了它**（R6）。

## 5. 结论

**能跑的部分是真能跑**：端点、记忆管线、识别（含真引擎）、安全边界、桌宠、心脏层、平台模拟、性能，全部有机器证据。
**不能跑的部分是"链没接"而不是"代码烂"**：图片 url→bytes、反馈回传、心脏层接管发送、能量经济。
**唯一的数据卫生问题**来自手工清理：`fact_log`/`fact_link` 有孤儿，建议清理脚本按身份级联删除（或改软删 + `status='deleted'`）。
