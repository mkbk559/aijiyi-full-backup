> **历史文档（设定已废弃）**：这里的「大肥鱼 / 鱼缸」是 2026-11-03 之前的人设，
> 那一天已整体替换为「玛卡巴卡」（约 6 岁半的小孩，见
> `docs/agent/design/persona-child-2026-11-03.md`），相关记忆与素材均已清空。
> 本文只作当时的决策/研究记录，不要当作现行设定。

# AIJiYi × AstrBot × QQ 对接资料

> 适用：本地 Windows，AIJiYi 作为记忆/人格中间件，AstrBot 只做 QQ 协议，NapCat 提供 OneBot v11。
> 不包含任何真实 token / 百度 cookie；登录态文件不要提交到仓库。

## 1. 架构

```text
QQ
 │
NapCat（QQ NT 插件）
 │  OneBot v11 反向 WS：ws://127.0.0.1:6199/ws
 ▼
AstrBot（aiocqhttp 平台，WebUI 6185）
 │  插件：aiji_memory
 │  HTTP：POST http://127.0.0.1:8787/v1/chat/completions
 ▼
AIJiYi（记忆 / 人格 / 摘要 / 语音 / 主动 / 学习）
 │
 ├─ SQLite：data/aiji.db
 ├─ 人格语料：data/persona/sunba.json
 ├─ 百度登录态：data/browser/baidu_state.json
 └─ 抓取脚本：tools/tieba_collect.py / tools/persona_learn.py
```

| 组件 | 地址 | 说明 |
|---|---|---|
| AIJiYi | `http://127.0.0.1:8787` | OpenAI 兼容 + 管理 API |
| AstrBot | `http://127.0.0.1:6185` | QQ 适配 + 插件宿主 |
| OneBot WS | `127.0.0.1:6199` | AstrBot 监听，NapCat 反向连入 |
| NapCat | `http://127.0.0.1:6099` | QQ 登录/协议；token 不要外传 |

## 2. 启动顺序

```powershell
# 1) AIJiYi
cd D:\AIJiYi
D:\Python313\python.exe -m aiji.cli serve

# 2) AstrBot（先监听 6199）
cd D:\AstrBot
.\.venv\Scripts\python.exe main.py

# 3) NapCat（QQ NT 需先登录，再连接 6199）
#    用 NapCat 的 launcher 启动，保持 6099 可开
```

检查：

```powershell
Invoke-WebRequest http://127.0.0.1:8787/health
Get-NetTCPConnection -LocalPort 6199
```

## 3. OpenAI 兼容接口

### 3.1 对话

`POST http://127.0.0.1:8787/v1/chat/completions`

```json
{
  "model": "aiji",
  "user": "qq_group_123456_user_7890",
  "messages": [
    {"role": "user", "content": "在吗，今天好累"}
  ],
  "stream": false,
  "temperature": 0.9,
  "top_p": 0.9,
  "max_tokens": 200,
  "aiji": {
    "tools": false,
    "history": true,
    "history_limit": 80,
    "history_chars": 12000,
    "persona": "sunba",
    "sender_name": "玛卡巴卡"
  }
}
```

Header：

```text
Content-Type: application/json
X-Aiji-Session: private:<QQ>    # 私聊
# 或
X-Aiji-Session: group:<群号>   # 群聊
Authorization: Bearer <AIJI_API_KEY>  # 仅当 AIJiYi 设了 AIJI_API_KEY
```

`aiji` 字段都是可选的：

| 字段 | 默认 | 作用 |
|---|---|---|
| `tools` | true | 是否允许文件/终端等工具；QQ 建议 false |
| `history` | false | 薄客户端只传本轮时，从 `turn` 表补历史 |
| `history_limit` | 80 | 最多补多少轮（服务端上限 200） |
| `history_chars` | 12000 | 历史字符预算 |
| `persona` | `default` | `sunba` 启用孙吧语气层 + 学习例句 |
| `sender_name` | 空 | 写入 `turn.extra`，群聊审计用 |

`user` 身份映射：

| 写法 | platform | platform_user_id | 说明 |
|---|---|---|---|
| `qq_user_<QQ>` | qq | `<QQ>` | 私聊 |
| `qq_group_<群>_user_<QQ>` | qq | `<QQ>` | 群聊，scope=`group:<群>` |
| `telegram_777` | telegram | 777 | 通用 `平台_用户` |
| `desktop` | 默认 | 默认 | 本地控制台/桌宠 |

