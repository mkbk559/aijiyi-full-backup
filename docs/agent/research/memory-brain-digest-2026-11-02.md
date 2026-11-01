# `y083245/dsh-memory-brain` 内容摘要 + 对 AIJiYi 的可用性判定（2026-11-02）

> **已合并**：结论已并入唯一入口
> [`docs/agent/design/borrow-plan-2026-11-02.md`](../design/borrow-plan-2026-11-02.md) §2.4。
> 本文件保留**内容级细节**（仓库地图、模块清单、可抄常数）。

> 上一份报告只读了 README / FORK_FEATURES / `memory-decay.js`。这一份按**手册同样的规格**把仓库
> **内容**过了一遍：仓库地图、记忆主线、人格主线、主动主线、工程实践，最后给可用性判定。
>
> 抓取物落盘：`.tmp/research/mb/`（8 个文件，见 §8）；早期 README 等在 `.tmp/research/raw/`。
> 全部来自原文（A 级=我读到源码/文档，B 级=项目自述数字），**没有运行过**。

---

## 1. 它到底是什么（一句话 + 一组事实）

**一句话**：一个把 QQ（经 SnowLuma/OneBot）接到 **DeepSeek Harness** 的桥，再在桥上长出一层
「长期记忆 + 人格 + 主动行为」——**同一个题目的 Node 版**（AIJiYi 是 Python 版中间件）。

| 项 | 值 | 来源 |
|---|---|---|
| 出身 | fork 自 `Derpyu520/qq-bridge`（MIT），保留上游历史 | README |
| 许可 | **MIT** | LICENSE |
| 运行时 | Node ≥22.13（推荐 24）+ Python 3.12（uv，仅 RAG sidecar） | package.json / README |
| 主程序 | `src/bridge.js` 约 **10,400 行**（785KB，单文件；作者自己写"QSH 计划拆分"） | FOLDER_MAP §一 |
| 脚本 | `scripts/` **87 个**（83 `.mjs` + 4 JSON 基线），按 `test-*/probe-*/verify-*/setup-*` 前缀分类 | FOLDER_MAP §二 |
| 记忆服务 | `sidecars/memory-rag`（Python，:8093）：Chroma 块向量 + SQLite FTS5 双写 | memory-rag README |
| 控制台 | `public/console.html`，127.0.0.1:3100 | FOLDER_MAP |
| 数据 | 全部在 `state/`（`.gitignore` 排除）：存档、账本、人格、代谢、报表快照 | FOLDER_MAP §一 |

**它自己划的边界**（FOLDER_MAP 原话）：`src/` 只应有 `.js`（`.bak` 既污染搜索又像 module）；
`state/` `audio/` `roles/` **绝不删除/移动**；改 preset 必须重新生成 patch。

---

## 2. 仓库地图（模块清单）

| 目录 | 作用 | 对 AIJiYi 的相关度 |
|---|---|---|
| `src/bridge.js` | 主程序：收发、闸门、注入、出口、指令 | 低（单文件巨石，反面教材） |
| `src/core/` | 目录存在（API 列出为 dir），未逐个读 | — |
| `src/long-memory.js` | 按天 JSONL 永久存档 + 人物账本 | **高**（原文永久化） |
| `src/memory-decay.js` | FSRS-5 代谢（S/D/R、冻结、解冻） | **最高**（已单列，见主报告 P0-2） |
| `src/memory-rag-client.js` / `rag-control.js` | RAG 开关、降级、进程拉起 | **高**（总开关模式） |
| `src/ledger-suggest.js` | 账本建议（候选 + 采用率） | **高**（治"硬写"） |
| `src/life-loop.js` | 到期约定提醒 + 忽然想起 | 中 |
| `src/persona*.js` | 人格判读（大五/后天/好恶），先影子模式 | 中 |
| `src/daily-report.js` | 零模型日报（今天现算，历史读快照） | 中 |
| `src/api-hub.js` | 控制台「API 管理」统一填写 | 中 |
| `dsh/agent-presets/qq-chat{,-v2}` | preset 权威源 → 生成 bundle patch | 低（DSH 特有） |
| `plugins/qq-agent-presets` | 把角色卡/人格/记忆拼进提示词 | 低 |
| `roles/*.md` | 人格卡（控制台可编辑，保存即生效） | 低 |
| `scripts/test-audit.mjs` | **测试清单 `tests[]`**（离线、不发真消息） | **高**（清单化） |
| `scripts/probe-*.mjs` | 探针：问 DSH「实际支持什么」 | **高**（方法） |
| `scripts/console-ui-*.json` | 控制台**离线 UI 回归基线** | 中 |
| `scripts/harden-state-acl.mjs` / `diagnose-acl.mjs` | 收/查 ACL（Windows 上 `0o600` 是空操作） | 中（与本机沙箱同题） |

