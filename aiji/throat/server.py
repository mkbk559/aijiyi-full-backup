"""本地 HTTP 服务：任意 agent / 任意平台用 JSON 接。

    身份一律三个字段：platform / platform_user_id / scope（scope 缺省 default）。
    媒体只传引用：{"kind":"image","ref":"msg-7781","url":"https://…"}，不要传文件字节。

    POST /turns    {"platform":"wechat","platform_user_id":"u1","session":"s1","role":"user","content":"…"}
    POST /remember {"platform":"wechat","platform_user_id":"u1","text":"…"}
    POST /ingest   {"platform":"wechat","platform_user_id":"u1","kind":"image","ref":"msg-1","url":"…"}
    POST /media    {"platform":"wechat","platform_user_id":"u1","kind":"image","ref":"msg-1","url":"…"}
    POST /recall   {"platform":"wechat","platform_user_id":"u1","query":"我住哪"}
    POST /inject   {"platform":"wechat","platform_user_id":"u1","query":"我住哪"}  -> 软引导文本块
    POST /feedback {"platform":"wechat","platform_user_id":"u1","injection_id":3,"used_ids":[12]}
    GET  /media?platform=&platform_user_id=&session=   -> 引用 + 识别结果
    GET  /style?platform=&platform_user_id=
    GET  /health | /stats
    GET  /                 -> 零依赖运行与功能页面
    GET  /v1/models        -> OpenAI 兼容模型发现
    POST /v1/chat/completions -> OpenAI 兼容对话（记忆注入/闸门/SSE）
    POST /flush    {"timeout":10}
"""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from aiji import __version__
from aiji.right_hand import admin
from aiji.right_hand import operations, service_control
from aiji.brain import persona as persona_mod
from aiji.brain.head import Head
from aiji.hippocampus.memory import Memory
from aiji.brain.pool import PoolExhausted
from aiji.hippocampus.store import Ident
from aiji.skeleton.paths import ROOT, WEB_DIR

logger = logging.getLogger(__name__)

_QUERY = re.compile(r"[?&]([^=]+)=([^&]*)")
_PROTECTED_EXACT = frozenset((
    "/models", "/chat/completions", "/turns", "/remember", "/media", "/ingest",
    "/recall", "/inject", "/feedback", "/flush", "/style", "/accounts", "/stats",
    "/being", "/being/note", "/being/event",
))
# CORS allow-list for the operations page opened straight from disk (origin null)
# or served by this process. Anything else is treated as a foreign origin.
_LOCAL_ORIGINS = frozenset((
    "null", "http://127.0.0.1:8787", "http://localhost:8787",
))


_IDENT_GROUP = re.compile(
    r"^(?:(?P<platform>[A-Za-z0-9_-]+)[_:/])?"
    r"group[_:/](?P<group>[^_:/]+)[_:/]user[_:/](?P<user>.+)$",
    re.IGNORECASE,
)
_IDENT_PLATFORM_USER = re.compile(
    r"^(?P<platform>[A-Za-z0-9_-]+)[_:/](?P<user>[^_:/]+)$",
    re.IGNORECASE,
)
# qq_user_12345 / telegram_user_777：平台 + 用户，不能被通用正则拆成 qq_user/12345。
_IDENT_PLATFORM_USER_PREFIX = re.compile(
    r"^(?P<platform>[A-Za-z0-9_-]+)[_:/]user[_:/](?P<user>.+)$",
    re.IGNORECASE,
)


def parse_identity_hint(raw: str) -> dict[str, str]:
    """把前端塞进 OpenAI `user` 字段的“身份串”解析成 platform/user/scope。

    支持（大小写不敏感）：
      qq_group_67890_user_12345
      qq:group:67890:user:12345
      group_67890_user_12345      （无 platform → 默认 qq）
      qq_12345 / qq:12345
      12345                       （裸 id → platform 缺省，由调用方补）
    """
    text = str(raw or "").strip()
    if not text:
        return {}
    match = _IDENT_GROUP.match(text)
    if match:
        return {
            "platform": (match.group("platform") or "qq").lower(),
            "platform_user_id": match.group("user"),
            "scope": f"group:{match.group('group')}",
        }
    match = _IDENT_PLATFORM_USER_PREFIX.match(text)
    if match:
        return {
            "platform": match.group("platform").lower(),
            "platform_user_id": match.group("user"),
        }
    match = _IDENT_PLATFORM_USER.match(text)
    if match:
        return {
            "platform": match.group("platform").lower(),
            "platform_user_id": match.group("user"),
        }
    return {"platform_user_id": text}


