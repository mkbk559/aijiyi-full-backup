"""Minimal, allowlisted stdio MCP client used by the research loop.

The client starts one MCP process per operation. That is intentionally slower
than a long-lived pool but keeps third-party servers isolated and makes timeout
and shutdown behavior predictable.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_ALLOWED_COMMANDS = frozenset((
    "uvx", "uvx.exe", "npx", "npx.cmd", "node", "node.exe",
    "python", "python.exe",
))
_UNSAFE_SHELL = re.compile(r"[;&|<>`\r\n]")


class MCPError(RuntimeError):
    pass


class MCPConfigError(MCPError):
    pass


class MCPToolDenied(MCPError):
    pass


@dataclass(slots=True)
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    enabled: bool = False
    allowed_tools: frozenset[str] = frozenset()
    timeout_s: float = 60.0
    max_output_chars: int = 24_000
    read_only: bool = True
    note: str = ""


@dataclass(slots=True)
class MCPCallResult:
    server: str
    tool: str
    text: str
    is_error: bool
    took_ms: float


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_error_text(item) for item in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"


class MCPRegistry:
    def __init__(
        self,
        config_path: str | Path,
        *,
        allowed_commands: frozenset[str] = DEFAULT_ALLOWED_COMMANDS,
    ) -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        self.allowed_commands = frozenset(
            item.lower() for item in allowed_commands)
        self._servers: dict[str, MCPServerConfig] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._tool_cache: dict[str, list[dict[str, Any]]] = {}
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.config_path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise MCPConfigError(f"cannot read MCP config: {exc}") from exc
        raw_servers = payload.get("servers") if isinstance(payload, dict) else None
        if not isinstance(raw_servers, dict):
            raise MCPConfigError("MCP config must contain a servers object")
        servers: dict[str, MCPServerConfig] = {}
        for name, raw in raw_servers.items():
            if not isinstance(raw, dict):
                continue
            clean_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(name)).strip("-")
            if not clean_name:
                continue
            command = str(raw.get("command") or "").strip()
            args = raw.get("args") or []
            env = raw.get("env") or {}
            allowed_tools = raw.get("allowed_tools") or []
            enabled = bool(raw.get("enabled", False))
            if not command:
                raise MCPConfigError(f"{clean_name}: command is required")
            if _UNSAFE_SHELL.search(command):
                raise MCPConfigError(f"{clean_name}: unsafe command")
            executable = Path(command).name.lower()
            if enabled and executable not in self.allowed_commands:
                raise MCPConfigError(
                    f"{clean_name}: command is not allowlisted: {executable}")
            if (
                not isinstance(args, list)
                or not all(isinstance(item, str) for item in args)
                or any(_UNSAFE_SHELL.search(item) for item in args)
            ):
                raise MCPConfigError(f"{clean_name}: invalid args")
            if not isinstance(env, dict) or not all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in env.items()
            ):
                raise MCPConfigError(f"{clean_name}: invalid env")
            if not isinstance(allowed_tools, list) or not all(
                isinstance(item, str) and item.strip()
                for item in allowed_tools
            ):
                raise MCPConfigError(
                    f"{clean_name}: allowed_tools must be an explicit list")
            servers[clean_name] = MCPServerConfig(
                name=clean_name,
                command=command,
                args=list(args),
                env={
                    key: os.path.expandvars(value)
                    for key, value in env.items()
                },
                cwd=str(raw.get("cwd") or ""),
                enabled=enabled,
                allowed_tools=frozenset(str(item) for item in allowed_tools),
                timeout_s=max(5.0, min(300.0, float(raw.get("timeout_s", 60)))),
                max_output_chars=max(
                    1000,
                    min(100_000, int(raw.get("max_output_chars", 24_000))),
                ),
                read_only=bool(raw.get("read_only", True)),
                note=str(raw.get("note") or "")[:500],
            )
        self._servers = servers
        self._locks = {
            name: threading.Lock() for name, cfg in servers.items() if cfg.enabled
        }

    def get(self, name: str) -> MCPServerConfig:
        server = self._servers.get(str(name))
        if server is None:
            raise MCPConfigError(f"MCP server not found: {name}")
        if not server.enabled:
            raise MCPConfigError(f"MCP server is disabled: {name}")
        return server

    def snapshot(self) -> dict[str, Any]:
        return {
            "config": str(self.config_path),
            "servers": [
                {
                    "name": cfg.name,
                    "enabled": cfg.enabled,
                    "read_only": cfg.read_only,
                    "tools": sorted(cfg.allowed_tools),
                    "note": cfg.note,
                    "cached_tools": self._tool_cache.get(cfg.name, []),
                }
                for cfg in self._servers.values()
            ],
        }

    def probe_sync(self, name: str, *, timeout_s: float | None = None) -> dict[str, Any]:
        self.get(name)
        tools = self.list_tools_sync(name, timeout_s=timeout_s)
        self._tool_cache[name] = tools
        return {"server": name, "tools": tools}

    def list_tools_sync(
        self, name: str, *, timeout_s: float | None = None,
    ) -> list[dict[str, Any]]:
        return self._run_sync(
            self._list_tools(name, timeout_s=timeout_s))

    def call_sync(
        self,
        name: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> MCPCallResult:
        cfg = self.get(name)
        if tool not in cfg.allowed_tools:
            raise MCPToolDenied(
                f"tool {tool!r} is not allowed on MCP server {name!r}")
        if not cfg.read_only:
            raise MCPToolDenied(
                f"MCP server {name!r} is not marked read-only")
        lock = self._locks[name]
        if not lock.acquire(timeout=5):
            raise MCPError(f"MCP server {name!r} is busy")
        try:
            return self._run_sync(
                self._call_tool(
                    name, tool, arguments or {}, timeout_s=timeout_s))
        finally:
            lock.release()

    def _run_sync(self, coroutine: Any) -> Any:
        try:
            return asyncio.run(coroutine)
        except MCPError:
            raise
        except BaseException as exc:
            raise MCPError(_error_text(exc)) from exc

    @staticmethod
    async def _with_timeout(awaitable: Any, timeout: float) -> Any:
        try:
            return await asyncio.wait_for(awaitable, timeout=max(1.0, timeout))
        except TimeoutError as exc:
            raise MCPError("MCP operation timed out") from exc

    async def _open(self, cfg: MCPServerConfig):
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise MCPError(
                "MCP dependency is missing; install the 'mcp' extra") from exc
        params = StdioServerParameters(
            command=cfg.command,
            args=cfg.args,
            env={**os.environ, **cfg.env} if cfg.env else None,
            cwd=cfg.cwd or None,
        )
        return ClientSession, stdio_client, params

    async def _list_tools(
        self, name: str, *, timeout_s: float | None,
    ) -> list[dict[str, Any]]:
        cfg = self.get(name)
        ClientSession, stdio_client, params = await self._open(cfg)
        timeout = timeout_s or cfg.timeout_s
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await self._with_timeout(session.initialize(), timeout)
            result = await self._with_timeout(session.list_tools(), timeout)
        return [
            {
                "name": tool.name,
                "description": str(tool.description or "")[:1000],
                "input_schema": dict(
                    getattr(tool, "input_schema", None)
                    or getattr(tool, "inputSchema", None)
                    or {}
                ),
            }
            for tool in result.tools
            if tool.name in cfg.allowed_tools
        ]

    async def _call_tool(
        self,
        name: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        timeout_s: float | None,
    ) -> MCPCallResult:
        cfg = self.get(name)
        ClientSession, stdio_client, params = await self._open(cfg)
        timeout = timeout_s or cfg.timeout_s
        started = time.perf_counter()
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await self._with_timeout(session.initialize(), timeout)
            result = await self._with_timeout(
                session.call_tool(tool, arguments), timeout)
        pieces: list[str] = []
        for item in result.content:
            text = getattr(item, "text", None)
            if text is not None:
                pieces.append(str(text))
            else:
                pieces.append(f"[{type(item).__name__}]")
        output = "\n".join(pieces).strip()
        if len(output) > cfg.max_output_chars:
            output = output[:cfg.max_output_chars] + "\n...[truncated]"
        return MCPCallResult(
            server=name,
            tool=tool,
            text=output,
            is_error=bool(getattr(result, "is_error", False)),
            took_ms=round((time.perf_counter() - started) * 1000, 1),
        )


__all__ = [
    "MCPCallResult",
    "MCPConfigError",
    "MCPError",
    "MCPRegistry",
    "MCPServerConfig",
    "MCPToolDenied",
]
