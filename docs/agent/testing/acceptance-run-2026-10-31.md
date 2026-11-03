> **历史文档（设定已废弃）**：这里的「大肥鱼 / 鱼缸」是 2026-11-03 之前的人设，
> 那一天已整体替换为「玛卡巴卡」（约 6 岁半的小孩，见
> `docs/agent/design/persona-child-2026-11-03.md`），相关记忆与素材均已清空。
> 本文只作当时的决策/研究记录，不要当作现行设定。

# 验收执行记录（2026-10-31 01:15）

按 [acceptance-checklist.md](acceptance-checklist.md) 顺序执行；自动项已跑，QQ 真机项需要你在客户端点/等。

## 环境

- AIJiYi 8787：PID 26424，`/health` 200
- AstrBot 6185：运行中
- OneBot 6199：已连接（socket 可连）
- 测试群：`562686653`
- A：`2216608312`（主人/花园小区）
- B：`2221813100`（小老虎/笼子里面）
- 百度登录态：`data/browser/baidu_state.json` 存在

## P0 记忆核心

| ID | 结果 | 证据 |
|---|---|---|
| P0-01 重启持久化 | ✅ PASS | 重启后 A facts=2（主人/花园小区），B facts=3（小老虎/笼子），`/api/memories` 一致 |
| P0-02 群内多人隔离 | ✅ PASS | `cross_talk.a_has_b=false, b_has_a=false` |
| P0-03 非 @ 消息入库 | ✅ PASS | 群 `turn` 表 16 条，A/B 各 5–7 条 |
| P0-04 @ 回复带历史 | 🟡 PARTIAL | A 回复“名字没记住，住在花园小区”：地址对；“主人”被当称呼，不是姓名 |
| P0-05 事实抽取 | ✅ PASS | A：自称主人/住花园小区；B：小老虎/住笼子 |
| P0-06 旧客服腔过滤 | ✅ PASS | `在吗` → “在，鱼缸里泡着呢。主人有啥事？”；banned_hit=[] |
| P0-07 长历史/摘要 | ✅ PASS | 25 轮测试：`summary` 生成，`upto_turn=20`，摘要“用户连续20轮发言…住在测试路0–19号”；测试数据已清理 |
| P0-08 跨平台隔离 | ✅ PASS | 全量单测 |
| P0-09 冲突消解 | ✅ PASS | 全量单测 |
| P0-10 OpenAI 身份契约 | ✅ PASS | `test_openai_user_field_maps_qq_group_to_isolated_memory` |
| P0-11 下游 API Key | ✅ PASS | `test_openai_routes_require_bearer_key_when_configured` |
| P0-12 记忆搜索/列表 | ✅ PASS | `/api/memories` 返回 A/B 事实 |

## P1 QQ / 语音 / 主动

| ID | 结果 | 证据 |
|---|---|---|
| P1-01 私聊 | ✅ PASS | 历史 turn 有 private 记录 |
| P1-02 群 @ | ✅ PASS | #153/#154、#155/#156 |
| P1-03 语音单条 | 🟡 PARTIAL | 插件链测 `auto/text=['At','Plain']`、`auto/voice=['Record']`；QQ 真机待验 |
| P1-04 语音失败回退 | ✅ PASS | 模拟 `_tts` 返回 None → 链为 `['At','Plain']`，回退文字 |
| P1-05 语音打断/VAD | ⬜ PENDING | 未实现 VAD |
| P1-06 主动消息 | ✅ PASS（逻辑）/🟡 真机 | 逻辑单测：`_tick_heartbeat` 发送并计次；23:33 日志有真实 `proactive -> private:2216608312` |
| P1-07 主动冷却/额度 | ⬜ PENDING | 未连续压测 |
| P1-08 群限流/冷却 | ✅ PASS | 滑动窗口：群 8/60s、私聊 15/60s；超限先等（max 20s），等太久回“慢点”一次；单测 `LIMITER_WAIT_OK / LIMITER_REJECT_OK / PLUGIN_NOTICE_OK` |
| P1-09 断线重连 | ✅ PASS | 多次重启 AstrBot 后 6199 自动 Established |
| P1-10 图片入站 | ⬜ PENDING | 未做（B05） |
| P1-11 语音入站 | ⬜ PENDING | 未做（B05） |
| P1-12 图片/表情出站 | ⬜ PENDING | 未做 |
| P1-13 长回复分段 | ⬜ PENDING | 未做 |
| P1-14 白名单/管理员 | ⬜ PENDING | 未做 |

