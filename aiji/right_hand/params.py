"""集中参数表（阶段 0 固化）：所有可调数字只允许出现在这里。

每个参数必须带来源标签：
- 来自方案 §x —— 设计文档明确给出
- 先验       —— 工程判断拍定，有依据说明但无实测数据
- 待实测     —— 已有初步数据但需真实负载验证（数据记进 baseline.json）

改任何值前先读对应来源；改完跑 tools/check_baseline.py 看漂移。
"""

# ---- 心脏（调度）：aiji/heart/heart.py ----
HEART_LEASE_SECONDS = 120      # 先验：processing 租约互斥超时（方案 1.11 #8；单事件处理实测 <1s，120s≈120× 余量）
HEART_MAX_ATTEMPTS = 5         # 先验：重试上限，超限进死信（方案 1.11 #9 死信语义）
HEART_SCAN_BATCH = 16          # 先验：单次 pump 领取上限（批处理事务减少锁竞争）
HEART_BACKOFF_CAP = 60.0       # 先验：指数退避封顶秒数（方案 1.11 #9）
HEART_INSTANCE_TTL = 90.0      # 先验：实例心跳过期秒数（solid_queue 进程注册表语义：心跳过期实例名下 processing 行被批量退回 pending；90 = 3×典型 pump 间隔 30s；崩溃恢复从 ≤LEASE_SECONDS(120s) 压到 ≤TTL+SCAN）

# ---- 出站：aiji/dispatch.py ----
DISPATCH_INTERVAL = 1.0        # 先验：发送线程扫描周期（秒）
PLATFORM_BLOCK_DELAY = 30.0    # 先验：平台限流时该平台行推迟秒数（等 rate_info.reset_at 或退避）
RATE_RESET_CAP = 3600.0        # 先验：reset_at 异常值防御封顶

# ---- 闸门：aiji/brain/gate.py ----
MAX_REPLY_CHARS = 500          # 来自方案 1.5：回复长度出口硬校验（默认/非 IM 平台的字符上限）
MAX_REPLY_TOKENS = 400         # 2026-11-03 新加：模型侧硬上限。生成前就掐住，不为注定被裁的字付钱
IM_REPLY_CHAR_LIMIT = 200      # 2026-11-03 新加：IM（QQ/微信等）出口字符上限 = 原 QQ_MAX_REPLY_CHARS 的通用名

# ---- 清理：aiji/janitor.py ----
CONF_FLOOR = 0.15              # 待实测：纠错地板（方案 1.11 #4）。初数据：0.9 置信 4 连纠错触底（NEG_FACTOR=0.7）——真实负载下是否过狠待验证
NEG_FACTOR = 0.7               # 待实测：cited 纠错下调乘数（方案 1.7）。0.6 时 4 次到底且无法回升（锁死红信号，2026-10-29 复盘）；0.7 从 0.9 到底需 6-7 次，且配合正向回升路径
NEG_FACTOR_FALLBACK = 0.35     # 来自方案 1.7 轮1补：无 cited 降级定位时幅度减半
POS_FACTOR = 1.25              # 先验：正向确认回升乘数（不对称升降：一次纠错 ×0.7 需约 3 次确认拉回，防"一次夸就满血"）
POS_CAP = 0.95                 # 先验：回升封顶（不到 1.0，留模型不确定度）
LINK_CAP = 6                   # 实测（2026-10-29 linkcap_audit）：cap=3 在簇密语料丢 9% 阈值上边（含 0.66 语义近邻）；cap=6 全保留，边数仅 +10%，且召回侧 RECALL_LINK_BUDGET=6 已封顶遍历——多边只是图材料不进 prompt（方案风险 16 的"十万级"指不封顶，6 仍硬封顶）
HEARTSTATE_KEEP_BUCKETS = 2    # 来自方案 1.11 #5：day-scoped 状态桶保留天数
EVENTLOG_KEEP_DAYS = 7.0       # 来自方案 1.11 #5：event_log 保留天数（短期缓冲恢复用）
JANITOR_LEASE_TTL = 600.0      # 先验：单实例租约 TTL（janitor 全程实测 <1s，TTL≈600× 余量；持锁崩溃停摆上限=600s，见 test_lease_single_instance）
STALE_DAYS = 30.0              # 先验：低置信且从未命中的事实保留 30 天后软删
STALE_CONFIDENCE = 0.4         # 先验：只清理低于 0.4 的陈旧猜测，正常 0.7 事实不受影响

