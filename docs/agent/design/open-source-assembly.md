# 开源拼装方案（基于现有积分网关）

## 结论

大肥鱼不再重复建设“多供应商聚合”。用户已有 Agent2API 这类积分制反代
OpenAI 网关，它负责上游模型选择、额度、限流和故障转移；AIJiYi 只负责：

1. 对外提供 OpenAI 兼容入口，让桌宠、IDE、IM 网关直接接入。
2. 在每次模型调用前注入快召回记忆、自我层和软约束。
3. 在模型返回后执行出口闸门，并异步记录一问一答。
4. 用本地控制台展示上游路由、队列、记忆和对话结果。

因此不引入 `llmrouter` / `airmux`，否则会和 Agent2API 重复一层路由。

## 复用边界

| 能力 | 采用方式 | 当前落点 |
|---|---|---|
| OpenAI 协议 | 按公开协议实现，不复制某家客户端代码 | `aiji/head.py`、`aiji/server.py` |
| 积分模型网关 | 复用用户已有 Agent2API/WorkBuddy | `aiji/pool.py` + `config.py` |
| 桌宠外壳 | 通过自定义 API 地址接入，可换壳 | 现有 `aiji.pet`；[Desktop-pet](https://github.com/git2968/Desktop-pet) |
| 本地控制台 | 先用零依赖单页，避免过早引入 React 构建链 | `aiji/web/console.html` |
| 后续重控制台 | [Pengy](https://github.com/patw/Pengy) / [Pi-Agent-Web](https://github.com/MaddieMo1/Pi-Agent-Web) | 两者均 MIT，可用于布局和 SSE 实现参考 |
| 记忆星图 | [HippoGraph Pro](https://github.com/artemMprokhorov/hippograph-pro) | 只借鉴扩散激活、时间衰减和 D3 交互 |
| IM 接入 | [Octop Gateway](https://github.com/TencentCloud/octop-gateway) | MIT；外部进程，10 平台统一 `InboundMessage` |
| 主动发送策略 | [proactive-gate](https://github.com/Bubblegunn/proactive-gate) / [proactive-nudge](https://github.com/Shitsuten/proactive-nudge) | 均 MIT；现有 Proactive 已覆盖主要逻辑，只补缺口 |
| 主动调度 | [NagaAgent](https://github.com/RTGS2017/NagaAgent) | AGPL；只借鉴触发/周期任务机制，不复制代码 |
| MCP 搜索 | [nickclyde/duckduckgo-mcp-server](https://github.com/nickclyde/duckduckgo-mcp-server) | MIT；默认启用，只开放 search/fetch/expand_link |
| MCP 网页抓取 | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | MIT；默认启用官方 fetch，保留私网与 robots 防护 |
| MCP GitHub | [github/github-mcp-server](https://github.com/github/github-mcp-server) | MIT；默认停用，安装二进制并提供令牌后人工启用 |
| 多模型聚合 | [llmrouter](https://github.com/paularlott/llmrouter) | MIT，但当前已有 Agent2API，不引入 |
| 人设面板 | [dsh-prefill-plugin](https://github.com/NekoDD-wow/dsh-prefill-plugin) | MIT；后续参考角色卡与预设保存 |

## 许可证纪律

- 直接复制代码或素材前必须确认仓库许可证并保留署名。
- [git2968/Desktop-pet](https://github.com/git2968/Desktop-pet) 为 MIT，
  可作为需要直接复制时的首选桌宠基座。
- [Pengy](https://github.com/patw/Pengy) 和
  [Pi-Agent-Web](https://github.com/MaddieMo1/Pi-Agent-Web) 为 MIT，
  可作控制台结构参考。
- [Octop Gateway](https://github.com/TencentCloud/octop-gateway) 为 MIT，
  是 QQ/微信/飞书等统一 IM 接入的首选。
- [HippoGraph Pro](https://github.com/artemMprokhorov/hippograph-pro) 的许可证
  GitHub 当前未识别为 SPDX 标准项，因此只借鉴机制，不复制源码。
- [NagaAgent](https://github.com/RTGS2017/NagaAgent) 为 AGPL-3.0，
  直接复制会把分发义务带入整个项目；当前只借调度思想。
- Moepet、CianLaw/pet-agent 当前仓库未检测到明确 SPDX 许可证；
  在作者补充许可证前，只作为产品交互参考，不复制源码或美术资产。
- 协议、接口形状和架构思想可以独立实现；这不等于复制源代码。

### 链接核对状态

**已确认到唯一仓库：**

- [TencentCloud/octop-gateway](https://github.com/TencentCloud/octop-gateway)
- [RTGS2017/NagaAgent](https://github.com/RTGS2017/NagaAgent)
- [artemMprokhorov/hippograph-pro](https://github.com/artemMprokhorov/hippograph-pro)
- [Bubblegunn/proactive-gate](https://github.com/Bubblegunn/proactive-gate)
- [Shitsuten/proactive-nudge](https://github.com/Shitsuten/proactive-nudge)
- [patw/Pengy](https://github.com/patw/Pengy)
- [MaddieMo1/Pi-Agent-Web](https://github.com/MaddieMo1/Pi-Agent-Web)
- [paularlott/llmrouter](https://github.com/paularlott/llmrouter)
- [git2968/Desktop-pet](https://github.com/git2968/Desktop-pet)
- [CianLaw/pet-agent](https://github.com/CianLaw/pet-agent)（无明确许可证）
- [zhuge-Tom/moepet](https://github.com/zhuge-Tom/moepet)（无明确许可证）
- [NekoDD-wow/dsh-prefill-plugin](https://github.com/NekoDD-wow/dsh-prefill-plugin)

**名称无法唯一对应，暂不作为目标：**

- `AutoMind Agent`：没有找到与分享描述完全一致、可确认的官方仓库。
- `harness-im-bridge`：通用名，未找到唯一仓库；由 Octop Gateway 替代。
- `ambientHarness` / `octop` / `marvis` 等：分享上下文混入了其他项目名，
  未确认前不建立依赖。
- `DogTag`：被认为是 NagaAgent 内部模块，但未找到独立官方仓库链接。

## 接入拓扑

```text
桌宠 / IDE / IM 网关 / 浏览器
              |
       OpenAI 兼容 HTTP
              |
          AIJiYi
  memory -> head -> gate -> exchange
              |
      Agent2API / WorkBuddy
       （积分与上游平台）
```

### 第一把钥匙：OpenAI 兼容 API

- `GET /v1/models`：模型名固定为 `aiji`。
- `POST /v1/chat/completions`：支持普通 JSON 与 SSE。
- 标准 `user` 字段无额外请求头时映射为 `local/<user>` 身份。
- 需要隔离平台时传 `X-Aiji-Platform`、`X-Aiji-User`、`X-Aiji-Scope`、
  `X-Aiji-Session`。
- 设置 `AIJI_API_KEY` 后，`/v1/*` 需要 `Authorization: Bearer <key>`。

### 第二把钥匙：IM 网关

IM 网关只做两件事：

1. 把 QQ/微信/抖音消息归一成 `platform/platform_user_id/scope/session/content`。
2. 调用 `/v1/chat/completions`，再把返回文本交给平台适配器发送。

AIJiYi 不应知道 NapCat、抖店或企业微信的底层协议。

### 第三把钥匙：控制台

当前 `/` 是零依赖控制台，显示：

- 上游积分网关可用/冷却状态；
- 写入队列、积压和失败；
- 活跃事实、知识、关系、画像、对话轮次；
- 使用真实记忆与闸门的流式对话测试。

重前端阶段再替换为 React 控制台，后端协议保持不变。

## 实施顺序

1. 完成 OpenAI 兼容入口与控制台（当前）。
2. 接入一个现成开源桌宠，只填自定义 API 地址，验证换壳零侵入。
3. 接入 IM 网关，先做单群、限频、冷却和日志。
4. 加控制台死信/审批页面。
5. 加 D3 记忆星图与 WebSocket 主动消息。
6. 最后才考虑 MCP 工具与高风险电脑操作，并保留人工确认。

