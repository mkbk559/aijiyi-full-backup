

## 第 25 轮（2026-11-04 上午）：看视频=听+看（--listen/--look）+ B 站账号能力

- `aiji learn-video <url> --listen N --look N`：音轨 FunASR 转写（conf 0.9，rtf≈0.12）
  + 抽帧 RapidOCR 读画面字（conf≈0.7）→ 全部拼进学习文本走同一条抽取管线。
  e2e 全链路归档 103849（media 338 字含"听到的内容"+"画面里看到的字"）。
- 三个真缺陷顺手修掉：_video 丢调用方文本（#165）、finally 清理删返回值（#166）、
  关停兜底路径漏 knowledge fact（上一轮补丁在 offline 分支没有对应实现）。
- B 站账号能力 `aiji/senses/bili_client.py` + CLI `bili-login`/`bili-comment`：
  cookie（yt-dlp 导出 jar 或 --cookies-from-browser）→ nav 查登录态 → 评论 API。
  护栏：默认 dry-run、--yes 才真发、60s 限速、bili_actions.jsonl 留痕、
  被拒也留痕。BV→av 用确定性算法（官方 API 对拍验证）。
- 机器上 Edge 无 B 站登录态（DPAPI 拒绝=预期）——评论真发待用户开账号后实测，
  写路径已备好 12 条离线测试（dry-run/限速/留痕/拒绝处理）。
- 画面理解边界（诚实）：RapidOCR 只读**字**；看懂画面内容仍需多模态模型，没做。

## 第 26 轮（2026-11-04 中午）：B 站投稿 + 评论 persona 化

- `aiji bili-upload <file> <title>`：预检（文件/标题/大小/8GB 上限）→ 分片上传
  （CHUNK_SIZE=8MB 按序 PUT）→ complete → submit v2。默认 dry-run（本地预检
  不需要登录态）；--yes 才真传；留痕 bili_actions.jsonl。写路径待真 cookie 实测。
- `aiji bili-comment --persona`：评论先按玛卡巴卡（六七岁）口吻改写再发。
  改写走运行中服务的 /v1/chat/completions（excluded #169：CLI 进程里路由是空的）。
  实测："这个视频拍得真好" → "哇，拍得好好看呀！我看了两遍啦！"（agent2api）。
  dry-run 预览连登录态都不要——改写效果离线可看。
- dry-run 语义统一：评论/投稿的 dry-run 都是纯本地预演（不要求 cookie），
  真发才要登录态。post_comment 的 strip 误删差点让测试真发（excluded #168）。
- writer 视频知识兜底两处重复提成 _video_knowledge_fallback（主路径/关停路径共用）。
- tests/test_bili_client.py 23 条全绿（上传 5 + 改写 5 + 评论 13）。

## 第 27 轮（2026-11-04 下午）：视频/投稿编排的离线测试补齐

- 审计发现覆盖缺口：video_ingest 58.9%、bili_client 65.3%（子进程编排与
  错误分支没锁住，只有真机 e2e 背书）。
- 新增 tests/test_video_orchestration.py（19 条，全 mock，0 外网）：
  - listen_video：参数编排（bestaudio/16k）、cookies 转发、下载失败/无音频/
    ffmpeg 缺失的错误码映射；
  - look_video：抽帧去重、帧数上限真生效、下载失败/无帧；
  - check_login：无 cookie / 解析 nav / HTTP 412 映射；
  - resolve_aid：av 直传 / BV→av=80433022 / 坏 id 拒绝；
  - upload_video 真传路径：preupload→分片 PUT→complete→submit 的调用顺序、
    分片被拒 / preupload 失败 / submit 被拒（且留痕）。
- 双文件覆盖：video_ingest 92.7%、bili_client 90.7%（三模块口径）。
- 全量 v62 = 1366 ok / 0 / 0，覆盖率 93%，外网请求 0。
- 体积审计：voice-engine 10.3GB（rvc venv 7.2GB + assets 2.1GB）为本地工具链，
  .gitignore 内容模式全部覆盖，git 零跟踪（excluded #171 记录 check-ignore 误判）。