---

## 3. 记忆主线（它的核心，也是我们最该看的）

### 3.1 三层文件（醒时）
1. **存档**：`state/long-memory/archive/<group_X|private_X>/<YYYY-MM>.jsonl`，按天追加，**永久保存**，
   写盘前剔除已知会话令牌；
2. **账本**：按 QQ 号的长期事实（喜好/约定/经历），含核心条目、约定到期/兑现状态、归档与去重提示；
3. **代谢状态**：`state/memory-decay.json` 只存 `ID + 数字`（`ltm:` / `msg:` / `diary:`），**不存原文**。

→ 与 AIJiYi 对照：AIJiYi 的 `turn` 表是**唯一**原文，超 400 轮 `DELETE`；账本≈`fact`+`profile`；
代谢状态**没有**。这三层的分工值得照搬（原文永久 / 结构化条目 / 只存数字的状态）。

### 3.2 RAG sidecar（睡时的整理对象）
- 切片：**30 分钟时间分段 + 话题低谷切块，每块 3–15 条**（`segmenter.py`）；
- 双写：Chroma（块向量）+ SQLite `FTS5`（jieba / BM25，OR 查询）；**两路 RRF**；
- L1/L2 分层：`chunks`（L1，块+向量+水位）与 `summaries/entities/summary_entities/relations/distill_state`（L2）；
  **L2 每行带会话 key**（"从群里的点只能走到群里的摘要"）；
- 提炼层：块结束就写一句摘要 + 抽实体/关系，**摘要挂「块编号 + 块原文哈希」**；
  对账时"同块同哈希 → 直接标 done，**不调模型**；原文变了 → 删旧摘要重提炼"；
- 检索**四路**：块向量 / 块 BM25 / **摘要向量** / **关系图**（实体种子去枢纽点，得分 `Σ ln(1+N/df)`，只走 1 跳），
  五路一起 RRF（k=60）；
- `/recall`（唤醒时自动想起）：查询=最近 1–3 条来信，**排除 `t_end` 在 12 小时内的摘要**（避免"刚聊完又想起来"），
  三路 RRF 取前 5；
- 遗忘（P2）：`POST /forget {key}` → **墓碑** + 同一事务删 L1+L2；有遗忘请求且拿不到索引锁 → 503；
- 重建（P2）：`POST /rebuild` 只清 L1（块/向量/水位），**保 L2 摘要**，靠哈希对账挂回，再 `prune_orphans()`。

→ 与 AIJiYi 对照（可直接借的四个点）：
| 它的做法 | AIJiYi 现在 | 借法 |
|---|---|---|
| 摘要按**块原文哈希**去重复用 | 摘要只有 `up_to_turn` 游标 | 给摘要加 `source_hash`，同哈希直接复用，省调用 |
| 临时"进行中消息"单独打分 | 无（只有 turn 表） | 可省（AIJiYi 的 turn 就是上下文） |
| `/recall` **排除 12h 内**的旧事 | 无"忽然想起" | P2 时照抄这个防重复参数 |
| 遗忘=**墓碑 + 逐 key 删 + 锁** | 无显式遗忘 API | 用户说"忘掉"时用它这套语义 |

