"""发送失败分类（2026-11-03）：把"一次失败"分成"重试有用 / 稍后再说 / 再试也无用"。

为什么单独一层：`Heart.dispatch` 只会把失败统一按指数退避重试到 `MAX_ATTEMPTS`。
token 失效（1401/1403）、参数非法（100/104/1400）、目标不存在（1404）这类**确定性失败**
重试 6 次纯属浪费，还会把队列里的真消息堵在后面——它们应该直接进死信。

判据来源：
- OneBot 11 HTTP API 的错误码约定（`retcode`：0 成功、1xx 参数/权限、1400 参数校验失败、
  1401 token 无效、1403 token 无权限、1404 资源不存在；负数为实现内部错误，通常可重试）
- HTTP 语义（401/403 权限、404 资源、429 限流、5xx 与网络错误可重试）

**未在真机逐个验证**：本机没有可用 QQ 账号，这些码值来自规范与常见实现（NapCat / go-cqhttp）。
真机跑起来后应拿 `send_queue.status='dead'` 的行对照本表校准。
"""

from __future__ import annotations

from typing import Any

from aiji.heart.heart import SendFatal, SendThrottled
from aiji.right_hand import params
from aiji.throat.bridge.onebot import SendOutcome

OK = "ok"
RETRY = "retry"
THROTTLE = "throttle"
FATAL = "fatal"

# 确定性失败：重试一万次也一样
FATAL_RETCODES = frozenset({100, 102, 103, 104, 1400, 1401, 1403, 1404})
# 明确"过会儿再来"的：平台侧频控
THROTTLE_RETCODES = frozenset({1200})


def classify(outcome: SendOutcome) -> str:
    """把一个发送结果归成 OK / RETRY / THROTTLE / FATAL 之一。"""
    if outcome.ok:
        return OK
    status = outcome.http_status
    if status == 429:
        return THROTTLE
    if status in (400, 401, 403, 404):
        return FATAL
    if status is not None and status >= 500:
        return RETRY
    if status is None:
        # 连接被拒/超时/DNS：网络层面的问题，重试是有意义的
        return RETRY
    code = outcome.retcode
    if code is None:
        return RETRY
    if code in FATAL_RETCODES:
        return FATAL
    if code in THROTTLE_RETCODES:
        return THROTTLE
    return RETRY


def describe(outcome: SendOutcome) -> str:
    parts = []
    if outcome.retcode is not None:
        parts.append(f"retcode={outcome.retcode}")
    if outcome.http_status is not None:
        parts.append(f"http={outcome.http_status}")
    if outcome.message:
        parts.append(outcome.message)
    return " ".join(parts) or "unknown"


def raise_for(outcome: SendOutcome, *, now: float | None = None) -> bool:
    """按分类抛出对应异常，返回是否送达。

    - 成功 → True
    - THROTTLE → `SendThrottled(now + PLATFORM_BLOCK_DELAY)`
    - FATAL → `SendFatal(describe, retcode)`
    - RETRY → False（交给 Heart 的指数退避）
    """
    import time as _time

    verdict = classify(outcome)
    if verdict == OK:
        return True
    if verdict == THROTTLE:
        t = _time.time() if now is None else float(now)
        raise SendThrottled(t + params.PLATFORM_BLOCK_DELAY)
    if verdict == FATAL:
        raise SendFatal(describe(outcome), retcode=outcome.retcode)
    return False


def wrap_post(post: Any) -> Any:
    """工具：把旧的 `post(url, payload, headers) -> bool` 适配成返回 SendOutcome 的形态。"""
    def _post(url: str, payload: dict, headers: dict) -> SendOutcome:
        ok = bool(post(url, payload, headers))
        return SendOutcome(ok=ok, retcode=0 if ok else -1, http_status=200 if ok else None)
    return _post