# ---- 主动搭话：aiji/proactive.py ----
PROACTIVE_SILENCE = 1800.0     # 先验：scope 静默多久才允许主动（方案 1.6）
PROACTIVE_QUOTA_24H = 3        # 先验：滚动 24h 主动上限（方案 1.6 额度语义）
PROACTIVE_COOLDOWN = 3600.0    # 先验：退避下限（方案 1.6），×2^n 指数
PROACTIVE_BACKOFF_MAX_N = 6    # 来自方案 1.6：退避档位封顶（3600×2^5≈32h）

# ---- 主动配额 = 反代积分（2026-11-02，aiji/quota.py）----
PROACTIVE_RESERVE_RATIO = 0.2   # 先验：给"用户回话"留 20% 积分，主动搭话不许吃到见底
PROACTIVE_QUOTA_MAX = 8         # 先验：积分再多，24h 主动也不超过 8 次（防充值后刷屏）
CREDITS_CACHE_S = 60.0          # 先验：积分查询缓存 60s（网关是本地进程，但别每条消息都问）
CREDITS_PER_PROACTIVE_DEFAULT = 1.0  # 先验：读不到网关单价时，按"一条一积分"折算

# ---- 概率回忆（认知启发：aiji/recall.py mode="chat"）----
RECALL_CHAT_POOL = 10          # 先验：闲聊模式下轮盘抽样池大小（锚点之外）
RECALL_CHAT_TAKE = 3           # 先验：锚点之外抽 k 条注入（防复读机）
RECALL_LINK_HOPS = 2           # 来自方案（差序联想）：图遍历最多 2 跳（防上下文爆炸）
RECALL_LINK_BUDGET = 6         # 先验：联想扩展命中预算（1 跳 + 2 跳合计上限）
EPISODIC_HALF_LIFE_DAYS = 14.0 # 先验：情景记忆快衰减（"上次被群友嘲笑"类，鲜活但易忘）
RECALL_DECAY_MODE = "stability" # 2026-11-03：新近度口径。stability=FSRS-5 幂律曲线(用事实自己存的 stability，随引用增长) / half_life=旧的指数衰减(不看用没用过)

# 换声（RVC）时长护栏：语音条超过这个秒数就不换声，直接用 TTS 原声。
# 2026-11-03 真机实测（tools/archcheck/{rvc_gpu_bench,rvc_half_bench}.json，RTX 3050 4 GB，半精度）：
#   5 s 素材 444 ms（RTF 0.089，显存 1202 MiB）
#   13 s 素材 682 ms（RTF 0.052，显存 2262 MiB）
#   30 s 素材 23 799 ms（RTF 0.79，显存 3954/4096 MiB）← 断崖：一次性推理把 4 GB 打满
# （fp32 更惨：30 s 要 59 983 ms）。所以拐点在 15 s 上下，超过就别在语音条上换声。
# 点歌/翻唱走 mode="song"，不受此限（离线任务，30 s 素材 24 s 可以等）。
VOICE_MAX_CONVERT_SECONDS = 15.0
                               # 默认 fsrs：bench/bench_memory_quality.py 的 6 条排序不变量在两种口径下都过（见证据文档）

# ---- 反思 Worker：aiji/reflect.py（性格涌现，2026-10-29 新增）----
REFLECT_EVERY_TURNS = 30       # 先验：攒够 30 条新对话才反思一次（太勤=性格抖动，太懒=学不到）
REFLECT_MIN_INTERVAL = 1800.0  # 先验：两次反思最小间隔 30min（防刷屏时连续反思把算力吃光）
REFLECT_MAX_TURNS = 60         # 先验：单次反思最多回看 60 条（控 prompt 体积）

