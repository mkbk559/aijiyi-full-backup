"""主动配额 = **反向代理网关（反代）那边剩下的积分**，不是本地拍一个每日次数。

为什么改成这样（2026-11-02，用户口径）：主动搭话和正常聊天花的是同一池积分，
本地写死"每天 3 次"会出现两种坏情况：积分快没了还在主动说话（把额度烧光，用户回话时反而没得用），
积分充了一大笔却还是只敢说 3 次（明明可以多关心几句）。

所以配额是一个**推导值**：

    allowed_24h = min( max_cap, floor(remaining * (1 - reserve) / cost_per_msg) )

- `remaining`：反代剩余积分（四种来源，优先级从高到低）：
  ① 显式配置的 URL（任意网关，返回 JSON 里的某个数字）；
  ② 正常调用时网关响应头里的积分/配额字段（`x-credits-remaining` 等）；
  ③ `data/credits.json`（`aiji quota --set N` 手填，带时间戳知道你填了多久）；
  ④ 静态配置（`AIJI_CREDITS` 环境变量或 settings）。
- `cost_per_msg`：单条消息花多少积分。能读到网关 `/v1/models` 里每个模型的 `credits`
  字段就用它（本机 agent2api 实测 `0.08`，格式是 `"x0.5 credits"` 这种），读不到按 1.0 算。
- `reserve`：给"用户回话"留的余量（默认 20%），主动搭话不许把积分吃到见底。
- `max_cap`：防刷屏的硬顶（`params.PROACTIVE_QUOTA_MAX`=8/24h），积分再多也不许更频繁。
  积分**取不到**时才用 `daily_cap`（`params.PROACTIVE_QUOTA_24H`=3），即"今天为止的老行为"。

**任何一步取不到数字，就退回 `daily_cap`** —— 宁可按老规矩少说话，也不能因为取不到积分而放飞。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from . import params

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def parse_credits(value: Any) -> float | None:
    """把各种写法的积分值解析成 float。

    实测本机两种格式：agent2api `"x0.5 credits"`、workbuddy 模型表里的 `0.5`。
    取不到数就返回 None（调用方据此回退，而不是当成 0）。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _NUM_RE.search(str(value).replace(",", ""))
    return float(m.group(0)) if m else None


@dataclass(slots=True)
class QuotaState:
    """一次配额判定的完整依据（进 `/api` 与 `aiji quota`，便于解释"为什么现在不能主动"）。"""
    remaining: float | None = None
    cost: float | None = None
    reserve: float = params.PROACTIVE_RESERVE_RATIO
    daily_cap: int = params.PROACTIVE_QUOTA_24H
    max_cap: int = params.PROACTIVE_QUOTA_MAX
    allowed_24h: int = params.PROACTIVE_QUOTA_24H
    source: str = "default"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "remaining": self.remaining,
            "cost": self.cost,
            "reserve": self.reserve,
            "daily_cap": self.daily_cap,
            "max_cap": self.max_cap,
            "allowed_24h": self.allowed_24h,
            "source": self.source,
            "note": self.note,
        }


