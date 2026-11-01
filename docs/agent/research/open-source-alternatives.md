# 开源同类调研（2026-10-30）

> 问题：有没有“像 AIJiYi 这种”的开源项目——个人 AI 记忆 + Web 控制台 + 桌宠 + 多模型/多平台接入？
> 结论先说：**没有单个仓库完整等于这套拼装**；最接近的是三类项目，各自覆盖一段。
> Stars / license 来自 GitHub API（2026-10-30 查询），会变；README 承诺也会变。

## 一、最接近“桌宠 + 记忆 + 主动陪伴”的

| 项目 | 覆盖 | 与 AIJiYi 的关系 | License / Stars |
|---|---|---|---|
| [kiyotakali/Miru](https://github.com/kiyotakali/Miru) | 桌面宠物 + 长期记忆 + 主动陪伴 + 多设备同步，self-hosted（Mac/服务器） | **整体形态最像**：把“记住你”当卖点；但单用户、无多平台表、无 OpenAI 网关 | Apache-2.0 / 188 |
| [sjmsltx/deepseek-desktop-pet](https://github.com/sjmsltx/deepseek-desktop-pet) | PySide6 + DeepSeek，双角色、长期记忆、Function Calling，Windows | **和你的 Tk 桌宠最像**；小项目（16★），可读实现、可对照 | MIT / 16 |
| [Open-LLM-VTuber](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber) | Live2D 桌宠/透明背景/语音打断/多 ASR-TTS-LLM/主动说话/视觉感知 | **桌宠交互与语音链路最强**；README 明确 v1 **长期记忆暂时移除**，可通过 Agent 接口接 Mem0 等 | MIT（Live2D 素材另授权）/ 14.5k |
| [moeru-ai/airi](https://github.com/moeru-ai/airi) | Web/桌面/移动，Live2D/VRM、实时语音、Discord/Minecraft 等 | 生态最大；**记忆仍是 WIP**（Memory Alaya），别当成熟记忆系统 | MIT / 50.9k |
| [awesome-ai-companion](https://github.com/DasterProkio/awesome-ai-companion) | 中文“人机恋/桌宠/长期记忆/主动性”项目大全（清单） | 找同类/找灵感最快入口 | CC-BY-4.0 / 1.1k |

## 二、最接近“记忆层 / 状态代理平台”的

| 项目 | 覆盖 | 可抄的机制 | License / Stars |
|---|---|---|---|
| [letta-ai/letta](https://github.com/letta-ai/letta)（MemGPT） | 有状态 Agent、可自编辑 memory blocks、桌面/Web app（现行源码在 [letta-code](https://github.com/letta-ai/letta-code)） | memory block 的读写/编辑语义、长期记忆与上下文分层 | Apache-2.0 / 25.3k（letta-code 3.7k） |
| [mem0ai/mem0](https://github.com/mem0ai/mem0) | 通用记忆层：抽取、ADD-only、图记忆、混合检索 | 你已借鉴的 ADD-only；可对照其“图记忆 + 多信号检索” | Apache-2.0 / 68.1k |
| [getzep/graphiti](https://github.com/getzep/graphiti) | 实时知识图谱、双时态（valid/invalid）、episode 溯源 | 你的 `valid_from/valid_to + fact_log` 已有；可补 episode 溯源/多跳 | Apache-2.0 / 32.4k |
| [topoteretes/cognee](https://github.com/topoteretes/cognee) | 图 + 向量记忆平台、认知架构、低门槛接入 | “记忆流水线”拆层方式（ingest→graph→retrieve） | Apache-2.0 / 32.9k |
| [BAI-LAB/MemoryOS](https://github.com/BAI-LAB/MemoryOS) | EMNLP 2025，短/中/长三段式个人记忆 OS | 分段记忆 + 热度淘汰，和你的“摘要后淘汰”可对照 | Apache-2.0 / 1.6k |

## 三、最接近“个人 AI 控制台”的

| 项目 | 覆盖 | 与 AIJiYi 的关系 | License / Stars |
|---|---|---|---|
| [khoj-ai/khoj](https://github.com/khoj-ai/khoj) | 自托管“AI 第二大脑”：Web UI、文档/网页接入、语义搜索、Agent、自动化、多端 | **Web 控制台 + 个人记忆最像**；但单用户、无桌宠、无多平台身份表 | AGPL-3.0 / 37.9k |
| [open-webui/open-webui](https://github.com/open-webui/open-webui) | 通用 LLM Web UI + 知识库/RAG + 工具/MCP | UI/管理台参考；长期“事实记忆”不是它的核心 | 自定义许可（非标准 OSI）/ 157k |

## 四、AIJiYi 相对它们的差异点（别轻易换底座）

- **多平台共用一套表**：`(platform, platform_user_id, scope)` + SQL 强制隔离——上面几乎都是单用户。
- **100ms 硬预算召回 + 软注入协议**：`persona_touched=False / generated_reply=None / soft=True`、来源标注、关系标签、注入留痕 + 反馈回写。
- **入口拼装**：OpenAI 兼容 `/v1` + 统一 IM Webhook + OneBot + WS presence + 本地控制台 + 桌宠，一套进程。
- **ADD-only + 双时态 + supersede** 已落地（mem0/Graphiti 是同类机制，不是现成可替换）。
- **OCR/ASR 真引擎**（RapidOCR/FunASR/whisper）已接，不是只留接口。

## 五、拼装建议（如果要“开源化/借力”）

1. 主动陪伴调度：看 Miru 的主动消息节流/多设备；你已有 proactive/heart，可对照。
2. 文档/网页/邮件接入：看 Khoj 的 connector 设计；你的 `recognize` 目前偏消息输入。
3. 记忆编辑 UI：看 Letta 的 memory block 编辑体验；你的控制台“人设工坊/记忆库”可加编辑。
4. episode 溯源/多跳：看 Graphiti；你的 `fact_link` 目前 1 跳、每事实 ≤3 边。
5. 分段记忆 + 热度淘汰：看 MemoryOS；你的压缩是“摘要后删原文”，可加分层。
6. 桌宠语音/打断：看 Open-LLM-VTuber；你的桌宠是精灵图 + SAPI/TTS 可继续加。

## 六、注意事项

- Open-LLM-VTuber README 明确：v1 的长期记忆**暂时移除**（“coming back soon”），不要按旧文章宣称它有记忆。
- AIRI 的 memory 是 WIP；它的强项是 Live2D/VRM/语音/游戏 Agent。
- Khoj 是 **AGPL-3.0**：想闭源分发/商用要谨慎。
- mem0/Graphiti/Cognee 是库/平台，不是完整“控制台 + 桌宠”；接进来仍要自己写 UI/身份/网关。
- star 数只作热度参考，不代表质量；小项目（Miru/deepseek-desktop-pet）代码量小、反而更适合通读借鉴。
