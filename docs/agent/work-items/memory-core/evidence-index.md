# 三平台社交闭环 · 证据索引（2026-11-05）

> 谁都能照着复跑。每条都写：**怎么做 → 证据在哪 → 判什么**。
> 状态只认"落盘的机器输出"（`raw_run` 归档目录在 `C:\Users\22166\.dsh\evidence\2026-11-05\`）。

## 0. 一眼看全（`social selfcheck --live`，2026-11-05 13:14）

```bash
python -m aiji.senses.cli social selfcheck --live --json   # → data/social_selfcheck.json
```

| 能力 | QQ | 抖音 | B站 |
|---|---|---|---|
| 私聊 | ✅ | ✅ (×2 已验) | ⚠ 接口通，缺真人收件人 |
| 群聊 / 主动聊天 | ✅ | ✅ (×3 已验) + 常驻任务 | ❌ 无 web 接口 |
| 刷视频学习 | —（平台没有） | ✅ | ✅ |
| 续火花 | ✅ | ✅ | —（平台没有） |
| 发评论 | ❌ 不支持 | ✅ cid 级证据（页面 fetch + 人工过一次验证） | ✅ |
| 自建群 | 不做（用户 2026-11-05 定调） | 不做 | 不做 |
| 跨平台记忆共享 | ✅ 三平台共享（真机） | ✅ | ✅ |

登录态（同一次 `--live` 实测）：QQ `小肥鱼/2684827447`；B站 `玛卡巴卡的学习日记/mid 1402977803`；
抖音 `is_login=true, login_buttons=0`。

## 1. QQ

| 项 | 怎么跑 | 证据 | 判定 |
|---|---|---|---|
| 私聊 | `social qq dm --peer 960022961 --text … --yes` | `message_id 1193904557` + `social qq history` 回读 | ✅ |
| 群聊 | `social qq group --peer 849663082 --text … --yes` | 群历史回读能看到 | ✅ |
| 主动聊天 | AstrBot 插件 `D:\AstrBot\data\plugins\aiji_memory\main.py`（persona `child`，history 80） | 真人对话回读（11:37–11:39 十二轮） | ✅ |
| 续火花 | `aiji social qq streak --peer … --touch` | 账本 `days 1→2` | ✅ |
| 发评论 | — | qzone 不走 OneBot | ❌ 平台限制 |

## 2. 抖音

| 项 | 怎么跑 | 证据 | 判定 |
|---|---|---|---|
| 私聊 | `social douyin dm --peer 霸王辣条丝 --text … --yes` | `verified:true`；重开会话页列表预览 `霸王辣条丝 \| 刚刚 \| 你好呀` | ✅ |
| 群聊 | `social douyin group --peer 玖玖大王万岁 --text … --yes` | `verified:true`；独立回读群里最后一条是这句（带"已读"） | ✅ |
| 主动聊天 | `social douyin watch --peer 玖玖大王万岁 --once --yes`（计划任务 `AIJiYi DouyinChat` 每 5 分钟） | `data/logs/douyin_chat.log`：读 13 条 → 挑 `霸王辣条丝:666` → 回复 → `verified:true` | ✅ |
| 续火花 | `social douyin streak --peer … --yes` | `days:1, sent:true, verified:true, left_box:true`（12:37）；账本 `data/douyin_streak.json` | ✅ |
| 刷视频学习 | `social douyin feed --limit N --learn` | 12:59 拿到真卡片（`7702763607037789158` 等） | ✅ |
| 发评论 | `social douyin comment --url <video> --text … --yes` | 两次都 `code:"unverified"`（发送不落地） | ⚠ **人工侧** |

⚠ 注意：`comment` 自 2026-11-05 14:39 起**已打通**（excluded#225）——做法是"页面上下文 fetch"
（点 UI 会触发短信验证），前提是该账号在网页端过一次验证（人工一次即可）。没过验证时服务端回
`200 + 0 字节`，适配器报 `verify_required`/`unverified`，不会假成功。

## 3. 哔哩哔哩

| 项 | 怎么跑 | 证据 | 判定 |
|---|---|---|---|
| 发评论 | `social bilibili comment --url … --text … --yes` | 真号发出并回读 | ✅ |
| 点赞 | `social bilibili like --url …` | `liked=1`（取消点赞 `-400`，见 excluded#193） | ✅ |
| 刷视频学习 | `social bilibili feed --learn` | 排行榜卡片入记忆 | ✅ |
| 私信 | `social bilibili dm --peer <mid> --text … --yes` | 字段修正后 `code=-400` → `21026 不能给自己发送消息哦~` | ⚠ **缺真人收件人** |

## 4. 跨平台记忆共享（2026-11-05 新增）

```bash
# 真机验收脚本（打真服务 + 真 DB）
python .tmp/probe/share_accept.py
```

- 写：`store.add_fact` 包装层把主人平台的事实镜像到 `Ident("shared","owner","global")`；
  `writer` 的 turn 也走 `share.add_turn_pair`。
- 读：`share.recall_union()`（shared 侧强制 `session=None`，见 excluded#221）。
- 实测输出（13:05）：`HAS_SHARED_FACT: True`，`QQ_CAN_RECALL_DOUYIN: True`，
  且能同时召回 12:5x 与 13:0x 两句（累积）。
- 开关：`AIJI_SHARED_MEMORY=0` 关闭镜像（默认开）。

## 5. 回归基线

```bash
python -m unittest discover -s tests -p "test_*.py"     # 2026-11-05 13:09 → Ran 1457, OK (skipped=1)
```

## 6. 只剩人工的步骤

1. **抖音发评论**：需要人在 App 里确认这个号能否评论（新号/限流/青少年模式都可能），
   网页端本身没有"发送"控件。
2. **B站私信**：需要一个真人 mid 作为收件人（自己的号会被 `21026` 拒），给了就能真发。
3. **自建群**：用户 2026-11-05 明确不做；QQ 侧 OneBot 也没有建群动作（excluded#206）。
