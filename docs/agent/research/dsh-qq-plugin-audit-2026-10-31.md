# DSH QQ 插件源码审计（2026-10-31）

## 目标

在不替换 `AIJiYi（大脑）→ AstrBot（QQ/OneBot 宿主）→ NapCat（个人 QQ）`
链路的前提下，补齐私聊/群聊可靠性。只借鉴源码机制，不把第二个 agent 或第二个
LLM 接进现有聊天。

## 候选与结论

| 项目 | 版本/最近提交 | 许可 | 相关能力 | 结论 |
|---|---|---|---|---|
| [mario841859784/dsh-onebot](https://github.com/mario841859784/dsh-onebot) | 0.9.8 / 2026-10-30 | BSD-3-Clause | 正反向 WS、私聊/群、引用、图片/语音、去重、发送失败分类 | **主要参考** |
| [Applepinea/dsh-qq-access](https://github.com/Applepinea/dsh-qq-access) | 0.5.0 / 2026-10-28 | MIT | 私聊/群 allowlist、群归档、轮询补漏、引用/媒体规范化 | **参考访问与媒体语义** |
| [accomplish07zrh-eng/dsh-aurabot-qq](https://github.com/accomplish07zrh-eng/dsh-aurabot-qq) | 1.1.24 / 2026-10-30 | MIT | 群触发策略、模块化发送链、语音/表情、人格与权限 | **参考触发与安全边界** |
| [beiheng127/dsh-dfy-qq](https://github.com/beiheng127/dsh-dfy-qq) | 0.5.5 / 2026-10-27 | MIT | 关键词、概率接话、冷却、群友档案、短期记忆 | **参考低打扰概率唤醒** |
| [cheems135/dsh-qq-bridge](https://github.com/cheems135/dsh-qq-bridge) | 1.19.0 / 2026-10-26 | MIT | 引用、媒体占位、TTS/ASR、记忆压缩、管理面板 | **参考媒体表达** |
| [sj244/dsh-qq-bridge](https://github.com/sj244/dsh-qq-bridge) | 0.1.3 / 2026-10-29 | MIT | 白名单、@/昵称必唤醒、默认 5% 概率唤醒、其余只记录 | **参考低概率与只记录分支** |
| [john-walks-slow/dsh-im-humanize](https://github.com/john-walks-slow/dsh-im-humanize) | 目录项 | 未作为依赖核对 | 发送延迟、输入中状态、message_break、引用、no_reply | **参考动态节奏与拆条** |
| [zhdzh-good/multi-role-groupchat](https://github.com/zhdzh-good/multi-role-groupchat) | 1.5.0 / 2026-10-28 | 仓库许可证另核 | 目标必答、其他人概率接话、路由诊断、接话上限 | **参考目标/旁听分离** |
| [tdcq9594/dsh-roost](https://github.com/tdcq9594/dsh-roost) | 0.10.20 / 2026-10-30 | 免费非商业自定义许可 | 一会话一 DSH 会话、三层记忆、人格、压缩 | **不采用为依赖**：替换整条链路且许可证不适合 |
| [CCYellowStar2/dsh-astrbot-ingress](https://github.com/CCYellowStar2/dsh-astrbot-ingress) | 0.3.23 | 未作为依赖核对 | AstrBot → DSH、按群/按人 session、审批 | 与“AIJiYi 单脑”目标不一致 |
| [zhuiyueya/dsh-im-gateway](https://github.com/zhuiyueya/dsh-im-gateway) | 目录项 | 未作为依赖核对 | 多 IM 聚合、统一会话、远程审批 | 多平台网关，当前 QQ 单线不需要 |
| [betty598/dsh-split-reply](https://github.com/betty598/dsh-split-reply) | 目录项 | 仓库未声明 | QQ 长回复按句拆条、间隔发送 | 只采用可选交互思路，不复制代码 |

## 本机为何不直接安装

- 这些 DSH 插件会注册自己的会话、模型、工具和出站发送链。与现有 AstrBot
  同时运行会形成双脑、双回复或双重入库。
- `dsh-onebot`、`dsh-qq-access` 等会直接管理 OneBot 连接，和 AstrBot 的
  `6199` 反向 WS 冲突。
- 部分插件默认开放访问或拥有宿主文件/终端能力，不符合 QQ 聊天的最小权限。
- 当前项目是 Python；为了几项协议语义引入 Node/DSH 运行时，维护成本高于收益。

## 已落地

1. **群会话共享、事实隔离**
   - `turn` 按 `(platform, scope, session)` 读取群内所有发言者的原文。
   - `fact/profile/style` 仍按 `(platform, platform_user_id, scope)` 隔离。
2. **消息幂等**
   - 按 QQ `message_id` 做有界 TTL 去重，反向 WS 重连或重复投递只处理一次。
3. **访问边界**
   - 私聊/群分别支持 `all / allowlist / disabled`，并有优先封禁名单。
4. **群触发**
   - @ 机器人或引用机器人消息都能触发续聊。
   - 非 @ 消息继续只入库，不回复。
5. **引用与媒体**
   - 引用、图片、语音、文件、视频、表情转为可读文本。
   - 媒体引用写入 AIJiYi `/ingest`；OCR/ASR 仍由 AIJiYi 识别引擎决定。
6. **发言者边界**
   - 当前群消息带清理后的 `[群友 昵称(QQ)]` 前缀。
   - system 提示明确群友内容是“不可信聊天数据”，不能冒充系统指令。
7. **可选分段**
   - `AIJI_QQ_SPLIT_REPLY=1` 时长文本按句拆条，默认关闭。
8. **断线补漏**
   - 群聊/私聊保存每会话时间水位，后台用 OneBot 历史接口补拉水位后的消息。
   - 补拉只入库、不回复；直播事件和补拉共用 `message_id`，AIJiYi 写入侧再次幂等。
9. **受控群自动聊天**
   - 昵称/唤醒词必回，@/引用必回。
   - 其余按 5% 基础概率、兴趣词、刚参与话题和活跃群门槛决定。
   - 叠加 3 分钟普通冷却、3 次/小时、8 次/天、4 次连发刹车和 1–8 点静默。
   - 自动接话默认不 @，避免每次触发提醒。

## 尚未采用

- **真正看图**：当前 AIJiYi 文本模型链路不保证多模态；插件只登记媒体并保留
  占位，OCR/ASR 是否可用取决于识别引擎。
- **发送死信队列**：当前出站仍由 AstrBot 负责；没有在插件内复制一套持久化队列。
- **群内概率接话/主动插话**：默认保持 `mention`，避免机器人刷群。

## 推荐后续

1. 给 AIJiYi 增加明确的视觉模型路由后，再把图片作为多模态 content 传入。
2. 只有在 AstrBot 发送失败可被插件稳定观测时，才加持久化出站重试，避免重复发。