# ---- QQ 接入：aiji/bridge/onebot.py（低风险路径）----
QQ_RATE_PER_MIN = 4            # 现场实测：平台硬上限 5 条/分钟；保留 1 条给表情/人工动作
QQ_COOLDOWN_S = 2.0            # 先验：同一条消息最短间隔，防连发触发频控
QQ_MAX_REPLY_CHARS = IM_REPLY_CHAR_LIMIT   # 兼容旧名：真正的取值在闸门区 IM_REPLY_CHAR_LIMIT，别在这里改

# ---- 反馈下修：aiji/hippocampus/store.py ----
FEEDBACK_FLOOR = 0.15          # 同 CONF_FLOOR（apply_feedback 负向路径地板）

# ---- 运维：aiji/hippocampus/db.py ----
BUSY_TIMEOUT_MS = 5000         # 先验：SQLite busy_timeout（excluded #23：1s 在初始化竞态下不够）

# ---- 记忆代谢（2026-11-02 borrow-plan P0：FSRS-5 门禁层 + 退役不删）----
FREEZE_BELOW = 0.5             # 来自 dsh-memory-brain memory-decay.js：可提取度低于此值冻结（不注入，仍可查）
REVIEW_COOLDOWN_S = 3600.0     # 来自同上 reviewCooldownMs=1h：1 小时内重复想起只算一次强化
STABILITY_INIT_DAYS = 7.0      # 来自同上 initialStability(fact/trait)=7 天；episodic 用 14
STABILITY_MAX_DAYS = 3650.0    # 来自同上 S 上限 3650 天（防一次强化就"永不遗忘"）
FSRS_F = 0.23456790123456789   # R=(1+F·t/S)^-0.5，F=19/81；t=S 时 R=0.9（与 memory-decay.js 一致）
FSRS_SPACED_GAIN = 0.2         # 来自 dsh-somni：跨天再想起 +0.2
FSRS_SAME_DAY_GAIN = 0.05      # 来自同上：同一天重复想起只 +0.05（集中重复收益递减）
STALE_TIER_DAYS = 90.0         # 来自手册 v5 staleDays=90：超 90 天没更新按"可能过时"标注
TURN_ARCHIVE_KEEP_DAYS = 90.0  # turn 原文归档保留天数（退役不删，到期满才物理清）
SELF_MEMORY_MAX = 200          # 来自手册 v5：自我记忆 200 条上限，淘汰最久没用
REFLECT_BACKLOG_SESSIONS = 2   # 来自 dsh-somni：积压 ≥2 个会话就值得跑一轮巩固
REFLECT_IDLE_S = 900.0         # 来自 dsh-somni dream.idleMs=15min：空闲这么久才批处理
SELF_TEST_SAMPLE = 20          # 巩固后自检抽样条数（anda-brain self-test 思路）

# ---- 口径（S5 前置，跨版本对比绑定） ----
INJECTION_SEMANTICS = (
    "injection 表行数 = AIJiYi 注入留痕数（每次 m.inject() 给主模型落一条软引导块，"
    "store.log_injection）。它不是 prompt 注入攻击计数；攻击计数 = payload 命中 "
    "extract._INJECT_CUES 的入站事件数（模拟断言『注入对抗』两条）。两者不得混用。"
)


def snapshot() -> dict[str, dict]:
    """参数快照（进 baseline.json，供 check_baseline 漂移检测）。"""
    import sys
    mod = sys.modules[__name__]
    out: dict[str, dict] = {}
    for name in dir(mod):
        if name.startswith("_") or name in ("snapshot", "annotations", "INJECTION_SEMANTICS"):
            continue
        val = getattr(mod, name)
        if not isinstance(val, (int, float)):
            continue
        out[name] = {"value": val, "source": _source_of(name)}
    return out


def _source_of(name: str) -> str:
    """从本文件源码抓参数的注释（source 标签），保证快照和注释不脱节。"""
    import re
    from pathlib import Path
    src = Path(__file__).read_text(encoding="utf-8")
    m = re.search(rf"^{name}\s*=.*?# (.+)$", src, re.M)
    return m.group(1) if m else "(无来源标注！)"