## P2 人格学习

| ID | 结果 | 证据 |
|---|---|---|
| P2-01 百度登录态 | ✅ PASS | `baidu_state.json` 15442 bytes |
| P2-02 抓帖子 | 🟡 PARTIAL | 9 帖 + 2 正文，样本小 |
| P2-03 过滤 | 🟡 PARTIAL | 关键词过滤；需人工抽检 |
| P2-04 学习写盘 | ✅ PASS | `data/persona/sunba.json`：8 traits + 8 examples |
| P2-05 注入生效 | ✅ PASS | 回复“五百米全靠迟到激发” |
| P2-06 QQ 触发学习 | 🟡 PARTIAL | 触发逻辑单测 PASS；真机待验 |
| P2-07 每轮不同内容 | ⬜ PENDING | 当前固定页 |
| P2-08 不学坏 | 🟡 PARTIAL | 过滤存在，需抽检 |

## P3 控制台 / 桌宠

| ID | 结果 | 证据 |
|---|---|---|
| P3-01 4 页签 | ✅ PASS | `nav_buttons=4`，无 workspace nav |
| P3-02 工作区关闭 | ✅ PASS | `/api/fs?path=` → 403 |
| P3-03 仪表盘/记忆/日志 | ✅ PASS | 之前真实窗口截图 |
| P3-04 聊天统计 | ✅ PASS | 之前截图/SSE |
| P3-05 错误中心/死信重试 | ⬜ PENDING | 未做 |
| P3-06 桌宠语音 | 🟡 PARTIAL | 模块级 stop/Edge 测过；桌宠真机待验 |
| P3-07 桌宠热键/养成 | ✅ PASS | 历史验证 |
| P3-08 手机/窄屏 | ⬜ PENDING | 未验 |

## P4 工程 / 安全 / 性能

| ID | 结果 | 证据 |
|---|---|---|
| P4-01 全量单测 | ✅ PASS | `Ran 286 tests OK`（`.tmp/unittest-checklist.log`） |
| P4-02 召回 p99 | ✅ PASS | p99 2.753ms，over_budget=0，`C:\Users\22166\.dsh\evidence\2026-10-30\011603-acceptance-bench-recall-36c164\stdout.txt` |
| P4-03 注入预算 | ✅ PASS | p99 6.513ms，over_char_budget=0，persona_violations=0，`C:\Users\22166\.dsh\evidence\2026-10-30\011627-acceptance-bench-inject-a030e4\stdout.txt` |
| P4-04 数据库迁移/备份 | ✅ PASS | `data/aiji.db.bak-before-qq-platform` |
| P4-05 控制台安全 | ✅ PASS | `/api/fs` 403 |
| P4-06 日志轮转 | ⬜ PENDING | 未做 |
| P4-07 测试数据清理 | ✅ PASS（本轮） | 删除 `summarytest`：fact 25、turn 25、summary 1、profile 3、account 1、style 2、fact_link 124、fact_log 40、reflection 1；剩余 turn=0 |

## 机器可读结果

`.tmp/acceptance_probe.json`（health/facts/cross_talk/reply/style/console/plugin 等）。

## 需要你在 QQ 完成的 3 项（按顺序）

1. **P1-03 语音单条**：发“发条语音给我”，确认只收一条语音。
2. **P1-06 主动消息**：发一条后等 30 分钟（或告诉我，我把测试阈值调成 2 分钟）别理它。
3. **P2-06 QQ 触发学习**：发“你去孙笑川吧继续学习”，等 30–90 秒，看它是否回“学完了…”。

其余 P1/P2 的 ⬜ 是**功能未做**，不是测试失败；下一步按优先级补：图片/语音入站 → 群限流 → 错误中心/死信重试 → 每轮抓不同页。