也可以用 `metadata.aiji` 覆盖 `aiji` 选项；`user` 字段优先于 header 身份提示。

### 3.2 只入库、不回复（群非 @ 消息）

`POST http://127.0.0.1:8787/turns`

```json
{
  "platform": "qq",
  "platform_user_id": "7890",
  "scope": "group:123456",
  "session": "group:123456",
  "role": "user",
  "content": "我是小老虎，住在笼子里面",
  "extra": {"sender_name": "相逢太短，不等茶凉"}
}
```

返回 202；写入是异步队列，通常 1–3 秒内可查。

### 3.3 查询

```text
GET /api/recent-turns?platform=qq&platform_user_id=7890&scope=group%3A123456&limit=100
GET /api/memories?platform=qq&platform_user_id=7890&scope=group%3A123456
GET /health
```

`scope` 里的 `:` 要 URL 编码成 `%3A`。

### 3.4 最小客户端示例

```python
import aiohttp

payload = {
    "model": "aiji",
    "user": "qq_user_7890",
    "messages": [{"role": "user", "content": "我叫什么？"}],
    "stream": False,
    "aiji": {"tools": False, "history": True, "persona": "sunba"},
}

async with aiohttp.ClientSession() as http:
    async with http.post(
        "http://127.0.0.1:8787/v1/chat/completions",
        json=payload,
        headers={"X-Aiji-Session": "private:7890"},
    ) as resp:
        data = await resp.json()
        print(data["choices"][0]["message"]["content"])
```

## 4. AstrBot 侧配置

### 4.1 插件位置

```text
D:\AstrBot\data\plugins\aiji_memory\main.py
```

插件职责：

- 把 QQ 事件转成 AIJiYi 身份
- 私聊直接回复
- 默认 `auto`：群聊被 @/引用必回，未叫到时按受控概率参与
- 群里“回复机器人消息”也视为触发，不必额外 @
- 群聊非 @ 消息始终入库；自动策略不接时只入库
- 同一群的对话原文跨发送者可见；个人事实/画像/风格仍按 QQ 号隔离
- 按 QQ `message_id` 去重，防止反向 WS/重连造成重复回复或重复入库
- 群聊和私聊按持久化水位补拉 OneBot 历史，只入库、不回复旧消息
- 私聊 / 群访问范围可按 allowlist 收口，并支持用户、群封禁名单
- 引用消息、图片、语音、文件、视频、表情会转成可读文本进入本轮和记忆
- 空 @ 5 秒内只回一次
- 语音/文字单条回复
- 每会话滑动窗口限流
- 主动消息
- “去孙笑川吧继续学习”触发抓取 + 学习
- 自动学习（默认 1 小时）

### 4.2 平台配置

`D:\AstrBot\data\cmd_config.json` 关键项：

```json
{
  "platform": [
    {
      "id": "qq-personal",
      "type": "aiocqhttp",
      "enable": true,
      "ws_reverse_host": "127.0.0.1",
      "ws_reverse_port": 6199,
      "ws_reverse_token": "<与 NapCat 客户端一致的随机 token>"
    }
  ],
  "provider_settings": {
    "enable": false
  },
  "platform_settings": {
    "empty_mention_waiting": false,
    "empty_mention_waiting_need_reply": false
  }
}
```

说明：

- **必须关掉 AstrBot 自带 LLM**，否则空消息/未处理事件会报“未找到可用对话模型”，并可能和 AIJiYi 双脑回复。
- 改前备份 `cmd_config.json`。
- 如果以后想用 AstrBot 自己的 LLM，先移除 `aiji_memory` 的回复，避免两边都回。

