# 自我迭代第 24 轮（2026-11-04 08:30–09:05）：让 AIJiYi "看视频学习"（B 站/抖音）

用户需求：接抖音和 B 站学习 + 怎么去看视频。这轮从探针到端到端把链路打通，并回答了
"为什么不理解画面"。

## 一、先探边界（机器证据，不猜）

探针 `​.tmp/probe_video_net.py`，归档 `…\083635-video-net-probe-34e6a7\stdout.txt`：

| 探测 | 结果 |
|---|---|
| bilibili 首页 / view API（直连） | **200 OK** |
| bilibili / douyin（走 127.0.0.1:7897 代理） | **ConnectionRefused——本机 Clash 是死代理** |
| yt-dlp `--dump-json`（默认环境） | **失败：yt-dlp 读 Windows 系统代理**，连死代理 7897 |
| yt-dlp `--proxy ""` 强制直连 | **成功**：标题/时长/UP主/播放量/标签全拿到 |
| yt-dlp 抖音（无 cookie） | **拒绝：`Fresh cookies are needed`**——需要登录态 |
| BV1GJ411x7h7 的字幕 | 只有 `danmaku`（弹幕 xml），无 CC 字幕；弹幕内容信噪比极低（"你被骗了"×7000） |

结论：**B 站开箱即用（必须 `--proxy ""`）；抖音需要 `--cookies-from-browser edge/chrome`**。

## 二、"怎么去看视频"——分层诚实

本地没有视频理解引擎（不能真"看"画面），但知识含量高的层都能拿：

| 层 | 能拿到吗 | 进记忆吗 |
|---|---|---|
| 标题/UP主/简介/标签/时长/播放量 | ✅ yt-dlp 元数据 | ✅ 走同一条抽取管线 |
| CC 字幕（zh-Hans） | 看视频（知识区常见，MV 常无） | 有就进（本版先出计划，字幕文本留待下轮拼接） |
| 弹幕 | ✅ xml | ❌ 不进（噪音），只在 meta 里留语言列表 |
| 画面内容 | ❌ 无本地视觉引擎 | ❌ |
| 音频（无字幕时） | ✅ ffmpeg 可提取 | 计划 `audio_asr`（≤30 分钟视频），转写走已有 FunASR/whisper |

`learning_plan()` 把这个决定变成可打印的计划：`subtitles` / `audio_asr` / `metadata_only`。

## 三、实现（3 个文件 + 1 个管线补丁）

1. **`aiji/senses/video_ingest.py`（新）**：`fetch_video_info`（yt-dlp 子进程，
   `--proxy ""`、`--no-playlist`、超时、cookies 透传，失败信息带"下一步怎么办"）；
   `build_learn_text`（元数据→学习文本）；`learning_plan`（降级计划）。
   抖音无 cookie 的报错直接提示加 `--cookies-from-browser edge`。
2. **`aiji/hippocampus/recognize.py` `_video` 升级**：`item.extra["video_info"]`
   存在 → 产出 `status=ok` + 学习文本（recognizer=`yt-dlp-meta`）→ 走**和聊天文本
   完全相同**的抽取管线；不存在 → 保持 `metadata_only`（老行为不变）。
3. **CLI `aiji learn-video <url>`**（`senses/cli.py`）：抓元数据 → ingest → 打印
   video/plan/flushed。
4. **`writer._handle_ingest` 知识兜底（真缺陷修复）**：视频是**第三方知识源**，
   实测 agent2api 模型对 MV 文本返回**空 facts**（判"没有用户事实"——它是对的），
   但学过的内容不能就这么丢了：现在额外落一条 `kind=knowledge` 的 fact
   （subject=UP主/视频号，object=学习文本），FTS + 语义召回都能搜到。

## 四、端到端验证（两条独立路径，全归档）

**学**：`aiji --db .tmp/learn_e2e.db learn-video https://www.bilibili.com/video/BV1GJ411x7h7`
- 路径 1（CLI stdout，归档 `…\085709-learn-video-e2e-v5-c92bec`）：
  `rc=0`，title=【官方 MV】Never Gonna Give You Up - Rick Astley，plan=audio_asr，flushed=True。
