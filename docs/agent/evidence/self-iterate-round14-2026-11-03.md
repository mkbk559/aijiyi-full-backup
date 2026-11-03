# 自我迭代第 14 轮（2026-11-03 22:50–23:00）：运行时 SQL 审计 + 控制台路由契约

第 12 轮留了个尾巴：静态审计看不见 `f"SELECT … WHERE {w}" + ident.where()`
这种**运行时拼条件**的查询。这一轮把盲区补上，顺便给控制台按钮加了契约测试。

## 1. 运行时 SQL 审计：`tests/test_identity_sql_runtime.py`（3 条）

做法：patch `sqlite3.connect`，给每个新连接挂 `set_trace_callback`，
然后跑一个**纯单身份**的业务流程（写 11 个接口 + 读 11 个接口），
把真正执行过的 SQL 全抓下来。

实测（证据归档 `2026-11-03\225315-runtime-sql-audit-24d688`）：

```
总语句数: 56
碰身份表的语句: 36
其中带 platform 谓词: 36
违规（碰身份表但无 platform）: 0
```

第一版抓到 18 条"违规"，逐条查下来全是**设计上就该全局**的：

- 4 条 `UPDATE fact SET valid_from/valid_to/stability …` —— 建库时的 schema 回填，
  只跑一次，把所有行数据整形，不带身份条件是对的
- 14 条 `SELECT COUNT(*) FROM fact/turn/profile/…` —— 来自 `store.stats()`，
  它报的是**全库规模**（还带一个 `"platforms": [...]` 列表），
  按身份的那一个键是 `facts_active_<platform>`

所以测试里：建库阶段的语句 `statements.clear()` 掉，`stats()` 单独用
`test_store_stats_is_intentionally_global` 钉住语义（免得以后有人当 bug 改掉，
也免得有人误以为它已经做了身份隔离）。

**结论：运行时层面，碰到身份表的语句 36/36 全部带 `platform` 谓词，没有串号口子。**
这比静态清单硬——它验的是真正发出去的 SQL。

## 2. 控制台 ↔ 服务端路由契约：`tests/test_console_contract.py`（5 条）

第 11 轮 E2E 发现 `/api/turns`、`/api/stats`、`/api/memory/library` **并不存在**，
当时是靠真机点出来的。现在机械对一遍：

- 从 `aiji/right_hand/web/operations.html` 抽出全部 `fetch` 路径（**32 个**）
- 从 `aiji/throat/server.py` 抽出路由字面量 + `startswith("…")` 前缀式路由
- 每个路径必须能对上，否则测试红

顺带钉住三条历史：`/api/turns`、`/api/stats`、`/api/memory/library` 不许再出现在页面里；
页面里不许出现 `sk-…` 或硬编码 `Bearer …`。

还有一条 `test_checker_actually_catches_dead_paths`：喂已知不存在的路径给检查器，
必须报出来。**没有这条，前一条测试可能只是"永远绿"。**

当前 32 个路径全部有效（`/api/fs/write`、`/api/mcp/research`、
`/api/social/qzone/publish`、`/api/stickers/update`、`/api/being/checkup/live`、
`/v1/gateway/messages` 都在 server.py 里有对应分支）。

## 3. 顺手做的事

- 把线上服务重启到新代码（`_finish_body` 修复生效），`/api/capabilities` 200

## 验证

- `tests/test_identity_sql_runtime.py` 3/3、`tests/test_console_contract.py` 5/5
- ruff 全过
- 全量 suite：v43

## 下一轮候选

1. `social_service.py` 77%、`onebot.py` 78%（纯覆盖率）
2. 控制台**按钮级** E2E（点动作 → DOM 变化）：本机 Edge headless 被沙箱 ACL 拦住，
   只能走 `gui_*` 驱动真实窗口
3. `being_*` 表按 `persona` 存、但人格只有一份 —— 多实例部署时会不会串？值得想