### 4.3 插件环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `AIJI_URL` | `http://127.0.0.1:8787/v1/chat/completions` | 对话地址 |
| `AIJI_CORE` | `http://127.0.0.1:8787` | `/turns` 入库地址 |
| `AIJI_API_KEY` | 空 | 下游 Bearer key |
| `AIJI_QQ_MODE` | `auto` | `auto` / `mention` / `all` / `off` |
| `AIJI_INGEST_UNMENTIONED` | `1` | 群非 @ 消息是否调用 `/turns` 入库 |
| `AIJI_QQ_PRIVATE_MODE` | `all` | 私聊访问：`all` / `allowlist` / `disabled` |
| `AIJI_QQ_GROUP_MODE` | `all` | 群访问：`all` / `allowlist` / `disabled`，与是否回复的 `AIJI_QQ_MODE` 分开 |
| `AIJI_QQ_ALLOW_USERS` | 空 | 私聊白名单，QQ 号逗号分隔 |
| `AIJI_QQ_ALLOW_GROUPS` | 空 | 群白名单，群号逗号分隔 |
| `AIJI_QQ_BLOCK_USERS` | 空 | 用户封禁名单，优先于白名单 |
| `AIJI_QQ_BLOCK_GROUPS` | 空 | 群封禁名单，优先于白名单 |
| `AIJI_QQ_REPLY_TO_BOT` | 1 | 群里引用机器人消息时是否算触发 |
| `AIJI_QQ_DEDUP_SECONDS` | 600 | 消息 ID 去重窗口秒 |
| `AIJI_QQ_DEDUP_MAX` | 4096 | 去重缓存最多保留多少条 |
| `AIJI_QQ_MEDIA_LIMIT` | 4 | 单条消息最多登记几个媒体引用 |
| `AIJI_QQ_ALIASES` | `大肥鱼,肥鱼` | 群友叫到这些名字时必接话 |
| `AIJI_QQ_AUTO_KEYWORDS` | 内置兴趣词 | 命中后提高插话概率的逗号分隔词表 |
| `AIJI_QQ_AUTO_INTERJECT_CHANCE` | 0.05 | 普通群消息基础插话概率 |
| `AIJI_QQ_AUTO_MAX_CHANCE` | 0.35 | 话题相关后的最高插话概率 |
| `AIJI_QQ_AUTO_ENGAGED_CHANCE` | 0.65 | 刚参与过话题后的续聊概率 |
| `AIJI_QQ_AUTO_COOLDOWN` | 180 | 两次普通自动接话最小间隔秒 |
| `AIJI_QQ_AUTO_ENGAGED_SECONDS` | 120 | 机器人发言后允许自然续聊的窗口秒 |
| `AIJI_QQ_AUTO_HOURLY_MAX` | 3 | 每群每小时自动接话上限 |
| `AIJI_QQ_AUTO_DAILY_MAX` | 8 | 每群每天自动接话上限 |
| `AIJI_QQ_AUTO_BURST_MAX` | 4 | 连发窗口内机器人最多说几次 |
| `AIJI_QQ_AUTO_BURST_WINDOW` | 600 | 连发刹车窗口秒 |
| `AIJI_QQ_AUTO_ACTIVE_WINDOW` | 300 | 判断群是否正在聊的窗口秒 |
| `AIJI_QQ_AUTO_MIN_RECENT` | 3 | 窗口内至少多少条群消息才允许普通插话 |
| `AIJI_QQ_AUTO_QUIET_START` | 1 | 自动接话静默开始小时 |
| `AIJI_QQ_AUTO_QUIET_END` | 8 | 自动接话静默结束小时 |
| `AIJI_QQ_AUTO_MENTION` | 0 | 自动接话时是否额外 @ 发言者 |
| `AIJI_QQ_BACKFILL` | 1 | 是否在后台补拉断线期间漏掉的历史消息 |
| `AIJI_QQ_BACKFILL_SECONDS` | 120 | 补漏轮询间隔秒，最低 30 |
| `AIJI_QQ_BACKFILL_COUNT` | 20 | 每会话每次查询最近多少条，范围 1–50 |
| `AIJI_QQ_BACKFILL_STATE` | `D:\AstrBot\data\aiji_memory_backfill.json` | 每会话补漏水位文件 |
| `AIJI_QQ_SPLIT_REPLY` | 1 | 是否把长文本按句拆成多条发送 |
| `AIJI_QQ_SPLIT_MIN_CHARS` | 60 | 分段合并阈值 |
| `AIJI_QQ_SPLIT_MAX` | 4 | 单次最多发送几段 |
| `AIJI_QQ_SPLIT_DELAY` | 1.2 | 分段之间等待秒 |
| `AIJI_QQ_STICKERS` | 1 | 是否允许独立表情包图片消息 |
| `AIJI_STICKER_DIR` | `D:\AstrBot\data\sticker_packs\aiji` | 本地表情包目录与 `manifest.json` |
| `AIJI_STICKER_CHANCE` | 0.18 | 没有明显情绪时的默认发图概率 |
| `AIJI_STICKER_MOOD_CHANCE` | 0.65 | 情绪和贴纸标签匹配时的发图概率 |
| `AIJI_STICKER_COOLDOWN` | 120 | 两次表情包之间的最短秒数 |
| `AIJI_STICKER_DAILY` | 30 | 每日最多发送几张表情包 |
| `AIJI_STICKER_UPDATE` | 1 | 是否定时合并公开远程图库 |
| `AIJI_STICKER_FEED_URL` | 内置公开 GitHub feed | 远程 manifest 地址 |
| `AIJI_STICKER_UPDATE_INITIAL` | 120 | 服务启动后首次更新等待秒 |
| `AIJI_STICKER_UPDATE_INTERVAL` | 86400 | 后续更新间隔秒，最低 30 |
| `AIJI_STICKER_MAX_MB` | 8 | 单个 manifest/图片下载上限 |
| `AIJI_SOCIAL_MEDIA_DIR` | `D:\AIJiYi\data\social\media` | QQ 空间说说/头像允许读取的本地图片目录 |
| `AIJI_TIMEOUT` | 120 | HTTP 超时秒 |
| `AIJI_QQ_VOICE` | 1 | 允许语音 |
| `AIJI_QQ_REPLY_MODE` | `auto` | `auto` / `text` / `voice` / `both` |
| `AIJI_VOICE` | `zh-CN-XiaoxiaoNeural` | Edge TTS 音色 |
| `AIJI_VOICE_RATE` | `+0%` | Edge TTS 语速，如 `-10%` / `+15%` |
| `AIJI_VOICE_PITCH` | `+0Hz` | Edge TTS 音高，如 `-5Hz` / `+8Hz` |
| `AIJI_VOICE_VOLUME` | `+0%` | Edge TTS 音量，如 `-10%` / `+15%` |
| `AIJI_HEART_SILENCE` | 1800 | 主动消息静默秒 |
| `AIJI_HEART_COOLDOWN` | 21600 | 主动消息冷却秒 |
| `AIJI_HEART_DAILY` | 3 | 每日主动上限 |
| `AIJI_HEART_INTERVAL` | 300 | 主动检查周期秒 |
| `AIJI_AUTO_LEARN` | 1 | 自动学习开关 |
| `AIJI_LEARN_INTERVAL` | 3600 | 自动学习间隔秒 |
| `AIJI_LEARN_PAGES` | 5 | 每轮抓页数 |
| `AIJI_LEARN_THREADS` | 4 | 每轮抓帖子数 |
| `AIJI_RATE_LIMIT` | 1 | 回复限流开关 |
| `AIJI_RATE_GROUP` | 8 | 群 60s 上限 |
| `AIJI_RATE_PRIVATE` | 15 | 私聊 60s 上限 |
| `AIJI_RATE_GROUP_MIN` | 1.5 | 群最小间隔秒 |
| `AIJI_RATE_PRIVATE_MIN` | 0.8 | 私聊最小间隔秒 |
| `AIJI_RATE_MAX_WAIT` | 20 | 最多等待秒 |
| `AIJI_RATE_WINDOW` | 60 | 窗口秒 |

