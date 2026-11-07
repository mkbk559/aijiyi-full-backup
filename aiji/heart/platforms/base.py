"""平台适配层的公共件：能力、结果、闸门、留痕。

设计口径（2026-11-05）：
- **一个动作要么真做到，要么显式报错**。做不到的能力返回 `unsupported`，
  缺登录态返回 `needs_login`，绝不静默成功（这类"看起来发了"的假成功是事故源）。
- **写动作默认 dry-run**：只有调用方显式 `--yes` / `dry_run=False` 才真发；
  每一次尝试（含被拒）都进 jsonl 留痕，事后可查。
- **限速与配额在这里统一**，不散落在各平台：抖音/B站的风控是账号风险，不是礼貌问题。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# 跨平台统一动作名。各适配器只声明自己真支持的子集。
CAPABILITIES = (
    "dm",            # 私聊 / 单聊
    "group",         # 群聊发言
    "comment",       # 发评论（视频/动态）
    "feed",          # 刷内容（推荐流/排行榜/搜索）
    "streak",        # 连续互动（QQ 火花那一类）
    "group_create",  # 自建社交群
    "like",          # 点赞
)

# 能力状态
OK = "ok"
NEEDS_LOGIN = "needs_login"
UNSUPPORTED = "unsupported"
EXPERIMENTAL = "experimental"   # 代码在，但真机没验过选择器/接口
OFFLINE = "offline"             # 传输没通（OneBot HTTP 没起、浏览器起不来）


class PlatformError(RuntimeError):
    """平台层失败。`code` 是机器判据，别只给人看。"""

    def __init__(self, code: str, message: str = "", *, status: int = 400,
                 hint: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.status = status
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        out = {"ok": False, "code": self.code, "error": self.message}
        if self.hint:
            out["hint"] = self.hint
        return out


@dataclass
class Result:
    """一次平台动作的结果。`dry_run=True` 表示只是预演，什么都没发出去。"""

    ok: bool
    platform: str
    action: str
    detail: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False
    code: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = {
            "ok": self.ok,
            "platform": self.platform,
            "action": self.action,
            "dry_run": self.dry_run,
        }
        if self.code:
            out["code"] = self.code
        out.update(self.detail)
        return out


def _now() -> float:
    return time.time()


class Guard:
    """写动作的闸门：dry-run 策略 + 最小间隔 + 每日上限 + jsonl 留痕。

    计数直接从留痕文件里读（只解析尾部 `scan` 行），所以**单次 CLI 调用之间也生效**，
    不需要额外的状态文件，也不会出现"文件里的账和内存里的账对不上"。
    """

    def __init__(
        self,
        *,
        log_path: str | Path | None = None,
        min_interval: float = 60.0,
        daily_cap: int | None = None,
        clock: Callable[[], float] = _now,
        scan: int = 400,
    ) -> None:
        self.log_path = Path(log_path) if log_path else None
        self.min_interval = float(min_interval)
        self.daily_cap = daily_cap
        self.clock = clock
        self._last: dict[str, float] = {}
        self._last_action: dict[str, float] = {}
        self._today: dict[str, int] = {}
        self._day = ""
        if self.log_path and self.log_path.exists():
            self._load(scan)

    # ---- 留痕 -------------------------------------------------------------
    def _load(self, scan: int) -> None:
        try:
            with self.log_path.open("r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()[-scan:]
        except OSError:
            return
        for line in lines:
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(row, dict):
                continue
            key = f"{row.get('platform')}:{row.get('action')}:{row.get('key', '')}"
            ts = row.get("ts")
            if isinstance(ts, (int, float)):
                self._last[key] = max(self._last.get(key, 0.0), float(ts))
                cap_key = f"{row.get('platform')}:{row.get('action')}"
                self._last_action[cap_key] = max(
                    self._last_action.get(cap_key, 0.0), float(ts))
            if row.get("day"):
                cap_key = f"{row.get('platform')}:{row.get('action')}"
                self._today[cap_key] = self._today.get(cap_key, 0) + 1

    def log(self, entry: dict[str, Any]) -> dict[str, Any]:
        ts = float(entry.get("ts") or self.clock())
        day = entry.get("day") or datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        row = dict(entry)
        row["ts"] = ts
        row["day"] = day
        row.setdefault("iso", datetime.fromtimestamp(ts).isoformat(timespec="seconds"))
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        key = f"{row.get('platform')}:{row.get('action')}:{row.get('key', '')}"
        self._last[key] = ts
        cap_key = f"{row.get('platform')}:{row.get('action')}"
        self._last_action[cap_key] = ts
        self._day = day
        self._today[cap_key] = self._today.get(cap_key, 0) + 1
        return row

    # ---- 判定 -------------------------------------------------------------
    def check(self, platform: str, action: str, key: str = "") -> None:
        """真发之前必须过这一关。超限抛 `rate_limited` / `daily_cap`。"""
        now = self.clock()
        day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        cap_key = f"{platform}:{action}"
        if self.daily_cap is not None and self._used_today(cap_key, day) >= self.daily_cap:
            raise PlatformError(
                "daily_cap",
                f"{platform}.{action} 今天已经 {self._used_today(cap_key, day)} 次，"
                f"上限 {self.daily_cap}",
                status=429,
                hint="改 data/platform_actions.jsonl 之外的配置或等到明天",
            )
        last = self._last.get(f"{platform}:{action}:{key}", 0.0)
        if not key:
            # 没给具体对象时退回"这个动作最后一次是什么时候"（CLI 常见）
            last = max(last, self._last_action.get(cap_key, 0.0))
        if last and now - last < self.min_interval:
            raise PlatformError(
                "rate_limited",
                f"{platform}.{action} 距上次只有 {now - last:.0f}s，"
                f"最小间隔 {self.min_interval:.0f}s",
                status=429,
            )

    def _used_today(self, cap_key: str, day: str) -> int:
        if day != self._day:
            # 换天了：内存计数器清零（留痕文件里旧天的行不再参与）
            self._today = {k: 0 for k in self._today}
            self._day = day
        return self._today.get(cap_key, 0)


class Adapter:
    """平台适配器基类。子类只实现自己支持的动作。"""

    name = "base"

    def capabilities(self) -> dict[str, str]:
        """动作 → 状态（OK / NEEDS_LOGIN / UNSUPPORTED / EXPERIMENTAL）。"""
        return {action: UNSUPPORTED for action in CAPABILITIES}

    def supports(self, action: str) -> bool:
        return self.capabilities().get(action, UNSUPPORTED) == OK

    def require(self, action: str) -> str:
        state = self.capabilities().get(action, UNSUPPORTED)
        # EXPERIMENTAL 是"能跑，但没真机验证过"——允许执行，把边界写进 notes
        if state not in (OK, EXPERIMENTAL):
            raise PlatformError(
                "unsupported" if state == UNSUPPORTED else state,
                f"{self.name}.{action} 当前不可用（{state}）",
                hint=self.notes().get(action, ""),
            )
        return state

    def notes(self) -> dict[str, str]:
        """每个能力的边界说明（如实：哪部分是没验过的）。"""
        return {}

    def describe(self) -> dict[str, Any]:
        caps = self.capabilities()
        notes = self.notes()
        return {
            "platform": self.name,
            "actions": {
                action: {"state": state, "note": notes.get(action, "")}
                for action, state in caps.items()
            },
        }
