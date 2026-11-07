"""平台注册表：三个适配器怎么造出来、各自能做什么。

调用方（CLI / 服务 / 插件）只需要 `build()` + `matrix()`，
不用关心"QQ 走 OneBot、B 站走 cookie、抖音走浏览器"这些差别。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from aiji.heart.platforms.base import Guard, PlatformError
from aiji.heart.platforms.bilibili import BiliAdapter
from aiji.heart.platforms.douyin import DouyinAdapter
from aiji.heart.platforms.qq import QQAdapter
from aiji.skeleton.paths import DATA

PLATFORMS = ("qq", "bilibili", "douyin")

# 写动作的默认闸门：QQ 是自己的号（松一些），抖音风控最狠（紧一些）。
LIMITS = {
    "qq": {"min_interval": 5.0, "daily_cap": 200},
    "bilibili": {"min_interval": 60.0, "daily_cap": 20},
    "douyin": {"min_interval": 90.0, "daily_cap": 10},
}

ACTION_LOG = DATA / "platform_actions.jsonl"


def _action_log() -> Path:
    """动作账本的落点。`AIJI_ACTION_LOG` 可覆盖——测试用它把行写到 `.tmp/`，
    免得污染生产账本（2026-11-05 就被测试行污染过，见 excluded#209）。"""
    override = os.environ.get("AIJI_ACTION_LOG")
    return Path(override) if override else ACTION_LOG


def runtime_config(path: str | Path | None = None) -> dict[str, Any]:
    """读 config/runtime.json（OneBot 地址/token 在这）。读不到就空。"""
    p = Path(path) if path else Path("config/runtime.json")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def make_guard(platform: str, *, log_path: str | Path | None = None,
               overrides: dict[str, Any] | None = None) -> Guard:
    cfg = dict(LIMITS.get(platform, {"min_interval": 60.0, "daily_cap": 20}))
    cfg.update(overrides or {})
    return Guard(log_path=log_path or _action_log(), **cfg)


def build(*, onebot: Any = None, onebot_api: str = "", onebot_token: str = "",
          bili_cred: Any = None, bili_cred_loader: Callable[[], Any] | None = None,
          douyin_session: Callable[..., Any] | None = None,
          douyin_profile: str = "douyin", douyin_headless: bool = True,
          dry_run: bool = True, log_path: str | Path | None = None,
          only: tuple[str, ...] | None = None,
          runtime: dict[str, Any] | None = None) -> dict[str, Any]:
    """造适配器。`onebot` 可以直接给一个 call 可调用对象（测试用）。

    `runtime={}` 表示**不要读** `config/runtime.json`（测试/离线用）。
    """
    runtime = runtime_config() if runtime is None else dict(runtime)
    onebot_api = onebot_api or str(runtime.get("onebot_api") or "")
    onebot_token = onebot_token or str(runtime.get("onebot_token") or "")
    call = onebot
    if call is None and onebot_api:
        from aiji.throat.bridge.onebot import OneBotClient
        call = OneBotClient(onebot_api, token=onebot_token).call
    if call is None:
        # 没有传输层：给一个明确失败的 stub，读写都报 transport_down
        def call(action: str, payload: dict[str, Any] | None = None) -> Any:
            raise PlatformError("transport_down",
                                "没有配置 OneBot 地址（config/runtime.json: onebot_api）",
                                status=503)
    adapters: dict[str, Any] = {
        "qq": QQAdapter(call, guard=make_guard("qq", log_path=log_path),
                        dry_run=dry_run),
        "bilibili": BiliAdapter(bili_cred, cred_loader=bili_cred_loader,
                                guard=make_guard("bilibili", log_path=log_path),
                                dry_run=dry_run),
        "douyin": DouyinAdapter(douyin_session, profile=douyin_profile,
                                headless=douyin_headless,
                                # "背后跑"：抖音 /chat 在 headless 会崩，只能有头；
                                # 设 AIJI_DOUYIN_OFFSCREEN=1 就把窗口挪到屏幕外（人看不见）。
                                offscreen=os.environ.get(
                                    "AIJI_DOUYIN_OFFSCREEN", "") == "1",
                                guard=make_guard("douyin", log_path=log_path),
                                dry_run=dry_run),
    }
    if only:
        adapters = {k: v for k, v in adapters.items() if k in only}
    return adapters


def matrix(adapters: dict[str, Any] | None = None, *,
           live: bool = False) -> dict[str, Any]:
    """能力矩阵。`live=True` 时顺带查一次登录/传输状态（会慢，会联网）。"""
    if adapters is None:
        adapters = build()
    out: dict[str, Any] = {"platforms": {}, "actions": {}}
    for name, adapter in adapters.items():
        desc = adapter.describe()
        entry = {"actions": desc["actions"]}
        if live:
            try:
                if name == "qq":
                    entry["status"] = adapter.probe()
                elif name == "bilibili":
                    entry["status"] = adapter.login_state()
                elif name == "douyin":
                    entry["status"] = adapter.login_state(headless=True)
            except Exception as exc:  # noqa: BLE001 - 状态查不到不影响矩阵
                entry["status"] = {"error": str(exc)[:200]}
        out["platforms"][name] = entry
        for action, info in desc["actions"].items():
            out["actions"].setdefault(action, {})[name] = info["state"]
    return out


def summary_lines(data: dict[str, Any]) -> list[str]:
    """给人看的能力矩阵（CLI 打印）。"""
    actions = ("dm", "group", "comment", "feed", "streak", "group_create", "like")
    names = list((data.get("platforms") or {}).keys())
    width = max([len(a) for a in actions] + [6])
    lines = ["技能".ljust(width) + "  " + "  ".join(n.ljust(12) for n in names)]
    for action in actions:
        cells = []
        for name in names:
            state = ((data["platforms"][name]["actions"] or {}).get(action) or {}).get(
                "state", "-")
            cells.append(state.ljust(12))
        lines.append(action.ljust(width) + "  " + "  ".join(cells))
    return lines
