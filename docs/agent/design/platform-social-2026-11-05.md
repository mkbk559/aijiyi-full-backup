# 三平台社交闭环（QQ / 抖音 / 哔哩哔哩）—— 设计与真实边界

> 2026-11-05。用户目标（原话）："qq，抖音，哔哩哔哩可以实现主动聊天，刷视频学习，
> 然后续火花，然后发评论，然后私聊，群聊，然后他有自己的社交群"。
> 本文件是这件事的**唯一事实来源**：能力矩阵、代码位置、真机证据、以及哪些只有人能解。

## 1. 一句话结论

三个平台的动作被收进**同一层**（`aiji/heart/platforms/`），每个动作只有三种状态：
真能做到（`ok`）/ 要登录（`needs_login`）/ 平台根本不支持（`unsupported`），
外加一个坦诚的第四种 `experimental`（代码在、离线测过、**真机没过**）。
写动作一律 **dry-run 默认**，`--yes` 才真发，每次尝试都进 `data/platform_actions.jsonl`。

## 2. 能力矩阵（代码可自证）

`aiji social`（无参数）打印的就是这张表；`GET /api/platforms` 同一份数据。

| 动作 | QQ | 哔哩哔哩 | 抖音 |
|---|---|---|---|
| 私聊 `dm` | ✅ ok（真机 `message_id` + 历史回读，2026-11-05） | ⚠ 接口已通（`msg[content]` 字段修正后 `-400`→`21026 不能给自己发`，只差真人收件人） | ✅ ok（`/chat` 会话 → `messageEditorinputArea` → 回车，回读验过） |
| 群聊 `group` | ✅ ok（真机：群里真人接话，历史回读） | ❌ 无 web 接口（粉丝群只在 App） | ✅ ok（真机：在 66 人群里发过并回读；`social douyin watch` 主动聊天跑通） |
| 发评论 `comment` | ❌ qzone 不走 OneBot | ✅ ok（2026-11-04 真号发过） | ⚠ experimental（登录已确认 `login_buttons=0`，但评论输入框不在 DOM，见 excluded#202） |
| 刷内容 `feed` | ❌ QQ 没有公开内容流 | ✅ ok（排行榜，免 wbi） | ✅ ok（2026-11-05 真机 5 条；`/hot` 免登录） |
| 续火花 `streak` | ✅ ok（真机 `days 1→2`，账本自增） | ❌ B 站没有火花机制 | ❌ 抖音没有 |
| 自建群 `group_create` | ⚠ experimental（OneBot 无标准动作） | ❌ | ❌ |
| 点赞 `like` | ✅ ok（`send_like` 名片赞） | ✅ ok（2026-11-05 真机点赞 + has/like 回查 liked=1；**取消点赞不可用**） | ⚠ experimental |

## 3. 代码在哪

```
aiji/heart/platforms/
  base.py       能力状态 / Result / PlatformError / Guard（dry-run+限速+日上限+jsonl 留痕）
  qq.py         QQAdapter：Probe 登录态、好友/群列表、私聊、群聊、名片赞、历史、建群（如实 unsupported）
  bilibili.py   BiliAdapter：排行榜学习、评论（真发过）、私信、点赞；缺登录态报 needs_login
  douyin.py     DouyinAdapter：真实浏览器（Playwright + 本机 Edge）+ DOM；feed/评论/私信/登录
  streak.py     StreakBook：连续互动账本（schema v12 的 streak 表）+ 从 turn 回填
  engine.py     learn_feed（刷内容→记忆）、streak_sweep（该续的人→发→记账）
  registry.py   build()/matrix()/summary_lines()：按平台造适配器、出能力矩阵
aiji/left_hand/browser.py   Playwright 持久 profile 会话（TEMP 重定向、绕代理、有头/无头）
aiji/senses/bili_client.py  新增 ranking/video_detail/send_dm/like；urlopen 改走 aiji.net
```

CLI 入口：`aiji social [platform] [action] ...`