当前实测配置：`LEARN_INTERVAL=3600`、`PAGES=8`、`THREADS=6`、`HEART_SILENCE=1800`、`RATE_LIMIT=1`。

媒体入站当前做两件事：把 `[图片]/[语音]/[文件]` 等语义拼进本轮文本，并调用
AIJiYi `/ingest` 登记引用。OCR/ASR 是否真能产出文字仍取决于 AIJiYi 侧是否注入了
识别引擎；插件不会假装“看懂了”图片。视频只登记元数据。

补漏只在该会话已经出现过至少一条实时消息后启动。首次见到会话时只建立时间水位，
不会把安装前的旧聊天整段灌入记忆；之后每轮只处理水位后的消息。直播事件、补漏和
AIJiYi 写入都会按 `message_id` 做幂等，避免重复事实。

`auto` 群聊顺序：

1. 被 @、引用机器人：必回。
2. 命中 `AIJI_QQ_ALIASES`：必回。
3. 刚参与过该话题：按 `AIJI_QQ_AUTO_ENGAGED_CHANCE` 续聊。
4. 群里最近 5 分钟至少有 3 条消息：按兴趣词提高概率，基础 5%，最高 35%。
5. 冷却、每小时、每日、连发刹车和 1–8 点静默时段任一命中就不说。

自动接话默认不带 @，模型会收到最近群聊记录和“没人明确叫你”的提示。

