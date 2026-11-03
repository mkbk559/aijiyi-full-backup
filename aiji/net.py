"""统一出站 HTTP：默认直连，代理只作兜底。

为什么需要这一层（2026-11-03 实测）：
本机全局设了 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7897`（Clash 一类），
但代理经常没开。`urllib.request.urlopen()` 默认 `trust_env` 语义会读这两个变量，
于是"本来完全能通的请求"变成 `WinError 10061 由于目标计算机积极拒绝`：

    直连 jsdelivr/raw.githubusercontent → 200 OK（17 518 字节 manifest）
    走环境代理                          → 10061 refused（代理没启动）

结果就是控制台"更新贴纸"502、QQ 发消息偶发失败、模型网关（127.0.0.1）也受影响。

策略：先用空 ProxyHandler 直连；只有直连失败、且环境里确实配了代理时，
再用环境代理重试一次。两个都不通就把**直连的原始异常**抛出去（错误信息更接近真相）。
想强制走代理：`AIJI_USE_ENV_PROXY=1`。
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from typing import Any

_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_PROXY: urllib.request.OpenerDirector | None = None


def _env_proxy_configured() -> bool:
    return any(os.environ.get(name) for name in
               ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"))


def _proxy_opener() -> urllib.request.OpenerDirector | None:
    global _PROXY
    if not _env_proxy_configured():
        return None
    if _PROXY is None:
        _PROXY = urllib.request.build_opener()
    return _PROXY


def _force_proxy() -> bool:
    return str(os.environ.get("AIJI_USE_ENV_PROXY", "")).strip().lower() in {
        "1", "true", "yes", "on"}


def urlopen(request: Any, *, timeout: float | None = None, **kwargs: Any):
    """同 `urllib.request.urlopen`，但默认不吃环境代理。"""
    if _force_proxy():
        opener = _proxy_opener()
        if opener is not None:
            return opener.open(request, timeout=timeout, **kwargs)
    try:
        return _DIRECT.open(request, timeout=timeout, **kwargs)
    except (urllib.error.URLError, OSError) as exc:
        proxy = _proxy_opener()
        if proxy is None:
            raise
        try:
            return proxy.open(request, timeout=timeout, **kwargs)
        except (urllib.error.URLError, OSError):
            raise exc from None
