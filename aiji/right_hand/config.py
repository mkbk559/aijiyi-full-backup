"""配置：数据库路径、召回预算、免费模型池路由。

路由来源优先级：显式 JSON 文件 > AIJI_ROUTES 环境变量(JSON) > 内置示例(全部从环境变量取 key)。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Route:
    name: str
    base_url: str
    model: str
    api_key: str
    timeout_s: float = 60.0
    max_inflight: int = 2


# 免费/低成本 OpenAI 兼容端点。key 一律从环境变量读，仓库里不落任何密钥。
# 缺 key 的路由会被跳过（doctor 里能看到谁在场）。
# 2026-10-29 接入本机两个反代网关（实测连通，archive 174236-gateway-live-probe）：
#   workbuddy  = WorkBuddy 网关（腾讯积分制反代，8788，glm-5.3-flash 实测 2.2s）
#   agent2api  = Agent2API 网关（多平台聚合反代，3065，glm-5.3-flash 实测 4.6s）
# 两网关都在本机，无出网 SSL 问题——LLM 第二层/模型摘要/真实抽取从此可验。
# key 从 ~/.dsh/.credentials.yaml refs 取出后设进环境变量（AIJI 启动脚本注入）。
DEFAULT_ROUTES: tuple[tuple[str, str, str, str], ...] = (
    ("workbuddy", "http://127.0.0.1:8788/v1", "glm-5.3-flash", "WORKBUDDY_HUB_KEY"),
    ("agent2api", "http://127.0.0.1:3065/v1", "glm-5.3-flash", "AGENT2API_KEY"),
    ("pollinations", "https://text.pollinations.ai/openai", "openai", "POLLINATIONS_API_KEY"),
    ("zhipu-flash", "https://open.bigmodel.cn/api/paas/v4", "glm-4-flash", "ZHIPU_API_KEY"),
    ("siliconflow", "https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct", "SILICONFLOW_API_KEY"),
    ("openrouter-free", "https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free", "OPENROUTER_API_KEY"),
)


@dataclass(slots=True)
class Settings:
    db_path: Path = field(default_factory=lambda: Path("data/aiji.db"))
    budget_ms: int = 100
    workers: int = 2
    top_k: int = 8
    recent_turns: int = 40
    max_attempts: int = 3
    vector: str = "hash"  # off | hash | hybrid（hybrid = hashing + LLM 软嵌入）
    routes: tuple[Route, ...] = ()
    # 识别层
    # 识别层：真实引擎（RapidOCR ~2s/图、whisper tiny ~10s+/条音频）下 2s 不够，
    # 2026-10-29 上调。纯文本/表情路径不受影响（它们不经过 _timed）。
    recognize_timeout_s: float = 45.0
    min_confidence: float = 0.5
    # whisper 回退模型大小（FunASR 首选，非中文/无 funasr 时回退 whisper）
    asr_model: str = "tiny"
    # 总结
    summary_every: int = 20
    summary_model: bool = True  # 先用模型写摘要，池子不可用/输出不合法时退回本地压缩
    # 会话压缩（超长会话强制摘要 + 淘汰原文）
    max_turns_per_session: int = 400  # 0 = 不淘汰
    turn_keep: int = 40
    # 注入
    inject_chars: int = 700
    inject_items: int = 12
    # 弃答下限：连最好一条的相关度都低于此值就整块不出（0 = 关，默认关）。
    # 实测（bench/bench_self_test.py，2026-11-02）：开 0.15 能把"没提过的事"的硬凑
    # 从 4/6 降到 0/6，但也会把"我住哪"这种只能靠单字命中的真问题一起吞掉，故默认关。
    inject_min_rel: float = 0.0
    # 主动配额：额度来自反向代理网关的积分（见 aiji/quota.py）。
    # credits_url：任意返回数字的网关接口（如余额 API）；credits_json_path 指到那个数字，如 "data.balance"
    # credits_models_url + credits_model：读网关 /v1/models 里该模型的 credits 当"单条单价"
    # credits_static：最后兜底（等价环境变量 AIJI_CREDITS）
    # 三条都是空 → 只用 params.PROACTIVE_QUOTA_24H 的每日硬上限（即今天为止的行为）
    credits_url: str = ""
    credits_json_path: str = ""
    credits_models_url: str = ""
    credits_model: str = ""
    credits_static: float | None = None
    credits_reserve: float = 0.2
    # 排序融合：weighted = 手工权重（既有行为）；rrf = 按名次融合；
    # rrf_shadow = 只记录"若开 RRF 会选哪几条"，不改实际返回（先对比再切）
    rank_mode: str = "weighted"
    # 代谢层开关：关掉则注入不计 hit_count（退回 v10 的"只读不强化"）。
    # 冻结判据不走这个开关——它由 params.STALE_DAYS/FREEZE_BELOW 决定，janitor 无条件执行。
    decay_enabled: bool = True
    # 生命体运行层
    reflection_interval_s: float = 5.0
    writer_backlog_heuristic_at: int = 20
    debounce_ms: int = 800
    humanize_min_ms: int = 600
    humanize_max_ms: int = 4500
    fallback_cooldown_s: float = 60.0
    typing_min_ms: int = 300
    typing_max_ms: int = 1800
    persona: str = "child"
    # 成长引擎 v2：阶段由"学习量（学分）"决定，**没有按时间长大的速率**。
    # persona_stage 直接把阶段钉住（primary/middle/high/university/work，None=按学分走）；
    # persona_age 是兼容旧配置的另一种钉法（按年龄落到对应阶段）。
    persona_age: float | None = None
    persona_stage: str | None = None
    # 执行层：Function Calling 工具与人工审批
    actions_enabled: bool = True
    action_root: str = ""
    action_approval_timeout_s: int = 120
    action_max_rounds: int = 4
    # NapCat / OneBot 11 个人 QQ
    onebot_api: str = ""
    onebot_token: str = ""
    onebot_group_mode: str = "mention"
    onebot_webui: str = ""
    # QQ 空间写动作：本地媒体目录 + 只读表情包目录
    social_media_dir: str = ""
    sticker_dir: str = "D:/AstrBot/data/sticker_packs/aiji"
    sticker_feed_url: str = (
        "https://cdn.jsdelivr.net/gh/cheems135/dsh-qq-bridge@main/"
        "assets/preset/manifest.json"
    )
    sticker_update: bool = True
    sticker_update_initial_s: float = 120.0
    sticker_update_interval_s: float = 86400.0
    sticker_max_bytes: int = 8 * 1024 * 1024
    # 风格：额外口癖词表（覆盖内置 20 条之外的高频口头禅，值=词本身）
    tics: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        """从 JSON 文件读配置，没有就用默认值 + 环境变量覆盖。"""
        cfg: dict[str, Any] = {}
        p = Path(path or os.environ.get("AIJI_CONFIG", ""))
        if path is not None or os.environ.get("AIJI_CONFIG"):
            cfg = json.loads(Path(p).read_text("utf-8"))
        s = cls()
        for k in ("db_path", "budget_ms", "workers", "top_k", "recent_turns", "max_attempts", "vector",
                  "recognize_timeout_s", "min_confidence", "summary_every", "summary_model",
                  "max_turns_per_session", "turn_keep", "inject_chars", "inject_items", "asr_model",
                  "reflection_interval_s", "debounce_ms", "humanize_min_ms", "humanize_max_ms",
                  "fallback_cooldown_s",
                  "writer_backlog_heuristic_at",
                  "typing_min_ms", "typing_max_ms", "actions_enabled", "action_root",
                  "action_approval_timeout_s", "action_max_rounds", "persona",
                  "rank_mode", "decay_enabled", "inject_min_rel",
                  "credits_url", "credits_json_path", "credits_models_url",
                  "credits_model", "credits_static", "credits_reserve",
                  "social_media_dir", "sticker_dir"):
            if k in cfg:
                setattr(s, k, cfg[k])
        if "db_path" in cfg:
            s.db_path = Path(cfg["db_path"])
        # 口癖词表：cfg["tics"] 优先（list[str]），否则 AIJI_TICS 环境变量（逗号分隔）
        tics_raw = cfg.get("tics")
        if tics_raw is None:
            tics_raw = os.environ.get("AIJI_TICS", "")
        if isinstance(tics_raw, str):
            tics_raw = [w for w in tics_raw.split(",") if w.strip()]
        s.tics = tuple(str(w).strip() for w in (tics_raw or []) if str(w).strip())
        s.onebot_api = str(cfg.get("onebot_api") or os.environ.get("AIJI_ONEBOT_API", "")).rstrip("/")
        s.onebot_token = str(cfg.get("onebot_token") or os.environ.get("AIJI_ONEBOT_TOKEN", ""))
        s.onebot_group_mode = str(
            cfg.get("onebot_group_mode") or os.environ.get("AIJI_ONEBOT_GROUP_MODE", "mention")
        ).lower()
        s.onebot_webui = str(cfg.get("onebot_webui") or os.environ.get("AIJI_NAPCAT_WEBUI", "")).rstrip("/")
        s.social_media_dir = str(
            cfg.get("social_media_dir")
            or os.environ.get("AIJI_SOCIAL_MEDIA_DIR", "")
        ).strip()
        s.sticker_dir = str(
            cfg.get("sticker_dir")
            or os.environ.get("AIJI_STICKER_DIR")
            or s.sticker_dir
        ).strip()
        s.sticker_feed_url = str(
            cfg.get("sticker_feed_url")
            or os.environ.get("AIJI_STICKER_FEED_URL")
            or s.sticker_feed_url
        ).strip()
        update_raw = cfg.get(
            "sticker_update", os.environ.get("AIJI_STICKER_UPDATE", "1"))
        s.sticker_update = str(update_raw).strip().lower() in (
            "1", "true", "yes", "on")
        try:
            s.sticker_update_initial_s = max(0.0, float(cfg.get(
                "sticker_update_initial_s",
                os.environ.get("AIJI_STICKER_UPDATE_INITIAL", "120"),
            )))
            s.sticker_update_interval_s = max(30.0, float(cfg.get(
                "sticker_update_interval_s",
                os.environ.get("AIJI_STICKER_UPDATE_INTERVAL", "86400"),
            )))
            s.sticker_max_bytes = max(64 * 1024, min(
                32 * 1024 * 1024,
                int(float(cfg.get(
                    "sticker_max_bytes",
                    os.environ.get("AIJI_STICKER_MAX_MB", "8"),
                )) * (
                    1 if "sticker_max_bytes" in cfg else 1024 * 1024
                )),
            ))
        except (TypeError, ValueError):
            pass
        s.persona = str(
            cfg.get("persona") or os.environ.get("AIJI_PERSONA") or s.persona
        ).strip() or "child"
        # 成长引擎 v2：阶段钉住 + 旧的年龄钉住（环境变量优先，方便演示与验收复现）。
        # 注意：**没有 aging_rate**——成长只看学分，不看真实时间。
        raw = cfg.get("persona_age")
        if raw is None:
            raw = os.environ.get("AIJI_PERSONA_AGE")
        if raw not in (None, ""):
            try:
                s.persona_age = float(raw)
            except (TypeError, ValueError):
                pass
        stage = cfg.get("persona_stage")
        if stage is None:
            stage = os.environ.get("AIJI_PERSONA_STAGE")
        if stage not in (None, ""):
            s.persona_stage = str(stage).strip() or None
        # 人格预设自带推荐口癖：配置/环境变量都没显式给 tics 时按 persona 取默认。
        # （例如 persona=child → "为什么呀/我跟你讲/才不是呢…"，口癖抽取与风格
        # 改写都会用到，否则小孩设定只影响提示词、不影响风格层。）
        if not s.tics:
            try:
                from aiji.brain.persona import PRESETS

                s.tics = tuple(PRESETS.get(s.persona, {}).get("tics") or ())
            except Exception:  # noqa: BLE001 - 预设读不到不能挡住启动
                s.tics = ()
        env_db = os.environ.get("AIJI_DB")
        if env_db:
            s.db_path = Path(env_db)
        for env_key, attr in (("AIJI_BUDGET_MS", "budget_ms"), ("AIJI_WORKERS", "workers"), ("AIJI_VECTOR", "vector")):
            if env_key in os.environ:
                raw = os.environ[env_key]
                setattr(s, attr, int(raw) if attr in ("budget_ms", "workers") else raw)
        s.routes = _load_routes(cfg)
        return s

    def describe(self) -> dict[str, Any]:
        return {
            "db_path": str(self.db_path),
            "budget_ms": self.budget_ms,
            "workers": self.workers,
            "top_k": self.top_k,
            "vector": self.vector,
            "recognize_timeout_s": self.recognize_timeout_s,
            "min_confidence": self.min_confidence,
            "asr_model": self.asr_model,
            "summary_every": self.summary_every,
            "summary_model": self.summary_model,
            "max_turns_per_session": self.max_turns_per_session,
            "turn_keep": self.turn_keep,
            "inject_chars": self.inject_chars,
            "inject_items": self.inject_items,
            "reflection_interval_s": self.reflection_interval_s,
            "writer_backlog_heuristic_at": self.writer_backlog_heuristic_at,
            "debounce_ms": self.debounce_ms,
            "humanize_min_ms": self.humanize_min_ms,
            "humanize_max_ms": self.humanize_max_ms,
            "fallback_cooldown_s": self.fallback_cooldown_s,
            "typing_min_ms": self.typing_min_ms,
            "typing_max_ms": self.typing_max_ms,
            "persona": self.persona,
            "actions_enabled": self.actions_enabled,
            "action_root": self.action_root,
            "action_approval_timeout_s": self.action_approval_timeout_s,
            "action_max_rounds": self.action_max_rounds,
            "onebot_api": self.onebot_api,
            "onebot_token_configured": bool(self.onebot_token),
            "onebot_group_mode": self.onebot_group_mode,
            "onebot_webui": self.onebot_webui,
            "social_media_dir": self.social_media_dir,
            "sticker_dir": self.sticker_dir,
            "sticker_feed_url": self.sticker_feed_url,
            "sticker_update": self.sticker_update,
            "sticker_update_initial_s": self.sticker_update_initial_s,
            "sticker_update_interval_s": self.sticker_update_interval_s,
            "sticker_max_bytes": self.sticker_max_bytes,
            "tics": list(self.tics),
            "routes": [
                {"name": r.name, "model": r.model, "base_url": r.base_url, "key": bool(r.api_key)}
                for r in self.routes
            ],
        }


def _load_routes(cfg: dict[str, Any]) -> tuple[Route, ...]:
    raw = cfg.get("routes")
    if raw is None and os.environ.get("AIJI_ROUTES"):
        raw = json.loads(os.environ["AIJI_ROUTES"])
    if raw is None:
        raw = [
            {"name": n, "base_url": u, "model": m, "api_key_env": k}
            for n, u, m, k in DEFAULT_ROUTES
        ]
    out = []
    for item in raw:
        key = item.get("api_key") or os.environ.get(item.get("api_key_env", ""), "")
        if not key:
            continue
        out.append(
            Route(
                name=item["name"],
                base_url=item["base_url"].rstrip("/"),
                model=item["model"],
                api_key=key,
                timeout_s=float(item.get("timeout_s", 60.0)),
                max_inflight=int(item.get("max_inflight", 2)),
            )
        )
    return tuple(out)