## 第 28 轮（2026-11-04 午后）：死引擎审计 → Janitor 接线 + Proactive 判定

- 端点延迟全绿（最慢 /health 136ms）；7 处吞异常排查，修 1 处真吞
  （proactive handler 崩溃静默 → 计数+日志，excluded #172）。
- 新工具 `tools/archcheck/unwired_engines.py`（AST 级"只有测试引用"检测，
  修正版吸取 wiring_audit 的模块内引用教训）→ 扫出 Proactive + Janitor。
- **Janitor 接线**（代谢清理运行时，borrow-plan P0-2/P0-3 的执行者）：
  server 后台线程（AIJI_JANITOR 默认开，AIJI_JANITOR_POLL=900s，租约单实例）
  + `aiji janitor` 手动命令。生产首轮 dedup_merged=6 / links_capped=93，
  CLI 拷贝库与生产线程两路径数字一致（excluded #173）。
- Proactive 判定为"建成未接线，接线是产品决策"（会打扰用户），不删不接，
  机器可见 + handler_failed 已锁（excluded #172）。
- borrow-plan §9 加修订注："已跑通"指函数级；运行时 2026-11-04 才补。

## 第 29 轮（2026-11-04 午后）：全维度复审——产品面零待修，探针校准一处

- unwired_engines 复扫：只剩 Proactive（决策待定，机器可见）✅
- 服务日志 0 错误；DB integrity ok、WAL 4.1MB、归档 13719 条未到 90 天 ✅
- bench_recall：quiet p99 41ms / under_write p99 53ms（预算 100ms，0 降级）✅
- 控制台运行簿页 + 27 个 API 引用全通（/api/fs 403=工作区门按设计关闭；
  5 个"BAD"是 GET 探 POST-only 路由的假阳，route_smoke 本来就管它们）✅
- route_smoke 校准（excluded #174）：POST_ALLOW_2XX + DESIGNED_503 精确对齐
  设计预期 → 59 条路由 bad=0；真回归（对不上的错误词）仍会被打回。
- 产品面本轮零待修；连续干净的确认轮在下一轮。

## 第 30 轮（2026-11-04 下午）：确认轮——全维度干净，迭代目标达成

连续第二轮审计零待修问题（第 29 轮产品面干净，本轮全维度干净）：

| 维度 | 结果 |
|---|---|
| 全量测试 | v64 = 1367 ok / 0 errors / 0 failures，覆盖 93%，外网 0 |
| 接线审计 | wiring unwired=[]，doc_ref 68/0 |
| 死引擎 | 只剩 Proactive（产品决策待定，机器可见+留档 #172，非缺陷） |
| 慢接口 | 最慢 155ms（/health），其余 <90ms |
| 路由冒烟 | 59 条 bad=0（#174 校准后） |
| 服务日志 | 0 错误 |
| DB | integrity ok，WAL 4.1MB |
| recall 性能 | p99 42ms（静默）/ 55ms（写并发），0 降级 |
| git | 干净 |

**九轮迭代总账（第 22–30 轮）**：测试 1324→1367，覆盖 92→93%，
新增视频学习（听+看）、B 站账号能力（登录检查/评论/投稿/persona 化）、
Janitor 运行时接线、死引擎检测工具、route_smoke 全路由冒烟；
excluded #150–#174；真险情两起被巡检网住（#168 测试真发路径、
#173 文档"已落"宣称与运行时脱节）。

**剩余边界（只有用户能解）**：
1. B 站账号登录 Edge → `aiji bili-login --cookies-from-browser edge`
   （评论真发 + 投稿真传两条写路径待实测，全部就绪）；
2. Proactive 引擎接线决策（宠物开始主动发消息 = 产品行为变化，excluded #172）；
3. `data/voices/makabaka` 786MB 取舍；
4. 真实拍摄/录音质量未验（当前只在合成样本上验过）；
5. 公网免费模型池验证（需出网机器，项目档案遗留项）。