class CreditProvider:
    """反代积分读取器。所有网络失败都被吞掉并回退，绝不阻塞主动搭话链路。"""

    def __init__(
        self,
        *,
        url: str = "",
        json_path: str = "",
        static: float | None = None,
        models_url: str = "",
        model: str = "",
        file_path: str = "",
        reserve: float = params.PROACTIVE_RESERVE_RATIO,
        cache_s: float = params.CREDITS_CACHE_S,
        timeout_s: float = 2.0,
        opener: Callable[..., Any] | None = None,
    ):
        self.url = url
        self.json_path = json_path
        self.static = static
        self.models_url = models_url
        self.model = model
        self.file_path = file_path
        self.reserve = max(0.0, min(0.95, float(reserve)))
        self.cache_s = max(0.0, float(cache_s))
        self.timeout_s = float(timeout_s)
        self._opener = opener or urllib.request.urlopen
        self._remaining_cache: tuple[float, float | None, str] | None = None
        self._cost_cache: tuple[float, float | None, str] | None = None
        self.header_hint: float | None = None  # 由 ModelPool 正常调用时顺手记录
        self.header_hint_at: float = 0.0

    # ---- 取值 ----

    def _get_json(self, url: str) -> Any:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with self._opener(req, timeout=self.timeout_s) as resp:
            raw = resp.read(65536)
        return json.loads(raw.decode("utf-8", "replace"))

    def _read_file(self, now: float) -> tuple[float | None, str]:
        """读 `data/credits.json`：`{"credits": 12.5, "at": 1793558445}`（由 `aiji quota --set` 写）。

        实测结论（2026-11-02）：本机两个反代都不吐余额响应头，workbuddy 的 dashboard 是纯 SPA，
        静态抓不到数字。所以给一个"手填一次、之后按单价折算"的落地通道，比假装能自动读更诚实。
        """
        try:
            with open(self.file_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None, "file(missing)"
        value = parse_credits(data.get("credits") if isinstance(data, dict) else data)
        if value is None:
            return None, "file(miss)"
        at = data.get("at") if isinstance(data, dict) else None
        age = f"{max(0.0, now - float(at)) / 3600.0:.1f}h" if at else "?"
        return value, f"file({age})"

    @staticmethod
    def _dig(obj: Any, path: str) -> Any:
        cur = obj
        for part in [p for p in (path or "").split(".") if p]:
            if isinstance(cur, list):
                try:
                    cur = cur[int(part)]
                    continue
                except (ValueError, IndexError):
                    return None
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    def remaining(self, *, now: float | None = None) -> tuple[float | None, str]:
        """反代剩余积分 + 来源标签。取不到返回 (None, 'unknown')。"""
        t = time.time() if now is None else now
        if self._remaining_cache and t - self._remaining_cache[0] < self.cache_s:
            return self._remaining_cache[1], self._remaining_cache[2]
        value: float | None = None
        source = "unknown"
        if self.url:
            try:
                data = self._get_json(self.url)
                value = parse_credits(self._dig(data, self.json_path) if self.json_path else data)
                source = "url" if value is not None else "url(miss)"
            except Exception:  # noqa: BLE001 - 网关挂了不能拖垮主动链路
                source = "url(error)"
        if value is None and self.header_hint is not None:
            value, source = self.header_hint, "header"
        if value is None and self.file_path:
            value, source = self._read_file(t)
        if value is None and self.static is not None:
            value, source = float(self.static), "static"
        if value is None:
            env = parse_credits(os.environ.get("AIJI_CREDITS"))
            if env is not None:
                value, source = env, "env"
        self._remaining_cache = (t, value, source)
        return value, source

    def cost_per_message(self, *, now: float | None = None) -> tuple[float | None, str]:
        """单条消息消耗多少积分（网关模型表里的 `credits`）。"""
        t = time.time() if now is None else now
        if self._cost_cache and t - self._cost_cache[0] < self.cache_s:
            return self._cost_cache[1], self._cost_cache[2]
        value: float | None = None
        source = "unknown"
        if self.models_url:
            try:
                data = self._get_json(self.models_url)
                items = data.get("data") if isinstance(data, dict) else data
                for item in items or []:
                    if not isinstance(item, dict):
                        continue
                    if self.model and item.get("id") != self.model:
                        continue
                    value = parse_credits(item.get("credits"))
                    if value is not None:
                        source = "models"
                        break
            except Exception:  # noqa: BLE001
                source = "models(error)"
        if value is None:
            value, source = params.CREDITS_PER_PROACTIVE_DEFAULT, "default"
        self._cost_cache = (t, value, source)
        return value, source

    def note_headers(self, headers: Any, *, now: float | None = None) -> None:
        """从正常调用的响应头里顺手抄一个积分余额（网关自己吐的话最省事）。"""
        if headers is None:
            return
        t = time.time() if now is None else now
        for key in ("x-credits-remaining", "x-quota-remaining", "x-credits",
                    "x-ratelimit-remaining"):
            try:
                raw = headers.get(key)
            except Exception:  # noqa: BLE001 - headers 可能是 dict / email.message
                raw = None
            value = parse_credits(raw)
            if value is not None:
                self.header_hint = value
                self.header_hint_at = t
                self._remaining_cache = (t, value, "header")
                return

    # ---- 判定 ----

    def state(self, *, daily_cap: int | None = None, max_cap: int | None = None,
              now: float | None = None) -> QuotaState:
        """算"今天还能主动几次"。

        积分已知 → `floor(积分*(1-reserve)/单价)`，再用 `max_cap`（防刷屏）封顶；
        积分未知 → 退回 `daily_cap`（老行为），**不会因为取不到数就放飞**。
        """
        cap = params.PROACTIVE_QUOTA_24H if daily_cap is None else int(daily_cap)
        ceiling = params.PROACTIVE_QUOTA_MAX if max_cap is None else int(max_cap)
        remaining, rsrc = self.remaining(now=now)
        cost, csrc = self.cost_per_message(now=now)
        st = QuotaState(remaining=remaining, cost=cost, reserve=self.reserve,
                        daily_cap=cap, max_cap=ceiling, allowed_24h=cap,
                        source=f"credits:{rsrc}/cost:{csrc}")
        if remaining is None or cost is None or cost <= 0:
            st.note = "取不到积分或单价 → 按每日硬上限走（保守）"
            return st
        usable = max(0.0, remaining * (1.0 - self.reserve))
        allowed = int(usable // cost)
        st.allowed_24h = max(0, min(ceiling, allowed))
        st.note = (f"可用积分 {usable:.2f} ÷ 单条 {cost:.2f} = {allowed}，"
                   f"再按防刷屏上限 {ceiling} 截断")
        return st


# ---- 24h 滚动窗口（与 proactive.py 共用同一份 heart_state 键）----

WINDOW_KEY = "recent_send_ts"


def sends_last_24h(heart: Any, ident: Any, now: float) -> int:
    ts = heart.state_get(ident, WINDOW_KEY) or []
    return len([x for x in ts if now - float(x) < 86400.0])


def record_send(heart: Any, ident: Any, now: float) -> None:
    """记一次主动发送（主动配额预扣）。保留最近 8 条即可（上限也就个位数）。"""
    ts = [float(x) for x in (heart.state_get(ident, WINDOW_KEY) or [])]
    ts = [x for x in ts if now - x < 86400.0]
    ts.append(now)
    heart.state_set(ident, WINDOW_KEY, ts[-8:])


__all__ = [
    "CreditProvider", "QuotaState", "parse_credits", "record_send", "sends_last_24h",
    "WINDOW_KEY",
]
