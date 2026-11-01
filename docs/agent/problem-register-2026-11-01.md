# AIJiYi 问题总表（2026-11-01 汇总）

> 汇总四路证据：①静态模块审计 `audit-2026-11-01-modules.md`；②运行时探测 297 用例
> `probe-2026-11-01-runtime.md`；③自我生活深剖（同审计文件第六节）；④**6 小时现场审计**
> `data/field_audit_6h_summary.json` + `field_audit_6h.jsonl`（290 采样，18:36→23:25）。
>
> 分级只看"现在有没有在害人"：**P0 = 线上正在发生**；P1 = 已损坏但还在忍；P2 = 设计缺口/边界。

---

## 一、6 小时现场审计说了什么（新证据）

| 指标 | 数值 | 说明 |
|---|---|---|
| 采样 | 290 次（18:36:15 → 23:25:30，实际 4.82h） | 用户提前停（原定 6h），**健康检查 0 次失败** |
| QQ 真人消息 | 13,142 条 | 平均约 45 条/分钟 |
| QQ 机器人回复 | 537 → 836（**+299**） | 约 62 条/小时 |
| 30 分钟窗口 408 超时 | 平均 **22.2%**，最高 **35.7%**（30/84） | **177/290 个采样 >20%** |
| token 速率告警 | **277/290 采样（95%）** | 几乎全程超阈值 |
| 写队列 pending | max **56**、avg 16、**100/290 采样 >20** | 与 408 超时同向 |
| 回复停滞 | 4 段 ≥10 分钟（最长 **12 分钟**），19 个采样告警 | 34% 的分钟**没有**新回复 |
| 链路断连 | onebot 4 次、astrbot 4 次、napcat webui 1 次 | 都在 4.8h 内 |
| 错误日志 | 133 条；QQ 侧 `本群每分钟只能发5条消息`（result 299）**22 次** | 平台硬限流 |
| 今日模型消耗 | 3,080 请求 / 2,950,443 tokens | 平均 958 tokens/请求 |
| 余额 | catpaw **0**、zcode **0**、kuku **100**、trae **700**、qoder 1,400 | 三家见底 |

**结论**：服务本身没崩（health 0 失败），但**回复链路在"模型 408 + 平台 5 条/分钟 + 写队列堆积"三重挤压下会卡住**，
4.8 小时里出现 4 次 10 分钟以上的空窗。而这正是 `aiji/dispatch.py` 那套"限流感知退避"要解决的问题——
**它没在生产里跑**（send_queue 0 行，见 P0-3）。

---

## 二、P0：线上正在发生（4 条）

### P0-1 模型侧 408 超时率 22%，且持续
- 证据：`field_audit_6h.jsonl` 290 采样，30 分钟窗口 408 平均 22.2%、最高 35.7%；`agent2api_timeout_rate_high` 告警 8 次；
  `agent2api_token_rate_high` 277/290。
- 影响：抽取/摘要/反射在超时后重试或退化 heuristic，写队列 pending 被顶到 56；
  同时 408 会触发 pool 冷却，进一步减少可用路由。
- 相关：余额 catpaw/zcode 已归零，kuku 只剩 100 → 可用路由在缩。

### P0-2 QQ 平台硬限流：每分钟 5 条
- 证据：AstrBot 日志 `本群每分钟只能发5条消息`（result 299）22 次；`field_audit_6h_summary.json` 的 `latest_logged_errors` 前 3 条都是它。
- 影响：超出的回复直接被平台拒（不是静默丢弃，是抛 `ActionFailed`），
  表现为"回复缺失"而不是"慢"。**当前没有任何发送侧限速**。
- 讽刺点：`dispatch.py` 有 `rate_info()` + 被推迟行回拉，但生产没走它（见 P0-3）。

### P0-3 心脏层（outbox/dispatch/死信/租约）生产闲置
- 证据：生产库 `outbox / send_queue / write_queue / event_log / heart_state` **全 0 行**；
  e2e `tools/e2e_heart.py` **10/10** 通过。
- 影响：幂等、CAS 领取、指数退避、死信、租约回收、回放**全部不生效**；
  实际发送由 AstrBot 插件直连 NapCat，撞限流就抛错，没有退避与重试。
- 这是 P0-2 与 P0-4 的**共同根因**。

### P0-4 图片识别在生产里 100% 空产出
- 证据：`media` 表 `kind=image` **2317/2317** 文本为空、conf=0、`low_confidence`；
  运行时探测复现：带 `bytes` 的图 → `ok` + "陈明"；**只有 url 的图 → `low_confidence` + 空文本**。
- 根因：`memory.py` 的 OCR 适配器只读 `item.extra["bytes"]`，没有 url→bytes 下载分支；
  ASR 有 `media_audio.transcribe_item_audio` 回退，OCR 没有。
- 影响：图片既不进记忆也不参与抽取；"任意输入降维"对图片不成立。

---

## 三、P1：已损坏但还在忍（8 条）

### P1-1 反馈闭环从未被触发
- `injection` 825 行、`feedback` **全 NULL**；`fact.hit_count>0` 的行 = 0。
- 插件不回传 `used_ids` → 排序反馈、`cited` 精确纠错、S3 全闭环在生产全部不生效（只在 sim 里成立）。

### P1-2 自我生活"永远很累"
- 活服务 `/being`：energy 0.01 / social 0.01 / `proactive_ok=false`；
  24h 快进模拟：生产量级负载下 **92% 时间能量 <28、93% 活动是休息/睡觉、主动搭话 0%**。
