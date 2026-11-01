"""Public-URL validation for user-controlled network fetches."""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from typing import Callable


class UrlGuardError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def validate_public_http_url(
    url: str,
    *,
    resolver: Callable[[str], list[str]] | None = None,
    allowed_ports: tuple[int, ...] = (80, 443),
) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UrlGuardError("invalid_url", "http(s) URL required")
    if parsed.username or parsed.password:
        raise UrlGuardError("invalid_url", "URL credentials are not allowed")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if allowed_ports and port not in allowed_ports:
        raise UrlGuardError("port_not_allowed", f"port {port} is not allowed")
    addresses = _resolve(parsed.hostname, resolver)
    if not addresses:
        raise UrlGuardError("dns_failed", "host did not resolve")
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        ):
            raise UrlGuardError("private_url", "private network URL is not allowed")
    return parsed


def _resolve(
    host: str,
    resolver: Callable[[str], list[str]] | None,
) -> list[str]:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return [str(ip)]
    try:
        if resolver is not None:
            return list(resolver(host))
        return sorted({
            str(item[4][0])
            for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        })
    except OSError as exc:
        raise UrlGuardError("dns_failed", str(exc)) from exc


__all__ = ["UrlGuardError", "validate_public_http_url"]
