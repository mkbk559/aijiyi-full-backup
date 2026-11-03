# 项目体检：还剩哪些问题 · 2026-11-03 10:20

> 触发：用户问"整个项目还有什么问题"。全部结论来自本轮真机跑出来的原始输出，
> 脚本都在 `tools/archcheck/`，JSON 落在 `tools/archcheck/results/`。
> 上一轮体检（结构/覆盖率/端点）见 `docs/agent/evidence/project-audit-2026-11-03.md`，
> 本文只写**这轮新发现 + 已经修掉的**。

## 0. 一句话结论

体检本身很干净（510/510 测试、58 条路由 0 个 5xx、控制台 9 系统/68 节点/56 接口全对上），
真正的问题不在"跑不起来"，而在**三个语义层面的坑 + 一堆身份改造留下的残渣**：

1. 旧人格学的"调侃/损人/自嘲"性格会直接喂给六七岁的小孩（**已修**）
2. 生命层 1001 条事件全挂在旧人格名字下，换人格就"失忆"（**已迁**）
3. 全局环境代理指向没启动的 Clash，把本来能通的下载变成 502（**已修**）
4. 声线包/架构文档/结论文档里还留着 `dafeiyu`、`大肥鱼`（**声线已改名**，文档待清）

## 1. 严重：成年语感性格漏进儿童人格 —— 已修

**证据**：`tools/archcheck/persona_child_trait_clash.py`（假模型，只取提示词，无随机性）

```
修复前：{"leak": true,  "probes": {"trait_调侃": true, "trait_自嘲": true, "trait_损对方": true, "persona_child": true}}
修复后：{"leak": false, "probes": {"trait_调侃": false, "trait_自嘲": false, "trait_损对方": false, "persona_child": true}}
```

**成因**：`head.py` 组装提示词时无条件把 `memory.traits(ident)` 交给
`selfhood.system_prefix()`；`persona=child` 只影响 `persona.style_block()`。
库里 1521 条 trait 有 **235 条**是成年语感，置信 0.95、最高 evidence 90，例如

- 「常带调侃语气损对方，如『你那是守锅还是守夜』」
- 「对话中喜欢调侃对方或自嘲，营造轻松的交流氛围」
- 「对方抛梗调侃时顺着接并自嘲反击」

**修法**：`selfhood.kid_safe_traits()` + `_ADULT_TONE_WORDS`（18 个词根）。
`persona="child"` 时过滤局部与全局 trait，**不改库**（切换到 sunba 仍然生效）。
`head.py` 传 `persona=persona_key`。回归测试 4 条（`tests/test_persona_child.py::TestKidTraitFilter`）。

**没修的**：库里 235 条脏数据还在。是否清理另议——它们对 sunba 人格仍然有效。

## 2. 中等：换人格导致生命层失忆 —— 已迁

**证据**：`tools/archcheck/audit_snapshot.py` + `tools/migrate_being_persona.py`

```
迁移前 BEFORE {"being_state": {"child": 1, "sunba": 1}, "being_event": {"child": 1, "sunba": 1001}, "being_note": {"sunba": 30}}
迁移后 AFTER  {"being_state": {"child": 1}, "being_event": {"child": 1002}, "being_note": {"child": 31}}
```

`being_state / being_event / being_note` 都按 `persona` 分区，默认人格从 `sunba` 改成 `child`
以后，1001 条生命事件 + 30 条笔记对新人格不可见。已备份 `data/backups/aiji.db.bak-before-being-migrate-20261103-101545`（48.2 MB）后迁移，
并补一条过渡说明笔记。

**根因没修**：生命层仍按人格名字分区。真正想要的是"一个身体，多套说话方式"，
那么 `being_*` 不该带 persona 分区。这是 schema 决策，未动。

## 3. 中等：全局代理把能通的下载变成 502 —— 已修

**证据**：`route_smoke` 修复前后

```
修复前：POST /api/stickers/update → 502 {"code": "download_failed", "message": "<urlopen error [WinError 10061] ...>"}
修复后：POST /api/stickers/update → 2xx
```

手工对照（同一台机器、同一时刻）：

```
走环境代理（127.0.0.1:7897）→ 10061 refused（代理没启动）
-NoProxy / ProxyHandler({}) → 200 OK，manifest 17 518 字节
jsdelivr 与 raw.githubusercontent 两个源都试过，都能直连
```

**成因**：`HTTP_PROXY/HTTPS_PROXY` 全局设置，`urllib.request.urlopen()` 默认吃环境变量，
代理一关全线失败——包括 QQ 本地 OneBot 接口（127.0.0.1）这种根本不该走代理的调用。

**修法**：新增 `aiji/net.py`，直连优先、失败才用环境代理重试一次，抛错时抛直连的原始异常；
`AIJI_USE_ENV_PROXY=1` 可强制走代理。已接线 6 个模块 8 处：
`sticker_service / song_service / voice_service / media_audio / mcp_research / onebot`。
相关测试全绿（onebot 10、song 9、voice 14、sticker 9、mcp_research 10）。

