# 自我迭代第 15 轮（2026-11-03 23:00–23:20）：QQ 侧两个模块打到 100% 覆盖

选它们不是因为"覆盖率好看"，而是因为它们是**失败会被静默吞掉**的地方：
QQ 桥接发不出去、空间发布假成功，用户看到的都是"机器人已读不回"。

## 1. `aiji/throat/bridge/onebot.py`：72% → **100%**（`tests/test_onebot_edges.py`，37 条）

补的全是出错路径：

| 区域 | 补了什么 |
|---|---|
| `_parse_message` | CQ 码 `[CQ:at,qq=…]` 判 @、别人 @ 变普通文本、图片/语音/文件/表情占位、非 dict 元素跳过、整体不是 str/list 时返回空 |
| `parse_event` | 非 message 上报、未知 message_type、空 user_id、自己发的、只有 CQ 码没正文、`group_mode=off`、群消息缺 group_id、sender 回退、message_id 回退到 time |
| `_default_post`（老接口） | ok / retcode≠0 / URLError / 非 JSON |
| `_default_post_ex`（新接口） | 成功带 retcode+200、业务失败带 wording、`HTTPError(403)` → `http_status=403`、网络错误 → `http_status=None`、坏 JSON → `bad response: …` |
| `send_ex` / `send` | 注入 post 的成功/失败两种结果、私聊端点与 `Authorization` 头 |
| `call` | 非法 action 名**先于网络**拒绝、HTTP 错误带 body 与 status、网络错误包装、非对象响应、retcode 非数字 → -1、失败消息优先级（`message` → `msg` → `OneBot action failed: <status>`） |
| `_loop` | 不该回且没有 ingest_fn 时静默丢弃、发送失败计入 `failed`+`last_error=send_failed`、回复函数抛异常被记下不崩线程、空回复不发、`stop()` 队列满时走 `queue.Full` 分支 |
| 去重 | 过期键被清掉（TTL 生效） |

## 2. `aiji/heart/social_service.py`：76% → **100%**（`tests/test_social_service_edges.py`，36 条）

| 区域 | 补了什么 |
|---|---|
| `SocialError` | 默认 `message=code`、自定义 status |
| `publish` | `include/exclude` 没给目标 → `target_uins_required`；可见性支持名字与数字（1/4/16/64/128）；未知可见性拒绝；空正文、超 2000 字、非法 QQ 号 |
| `_media_path` 闸门 | http URL 直通；相对路径落 media_root；**越界路径必须拒绝**（`media_path_forbidden`）；贴纸目录是合法根；非图片后缀、文件不存在 |
| `comment` / `auto_comment` | 空 tid；post_index 越界（-1、100）；msglist 为空或条目没 tid → 404；成功时 `action/post_index` 正确 |
| 昵称/签名 | 头像走路径闸门；签名 >200 拒绝；`\r\n\r\n\r\n` 归一成空行 |
| 分享 | 纯数字 → 网易云链接；查歌结果优先；查不到 → `song_not_found`；B 站 BV / av / 带查询串的完整链接 / `av` 段（`bvid` 应为空）/ 乱码拒绝 |
| `_invoke` | 未配置 → `social_not_configured` 503；`SocialError` 原样透传；其它异常 → `onebot_call_failed` 502 |
| `_sticker_snapshot` | 回调结果优先、回调崩了回退清单、无贴纸目录、清单里过滤"不存在/越界/非图片/非对象"的条目、坏 JSON 与错形状都回退成计数 0 |

关键一条：`test_manifest_parsing_and_filtering` 用 5 条脏数据（非图片、不存在、`../escape.png`、
字符串元素）验证**只有 1 条**算数——贴纸清单是用户可编辑的文件，
不做过滤就会把任意路径塞进发布请求。

## 顺手记下的坑

- `unittest.TestCase` 已经占用了 `_outcome` 这个名字，自定义辅助方法叫它 →
  `TypeError: '_Outcome' object is not callable`。改名 `_probe`。
- Python 源码里的 `b"中文"` 是 `SyntaxError`（bytes 字面量只能 ASCII）。
  JSON 响应体统一用 `json.dumps(obj, ensure_ascii=False).encode("utf-8")` 造。

## 验证

- `tests/test_onebot_edges.py` 37/37，`tests/test_social_service_edges.py` 36/36
- 覆盖率：`onebot.py` 100%、`social_service.py` 100%（statement）
- ruff 全过
- 全量 suite：v44

## 下一轮候选

1. 控制台**按钮级** E2E（`gui_*` 驱动真实 Edge 窗口）
2. `being_*` 表按 persona 存、人格只有一份 —— 多实例部署时的语义
3. 剩余低覆盖模块的全表扫描，找出还有哪些 <80%
