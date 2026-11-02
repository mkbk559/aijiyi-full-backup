"""Operations page data and persisted, non-secret runtime settings."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from aiji.right_hand.config import Settings
from aiji.skeleton.paths import ROOT

CONFIG_PATH = ROOT / "config" / "runtime.json"

# These values are read by Settings.load when the service starts. Secrets stay in
# environment variables and never pass through this page.
GROUPS: dict[str, tuple[str, ...]] = {
    "记忆与召回": ("budget_ms", "workers", "top_k", "recent_turns", "max_attempts",
                 "vector", "rank_mode", "decay_enabled", "summary_every", "summary_model",
                 "max_turns_per_session", "turn_keep", "inject_chars", "inject_items",
                 "inject_min_rel"),
    "识别与对话": ("recognize_timeout_s", "min_confidence", "asr_model", "persona",
                   "reflection_interval_s", "debounce_ms", "humanize_min_ms",
                   "humanize_max_ms", "typing_min_ms", "typing_max_ms",
                   "fallback_cooldown_s", "writer_backlog_heuristic_at"),
    "接入与操作": ("actions_enabled", "action_root", "action_approval_timeout_s",
                   "action_max_rounds", "onebot_api", "onebot_group_mode", "onebot_webui",
                   "social_media_dir"),
    "主动额度": ("credits_url", "credits_json_path", "credits_models_url",
                   "credits_model", "credits_static", "credits_reserve"),
    "表情包": ("sticker_dir", "sticker_feed_url", "sticker_update",
                "sticker_update_initial_s", "sticker_update_interval_s", "sticker_max_bytes"),
    "QQ群聊": ("qq_mode", "qq_auto_interject_chance", "qq_auto_max_chance",
                "qq_auto_cooldown", "qq_auto_hourly_max", "qq_auto_daily_max",
                "qq_auto_burst_max", "qq_auto_min_recent", "qq_heart_silence",
                "qq_heart_cooldown", "qq_heart_daily"),
}
QQ_DEFAULTS = {
    "qq_mode": "auto", "qq_auto_interject_chance": 0.22,
    "qq_auto_max_chance": 0.55, "qq_auto_cooldown": 90.0,
    "qq_auto_hourly_max": 8, "qq_auto_daily_max": 24,
    "qq_auto_burst_max": 6, "qq_auto_min_recent": 2,
    "qq_heart_silence": 1800.0, "qq_heart_cooldown": 21600.0,
    "qq_heart_daily": 3,
}
CHOICES = {
    "vector": ("off", "hash", "hybrid"),
    "rank_mode": ("weighted", "rrf", "rrf_shadow"),
    "onebot_group_mode": ("mention", "all", "off"),
    "qq_mode": ("auto", "mention", "all", "off"),
}
MINIMUM = {
    "budget_ms": 1, "workers": 1, "top_k": 1, "recent_turns": 1,
    "max_attempts": 1, "recognize_timeout_s": 0.1, "min_confidence": 0,
    "summary_every": 1, "max_turns_per_session": 0, "turn_keep": 0,
    "inject_chars": 1, "inject_items": 1, "inject_min_rel": 0,
    "reflection_interval_s": 0.1, "debounce_ms": 0, "humanize_min_ms": 0,
    "humanize_max_ms": 0, "typing_min_ms": 0, "typing_max_ms": 0,
    "fallback_cooldown_s": 0, "writer_backlog_heuristic_at": 1,
    "action_approval_timeout_s": 1, "action_max_rounds": 1,
    "credits_static": 0, "credits_reserve": 0,
    "sticker_update_initial_s": 0, "sticker_update_interval_s": 30,
    "sticker_max_bytes": 65536,
    "qq_auto_interject_chance": 0, "qq_auto_max_chance": 0,
    "qq_auto_cooldown": 0, "qq_auto_hourly_max": 1,
    "qq_auto_daily_max": 1, "qq_auto_burst_max": 1,
    "qq_auto_min_recent": 1, "qq_heart_silence": 0,
    "qq_heart_cooldown": 0, "qq_heart_daily": 1,
}
MAXIMUM = {"min_confidence": 1, "inject_min_rel": 1,
           "qq_auto_interject_chance": 1, "qq_auto_max_chance": 1}
EDITABLE = frozenset(key for names in GROUPS.values() for key in names)
DEFAULT = Settings()


def _default(key: str) -> Any:
    return QQ_DEFAULTS[key] if key in QQ_DEFAULTS else getattr(DEFAULT, key)


def _file_data(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("配置文件必须是 JSON 对象")
    return data


def read_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    persisted = _file_data(path)
    values = {key: persisted.get(key, _default(key)) for key in EDITABLE}
    return {
        "values": values,
        "defaults": {key: _default(key) for key in EDITABLE},
        "groups": {name: list(keys) for name, keys in GROUPS.items()},
        "choices": {key: list(value) for key, value in CHOICES.items()},
        "source": str(path),
        "restart_required": True,
        "secret_note": "密钥仅通过环境变量或受保护的本地配置管理；此页面不会读取或显示密钥。",
    }


def _validate(key: str, value: Any) -> Any:
    if key not in EDITABLE:
        raise ValueError(f"不允许修改：{key}")
    default = _default(key)
    if default is None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} 必须是数字或空值")
        value = float(value)
    elif isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{key} 必须是布尔值")
    elif isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} 必须是整数")
    elif isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} 必须是数字")
        value = float(value)
    elif isinstance(default, str):
        if not isinstance(value, str) or len(value) > 1000:
            raise ValueError(f"{key} 必须是短文本")
        value = value.strip()
    if value is not None and key in MINIMUM and value < MINIMUM[key]:
        raise ValueError(f"{key} 不能小于 {MINIMUM[key]}")
    if value is not None and key in MAXIMUM and value > MAXIMUM[key]:
        raise ValueError(f"{key} 不能大于 {MAXIMUM[key]}")
    if key in CHOICES and value not in CHOICES[key]:
        raise ValueError(f"{key} 只能为 {', '.join(CHOICES[key])}")
    return value


def save_config(path: Path, changes: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(changes, dict) or not changes:
        raise ValueError("没有需要保存的设置")
    validated = {key: _validate(key, value) for key, value in changes.items()}
    data = _file_data(path)
    data.update(validated)
    for low, high in (("humanize_min_ms", "humanize_max_ms"),
                      ("typing_min_ms", "typing_max_ms"),
                      ("qq_auto_interject_chance", "qq_auto_max_chance")):
        if data.get(low, _default(low)) > data.get(high, _default(high)):
            raise ValueError(f"{low} 不能大于 {high}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    os.replace(temp, path)
    return read_config(path)


def catalog() -> list[dict[str, str]]:
    """Inventory only implemented entry points; availability is checked live by UI."""
    details = {
        "身份与联系人": ("消息带平台、用户 ID 和范围进入系统，账号表保留每个身份的展示名与来源。", "无需外部服务；空列表表示还没有登记身份。"),
        "事实记忆": ("进入写队列后抽取事实、知识和关系；同值合并，单值冲突失效，历史保留。", "需要写队列运行；模型不可用时使用本地规则兜底。"),
        "记忆关系": ("在同一身份和范围中建立事实之间的关联，召回时最多扩展一跳。", "需要该身份已有事实；不会跨用户展示关联。"),
        "最近对话": ("按会话与身份读取最近轮次；长会话由摘要和归档控制长度。", "先选择正确的平台、用户和会话才能看到对应记录。"),
        "召回与注入": ("按相关度、重要度、时间与反馈排序，再把选中的事实做成有来源的上下文。", "注入只提供上下文，不会自行发消息。"),
        "自我画像": ("独立记录自我偏好与经历，按需要少量加入对话背景。", "内容由后台学习与反思形成；空状态不代表服务故障。"),
        "表达风格": ("根据互动维护表达维度与口癖，不改动角色核心边界。", "按身份读取；新身份可能还没有风格记录。"),
        "异步写队列": ("入站写入异步处理；失败任务留在队列中并记录重试和错误。", "队列持续堆积时检查模型线路和写入错误。"),
        "模型线路": ("模型池记录路由名称、可用性和冷却；生成失败时按策略尝试下一线路。", "外部网关和密钥需要在本机环境中配置。"),
        "系统能力": ("汇总当前加载的设置、可用依赖、服务模块与限制。", "此处是运行快照，配置页保存后需要重启才会更新。"),
        "生命状态": ("维护精力、压力、心情和事件；活动循环按周期推进。", "部分独立算法不一定参与每轮生产对话。"),
        "生命体检": ("检查生命层、状态写入和相关运行条件。", "现场体检会调用当前服务，耗时可能高于普通状态读取。"),
        "待确认操作": ("文件与桌面操作需要人批准时进入待确认队列；可明确批准或拒绝。", "只有操作工具请求审批后才会出现记录。"),
        "QQ / OneBot": ("NapCat 将 QQ 事件通过反向连接交给 AstrBot；插件调用 AIJiYi 生成回复。", "QQ 登录、NapCat、AstrBot 与 AIJiYi 均需在线。"),
        "主动消息": ("按静默时间、冷却、每日额度与余额判断是否可以主动发言。", "实际发送由 QQ 接入方完成；状态为空不表示已发消息。"),
        "对话接口": ("接受消息、召回记忆、调用模型、执行角色检查、返回回复并异步回写。", "至少需要一条可用模型线路；测试会产生真实调用。"),
        "声线与语音": ("注册声线、选择当前声线，按文本合成语音文件。", "Edge、SAPI 或本地转换引擎需按声线要求可用。"),
        "歌曲任务": ("提交歌名或链接后排队、截段、分离人声、换声和混音。", "依赖音源、外部工具和可用声线；任务可能需要较长时间。"),
        "QQ 空间与分享": ("可发表动态、评论、设置头像或签名，以及分享音乐和视频。", "这些操作会对外发布内容，执行前请核对文本与目标。"),
        "表情包": ("合并本地图与公开来源，按心情标签选图，可手动更新或导入。", "外部图片必须通过地址与格式检查。"),
        "MCP 研究": ("发现候选只读 MCP 工具，读取说明并生成研究报告。", "研究不自动安装或执行候选工具；需要网络与白名单配置。"),
        "工作区": ("在指定工作区内浏览、读取和保存文本文件。", "默认关闭；需设置 AIJI_ENABLE_WORKSPACE=1 才能使用。"),
    }
    rows = (
        ("记忆", "身份与联系人", "按平台、用户和会话隔离记录。", "/api/accounts"),
        ("记忆", "事实记忆", "搜索事实、知识、关系及来源。", "/api/memories"),
        ("记忆", "记忆关系", "查看事实之间的关联。", "/api/memory-graph"),
        ("记忆", "最近对话", "回看会话原文与身份范围。", "/api/recent-turns"),
        ("记忆", "召回与注入", "检查命中内容和注入记录。", "/api/injections"),
        ("记忆", "自我画像", "查看自我记忆与画像。", "/api/self"),
        ("记忆", "表达风格", "查看积累的说话习惯。", "/style"),
        ("运行", "异步写队列", "查看待写、失败和重试。", "/api/scheduler"),
        ("运行", "模型线路", "查看模型可用性和冷却状态。", "/api/models"),
        ("运行", "系统能力", "查看服务配置与依赖能力。", "/api/capabilities"),
        ("运行", "生命状态", "查看精力、压力、活动与事件。", "/being"),
        ("运行", "生命体检", "检查生命层当前状态。", "/api/being/checkup"),
        ("运行", "待确认操作", "审批文件或桌面操作。", "/api/actions/pending"),
        ("接入", "QQ / OneBot", "查看 QQ 接入状态与网关。", "/api/integrations/onebot"),
        ("接入", "主动消息", "查看配额、冷却和发送记录。", "/api/proactive"),
        ("接入", "对话接口", "测试记忆、模型与回复链路。", "/v1/models"),
        ("媒体", "声线与语音", "列出声线并提供语音合成入口。", "/v1/voices"),
        ("媒体", "歌曲任务", "查看歌曲任务及进度。", "/v1/songs"),
        ("媒体", "QQ 空间与分享", "查看可用社交动作。", "/api/social"),
        ("媒体", "表情包", "查看本地图库与更新状态。", "/api/stickers"),
        ("扩展", "MCP 研究", "查看只读工具和研究报告。", "/api/mcp"),
        ("扩展", "工作区", "浏览当前允许的工作区文件。", "/api/fs"),
    )
    return [{"group": group, "name": name, "description": description,
             "detail": details[name][0], "requirements": details[name][1],
             "endpoint": endpoint} for group, name, description, endpoint in rows]
