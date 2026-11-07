# 跨平台共享记忆（2026-11-05 用户定调）

> 用户原话："三个平台，记忆共享，以及全部共享，但是发消息之类的规矩你做决定。"

## 决策（我定的）

**一个"主人"，三个平台外壳。** 记忆按**人**存，不按**平台**存；平台只是入口。

- 主人身份：`Ident("shared", "owner", "global")`（下文 `SHARED`）。
- 平台身份：仍然保留（`qq:2216608312` / `douyin:<昵称>` / `bilibili:1402977803`），
  用于**隔离别人**——群里陌生人的话绝不能进主人记忆。
- 读写规则：
  - **写**：主人自己的每一轮对话，写平台身份的同时**镜像**一条到 `SHARED`。
  - **读**：`SHARED` ∪ 当前平台身份，按"文本前 40 字 + kind + id"去重，分数取 max。
  - **别人**（非主人）的对话：只落平台身份，永不镜像。
- 为什么不是"干脆不分平台"：`Ident.where()` 是所有查询的唯一隔离闸门（excluded 里有
  "一次漏写 platform 条件就是串号事故"）；去掉它等于把陌生人消息混进主人记忆。镜像写入
  是**加法**，隔离语义不变，回滚只需停掉镜像。

## 落点

| 层 | 文件 | 做什么 |
|---|---|---|
| 身份 | `aiji/hippocampus/store.py` | 加 `SHARED` 常量 + `Ident.is_owner()` |
| 写 | `aiji/hippocampus/store.py` | `remember(..., mirror_shared=True)`：同一批 turn/fact 再写一份到 `SHARED` |
| 读 | `aiji/hippocampus/recall.py` | `recall_union(query, ident, ...)`：两次 recall 合并去重 |
| 接线 | `aiji/throat/server.py`（/v1）、AstrBot 插件、`aiji/heart/platforms/douyin_chat.py` | 都改用 `recall_union`；写路径打开镜像 |
| 观测 | `aiji/hippocampus/inject.py` | 注入的每条记忆带 `from_platform`，方便回读时判断来源 |

## 验收（下一轮按这个测）

1. 单测：镜像写入后，`recall_union` 用**另一个平台**的 ident 也能召回这条；
   非主人 ident 召回不到。
2. 真机：抖音群里说一句 → QQ 私聊问「我刚在抖音说了什么」→ 回答必须含那句。
3. 回归：`Ident.where()` 的隔离测试全绿（不许有查询漏 platform 条件）。

## 发消息规矩（我定的，写进 guard）

- 平台最小间隔：qq 5s / bilibili 60s / douyin 90s；每日上限 qq 200 / bili 20 / douyin 10。
- 抖音主动聊天：每 5 分钟一轮（计划任务）、**两轮之间至少 180s**、每轮最多回 1 条、
  同一条消息只回一次（`data/douyin_chat_state.json` 记账）。
- 风控词命中（操作频繁/安全验证）→ 整轮中止，不做重试轰炸。
- 网页端不做批量群发、不加陌生人、不自动同意入群申请（这些留给人工）。