文字出口会硬剔除 emoji、QQ 小黄脸、`[表情包:开心]`、`[CQ:face,...]` 和
`<sticker>` 等标记。真正的表情包由本地贴纸库匹配情绪后作为独立图片消息发送，
不会混在正文里。默认素材及许可证见
[sticker-pack-notice.md](../research/sticker-pack-notice.md)。

QQ 空间写动作走 NapCat 扩展 OneBot HTTP action（`send_qzone_msg`、
`comment_qzone`、`set_qq_avatar`、`set_self_longnick` 等），因此除反向 WS 外还需
启用 NapCat HTTP Server，并把 `AIJI_ONEBOT_API` 指向该 HTTP API。可用动作和请求
示例见根目录 `README.md` 的“QQ 空间与分享”。表情包始终来自本地
`AIJI_STICKER_DIR/manifest.json`；AIJiYi 可定时从公开 feed 抓图并原子更新，
插件在下一次使用时按 manifest mtime 自动重载，不需要重启 AstrBot。

主动消息会带 `aiji.remember=false` 与 `aiji.proactive=true`，因此不会把
“现在没人说话”这条系统提示写成用户 turn；精力不足、夜间或社交电量过低时，
AIJiYi 会直接跳过本轮主动生成。全局精力/情绪/生活事件/私密本子的详细行为见
[companion-behavior-v1.md](companion-behavior-v1.md)。

### 4.4 语音

- AstrBot venv 需安装 `edge-tts`，系统需 `ffmpeg`
- 插件生成 wav，用 `Comp.Record` 发送
- `auto` 模式：用户说“语音/说给我听/听听你的声音”才发语音；普通消息只发文字
- `voice` 模式：所有回复只发语音（失败回退文字）
- **不要用 `both`**：QQ 会把文字和语音拆成两条

## 5. NapCat / OneBot

1. NapCat WebUI 登录 QQ（6099）
2. 启用 OneBot v11 反向 WS
3. 地址填：

```text
ws://127.0.0.1:6199/ws
```

4. AstrBot 日志出现 `aiocqhttp(OneBot v11) 适配器已连接`
5. 群里 @ 机器人测试

Token 只放在 NapCat/AstrBot 本地配置，不要写进文档或提交仓库。

## 6. 孙吧学习（B 路线）

### 6.1 登录态

```powershell
D:\Python313\python.exe D:\AIJiYi\tools\tieba_login.py
```

- 打开 Edge 登录页，用百度 App 扫码
- 检测到 `BDUSS` 后保存 `data/browser/baidu_state.json`
- 若过期，重跑一次

### 6.2 抓取 + 学习

```powershell
D:\Python313\python.exe D:\AIJiYi\tools\tieba_collect.py --pages 8 --threads 6
D:\Python313\python.exe D:\AIJiYi\tools\persona_learn.py --corpus D:\AIJiYi\.tmp\tieba_forum.json
```

- `learn_state.json` 记录已抓页/帖子，自动轮换
- 过滤挂人/辱骂/隐私/软色情
- 结果写 `data/persona/sunba.json`
- AIJiYi 每次请求读取，无需重启

### 6.3 QQ 触发

```text
你去孙笑川吧继续学习
```

插件会先回“行，我去池子里泡会儿…”，然后后台抓取/学习，完成后回“学完了。新记下的：…”。

自动学习：AstrBot 启动 120 秒后第一轮，之后每 `AIJI_LEARN_INTERVAL` 一轮；headless，不弹窗。

## 7. 验收清单（最小）

| 测试 | 期望 |
|---|---|
| 私聊“在吗” | 一条回复 |
| 群里不 @ 说“我是X，住Y” | 不回复，但 `/api/recent-turns` 能查到 |
| 群里 @ 问“我叫什么” | 答出刚才的 X/Y，不串号 |
| 群友 A 不 @ 说“暗号是 9527”，群友 B 随后 @ 问暗号 | B 能答 9527；但 B 的事实用户画像不能读到 A 的私人事实 |
| 空 @ 连发 | 5 秒内只回一条“咋了，光@不说话？” |
| 同一条 QQ 事件重复投递 | 只回复/入库一次 |
| NapCat 短暂断线后恢复 | 水位后的群/私聊消息自动补入，不回复旧消息 |
| 群里引用机器人上一句但不 @ | 正常续聊 |
| 群聊连续有 3 条普通消息 | 多数只入库；命中名字/术语/概率时偶尔自然接一句，不 @ |
| 自动接话后继续聊 | 120 秒内按 65% 继续接；超过后受 3 分钟冷却限制 |
| 凌晨 1–8 点普通群聊 | 不自动插话；被 @、引用或叫名字仍可回 |
| 快速连发 10 条 | 前 8 条正常，后面变慢/提示“慢点” |
| 发“发条语音给我” | 只收一条语音 |
| 发“去孙笑川吧继续学习” | 先确认，30–90 秒后回报学到的 trait |
| 重启 AIJiYi 后再问 | 事实仍在 |

