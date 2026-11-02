# 目标达成核对（2026-11-03 收尾）

对照 create_goal 时的三条要求，逐条给机器证据。

## (1) 把 `aiji/` 的模块重排进 9 个人体系统子包，测试全绿、CLI/HTTP/wheel 可用

| 断言 | 证据 | 结果 |
|---|---|---|
| 9 个子包、根目录只剩 `__init__.py` | `python -c`（本轮输出） | `['brain','endocrine','heart','hippocampus','left_hand','right_hand','senses','skeleton','throat']`，root modules `['__init__.py']` |
| 全量单测 | `tools/archcheck/results/suite_after.json` | **491/491 ok**（`Counter({'ok': 491})`），起点 449 → 491（新增 42 条：回复上限 12、反馈闭环 3、发送分类 10、属性测试 3、衰减口径 6、RVC 客户端 7、其他） |
| lint | `uvx ruff check aiji --select F401,F811,F821,F841` | `All checks passed!` |
| CLI | `python -m aiji.senses.cli --help` | 17 个子命令（serve/remember/recall/ingest/inject/feedback/style/media/accounts/stats/doctor/export/forget/archive/self-memory/quota/bench） |
| HTTP | `tools/archcheck/http_smoke.py` | **failures=0**：`/` 44 777 B 含四视图；`/api/operations/{features,config,services}`、`/api/models`、`/api/capabilities`、`/api/life`、`/v1/models` 全 200 且合法 JSON；两条不存在路径正确 404 |
| wheel | `dist/aiji-0.8.3-py3-none-any.whl`（271 873 B）+ 干净 venv 安装 | `aiji from: .tmp\wheeltest\Lib\site-packages\aiji`（confirm 不是源码树）、9 个子包、`imports ok`、`aiji.exe --help` 可用 |

## (2) 补齐图中承诺但没兑现的部分

| 承诺 | 处理 | 证据 |
|---|---|---|
| 心脏层接管发送 | 原来**已在链路**（`Memory._heart_send` 直打 OneBot），真缺的是失败分类 → 新增 `sender.py`（OK/RETRY/THROTTLE/FATAL、1401 之类直接判死、429 退避）、修 429 热循环真 bug | `EVIDENCE-heart-send.md`、`tests/test_send_classify.py`(10) |
| 回复长度硬上限 | 原来只有"超了拒发"，改成 `max_tokens` + `fit_reply` 双保险，IM 200 / 其他 500，按句末切不硬截 | `EVIDENCE-reply-cap.md`、`tests/test_reply_limit.py`(12) |
| `used_ids` 反馈闭环 | 插件与服务端本来就接好，缺端到端证明 → 真 HTTP 用例跑通 `used_ids` 非空、`/feedback` 后 `fact.hit_count` 涨 | `EVIDENCE-used-ids-loop.md`、`tests/test_feedback_loop.py`(3) |
| 生命卡引擎接线 / 能量经济学 | 审计清楚：`LifeCard`+`EndocrineEngine` 在链路；`PersonalityKernel/OfflineCompensation/ContextBudget` 经 LifeCard 内部调用；`Declarative/Procedural` 死代码；`SynapticDiffusion` 未接线。改掉图里一处错说法（`apply_delta` 不是只被测试调用）。能量账本（睡/醒收支、每条消息成本、力竭强制休息、主动闸门）核对为真 | `EVIDENCE-life-engines.md`、`tools/archcheck/wiring_audit.py` |
| RVC 常驻化 | 新增 `tools/rvc_server.py`（VC 常驻）+ 客户端 `--via auto|server|inproc`；**补装 7 个缺失依赖**（此前 RVC 在本机根本跑不起来）；1.5 s 素材 CPU：14 064 ms → **2 892 ms（×4.86）** | `EVIDENCE-rvc-resident.md`、`results/rvc_bench.json`、`tests/test_rvc_client.py`(7) |
| DSH 借鉴 3 项 | ① 发送失败分类 ② hypothesis 属性测试（`test_property_isolation.py`，还真的挖出一个非法输入） ③ FSRS-5（发现项目早有状态量，缺的是排序没读 `stability`；已接，旧口径可回退） | `EVIDENCE-heart-send.md`、`EVIDENCE-fsrs-decay.md` |

## (3) 每步留机器证据并更新 docs/agent 档案

- `tools/archcheck/` 现有可复现脚本：`extract.js`/`harness.js`（23 条 DOM 断言）/`measure.py`（真实字体度量）/
  `cdp_check2.py`（真 Edge CDP，本轮 `items=0` 无溢出）/`run_suite.py`/`http_smoke.py`/`dump_bands.py`/
  `engine_usage.py`/`wiring_audit.py`/`fsrs_ab.py`/`rvc_bench.py`/`make_bench_wav.py`
- 证据文档：`docs/agent/evidence/{body-reorg-2026-11-03,gap-closure-2026-11-03}/` 共 6 篇
- 档案：`docs/agent/PROJECT_STATE.md` 决策日志逐条追加；`docs/agent/work-items/memory-core/excluded.md` 追加 #90–#94
  （三条"以为没实现其实实现了"的假设、两条"图里说错了"的更正）
- 架构图：`gate`/`dispatch`/`recall`/`lifecard`/`d_voiceeng` 五处文字按实测改写，ROADMAP 里 RVC 那条从"P0 待做"改成"P1 缺守护"

## 还剩下的边界（不再是"承诺未兑现"，而是"已知局限"）

1. RVC 服务**没有守护/自愈**，要手动起；只测 CPU、只测 1.5 s 素材。
2. 常驻换声要生效需在 profile 的 `postprocess.command` 里加 `--via auto`，仓库无默认 profile。
3. `DeclarativeEngine`/`ProceduralEngine` 仍是死代码（删或接线需要产品决定），`SynapticDiffusion` 未接线。
4. 真实 QQ/NapCat 端到端发送、真实用户拍摄/录音质量的 OCR/ASR 质量、公网免费模型池——本机条件不具备，未验。
5. 回忆排序用上了 FSRS 曲线，但 `S'=f(S,D,R)` 完整迭代与 `difficulty` 未接（当前数据里 400 条只有 1 条被引用过，测不出差别）。