### 3.3 配置里能直接抄的常数（`config.example.json`，A）
```
role.maxInjectChars            6000        注入总字数上限
memoryRag.recall.mode          shadow      off / shadow / on（先只记录不注入）
memoryRag.recall.timeoutMs     400         唤醒路径上的硬上限，"超时当作没想起"
memoryRag.suggest              perWake 3 / maxShows 2 / ttlHours 72 / timeoutMs 300
decay                          freezeBelow 0.5 / reviewCooldownMs 3600000
lifeLoop                       recallPerDay 2 / cooldownDays 7 / quiet [3,8]
lifeLoop.associate             minAgeDays 2 / cooldownDays 7 / timeoutMs 1500
```
→ 这组数正好回答"AIJiYi 的注入预算、冻结线、去重窗口该取多少"：**注入 6000 字 / 冻结 0.5 / 冷却 1 小时 /
每条旧事 7 天只提一次 / 只提 2 天前的旧事**。

---

## 4. 人格主线
- `persona*.js`：从对话判读情绪、关系变化、行为倾向，维护**每人一份**人格状态（大五/后天/好恶/关系）；
  **模型只判断"发生了什么"，变多少由代码决定**（与 AIJiYi 的 `trait` 差分同构）；
- **影子模式**：先只记录判读、不改回复；稳定后再勾"注入"；
- 注入走 DSH preset（角色卡 + 人格状态 + 记忆片段拼提示词），控制台「人格与角色」页可编辑 `roles/*.md`；
- 判读状态在 `state/persona/`，**日志不写聊天原文**。

→ 借：**影子模式**（两段上线）+ **"模型只判发生了什么，变多少由代码定"**（AIJiYi 的 `extract` 已有这个味道，
但 `trait` 的 delta 是模型直接给的权重，可考虑改成"代码定步长"）。

## 5. 主动主线（life-loop）
- 每分钟检查账本里到期的约定 → 到点私聊提醒（每人每天 1 次、全局可配上限）；
- 静默唤醒补上"她最近写的日记 + 一条忽然想起的旧事"；**素材只取主人私聊 / 公开账本条目 / 她自己的日记，
  不碰群聊存档**（隐私边界写在代码里）；
- 配额：每次唤醒最多 1 条、每活动日 2 条、同一条 7 天内不重复、安静时段 `quiet [3,8]` 不投递。

→ 与 AIJiYi 对照：`proactive.py` 审计发现"生产 0 行、无调用方"（excluded #54）——它这套**配额 + 素材白名单 +
去重窗口**正好是 AIJiYi 缺的那部分（不是缺引擎，是缺"敢开"的约束）。

## 6. 工程实践（这部分与记忆无关，但对 AIJiYi 很实用）
1. **测试清单化**：`scripts/test-audit.mjs` 里的 `tests[]` 数组就是"什么算回归"，新增测试要登记；
   另有 `test-dsh-*.mjs`（需要活宿主，不在 audit 里）——**分层跑**。
2. **探针文化**：`probe-*.mjs` 专门问宿主"你实际支持什么"（RPC 形状、工具清单、事件流、settings 命名空间），
   并把"取错字段不报错"的坑写进注释（`probe-snowluma-heartbeat.mjs`：SDK 不校验 `data` 载荷）。
3. **UI 基线**：`console-ui-*.json` + `preview:console`，离线回归；基线**只在后端契约真变时才重录**。
4. **文档门禁**：`.gitignore` 把 `docs/audits|design|legacy|research` 整体排除（"扫出过多份含本机真实路径的内容"）
   → 公开仓库只剩 `docs/README` `FOLDER_MAP` `guides/`。
   ⚠ **对 AIJiYi 的提醒**：`docs/agent/` 里同样有大量本机绝对路径（`C:\Users\22166\.dsh\evidence\...`、
   `D:\AIJiYi`）；将来若要开源，必须先 grep 路径（与本项目 LICENSE 是专有许可这件事一起处理）。