def _workspace_enabled() -> bool:
    """控制台工作区/终端默认关闭（安全 + 与 IDE 重复）；显式设 1 才开。"""
    return os.environ.get("AIJI_ENABLE_WORKSPACE", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _sampling_params(body: dict[str, Any]) -> dict[str, Any]:
    """OpenAI 兼容采样参数：只收合法数值并夹到安全区间，非法值忽略。"""
    out: dict[str, Any] = {}
    for key, lo, hi in (("temperature", 0.0, 2.0), ("top_p", 0.0, 1.0)):
        value = body.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = round(max(lo, min(hi, float(value))), 4)
    value = body.get("max_tokens")
    if isinstance(value, int) and not isinstance(value, bool):
        out["max_tokens"] = max(1, min(8192, value))
    return out


def _query_int(
    qs: dict[str, str],
    key: str,
    default: int,
    *,
    low: int | None = None,
    high: int | None = None,
) -> int:
    try:
        value = int(qs.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _decode_console(data: bytes) -> str:
    """控制台命令输出解码：优先 UTF-8，中文 Windows 的 cmd 输出回退 GB18030。"""
    if not data:
        return ""
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


class _PayloadTooLarge(Exception):
    """请求体超过 MAX_BODY_BYTES；在 do_POST 里转成 413。"""

    def __init__(self, size: int) -> None:
        self.size = size
        super().__init__(f"payload too large: {size} bytes")


def _clip_text(value: Any, limit: int = 32_768) -> tuple[str, dict[str, Any]]:
    """截断超长文本并如实回报（不静默丢内容）。

    两端空白一律去掉：纯空白内容等于"没说话"。以前 `"   "` 是真值，
    一路通过 `if not content` 检查，于是**一条空白消息会占掉一格对话**，
    还会进反思批次（`original_chars` 仍报原始长度，截断信息不丢）。
    """
    raw = str(value or "")
    text = raw.strip()
    if len(text) <= limit:
        return text, {}
    return text[:limit], {"truncated": True, "original_chars": len(raw)}


class _Handler(BaseHTTPRequestHandler):
    server_version = f"aiji/{__version__}"
    protocol_version = "HTTP/1.1"
    memory: Memory  # 由 make_server() 注入
    voice: Any
    songs: Any
    social: Any
    stickers: Any
    sticker_loop: Any = None

    # ---- 工具 ----

    MAX_BODY_BYTES = 8 * 1024 * 1024
    MAX_TEXT_CHARS = 32_768

    def _body(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        self._body_consumed = True
        if n > self.MAX_BODY_BYTES:
            # 超过 8 MB 的请求体没有合法用途（实测 1.4 MB 的单条文本都能被 /ingest
            # 接进队列，会把库和注入预算一起撑坏）。但要**把 body 读空再回 413**：
            # 直接甩手会让客户端还在写 9 MB 时收到 RST（WinError 10053），
            # 连错误码都拿不到。
            self._drain(n)
            raise _PayloadTooLarge(n)
        try:
            data = json.loads(self.rfile.read(n))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _finish_body(self) -> None:
        """回应前把没读过的请求体读空。

        Windows 上"服务端先关、客户端还在写"会给出
        `ConnectionAbortedError: [WinError 10053]`，客户端连状态码都拿不到
        （401/404 这种早退路径最容易踩）。413 分支早就这么做了，
        这里把它变成所有响应的统一收尾。
        """
        if getattr(self, "_body_consumed", False):
            return
        self._body_consumed = True
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            self._drain(n)

    def _drain(self, n: int, chunk: int = 65_536) -> None:
        """把多余请求体读掉丢掉（最多 64 MB），避免客户端收到 RST。"""
        remaining = min(int(n), 64 * 1024 * 1024)
        while remaining > 0:
            block = self.rfile.read(min(chunk, remaining))
            if not block:
                break
            remaining -= len(block)
        self.close_connection = True

    def _ident(self, body: dict[str, Any]) -> Ident:
        meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        aiji = body.get("aiji") if isinstance(body.get("aiji"), dict) else {}
        hint = parse_identity_hint(str(body.get("user") or ""))
        platform = (
            self.headers.get("X-Aiji-Platform")
            or body.get("platform") or meta.get("platform") or aiji.get("platform")
            or hint.get("platform") or "local"
        )
        user = (
            self.headers.get("X-Aiji-User")
            or body.get("platform_user_id") or meta.get("platform_user_id")
            or meta.get("user") or aiji.get("platform_user_id")
            or hint.get("platform_user_id") or "default"
        )
        scope = (
            self.headers.get("X-Aiji-Scope")
            or body.get("scope") or meta.get("scope") or aiji.get("scope")
            or hint.get("scope") or "default"
        )
        return Ident(platform, user, scope)

    # ---- 工作区 ----

    def _fs_root(self) -> Path:
        root = self.memory.action_executor.root
        return Path(root).resolve() if root else Path.cwd().resolve()

    def _fs_target(self, rel: str) -> Path:
        """把控制台传来的相对路径限制在工作区根目录内（防 ../ 越权）。"""
        root = self._fs_root()
        clean = str(rel or "").replace("\\", "/").strip("/")
        target = (root / clean).resolve() if clean else root
        if target != root and root not in target.parents:
            raise PermissionError("path_outside_root")
        return target

    def _fs_rel(self, target: Path) -> str:
        try:
            rel = target.relative_to(self._fs_root())
        except ValueError:
            return ""
        return "" if str(rel) == "." else rel.as_posix()

    def _authorized(self, path: str) -> bool:
        protected = path.startswith(("/v1/", "/api/")) or path in _PROTECTED_EXACT
        if not protected:
            return True
        expected = os.environ.get("AIJI_API_KEY") or getattr(
            self.memory.settings, "api_key", "")
        if not expected:
            return True
        auth = self.headers.get("Authorization") or ""
        token = auth[7:].strip() if auth.startswith("Bearer ") else (
            self.headers.get("X-Aiji-Api-Key") or "")
        return bool(token) and hmac.compare_digest(str(expected), str(token))

    def _unauthorized(self) -> None:
        # 被拒绝的请求体可能还没读：先读空再关连接，客户端才能稳定拿到 401
        # （直接关会变成 RST / WinError 10053）。
        self._finish_body()
        self.close_connection = True
        self.send_response(401)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("WWW-Authenticate", "Bearer")
        self.send_header("Connection", "close")
        data = json.dumps({
            "error": {"message": "invalid api key", "type": "authentication_error",
                      "code": "invalid_api_key"},
        }, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self) -> None:
        # The operations page also runs from file:// (origin "null"), which makes
        # every call cross-origin. Only the local page and local files are allowed.
        origin = self.headers.get("Origin") if self.headers else None
        if origin and str(origin) in _LOCAL_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", str(origin))
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                "authorization,content-type,x-aiji-api-key,x-aiji-platform,"
                "x-aiji-user,x-aiji-scope,x-aiji-session",
            )
            self.send_header("Access-Control-Max-Age", "600")
        super().end_headers()

    def do_OPTIONS(self) -> None:  # noqa: N802
        origin = str(self.headers.get("Origin") or "")
        self.send_response(204 if origin in _LOCAL_ORIGINS else 403)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        self._finish_body()
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, html: str) -> None:
        self._finish_body()
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, data: bytes, content_type: str) -> None:
        """按字节返回（音频 / 工作区原文件）。

        一律 `no-store`：这些都是本地随时会变的东西（重新合成的语音、
        工作区里刚改过的文件），让浏览器缓存住只会让人看到旧内容。
        以前还有一个 `cache=` 参数和一个 `_send_asset` 帮手，两个都没有调用方
        ——2026-11-04 删掉（死代码，见 excluded #161）。
        """
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _audio_error(exc: Exception) -> tuple[int, dict[str, Any]]:
        code = str(getattr(exc, "code", "") or "audio_error")
        message = str(getattr(exc, "message", "") or exc)
        status = 404 if code in (
            "profile_not_found", "job_not_found", "output_missing",
        ) else 503 if code in (
            "not_configured", "provider_unavailable", "ffmpeg_missing",
            "tool_missing", "voice_converter_not_configured",
        ) else 400
        return status, {"error": {"code": code, "message": message}}

    @staticmethod
    def _social_error(exc: Exception) -> tuple[int, dict[str, Any]]:
        code = str(getattr(exc, "code", "") or "social_error")
        message = str(getattr(exc, "message", "") or exc)
        status = int(getattr(exc, "status", 400) or 400)
        return status, {"error": {"code": code, "message": message}}

    @staticmethod
    def _sticker_error(exc: Exception) -> tuple[int, dict[str, Any]]:
        code = str(getattr(exc, "code", "") or "sticker_error")
        message = str(getattr(exc, "message", "") or exc)
        status = int(getattr(exc, "status", 400) or 400)
        return status, {"error": {"code": code, "message": message}}

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    # ---- 路由 ----

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path, qs = parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if not self._authorized(path):
            return self._unauthorized()
        for key in ("limit", "turns", "token_budget"):
            if key not in qs:
                continue
            try:
                int(qs[key])
            except (TypeError, ValueError):
                return self._send(400, {"error": f"{key} must be an integer"})
        if path in ("/", "/operations", "/operations.html"):
            html = (WEB_DIR / "operations.html").read_text("utf-8")
            self._send_html(html)
        elif path == "/health":
            s = self.memory.stats()
            life_loop = getattr(self.memory, "life_loop", None)
            mcp_loop = getattr(self.memory, "mcp_research_loop", None)
            mcp_status = None
            if mcp_loop is not None:
                raw_mcp = mcp_loop.snapshot()
                latest = (raw_mcp.get("latest") or {}).get("latest") or {}
                mcp_status = {
                    "running": raw_mcp.get("running"),
                    "working": raw_mcp.get("working"),
                    "runs": raw_mcp.get("runs"),
                    "errors": raw_mcp.get("errors"),
                    "last_run_at": raw_mcp.get("last_run_at"),
                    "last_error": raw_mcp.get("last_error"),
                    "interval_s": raw_mcp.get("interval_s"),
                    "initial_delay_s": raw_mcp.get("initial_delay_s"),
                    "latest": {
                        key: latest.get(key)
                        for key in (
                            "created_at", "discovered", "fetched_readmes",
                            "eligible", "summary",
                        )
                    } if latest else None,
                }
            self._send(200, {
                "ok": True,
                "version": __version__,
                "queue_depth": s["writer"]["queue_depth"],
                "pending": s["writer"]["pending"],
                "facts": s["store"]["facts"],
                "platforms": s["store"]["platforms"],
                "pool": [{"name": p["name"], "cooldown_s": p["cooldown_s"]} for p in s["pool"]],
                "budget_ms": s["budget_ms"],
                "life_loop": life_loop.snapshot() if life_loop else None,
                "sticker_update": (
                    self.sticker_loop.snapshot()
                    if self.sticker_loop is not None else None
                ),
                "mcp_research": mcp_status,
            })
        elif path in ("/v1/models", "/models"):
            self._send(200, {
                "object": "list",
                "data": [{
                    "id": "aiji",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "aiji",
                }],
            })
        elif path == "/stats":
            self._send(200, self.memory.stats())
        elif path == "/v1/voices":
            self._send(200, self.voice.snapshot())
        elif path == "/v1/songs":
            self._send(200, self.songs.snapshot())
        elif path == "/api/social":
            self._send(200, self.social.snapshot())
        elif path == "/api/stickers":
            payload = {"service": self.stickers.snapshot()}
            if self.sticker_loop is not None:
                payload["loop"] = self.sticker_loop.snapshot()
            self._send(200, payload)
        elif path.startswith("/v1/songs/") and path.endswith("/audio"):
            job_id = path[len("/v1/songs/"):-len("/audio")].strip("/")
            try:
                audio = self.songs.audio_path(job_id)
                self._send_bytes(
                    audio.read_bytes(),
                    mimetypes.guess_type(audio.name)[0] or "audio/mpeg",
                )
            except Exception as exc:  # noqa: BLE001
                code, payload = self._audio_error(exc)
                self._send(code, payload)
        elif path.startswith("/v1/songs/"):
            job_id = path[len("/v1/songs/"):].strip("/")
            try:
                self._send(200, self.songs.get(job_id).public())
            except Exception as exc:  # noqa: BLE001
                code, payload = self._audio_error(exc)
                self._send(code, payload)
        elif path == "/api/memories":
            self._send(200, admin.memory_library(
                self.memory.db, Ident.coerce(qs), query=qs.get("q", ""),
                limit=_query_int(qs, "limit", 200, low=1, high=1000),
            ))
        elif path == "/api/memory-graph":
            self._send(200, admin.memory_graph(
                self.memory.db, Ident.coerce(qs),
                limit=_query_int(qs, "limit", 400, low=1, high=1000),
            ))
        elif path == "/api/self":
            self._send(200, self.memory.self_portrait(Ident.coerce(qs)))
        elif path == "/api/scheduler":
            ident = Ident.coerce(qs) if qs.get("platform") or qs.get("platform_user_id") else None
            self._send(200, admin.scheduler_snapshot(self.memory.db, ident))
        elif path == "/api/models":
            self._send(200, admin.models_snapshot(self.memory.pool.stats()))
        elif path == "/api/capabilities":
            self._send(200, admin.capabilities_snapshot(self.memory))
        elif path == "/api/operations/features":
            self._send(200, {"items": operations.catalog()})
        elif path == "/api/operations/config":
            self._send(200, operations.read_config(operations.CONFIG_PATH))
        elif path == "/api/operations/services":
            self._send(200, service_control.snapshot())
        elif path == "/api/growth":
            from aiji.brain import growth as growth_mod

            growth_mod.configure(
                age=getattr(self.memory.settings, "persona_age", None),
                stage=getattr(self.memory.settings, "persona_stage", None),
            )
            engine = growth_mod.current()
            try:
                engine.sync(self.memory.db, force=True)
            except Exception:  # noqa: BLE001 - 结算失败也要能看状态
                pass
            snap = engine.describe()
            snap["persona"] = getattr(self.memory.settings, "persona", "")
            snap["stages"] = [
                {"id": s.id, "label": s.label, "label_cn": s.label,
                 "school": s.school, "xp_min": s.xp_min,
                 "range": [s.age_min, s.age_max], "sentence": s.sentence}
                for s in growth_mod.STAGES
            ]
            self._send(200, snap)
        elif path == "/api/mcp":
            registry = getattr(self.memory, "mcp_registry", None)
            research = getattr(self.memory, "mcp_research_loop", None)
            if registry is None:
                return self._send(503, {"error": "mcp_unavailable"})
            # 研究循环是可选的（AIJI_MCP_RESEARCH_LOOP=0 时本来就不跑）。
            # 以前这里把"没有研究循环"也当成不可用，于是关掉研究循环后
            # 控制台的「MCP 研究」面板整块 503 —— 可有可无的东西不该拖垮整页。
            self._send(200, {
                "registry": registry.snapshot(),
                "research": research.snapshot() if research is not None else None,
                "research_enabled": research is not None,
            })
        elif path == "/api/recent-turns":
            self._send(200, admin.recent_turns_snapshot(
                self.memory.db, Ident.coerce(qs), qs.get("session"),
                _query_int(qs, "limit", 40, low=1, high=500),
            ))
        elif path == "/api/injections":
            self._send(200, admin.injection_snapshot(self.memory.db, Ident.coerce(qs)))
        elif path == "/api/proactive":
            self._send(200, admin.proactive_snapshot(self.memory.db))
        elif path == "/api/life":
            ident = Ident.coerce(qs)
            self._send(200, {
                "snapshot": self.memory.life_snapshot(ident),
                "context": self.memory.life_context(
                    ident,
                    scenario=qs.get("scenario", "chat"),
                    intent=qs.get("intent", ""),
                    turns=_query_int(qs, "turns", 0, low=0),
                    token_budget=_query_int(
                        qs, "token_budget", 6000, low=800, high=32000
                    ),
                ),
            })
        elif path == "/api/actions/pending":
            self._send(200, {
                "items": self.memory.action_approvals.pending(),
                "stats": dict(self.memory.action_approvals.stats),
            })
        elif path == "/api/actions/stats":
            self._send(200, {
                "stats": dict(self.memory.action_approvals.stats),
                "root": str(self.memory.action_executor.root),
            })
        elif path == "/api/integrations/onebot":
            self._send(200, {
                **self.memory.onebot.snapshot(),
                "api": self.memory.settings.onebot_api,
                "webui": self.memory.settings.onebot_webui,
                "webhook": "/v1/gateway/onebot",
            })
        elif path == "/media":
            ident = Ident.coerce(qs)
            self._send(200, {
                "items": self.memory.media(
                    ident, qs.get("session"), _query_int(qs, "limit", 20, low=1, high=500)
                )
            })
        elif path == "/style":
            ident = Ident.coerce(qs)
            self._send(200, {"style": self.memory.style(ident),
                             "summary": self.memory.summary(ident, qs.get("session"))})
        elif path == "/being":
            life_loop = getattr(self.memory, "life_loop", None)
            self._send(200, {
                "snapshot": self.memory.being_snapshot(),
                "events": self.memory.being_events(limit=20),
                "notes": self.memory.being_notes(limit=20, private_only=True),
                "loop": life_loop.snapshot() if life_loop else None,
            })
        elif path == "/api/being/checkup":
            from aiji.endocrine.being_checkup import BeingCheckup
            self._send(200, BeingCheckup(self.memory).run())
        elif path in ("/accounts", "/api/accounts"):
            self._send(200, {"items": self.memory.accounts()})
        elif path == "/api/fs":
            if not _workspace_enabled():
                return self._send(403, {"error": "workspace_disabled"})
            try:
                target = self._fs_target(qs.get("path", ""))
            except PermissionError:
                return self._send(403, {"error": "path_outside_root"})
            if not target.exists():
                return self._send(404, {"error": "not_found"})
            if not target.is_dir():
                return self._send(400, {"error": "not_a_directory"})
            entries = []
            for child in target.iterdir():
                try:
                    st = child.stat()
                except OSError:
                    continue
                entries.append({
                    "name": child.name,
                    "kind": "dir" if child.is_dir() else "file",
                    "size": int(st.st_size),
                    "mtime": float(st.st_mtime),
                })
            entries.sort(key=lambda e: (e["kind"] != "dir", e["name"].lower()))
            root = self._fs_root()
            self._send(200, {
                "root": str(root),
                "path": self._fs_rel(target),
                "parent": self._fs_rel(target.parent) if target != root else "",
                "entries": entries[:500],
                "total": len(entries),
            })
        elif path == "/api/fs/read":
            if not _workspace_enabled():
                return self._send(403, {"error": "workspace_disabled"})
            try:
                target = self._fs_target(qs.get("path", ""))
            except PermissionError:
                return self._send(403, {"error": "path_outside_root"})
            if not target.is_file():
                return self._send(404, {"error": "not_found"})
            size = target.stat().st_size
            limit = 2 * 1024 * 1024
            with target.open("rb") as fh:
                raw = fh.read(limit + 1)
            truncated = len(raw) > limit
            data = raw[:limit]
            binary = b"\x00" in data[:4096]
            self._send(200, {
                "path": self._fs_rel(target),
                "size": size,
                "binary": binary,
                "truncated": truncated,
                "text": "" if binary else data.decode("utf-8", "replace"),
            })
        elif path == "/api/fs/raw":
            if not _workspace_enabled():
                return self._send(403, {"error": "workspace_disabled"})
            try:
                target = self._fs_target(qs.get("path", ""))
            except PermissionError:
                return self._send(403, {"error": "path_outside_root"})
            if not target.is_file():
                return self._send(404, {"error": "not_found"})
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self._send_bytes(target.read_bytes(), ctype)
        else:
            self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if not self._authorized(path):
            return self._unauthorized()
        try:
            body = self._body()
        except _PayloadTooLarge as exc:
            return self._send(413, {"error": "payload_too_large",
                                    "limit_bytes": self.MAX_BODY_BYTES,
                                    "size_bytes": exc.size})
        try:
            if path in ("/api/operations/config", "/api/operations/services"):
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self._send(403, {"error": "local_only"})
                if path.endswith("/config"):
                    try:
                        result = operations.save_config(
                            operations.CONFIG_PATH, body.get("changes") or {})
                    except (ValueError, OSError) as exc:
                        return self._send(400, {"error": str(exc)})
                    return self._send(200, result)
                try:
                    result = service_control.control(str(body.get("action") or ""))
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})
                except (RuntimeError, OSError) as exc:
                    return self._send(503, {"error": str(exc)})
                return self._send(202, {"ok": True, **result})
            if path in ("/v1/chat/completions", "/chat/completions"):
                return self._chat_completion(body)
            if path in ("/v1/embeddings", "/embeddings"):
                from aiji.hippocampus.vectors import hashing_embed
                raw_input = body.get("input", "")
                values = raw_input if isinstance(raw_input, list) else [raw_input]
                if len(values) > 256:
                    return self._send(400, {"error": "too_many_inputs"})
                data = []
                token_count = 0
                for index, value in enumerate(values):
                    text = str(value or "")[:8000]
                    token_count += max(1, len(text) // 2)
                    data.append({
                        "object": "embedding",
                        "index": index,
                        "embedding": hashing_embed(text),
                    })
                return self._send(200, {
                    "object": "list",
                    "data": data,
                    "model": str(body.get("model") or "aiji-hash-256"),
                    "usage": {
                        "prompt_tokens": token_count,
                        "total_tokens": token_count,
                    },
                })
            if path == "/v1/audio/speech":
                text = str(body.get("input") or body.get("text") or "")
                profile_id = str(body.get("voice") or body.get("profile") or "")
                if profile_id in ("aiji", "default"):
                    profile_id = ""
                try:
                    # tempfile.TemporaryDirectory() creates 0o700 dirs, which this
                    # machine's sandbox cannot clean up; the voice service already
                    # provides an explicit 0o777 work dir for exactly this reason.
                    tmp = self.voice._workdir()
                    try:
                        result = self.voice.synthesize(
                            text,
                            tmp / "speech.wav",
                            profile_id=profile_id,
                            rate=body.get("rate"),
                            pitch=body.get("pitch"),
                            volume=body.get("volume"),
                            instruct=str(body.get("instruct") or ""),
                        )
                        self._send_bytes(
                            result.path.read_bytes(),
                            "audio/wav",
                        )
                    finally:
                        shutil.rmtree(tmp, ignore_errors=True)
                    return
                except Exception as exc:  # noqa: BLE001
                    code, payload = self._audio_error(exc)
                    return self._send(code, payload)
            if path == "/v1/voices/use":
                profile_id = str(body.get("profile") or body.get("id") or "").strip()
                if not profile_id:
                    # 空 body 以前会静默切到默认声线（控制台 operations.html 把 profile
                    # 定义为必填）。改状态的动作不该在没人说要切谁的时候发生。
                    return self._send(400, {
                        "code": "profile_required",
                        "message": "voice profile id is required "
                                   "(GET /v1/voices lists them)",
                    })
                try:
                    self.voice.set_active(profile_id)
                    return self._send(200, self.voice.snapshot())
                except Exception as exc:  # noqa: BLE001
                    code, payload = self._audio_error(exc)
                    return self._send(code, payload)
            if path == "/v1/songs":
                source = (
                    body.get("source") or body.get("url")
                    or body.get("song_id") or body.get("query") or ""
                )
                try:
                    job = self.songs.submit(
                        str(source),
                        profile_id=str(body.get("profile") or ""),
                        pitch=int(body.get("pitch") or 0),
                        max_seconds=int(body.get("max_seconds") or 60),
                    )
                    return self._send(202, job.public())
                except Exception as exc:  # noqa: BLE001
                    code, payload = self._audio_error(exc)
                    return self._send(code, payload)
            if path == "/api/social/qzone/publish":
                try:
                    result = self.social.publish(
                        str(body.get("content") or ""),
                        images=[
                            str(item) for item in (body.get("images") or [])
                        ],
                        visibility=str(body.get("visibility") or "public"),
                        target_uins=[
                            str(item) for item in (body.get("target_uins") or [])
                        ],
                    )
                    return self._send(200, result)
                except Exception as exc:  # noqa: BLE001
                    code, payload = self._social_error(exc)
                    return self._send(code, payload)
            if path == "/api/social/qzone/comment":
                try:
                    result = self.social.comment(
                        str(body.get("tid") or ""),
                        str(body.get("content") or ""),
                        target_uin=str(body.get("target_uin") or ""),
                        images=[
                            str(item) for item in (body.get("images") or [])
                        ],
                    )
                    return self._send(200, result)
                except Exception as exc:  # noqa: BLE001
                    code, payload = self._social_error(exc)
                    return self._send(code, payload)
            if path == "/api/social/qzone/auto-comment":
                try:
                    result = self.social.auto_comment(
                        str(body.get("target_uin") or ""),
                        str(body.get("content") or ""),
                        post_index=int(body.get("post_index") or 0),
                        images=[
                            str(item) for item in (body.get("images") or [])
                        ],
                    )
                    return self._send(200, result)
                except Exception as exc:  # noqa: BLE001
                    code, payload = self._social_error(exc)
                    return self._send(code, payload)
            if path == "/api/social/avatar":
                try:
                    result = self.social.set_avatar(
                        str(body.get("file") or body.get("path") or ""))
                    return self._send(200, result)
                except Exception as exc:  # noqa: BLE001
                    code, payload = self._social_error(exc)
                    return self._send(code, payload)
            if path == "/api/social/signature":
                try:
                    result = self.social.set_signature(
                        str(body.get("signature") or body.get("text") or ""))
                    return self._send(200, result)
                except Exception as exc:  # noqa: BLE001
                    code, payload = self._social_error(exc)
                    return self._send(code, payload)
            if path == "/api/social/share/music":
                try:
                    result = self.social.share_music(
                        str(body.get("query") or body.get("song_id") or ""),
                        caption=str(body.get("caption") or ""),
                        visibility=str(body.get("visibility") or "public"),
                    )
                    return self._send(200, result)
                except Exception as exc:  # noqa: BLE001
                    code, payload = self._social_error(exc)
                    return self._send(code, payload)
            if path == "/api/social/share/bilibili":
                try:
                    result = self.social.share_bilibili(
                        str(body.get("url") or body.get("bvid") or ""),
                        title=str(body.get("title") or ""),
                        caption=str(body.get("caption") or ""),
                        visibility=str(body.get("visibility") or "public"),
                    )
                    return self._send(200, result)
                except Exception as exc:  # noqa: BLE001
                    code, payload = self._social_error(exc)
                    return self._send(code, payload)
            if path == "/api/stickers/update":
                # 首次更新要联网下载，实测 27 s（route_smoke 2026-11-03）——同步会把
                # 控制台卡死。默认改成"后台跑、立刻返回"，要等结果就带 ?wait=1。
                if str(qs.get("wait") or "") in ("1", "true", "yes"):
                    try:
                        return self._send(200, self.stickers.update())
                    except Exception as exc:  # noqa: BLE001
                        code, payload = self._sticker_error(exc)
                        return self._send(code, payload)
                job = getattr(self.server, "sticker_job", None)
                if job and job.get("running"):
                    return self._send(200, {"started": False, "running": True,
                                            "at": job.get("at"),
                                            "last_update": self.stickers.snapshot()["last_update"]})
                started_at = time.time()

                def _run_update(srv=self.server) -> None:
                    err = None
                    payload: dict = {}
                    try:
                        payload = self.stickers.update()
                    except Exception as exc:  # noqa: BLE001
                        err = f"{type(exc).__name__}: {exc}"
                    finally:
                        srv.sticker_job = {"running": False, "error": err,
                                           "at": time.time(), "result": payload}

                self.server.sticker_job = {"running": True, "error": None, "at": started_at}
                threading.Thread(target=_run_update, name="aiji-sticker-update",
                                 daemon=True).start()
                return self._send(200, {"started": True, "running": True, "at": started_at})
            if path == "/api/stickers/import":
                try:
                    result = self.stickers.import_image(
                        str(body.get("url") or ""),
                        mood=str(body.get("mood") or ""),
                        description=str(body.get("description") or ""),
                        source=str(body.get("source") or ""),
                    )
                    return self._send(200, result)
                except Exception as exc:  # noqa: BLE001
                    code, payload = self._sticker_error(exc)
                    return self._send(code, payload)
            if path == "/api/being/checkup/live":
                # 这个探针要克隆整库 + 跑真模型，实测 33.8 s（route_smoke 2026-11-03）。
                # 策略：有缓存就先返回（哪怕过期，标 stale），后台再刷新——
                # 只有"从来没有过缓存"的第一次调用才会真的等。
                from aiji.endocrine.being_checkup import BeingCheckup
                ident = self._ident(body)
                key = (ident.platform, ident.platform_user_id, ident.scope)
                cache = getattr(self.server, "checkup_cache", None)
                if cache is None:  # 测试里可能用裸 ThreadingHTTPServer
                    cache = {}
                    self.server.checkup_cache = cache
                refreshing = getattr(self.server, "checkup_refreshing", None)
                if refreshing is None:
                    refreshing = set()
                    self.server.checkup_refreshing = refreshing
                hit = cache.get(key)
                if hit is None:
                    # 启动预热可能正在跑同一个身份：等它，别再克隆一份 48 MB 的库、
                    # 再打一遍模型（重复调用既慢又费额度）。
                    events = getattr(self.server, "checkup_events", None) or {}
                    event = events.get(key)
                    if event is not None and not event.is_set():
                        event.wait(timeout=120)
                        hit = cache.get(key)
                now = time.time()
                fresh = bool(hit) and now - hit[0] < 600

                def _refresh(srv=self.server) -> None:
                    try:
                        result = BeingCheckup(self.memory).live_probe(ident)
                        cache[key] = (time.time(), result)
                    except Exception as exc:  # noqa: BLE001 - 后台刷新失败不能影响响应
                        logger.warning("being checkup 刷新失败：%s", exc)
                    finally:
                        refreshing.discard(key)

                if hit and (fresh or not body.get("refresh")):
                    want_refresh = bool(body.get("refresh")) or not fresh
                    if want_refresh and key not in refreshing:
                        refreshing.add(key)
                        threading.Thread(target=_refresh, name="aiji-checkup-refresh",
                                         daemon=True).start()
                    return self._send(200, {**hit[1], "cached": True, "stale": not fresh,
                                            "refresh_requested": want_refresh,
                                            "age_s": round(now - hit[0], 1)})
                result = BeingCheckup(self.memory).live_probe(ident)
                cache[key] = (now, result)
                return self._send(200, {**result, "cached": False, "stale": False})
            if path == "/api/mcp/research":
                research = getattr(self.memory, "mcp_research_loop", None)
                if research is None:
                    return self._send(503, {"error": "mcp_unavailable"})
                queued = research.trigger()
                return self._send(
                    202 if queued else 409,
                    {"queued": bool(queued), "research": research.snapshot()},
                )
            if path == "/v1/gateway/messages":
                return self._gateway_message(body)
            if path == "/v1/gateway/onebot":
                return self._onebot_event(body)
            if path in ("/api/actions/approve", "/api/actions/reject"):
                approval_id = str(body.get("id") or "")
                approved = path.endswith("/approve")
                ok = self.memory.action_approvals.resolve(approval_id, approved)
                if ok:
                    self.memory.presence.publish({
                        "state": "acting" if approved else "thinking",
                        "mood": "专注" if approved else "警觉",
                        "preview": "已批准执行" if approved else "已拒绝执行",
                    })
                return self._send(200 if ok else 404, {
                    "approved": bool(ok and approved),
                    "resolved": ok,
                })
            if path == "/api/fs/write":
                if not _workspace_enabled():
                    return self._send(403, {"error": "workspace_disabled"})
                rel = str(body.get("path") or "")
                text = body.get("text")
                if not rel or not isinstance(text, str):
                    return self._send(400, {"error": "path_and_text_required"})
                try:
                    target = self._fs_target(rel)
                except PermissionError:
                    return self._send(403, {"error": "path_outside_root"})
                data = text.encode("utf-8")
                if len(data) > 2 * 1024 * 1024:
                    return self._send(413, {"error": "too_large"})
                if target.exists() and target.is_dir():
                    return self._send(400, {"error": "is_directory"})
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                return self._send(200, {
                    "ok": True, "path": self._fs_rel(target), "size": len(data),
                })
            if path == "/api/shell":
                return self._run_shell(body)
            if path == "/turns":
                content, clipped = _clip_text(body.get("content"), self.MAX_TEXT_CHARS)
                if not content:
                    return self._send(400, {"error": "content_required"})
                depth = self.memory.turn(
                    self._ident(body), str(body.get("session") or "default"),
                    str(body.get("role") or "user"), content, extra=body.get("extra"),
                )
                return self._send(202, {"queued": True, "queue_depth": depth,
                                        **clipped})
            if path == "/remember":
                text, clipped = _clip_text(body.get("text"), self.MAX_TEXT_CHARS)
                if not text:
                    return self._send(400, {"error": "text_required"})
                depth = self.memory.remember(
                    text, self._ident(body), session=str(body.get("session") or "default"),
                    extra=body.get("extra"),
                )
                return self._send(202, {"queued": True, "queue_depth": depth,
                                        **clipped})
            if path == "/media":
                if not body.get("ref"):
                    return self._send(400, {"error": "ref_required"})
                mid = self.memory.add_media(
                    self._ident(body), kind=str(body.get("kind") or "file"), ref=str(body["ref"]),
                    url=body.get("url"), session=body.get("session"), mime=body.get("mime"),
                    size_bytes=body.get("size_bytes"), expires_at=body.get("expires_at"),
                    extra=body.get("extra"),
                )
                return self._send(201, {"id": mid, "stored": "reference_only"})
            if path == "/ingest":
                kind = str(body.get("kind") or "text")
                clipped_text = {}
                if kind == "text":
                    text, clipped_text = _clip_text(
                        body.get("text"), self.MAX_TEXT_CHARS)
                    if not text:
                        return self._send(400, {"error": "text_required"})
                if kind != "text" and not (body.get("ref") or body.get("url")):
                    return self._send(400, {"error": "ref_or_url_required"})
                depth = self.memory.ingest(
                    self._ident(body), kind=kind, ref=str(body.get("ref") or ""),
                    text=text if kind == "text" else body.get("text"),
                    url=body.get("url"), mime=body.get("mime"),
                    size_bytes=body.get("size_bytes"),
                    session=str(body.get("session") or "default"), extra=body.get("extra"),
                )
                return self._send(202, {"queued": True, "queue_depth": depth,
                                        "kind": kind, **clipped_text})
            if path == "/recall":
                res = self.memory.recall(
                    str(body.get("query") or ""), self._ident(body),
                    session=body.get("session"), top_k=body.get("top_k"),
                    budget_ms=body.get("budget_ms"),
                )
                return self._send(200, res.to_dict())
            if path == "/inject":
                inj = self.memory.inject(
                    str(body.get("query") or ""), self._ident(body), session=body.get("session"),
                    max_chars=body.get("max_chars"), max_items=body.get("max_items"),
                    log=bool(body.get("log", True)),
                )
                return self._send(200, inj.to_dict())
            if path == "/feedback":
                n = self.memory.feedback(
                    self._ident(body), body.get("injection_id"),
                    [int(i) for i in (body.get("used_ids") or [])],
                    helpful=bool(body.get("helpful", True)), note=str(body.get("note") or ""),
                )
                return self._send(200, {"updated": n})
            if path == "/flush":
                ok = self.memory.flush(float(body.get("timeout") or 10))
                return self._send(200, {"flushed": ok, "stats": self.memory.writer.stats_dict()})
            if path == "/being/note":
                text = str(body.get("text") or "").strip()
                if not text:
                    return self._send(400, {"error": "text_required"})
                note_id = self.memory.being_note(
                    text, visibility=str(body.get("visibility") or "private"))
                return self._send(201, {"id": note_id, "visibility": "private"})
            if path == "/being/event":
                summary = str(body.get("summary") or "").strip()
                if not summary:
                    return self._send(400, {"error": "summary_required"})
                event_id = self.memory.being_record_event(
                    str(body.get("kind") or "life"),
                    summary,
                    valence_delta=float(body.get("valence_delta") or 0),
                    energy_delta=float(body.get("energy_delta") or 0),
                )
                return self._send(201, {"id": event_id})
        except Exception as exc:  # noqa: BLE001 - 服务不因单请求崩
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
        self._send(404, {"error": "not_found"})

    def _run_shell(self, body: dict[str, Any]) -> None:
        """工作区终端：单条命令、cwd 限制在工作区根内、默认 15s 超时。

        默认关闭（`AIJI_ENABLE_WORKSPACE=1` 才开；`AIJI_CONSOLE_SHELL=0` 仍可再关）。
        """
        if not _workspace_enabled():
            return self._send(403, {"error": "workspace_disabled"})
        if os.environ.get("AIJI_CONSOLE_SHELL", "1").strip().lower() in ("0", "false", "off", "no"):
            return self._send(403, {"error": "shell_disabled"})
        if os.environ.get("AIJI_UNSAFE_SHELL", "0").strip().lower() not in (
            "1", "true", "on", "yes",
        ):
            return self._send(403, {"error": "unsafe_shell_disabled"})
        cmd = str(body.get("cmd") or "").strip()
        if not cmd:
            return self._send(400, {"error": "cmd_required"})
        try:
            cwd = self._fs_target(str(body.get("cwd") or ""))
        except PermissionError:
            return self._send(403, {"error": "path_outside_root"})
        if not cwd.is_dir():
            cwd = self._fs_root()
        try:
            timeout = max(1.0, min(30.0, float(body.get("timeout") or 15.0)))
        except (TypeError, ValueError):
            timeout = 15.0
        started = time.perf_counter()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        timed_out = False
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=str(cwd), capture_output=True,
                timeout=timeout, creationflags=flags,
            )
            stdout = _decode_console(proc.stdout or b"")
            stderr = _decode_console(proc.stderr or b"")
            code = int(proc.returncode)
        except subprocess.TimeoutExpired as exc:
            stdout = _decode_console(exc.stdout if isinstance(exc.stdout, bytes) else b"")
            stderr = "timeout"
            code, timed_out = -1, True
        except Exception as exc:  # noqa: BLE001 - 命令失败要回给控制台而不是打崩服务
            stdout, stderr, code = "", f"{type(exc).__name__}: {exc}", -1
        took = (time.perf_counter() - started) * 1000
        self._send(200, {
            "code": code,
            "stdout": stdout[-200_000:],
            "stderr": stderr[-40_000:],
            "took_ms": round(took, 1),
            "cwd": self._fs_rel(cwd),
            "timed_out": timed_out,
        })

    def _onebot_event(self, body: dict[str, Any]) -> None:
        if not self.memory.onebot.configured:
            return self._send(503, {
                "status": "not_configured",
                "hint": "set AIJI_ONEBOT_API to the NapCat HTTP API base URL",
            })
        result = self.memory.onebot.submit(body)
        code = 202 if result.get("status") == "accepted" else 200
        self._send(code, result)

    def _gateway_message(self, body: dict[str, Any]) -> None:
        """统一 IM 网关入口：平台适配器只需 POST 归一化消息。"""
        content = str(body.get("content") or body.get("text") or "").strip()
        if not content:
            return self._send(400, {"error": "content_required"})
        ident = self._ident(body)
        session = str(body.get("session") or "default")
        try:
            reply = Head(self.memory).reply(
                [{"role": "user", "content": content}],
                ident,
                session=session,
                debounce=bool(body.get("debounce", True)),
                system_suffix=str(body.get("system_suffix") or ""),
            )
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})
        self._send(200, {
            "text": reply.text,
            "platform": ident.platform,
            "platform_user_id": ident.platform_user_id,
            "scope": ident.scope,
            "session": session,
            "route": reply.route,
            "injection_id": reply.injection_id,
            "used_ids": reply.used_ids or [],
            "degraded": reply.degraded,
            "finish_reason": reply.finish_reason,
            "took_ms": round(reply.took_ms, 1),
            "tools": reply.tool_traces or [],
            "life": reply.life,
        })

    def _chat_completion(self, body: dict[str, Any]) -> None:
        """OpenAI 兼容入口：下游客户端无需知道记忆/人格/积分网关的存在。"""
        metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        hint = parse_identity_hint(str(body.get("user") or ""))
        session = (self.headers.get("X-Aiji-Session") or body.get("session")
                   or metadata.get("session_id") or metadata.get("session")
                   or self.headers.get("X-AstrBot-Conversation-Id")
                   or hint.get("scope") or "default")
        sampling = _sampling_params(body)
        aiji_opts = body.get("aiji") if isinstance(body.get("aiji"), dict) else {}
        if isinstance(metadata.get("aiji"), dict):
            aiji_opts = {**metadata["aiji"], **aiji_opts}
        enable_tools = bool(aiji_opts.get("tools", True))
        remember_turn = bool(aiji_opts.get("remember", True))
        proactive = bool(aiji_opts.get("proactive", False))
        persona_name = str(aiji_opts.get("persona") or "").strip() or None
        sender_name = str(aiji_opts.get("sender_name") or "").strip()
        turn_extra = {"sender_name": sender_name} if sender_name else None
        ident = self._ident(body)
        messages = body.get("messages")
        history_turns = 0
        if aiji_opts.get("history") and isinstance(messages, list):
            # 薄客户端（AstrBot/IDE/自定义 bot）只传本轮一句话时，从 turn 表补最近对话。
            # 客户端自己带了 assistant 历史就不再补，避免重复。
            has_assistant = any(
                isinstance(item, dict) and item.get("role") == "assistant"
                for item in messages
            )
            if not has_assistant:
                try:
                    limit = max(0, min(200, int(aiji_opts.get("history_limit", 80))))
                except (TypeError, ValueError):
                    limit = 80
                try:
                    chars_budget = max(
                        1000, min(50000, int(aiji_opts.get("history_chars", 12000)))
                    )
                except (TypeError, ValueError):
                    chars_budget = 12000
                if limit:
                    try:
                        scope_text = str(ident.scope or "default")
                        if scope_text.startswith("group:"):
                            rows = self.memory.recent_scope_turns(
                                ident.platform, scope_text, session, limit=limit)
                        else:
                            rows = self.memory.recent_turns(
                                ident, session, limit=limit)
                    except Exception:  # noqa: BLE001 - 历史读失败不阻塞本轮
                        rows = []
                    # 从最新往回装，超字符预算就停；再翻回时间顺序。
                    picked: list[dict[str, str]] = []
                    used = 0
                    bad_assistant = (
                        "有什么可以帮", "想聊点什么", "想让我做", "抱歉",
                        "我只是一条", "出不了鱼缸", "作为一条鱼", "我可以帮",
                        "今天想聊", "你好，",
                    )
                    for row in reversed(rows):
                        role = str(row.get("role") or "")
                        text = str(row.get("content") or "")
                        if role not in ("user", "assistant") or not text.strip():
                            continue
                        if (
                            role == "user"
                            and str(ident.scope or "").startswith("group:")
                        ):
                            try:
                                extra = json.loads(row.get("extra") or "{}")
                            except (TypeError, ValueError):
                                extra = {}
                            speaker = str(extra.get("sender_name") or "").strip()
                            speaker_id = str(row.get("platform_user_id") or "").strip()
                            who = speaker or speaker_id or "群友"
                            if speaker_id and speaker_id != speaker:
                                who = f"{who}({speaker_id})"
                            text = f"[{who}] {text}"
                        # 历史里的旧客服腔会污染风格：不喂给模型。
                        if role == "assistant" and any(
                            marker in text for marker in bad_assistant
                        ):
                            continue
                        if used + len(text) > chars_budget:
                            break
                        picked.append({"role": role, "content": text})
                        used += len(text)
                    picked.reverse()
                    history: list[dict[str, str]] = []
                    try:
                        summary = self.memory.summary(ident, session)
                    except Exception:  # noqa: BLE001
                        summary = None
                    summary_text = str((summary or {}).get("text") or "").strip()
                    if summary_text:
                        history.append({
                            "role": "system",
                            "content": "更早的对话摘要（原文已被压缩）：\n" + summary_text[:4000],
                        })
                    history.extend(picked)
                    style_name = persona_name or getattr(
                        self.memory.settings, "persona", "default")
                    reminder = persona_mod.reminder(style_name)
                    if reminder:
                        history.append({"role": "system", "content": reminder})
                    if history:
                        insert_at = 0
                        while (insert_at < len(messages)
                               and isinstance(messages[insert_at], dict)
                               and messages[insert_at].get("role") == "system"):
                            insert_at += 1
                        messages = list(messages[:insert_at]) + history + list(messages[insert_at:])
                        history_turns = len(picked)
        try:
            reply = Head(self.memory).reply(
                messages,
                ident,
                session=session,
                debounce=bool(body.get("debounce", True)),
                enable_tools=enable_tools,
                persona=persona_name,
                extra=turn_extra,
                remember=remember_turn,
                proactive=proactive,
                **sampling,
            )
        except ValueError as exc:
            return self._send(400, {
                "error": {"message": str(exc), "type": "invalid_request_error",
                          "param": "messages", "code": str(exc)},
            })
        except PoolExhausted as exc:
            return self._send(503, {
                "error": {"message": f"upstream gateway unavailable: {exc}",
                          "type": "service_unavailable", "code": "model_unavailable"},
            })

        if body.get("stream"):
            return self._stream_chat(reply, body)

        prompt_chars = sum(len(str(m.get("content", "")))
                           for m in (messages or []) if isinstance(m, dict))
        prompt_tokens = max(1, prompt_chars // 2)
        completion_tokens = max(1, len(reply.text) // 2)
        payload = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": str(body.get("model") or "aiji"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": reply.text},
                "finish_reason": reply.finish_reason,
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            "aiji": {
                "injection_id": reply.injection_id,
                "used_ids": reply.used_ids or [],
                "route": reply.route,
                "model_latency_ms": round(reply.model_latency_ms, 1),
                "human_delay_ms": round(reply.human_delay_ms, 1),
                "took_ms": round(reply.took_ms, 1),
                "filter_reason": reply.reason,
                "degraded": reply.degraded,
                "tools": reply.tool_traces or [],
                "life": reply.life,
                "sampling": sampling,
                "history_turns": history_turns,
            },
        }
        self._send(200, payload)

    def _stream_chat(self, reply: Any, body: dict[str, Any]) -> None:
        """OpenAI SSE；上游当前是非流式时，至少保持下游协议完整。"""
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        model = str(body.get("model") or "aiji")
        sampling = _sampling_params(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def emit(payload: dict[str, Any]) -> None:
            line = "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()

        base = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
        }
        emit({**base, "choices": [{
            "index": 0, "delta": {"role": "assistant", "content": ""},
            "finish_reason": None,
        }]})
        emit({**base, "choices": [{
            "index": 0, "delta": {"content": reply.text},
            "finish_reason": None,
        }]})
        emit({**base, "choices": [{
            "index": 0, "delta": {}, "finish_reason": reply.finish_reason,
        }], "aiji": {
            "injection_id": reply.injection_id,
            "used_ids": reply.used_ids or [],
            "route": reply.route,
            "model_latency_ms": round(reply.model_latency_ms, 1),
            "human_delay_ms": round(reply.human_delay_ms, 1),
            "took_ms": round(reply.took_ms, 1),
            "filter_reason": reply.reason,
            "degraded": reply.degraded,
            "tools": reply.tool_traces or [],
            "life": reply.life,
            "sampling": sampling,
        }})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class AijiHTTPServer(ThreadingHTTPServer):
    life_loop: Any = None
    mcp_research_loop: Any = None
    sticker_loop: Any = None
    # socketserver 默认 listen backlog 只有 5。实测 8 线程同时发请求时
    # p95 从 29ms 跳到 522ms、32 线程到 1.5s，且步进正好是 Windows 的 SYN
    # 重传间隔（≈500ms）：连接在 backlog 里排不下，客户端在重传 SYN。
    # 控制台一屏会并发拉好几个接口，放宽到这个量级就不会排队了。
    request_queue_size = 128

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # /api/being/checkup/live 的结果缓存（身份 → (时间戳, 结果)）
        self.checkup_cache: dict[Any, Any] = {}

    def server_close(self) -> None:
        loop = self.life_loop
        if loop is not None:
            loop.stop()
        mcp_loop = self.mcp_research_loop
        if mcp_loop is not None:
            mcp_loop.stop()
        sticker_loop = self.sticker_loop
        if sticker_loop is not None:
            sticker_loop.stop()
        super().server_close()

    def handle_error(self, request: Any, client_address: Any) -> None:
        """客户端半路断开不是服务端故障：别打整篇 traceback。

        实测 `data/console.err.*.log` 里成篇的
        `ConnectionResetError: [WinError 10054]` + socketserver 栈——
        那是浏览器/探针提前关连接（或写入被 RST）造成的噪声，把它降成
        debug 一行，真异常照旧走默认处理（traceback 留给真问题）。
        """
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            logger.debug("客户端断开 %s：%s", client_address, type(exc).__name__)
            return
        super().handle_error(request, client_address)