## 4. 轻：旧身份的残渣

| 残渣 | 状态 |
|---|---|
| 声线包 `data/voices/dafeiyu`（name=大肥鱼，8×54.9 MB 权重 + 索引） | **已改名** `makabaka` / 玛卡巴卡；profile.json 的 id/name/model/index 全对上；真机合成验过：`POST /v1/audio/speech` → 200、audio/wav、4.92 s、236 KB |
| `tools/archcheck/results/nodes.json` 里的 `dafeiyu.pth`、`docs/architecture.html` 的"大肥鱼角色声线" | 已改；控制台已重新生成并 `console_check` 通过 |
| `voice_http_e2e.py` 写死 `"voice": "dafeiyu"` | 已改成从 `/v1/voices` 取活动声线（否则改名会制造"假失败"） |
| 29 个 docs 文件仍含"大肥鱼/鱼缸" | **未动**：多为历史方案与研究笔记（`dafeiyu-plan.md`、研究记录），改了等于篡改历史；只有面向用户/产品面的 `architecture.html` 已改 |
| 测试夹具里的"大肥鱼" | 未动：多为**用户消息文本**（"大肥鱼帮我记一下"），测的是解析不是身份 |
| `data/backups/` 里 8 个历史库快照 | 保留（含改造前状态，是回滚依据），已从 `data/` 根目录收进 `data/backups/` |

## 5. 慢路径 / 已知劣化（未修，量级已测）

| 项 | 数字 | 说明 |
|---|---|---|
| `POST /api/being/checkup/live` | **32.5 s** 同步返回 | 三条真机探针串行；有 10 分钟缓存，但第一次点击要干等半分钟。应改成异步任务 + 轮询 |
| `POST /api/stickers/update` | 2 s（成功）/ 27 s（首次全量） | 同步下载，控制台点了就卡住 |
| `media` 表 3760 条 `low_confidence` + 1930 条 `error` | — | 前者是旧版 1 ms 空识别留下的；后者是 QQ rkey 过期。**清理决策一直没做**（backlog） |
| 语音 HTTP 快路径 | 546 ms 子进程开销 | 见 backlog |

## 6. 卫生

- `tools/cleanup_junk.py --apply`：清掉 493 项 / **233.4 MB**（`.tmp` 测试残留、`__pycache__`、根目录 `aiji-life-loop-*` 等）
- 根目录 `aiji-life-loop-qthyvr0o` 反复出现：`being_checkup` 用 `mkdtemp(dir=temp_base)`，`temp_base` 有时落在仓库根；已随本轮清理删掉，**根因未改**
- `build/`(0.81 MB) `dist/`(5.8 MB) 陈旧产物：`cleanup_junk` 保留当前版产物，其余已删

## 6.5 顺带解决：`pip install` 装不上的真因是代理（2026-11-03）

之前 `pip install coverage` 报 `ERROR: No matching distribution found for coverage`，
被记成"本机装不了"。真因是环境变量 `HTTP_PROXY=http://127.0.0.1:7897` 指向没启动的代理，
pip 走它就直接失败。清掉大小写四个代理变量 + `--proxy ''` 之后：

```
python -m pip install --quiet --proxy '' ruff     → ruff 0.17.3
python -m pip install --quiet --proxy '' coverage → Coverage.py 7.17.0
```

所以"覆盖率无法在本机重测"这条**不再成立**，ruff 也重新可用（本轮 `ruff check` 0 error）。

## 7. 本轮没解决的清单（按建议顺序）

0. **`aiji/senses/cli.py` 覆盖率 0%（311 条语句一条没走到）**——本轮新测：
   `python -m coverage run --source=aiji -m unittest discover -s tests -t .`
   → `TOTAL 9545 语句 / 1817 未覆盖 / 81%`，529 tests OK。
   CLI 的所有子命令（`grow`/`quota`/`forget`/`archive`/`self-memory`…）只被手工敲过，
   没有一条自动化用例；同理 `octop_gateway` 53%、`media_audio` 53%。
   **这轮没补**：CLI 是薄壳，但"薄壳零覆盖"意味着改名/改签名不会有测试拦住。

1. `being_*` 去掉 persona 分区（schema 决策，一次到位就不会再有"换人格失忆"）
2. 慢端点异步化：`/api/being/checkup/live`（32.5 s）、`/api/stickers/update`
3. `media` 表 5690 条非 ok 行的清理或归档策略
4. 235 条成年语感 trait：清库 / 降权 / 保留，三选一
5. 29 个 docs 里旧身份的措辞：批量加"历史归档，人名已改"的抬头，而不是逐句改写
6. `mkdtemp` 落在仓库根的根因（`being_checkup` 的 `temp_base`）
7. ~~覆盖率无法在本机重测~~ **已解决**：清掉代理后 `coverage 7.17.0` 装上了，本文件 §6.5 有命令与依据