- 因果：每条消息 -0.7、白天 -0.8/h，恢复只有睡眠 +3/h、休息 +4/40min。
- 连带：prompt 每轮注入"现在确实有点累"；`can_proactive` 恒 false → 主动搭话被永久阻断。

### P1-3 私有小本子退化
- 15 条笔记里 **13 条同一句**"刚才有点被刺到，先不全说出来"。
- 根因：`infer_event` 把"笨蛋/滚/垃圾"判 anger，孙吧群聊日常触发；这两条每轮进 prompt。

### P1-4 反思空转
- `reflection` 1122 行：**41% 有 trait 产出、52.8% summary 空洞**；当天 980 次（每次一次模型调用）。
- trait 786 行、24 条/身份封顶、**732 条置信度卡在 0.5–0.7**，进 prompt 只有前 6 条。

### P1-5 生命卡七引擎只有三个在链路
- 活的：内分泌（perturb/regress）、离线补偿、`endo_factor`（注入条数）。
- 死的：`DeclarativeEngine` / `ProceduralEngine` / `SynapticDiffusion` / `PersonalityKernel.apply_delta`
  ——唯一调用方是 `tests/test_life.py`；`ContextBudget` 只由 `GET /api/life` 调用；
  `LifeCard.to_dict()` 只存 `personality/endo/last_tick`（算了也不落库）。
- 生产证据：200 张卡 `personality.vector` 全是 `{"identity":0.25}`。

### P1-6 视觉在核心被静默丢弃
- `head._content_text` 只取 text part，`image_url` 被丢；图片描述靠外部 `127.0.0.1:3070`（未监听）；
  AstrBot 日志 `image description failed` 108 次。

### P1-7 数据完整性：清理留下孤儿
- `fact_log` 孤儿 **25 条**、`fact_link` 孤儿 **2 条**，全部指向 10-30/31 手工清理掉的测试身份
  （777/888/99000123/99000124/99000201/99000202/learn_probe/live/style_check_3/style_final_3/sunba_probe）。
- 运行时本身从不物理删（软删走 status），是**清理脚本没级联**。

### P1-8 链路断连与写队列堆积
- onebot 断 4 次、astrbot 掉 4 次、napcat webui 掉 1 次（4.8h 内）。
- pending max 56 / 100 个采样 >20：408 超时期间抽取任务滞留。

---

## 四、P2：设计缺口与边界（10 条）

| # | 问题 | 位置 |
|---|---|---|
| P2-1 | 概率回忆 `recall(mode="chat")` 无任何调用方（死代码） | `recall.py` |
| P2-2 | `proactive.py` 无生产调用方（主动在插件里） | `proactive.py` |
| P2-3 | `janitor.STALE_DAYS` 预留未接线，`stale_deleted` 恒 0 | `janitor.py` |
| P2-4 | `media.expires_at` 只记录不刷新，签名 url 过期需调用方重取 | `store.py` |
| P2-5 | 向量层默认 off；网关无 `/v1/embeddings`；仍暴力余弦 | `vectors.py` |
| P2-6 | 工作区默认关；`/api/shell` 单命令 15s、无 PTY、命令本身不可沙箱（实测 `cd /d C:\ && dir` 能列 C 盘） | `server.py` |
| P2-7 | 控制台 11 个 section 是死 markup（`titles` 只有 4 个键）；聊天统计/ECharts 无浏览器测试 | `console.html` |
| P2-8 | 桌宠 `pet_state.json` 跨进程后写覆盖；无多宠物 | `pet/growth.py` |
| P2-9 | 无 git / 无 CI / 无 LICENSE；137 个残留临时目录；`dist/` 只到 0.5.1 而服务是 0.8.3；README 写 426 项实际 438 | 仓库 |
| P2-10 | 日志无轮转；`console.err.log` 全是未处理连接异常堆栈 | 运行 |

---

## 五、建议修复顺序（按"线上收益/成本"）

1. **P0-3 + P0-2：把发送收回心脏层**（`send_queue` + `Dispatcher` 已经写好且 e2e 10/10）。
   接上 `rate_info()`，平台限流时延迟而不是抛错——这一步能同时消掉 22 次 QQ 限流错误和 4 段 12 分钟空窗。
2. **P0-4：OCR 补 url→bytes**（复用 `media_audio` 的下载 + `net_guard` 私网拦截，30 行内）。
3. **P0-1：模型侧降载**。408 占 22% 说明并发/超时参数不匹配：把抽取/摘要/反思的并发降到 1–2、
   超时从 30s 提到 60s 并加 `background=True` 排队；先把重试风暴按住，再谈路由。
4. **P1-1：插件回传 `used_ids`**（回复生成时把引用的 object 映射回 fact id → POST `/feedback`）。
5. **P1-2/P1-3：能量经济重算 + 小本子去重**（系数 + 去重窗口，都在 `being.py` 一个文件里）。
6. **P1-7：清理脚本级联删**（或改软删），顺手清掉 25+2 条孤儿。
7. **P1-4：反思加前置闸**（无新 assistant 发言就不调模型），能省掉 59% 的空转调用——直接省钱。
8. 其余 P2 按需。

---

## 六、一句话

**不是"哪里坏了"，是"三套已经写好的保险没接上"**：心脏层没接发送（P0-3）、
反馈没接回写（P1-1）、OCR 没接下载（P0-4）；于是平台限流和模型 408 直接打在主链路上，
表现为 4.8 小时里 4 次 10 分钟以上的回复空窗。