def make_server(memory: Memory, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    from aiji.endocrine.life_loop import LifeLoop
    from aiji.left_hand.mcp_client import MCPConfigError, MCPRegistry
    from aiji.left_hand.mcp_research import MCPResearchLoop, MCPResearcher
    from aiji.heart.song_service import SongService
    from aiji.heart.social_service import SocialService
    from aiji.heart.sticker_service import DEFAULT_STICKER_FEED, StickerService, StickerUpdateLoop
    from aiji.heart.voice_service import VoiceRegistry

    data_root = Path(memory.settings.db_path).resolve().parent
    voices = VoiceRegistry(
        Path(os.environ.get("AIJI_VOICE_DIR", data_root / "voices")))
    songs = SongService(voices, data_root=data_root)
    sticker_default = Path("D:/AstrBot/data/sticker_packs/aiji")
    sticker_dir = Path(memory.settings.sticker_dir or sticker_default)
    stickers = StickerService(
        sticker_dir,
        feed_url=(
            memory.settings.sticker_feed_url or DEFAULT_STICKER_FEED
        ),
        max_bytes=int(memory.settings.sticker_max_bytes),
    )
    memory.stickers = stickers
    social = SocialService(
        memory.onebot_client.call,
        media_root=Path(
            memory.settings.social_media_dir
            or data_root / "social" / "media"
        ),
        sticker_dir=sticker_dir,
        song_lookup=songs.lookup,
        sticker_status=stickers.snapshot,
        enabled=bool(memory.settings.onebot_api),
    )
    memory.social = social

    handler = type("BoundHandler", (_Handler,), {
        "memory": memory,
        "voice": voices,
        "songs": songs,
        "social": social,
        "stickers": stickers,
    })
    srv = AijiHTTPServer((host, port), handler)
    srv.daemon_threads = True

    if os.environ.get("AIJI_LIFE_LOOP", "1").strip().lower() not in (
        "0", "false", "off", "no",
    ):
        poll_seconds = max(
            5.0, float(os.environ.get("AIJI_LIFE_LOOP_POLL", "60")))
        memory.life_loop = LifeLoop(
            memory.being, poll_seconds=poll_seconds).start()
        srv.life_loop = memory.life_loop
    # `settings.sticker_update` 是配置里的意愿；`AIJI_STICKER_UPDATE=0` 是运维/测试的
    # 临时开关（跟 AIJI_LIFE_LOOP、AIJI_MCP_RESEARCH_LOOP 同一层语义）。
    # 只有配置开关时，测试没法在不起 jsdelivr 请求的前提下 boot 一个服务。
    sticker_enabled = bool(memory.settings.sticker_update) and os.environ.get(
        "AIJI_STICKER_UPDATE", "1").strip().lower() not in (
            "0", "false", "off", "no")
    if sticker_enabled:
        sticker_loop = StickerUpdateLoop(
            stickers,
            initial_delay_s=memory.settings.sticker_update_initial_s,
            interval_s=memory.settings.sticker_update_interval_s,
        ).start()
        handler.sticker_loop = sticker_loop
        srv.sticker_loop = sticker_loop
        memory.sticker_loop = sticker_loop
    # 代谢清理 Worker（borrow-plan 1.8 / P0-2、P0-3 的运行时）：冻结低置信陈事实、
    # 去重合并、关联边封顶、归档 90 天清除、WAL 截断。租约保证多进程单实例。
    # 默认开；AIJI_JANITOR=0 关（与 AIJI_LIFE_LOOP 同层语义）。
    if os.environ.get("AIJI_JANITOR", "1").strip().lower() not in (
        "0", "false", "off", "no",
    ):
        poll_seconds = max(
            60.0, float(os.environ.get("AIJI_JANITOR_POLL", "900")))
        first_delay_s = max(
            10.0, float(os.environ.get("AIJI_JANITOR_FIRST_S", "60")))

        def _janitor_loop() -> None:
            import logging

            from aiji.hippocampus.janitor import Janitor

            log = logging.getLogger("aiji.janitor")
            stop = threading.Event()
            srv.janitor_stop = stop  # 供关闭路径（如有）使用
            if not stop.wait(first_delay_s):
                try:
                    summary = Janitor(memory.db).run()
                    log.info("janitor 首轮：%s", summary)
                except Exception:  # noqa: BLE001 - 维护线程不许带崩服务
                    log.exception("janitor 首轮失败")
            while not stop.wait(poll_seconds):
                try:
                    summary = Janitor(memory.db).run()
                    if summary.get("skipped"):
                        continue
                    log.info("janitor：%s", summary)
                except Exception:  # noqa: BLE001
                    log.exception("janitor 轮失败")

        janitor_thread = threading.Thread(
            target=_janitor_loop, name="aiji-janitor", daemon=True)
        janitor_thread.start()
        srv.janitor_thread = janitor_thread
        memory.janitor_thread = janitor_thread
    mcp_config = Path(os.environ.get(
        "AIJI_MCP_CONFIG",
        ROOT / "config" / "mcp_servers.json",
    ))
    try:
        mcp_registry = MCPRegistry(mcp_config)
        mcp_researcher = MCPResearcher(
            output_dir=data_root / "mcp" / "discovery",
            registry=mcp_registry,
            observer=lambda summary: memory.being_record_event(
                "mcp_research",
                summary,
                valence_delta=0.8,
                energy_delta=-1.2,
            ),
        )
        mcp_loop = MCPResearchLoop(
            mcp_researcher,
            initial_delay_s=float(os.environ.get(
                "AIJI_MCP_RESEARCH_INITIAL", "60")),
            interval_s=float(os.environ.get(
                "AIJI_MCP_RESEARCH_INTERVAL", "3600")),
        )
        memory.mcp_registry = mcp_registry
        memory.mcp_researcher = mcp_researcher
        srv.mcp_registry = mcp_registry
        if os.environ.get("AIJI_MCP_RESEARCH_LOOP", "1").strip().lower() not in (
            "0", "false", "off", "no",
        ):
            mcp_loop.start()
            memory.mcp_research_loop = mcp_loop
            srv.mcp_research_loop = mcp_loop
        else:
            # 关掉时不把"没跑起来的循环对象"挂到 server 上：
            # 以前 srv.mcp_research_loop 总是有值，`/api/mcp/research` 与自检看起来
            # 像是"研究循环在跑"，实际一次都没跑过。
            memory.mcp_research_loop = None
            srv.mcp_research_loop = None
    except (MCPConfigError, OSError, ValueError) as exc:
        # 别静默：注册表/研究循环没起来时，`/api/mcp` 与 `/api/mcp/research`
        # 会各自变成 503，没有这一行就只剩"为什么是 503"。
        logger.warning("MCP 注册表/研究循环没有起来：%s", exc)
        memory.mcp_registry = None
        memory.mcp_research_loop = None
        srv.mcp_research_loop = None
    return srv


def serve(memory: Memory, host: str = "127.0.0.1", port: int = 8787) -> None:  # pragma: no cover
    srv = make_server(memory, host, port)
    # 体检探针要克隆整库 + 3 个真模型调用（并行后实测 19.7 s）。用户点控制台时
    # 不该干等：启动后就预热默认身份与最近活跃身份的缓存（后台线程，失败只记日志）。
    if os.environ.get("AIJI_CHECKUP_PREWARM", "1").strip().lower() not in (
        "0", "false", "off", "no",
    ):
        def _prewarm(srv: AijiHTTPServer = srv, mem=memory) -> None:
            from aiji.endocrine.being_checkup import BeingCheckup
            from aiji.hippocampus.store import Ident

            keys = [("local", "default", "default")]
            try:
                rows = mem.db.read(
                    "SELECT platform, platform_user_id, scope FROM account "
                    "ORDER BY updated_at DESC LIMIT 1")
                keys += [(r["platform"], r["platform_user_id"], r["scope"])
                         for r in rows]
            except Exception as exc:  # noqa: BLE001 - 预热不该影响启动
                logger.warning("体外预热取身份失败：%s", exc)
            cache = {}
            events: dict[tuple[str, str, str], threading.Event] = {}
            srv.checkup_cache = cache
            srv.checkup_refreshing = set()
            srv.checkup_events = events
            for platform, user, scope in dict.fromkeys(keys):
                event = threading.Event()
                events[(platform, user, scope)] = event
                try:
                    result = BeingCheckup(mem).live_probe(
                        Ident(platform, user, scope))
                    cache[(platform, user, scope)] = (time.time(), result)
                    logger.info("体检缓存预热完成：%s/%s/%s", platform, user, scope)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("体检预热失败 %s/%s/%s：%s",
                                   platform, user, scope, exc)
                finally:
                    event.set()

        threading.Thread(target=_prewarm, name="aiji-checkup-prewarm",
                         daemon=True).start()
    print(f"aiji listening on http://{host}:{srv.server_address[1]}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()