## 8. 常见问题

| 现象 | 原因/处理 |
|---|---|
| `LLM 请求失败：未找到任何可用的对话模型` | AstrBot 自带 LLM 被触发；设 `provider_settings.enable=false`，并关 `empty_mention_waiting` |
| 一条回复收到文字+语音两条 | `AIJI_QQ_REPLY_MODE=both`；改 `auto`/`text`/`voice` |
| 群里记不住别人说的话 | 非 @ 消息没入库；确认插件 `_ingest` 和 `/turns` 正常 |
| “我是谁”答没说过 | 查 `turn`/`fact` 是否有该身份；确认 `history=true`、身份 scope 一致 |
| 百度贴吧 403 | 不要直接爬；用 Playwright 登录态（`tools/tieba_login.py`） |
| 语音没声音 | 查 AstrBot 日志 `AIJiYi TTS 失败`；确认 `edge-tts` 和 `ffmpeg` |
| 学习完成消息出现 `{'user':...}` | 已修：归一化 dict/list，只回 trait；不要再改回 raw example |
| 主动消息不来 | 检查静默/冷却/每日额度；看 `AIJiYi proactive` 日志 |

## 9. 安全与边界

- 服务只监听 `127.0.0.1`；不要绑 `0.0.0.0`
- `AIJI_API_KEY` 可选；跨机接入时务必设，且应覆盖 `/api/*`、`/turns` 和完整的 `/v1/*` 面
- 未设置 `AIJI_API_KEY` 时控制台 `/api/shell` 无认证，别暴露；即使设置 key，也不要把工作区/终端开放到公网
- OneBot 反向 WS 必须绑定 `127.0.0.1` 并设置 token；AstrBot WebUI 和 NapCat WebUI 同样只绑本机
- 对外暴露个人 QQ 时建议设 `AIJI_QQ_PRIVATE_MODE=allowlist`、`AIJI_QQ_GROUP_MODE=allowlist`，并先填好对应白名单；留空白名单等于没有可用会话
- 百度登录态、NapCat token、API key 都不要提交
- 当前边界是：**群内对话原文共享，个人事实/画像/风格隔离**；不要把私聊内容写入群 scope
- `auto` 不是“每条都回”；不要改成 `all` 来追求活跃，`all` 只用于调试
- 学习只取语气，不学网暴/挂人/隐私；上线前人工抽检

## 10. 相关文件

- 插件：`D:\AstrBot\data\plugins\aiji_memory\main.py`
- AIJiYi 入口：`aiji/server.py`、`aiji/head.py`、`aiji/persona.py`

## 11. DSH 开源项目对照

本轮只读源码、不安装第三方 DSH 插件，采用了以下低风险机制：

| 项目 | 许可 | 借鉴点 |
|---|---|---|
| [mario841859784/dsh-onebot](https://github.com/mario841859784/dsh-onebot) | BSD-3-Clause | 私聊/群会话边界、引用机器人触发、消息 ID 去重 |
| [Applepinea/dsh-qq-access](https://github.com/Applepinea/dsh-qq-access) | MIT | allowlist / blocklist、引用与媒体规范化、消息归档语义 |
| [cheems135/dsh-qq-bridge](https://github.com/cheems135/dsh-qq-bridge) | MIT | 引用、表情和媒体占位的人话表达 |
| [betty598/dsh-split-reply](https://github.com/betty598/dsh-split-reply) | 仓库未声明 | 长回复按句拆条；仅作为可选的交互思路 |

没有采用 `dsh-roost` 作为依赖：其许可证是免费非商业许可，且会替换整条
QQ→会话→记忆链路，不适合当前“AstrBot 只做协议、AIJiYi 只做大脑”的边界。
- 抓取/学习：`tools/tieba_login.py`、`tools/tieba_collect.py`、`tools/persona_learn.py`
- 验收：`docs/agent/testing/acceptance-checklist.md`、`docs/agent/testing/acceptance-run-2026-10-31.md`