```bash
aiji social                                  # 能力矩阵
aiji social bilibili feed --limit 5          # 只抓不着库
aiji social bilibili feed --limit 5 --learn  # 抓完写进记忆（knowledge fact）
aiji social bilibili login                   # 登录态（真号实测 is_login=true, mid 1402977803）
aiji social qq friends|groups|history --peer <id>
aiji social qq dm --peer <qq> --text "在吗" --yes     # 不加 --yes 只预演
aiji social qq group --peer <群号> --text "..." --yes
aiji social qq streak                        # 看一眼账本（谁该续）
aiji social qq streak-refresh                # 从 turn 表回填连续天数
aiji social qq streak --touch --yes          # 真的去续（人设现生成开场白）
aiji social douyin login --wait 180          # 开有头窗口等扫码，二维码落 data/temp/douyin-login.png
aiji social douyin feed --limit 8            # 刷抖音（需要登录态）
```

## 4. 真机证据（2026-11-05）

| 事 | 结论 | 归档 |
|---|---|---|
| Playwright + 本机 Edge 在本机沙箱**能起** | headless 起得来，读得到 DOM | `.tmp/probe` 实测；证据 `C:\Users\22166\.dsh\evidence\2026-11-05\` 下 social-* |
| B 站登录态 | `is_login=true, mid=1402977803, Lv2` | `100306-social-live-probes-2-8d840d` |
| B 站刷内容 | 真排行榜 3 条（DeepSeek Harness / 崩铁 / 原神） | `100408-bili-feed-live-cac6de` |
| 刷内容写进记忆 | knowledge fact 1 条、文本 731 字、flushed=true | `100940-bili-learn-with-cookie-e5aea0` |
| 火花账本从真实消息回填 | 真 QQ 号 103445269 / 2373837659 等入账 | 本机 `data/aiji.db` 副本跑 `streak-refresh` |
| 抖音 headless | 首页能开（`__ac_signature` 已在），但**没有 feed 卡片**（登录墙）→ 显式 `empty_feed` | 截图 `data/temp/douyin-probe2.png` |
| QQ 发消息 | **不通**：NapCat 的 OneBot HTTP(3000) 没监听，且 QQ 停在扫码登录页 | `100306` 里 `code=transport_down` |

## 5. 只有人能做的（做完立刻生效，不用改代码）

1. **QQ 扫码登录**：QQ 窗口正停在扫码页（NapCat `shell/cache/qrcode.png` 会刷新）。
   登进去之后 NapCat 的 3000 端口与 websocket 会自动起来，`aiji social qq login` 应返回 `online=true`。
2. **抖音扫码登录**：`aiji social douyin login --wait 180`，扫完窗口里的二维码。
   有的登录态之后，`feed / comment / dm` 才会真正跑通（现在是 `experimental`）。
3. **建群/加好友**：QQ 建群是账号侧动作（手机端），OneBot 没有标准接口。
   建完把群号记下来，`aiji social qq group --peer <群号>` 就能在群里说话。
4. **B 站发私信/点赞**：接口已接但**真号没发过**，第一次真发请人工看一眼
   （`aiji social bilibili dm --peer <mid> --text "..." --yes`）。

## 6. 已知边界（诚实清单）

- **抖音刷内容靠 DOM**：页面结构一改就 `selector_missing`，错误里会列出试过的选择器；
  headless 目前只能拿到"壳"，内容需要登录态。
- **抖音有风控**：默认 90s 间隔、每天 10 次写动作，超了直接 `rate_limited`。
- **B 站排行榜无 cookie 会 -352**（风控校验失败），所以 feed 一律带登录 cookie；
  `ranking/v2` 被拒时自动退到 `popular`。
- **续火花的"N 天"是我们自己数的**（`streak` 表，本地日），不是读 QQ 客户端里的数字；
  QQ 侧真正的火花数只增不减、断一天归零，这里的口径与之一致，但**不是它的镜像**。
- **ingest 不写 turn**：`Memory.ingest` 只落 `media` + 抽取出的 fact。
  所以刷内容学习必须靠 knowledge 兜底（已把 `_video_knowledge_fallback`
  扩成 `_knowledge_fallback`，`extra["learn"]=True` 触发），否则"学了但召不回"。