- 路径 2（直接 SQL 查库，同一归档）：
  `media` 行 `('video','BV1GJ411x7h7','ok','yt-dlp-meta')`；
  `fact` 行 `('视频','【视频】【官方mv】…','knowledge')`。

**想**：`aiji --db .tmp/learn_e2e.db recall "Rick Astley 的视频"`
（归档 `…\085803-recall-check-4ea4ff`）：14ms 命中刚学的 fact，score 0.81，
无 degraded。**学→存→召回闭环成立。**

诊断过程留痕：fact=0 的中间态归档 `…\085334-diag-auto-1c34e1`（writer.stats
`model_ok=1, heuristic=0, last_route=agent2api`——证明模型抽取跑了、判了空），
这一步直接催生了第四节的知识兜底补丁。

## 五、离线单测：`tests/test_video_ingest.py`（21 条）

- yt-dlp 输出解析：正常/danmaku 不算字幕/CC 算/playlist 拒绝/空标题=风控/
  超时→域错误/cookies 透传/失败信息含 cookies 提示
- 学习文本：要素齐全/长简介截断/4000 上限
- 学习计划：subtitles / audio_asr（短视频无字幕）/ metadata_only（长视频/直播）
- 识别器：带 video_info → ok+文本；不带 → metadata_only；无标题 → 忽略
- writer：学完必有 knowledge fact（召回得到）；普通文本 ingest 不受影响

## 六、修掉的顺带问题

- **v57 套件里 1 个 error（我自己本轮新加的用例）**：`ShellTest.test_command_timeout_is_reported_not_raised`
  在客户端 10s 超时——服务端杀 PowerShell 进程树在 Windows 上要好几秒，套件并行时
  10s 不够。修：客户端 timeout 10→30s、命令 `powershell Start-Sleep` → `ping -n 5`
  （进程树小、杀得快；断言 `timed_out=True` 不变）。单跑 8/8 OK。
- e2e 诊断顺手发现 `make_tmp` 只能用于 TestCase（`__main__` 没有 `addCleanup`），
  诊断脚本改用固定目录。

## 七、剩余边界（如实）

- **画面理解不存在**：没有本地视频视觉模型；"看视频"= 读元数据（+将来字幕/ASR）。
  如果要真看画面，需要接多模态模型——是另一个量级的工作。
- **字幕文本还没进记忆**：`learning_plan` 会判 subtitles 模式，但字幕文件的下载与
  拼接（json3→纯文本）留到下轮（本轮先把计划与管道打通）。
- **audio_asr 计划还没实现执行**：计划会给出"转音频用 ASR"，实际提取音频+转写
  的执行路径没写（ffmpeg 已在机器上，能力是有的）。
- 抖音只验证到"要 cookies"这一步（没有登录态可用）；有 cookies 后 yt-dlp 原生支持。
- B 站风控/限流未压测；`--no-playlist` 意味着合集只学单集（给了明确报错提示）。

## 八、第 24 轮后半：v58 抓到一个真丢数据缺陷（writer.stop）

v58 = 1321 ran / **1 error**：`test_cli.test_remember_then_recall` 间歇性空召回。
复现 3 次挂 1 次。根因不是视频改动，是**关停路径丢写入**（两层，见 excluded #164）：

1. flush 超时后队列里剩余 job 被旧 stop() 直接丢弃；
2. 更深一层：worker 手里的 job 卡在模型网关调用里，`join(timeout=2)` 到点就走，
   CLI 进程退出硬杀 daemon worker——**正在处理的写入凭空消失**。
   证据：`…\093141-debug-dead-pool-5e57a4`（depth=0、pending=2、facts=[]）。

修法（`writer.py`）：`_inflight` 登记在飞任务；`stop()` 两层兜底——先离线消费
队列剩余 job，再等 pending 归零，等不到就对在飞 job 离线落库（heuristic；
幂等靠 add_fact 的 merge 语义）。回归测试 `tests/test_writer_stop_no_drop.py`
3 条：慢池好结果不丢 / 死池排队 job 兜底 / 死池在飞 job 兜底。
修复后 CLI 用例连跑 4/4 稳定（修复前 3 挂 1）。