5. **失败不阻断**：`harden-state-acl.mjs` 任何路径都以 0 退出；`diagnose-acl.mjs` 只读盘点 + 打印可复制命令
   （"必须在无沙箱终端跑"）——与 AIJiYi 的 ACL 教训同题。

## 7. 对 AIJiYi 的可用性判定

| 优先级 | 借什么 | 落到哪 | 验收 |
|---|---|---|---|
| **P0** | FSRS-5 代谢 + 冻结线 + "查到即解冻" + 核心条目豁免 | `fact` 加 `stability/difficulty/last_review/frozen` | 90 天未提→frozen；查一次→解冻且 S 增大；bench p99 不退化 |
| **P0** | **影子模式两段上线** | 新打分/新注入先 `mode=shadow` 只记录 | 影子日志里能看到"本来会注入哪几条" |
| **P1** | 账本建议（候选 + `perWake/maxShows/ttl` + 采用率） | 替代"反思直接写 trait" | 候选采用率可查；trait 空转率下降 |
| **P1** | RAG 总开关 + 超时降级 + 换模型重建 | `vectors.py` 加 `mode/enabled`、超时回退 FTS | 关了不影响召回主路径；重建期只走关键词 |
| **P1** | 摘要按**原文哈希**复用 | `writer._summarize` 加 `source_hash` | 同哈希第二次不调模型（探针计数=0） |
| **P2** | 显式遗忘（墓碑 + 逐 key 删 + 索引锁） | 新增 CLI/HTTP `forget` | 删后召回为空、审计留痕 |
| **P2** | 注入/唤醒的**常数**（6000 字 / 12h 排除 / 7 天去重） | `inject.py` / 未来的"忽然想起" | 注入块体积与去重行为可测 |
| **P2** | 测试清单化 + 探针分层 | `tests/` 加清单、`bench/probe_*.py` | 新增测试必须登记才进 audit |

**不借**：`src/bridge.js` 单文件巨石（10,400 行）；Chroma+SQLite 双写（AIJiYi 单存储已够）；
DSH preset/bundle patch 机制；Linux QQ 版本锁定与 `setcap`；`socialV2` 那套"潜水/唤醒概率"参数
（那是"像人"的调参，不是记忆）。

**风险**：① 它自己说"记忆服务与一键脚本只在 Linux 测过"；② README 没有测试数字（只有脚本清单），
与 AIJiYi"每项能力都有 evidence 路径"的标准不同；③ RAG 依赖 ≥10GB 显存跑 Qwen3-Embedding 或云端 API，
AIJiYi 是 CPU + 软嵌入路线，**召回质量不可对标**。

---

## 8. 证据（本地可复核）

抓取时间 2026-11-02 01:3x（Asia/Shanghai），全部落在 `.tmp/research/mb/`：

| 文件 | 字节 |
|---|---:|
| `docs__FOLDER_MAP.md` | 15,129 |
| `docs__guides__FULL_GUIDE.md` | 34,391 |
| `config.example.json` | 9,179 |
| `sidecars__memory-rag__README.md` | 23,956 |
| `package.json` / `RULES.md` | 5,242 / 11,917 |
| `docs__guides__SECURITY_BASELINE.md` / `PRIVACY.md` | 16,887 / 5,435 |

早期抓取（README / FORK_FEATURES / `memory-decay.js`）在 `.tmp/research/raw/`，
清单与 sha256 见 `sleep-framework-survey-2026-11-02.md` §7。
`docs/design/MEMORY_DECAY_SPEC.md` **404**（该目录被 `.gitignore` 排除，公开仓库里没有）——
所以 FSRS 参数只能从 `memory-decay.js` 源码读（已读，见主报告 §6.3）。
