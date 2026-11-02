"""NapCat / OneBot 11 个人 QQ 桥接。

NapCat 负责 QQ 协议和登录，AIJiYi 只接收 OneBot HTTP 上报，再调用其发送 API。
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

_CQ_AT = re.compile(r"\[CQ:at,qq=([^,\]]+)[^\]]*\]")
_CQ_ANY = re.compile(r"\[CQ:[^\]]+\]")
_ACTION = re.compile(r"^[A-Za-z0-9_.-]+$")


class OneBotCallError(RuntimeError):
    def __init__(
        self,
        action: str,
        message: str,
        *,
        retcode: int | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.action = action
        self.retcode = retcode
        self.status = status


@dataclass(frozen=True)
class SendOutcome:
    """一次发送的**结构化**结果（2026-11-03）：以前 `send()` 只回 bool，分不清"重试有用"和"重试无用"。"""

    ok: bool
    retcode: int | None = None
    http_status: int | None = None
    message: str = ""


def _parse_message(message: Any, self_id: str) -> tuple[str, bool]:
    mentioned = False
    if isinstance(message, str):
        mentioned = self_id in _CQ_AT.findall(message)
        return _CQ_ANY.sub("", message).strip(), mentioned
    if not isinstance(message, list):
        return "", False
    parts: list[str] = []
    for item in message:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        if kind == "text":
            parts.append(str(data.get("text") or ""))
        elif kind == "at":
            qq = str(data.get("qq") or "")
            if qq == self_id:
                mentioned = True
            elif qq:
                parts.append(f"@{qq} ")
        elif kind == "image":
            parts.append("[图片]")
        elif kind == "record":
            parts.append("[语音]")
        elif kind == "file":
            parts.append("[文件]")
        elif kind == "face":
            parts.append("[表情]")
    return "".join(parts).strip(), mentioned


def parse_event(
    event: dict[str, Any],
    *,
    group_mode: str = "mention",
    ingest_unmentioned: bool = False,
) -> dict[str, Any] | None:
    if str(event.get("post_type") or "") != "message":
        return None
    message_type = str(event.get("message_type") or "")
    if message_type not in ("group", "private"):
        return None
    self_id = str(event.get("self_id") or "")
    user_id = str(event.get("user_id") or "")
    if not user_id or user_id == self_id:
        return None
    content, mentioned = _parse_message(event.get("message"), self_id)
    if not content:
        return None
    mode = str(group_mode or "mention").lower()
    if message_type == "group":
        if mode == "off":
            return None
        if mode == "mention" and not mentioned:
            if not ingest_unmentioned:
                return None
        group_id = event.get("group_id")
        if group_id is None:
            return None
        scope = f"group:{group_id}"
        session = scope
    else:
        scope = "default"
        session = f"private:{user_id}"
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    return {
        "message_type": message_type,
        "message_id": event.get("message_id") or event.get("time") or int(time.time() * 1000),
        "self_id": self_id,
        "user_id": user_id,
        "platform_user_id": user_id,
        "group_id": event.get("group_id"),
        "scope": scope,
        "session": session,
        "content": content,
        "mentioned": mentioned,
        "should_reply": message_type != "group" or mentioned or mode == "all",
        "sender_name": str(sender.get("card") or sender.get("nickname") or user_id),
    }


class OneBotClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        timeout_s: float = 15.0,
        post: Callable[[str, dict[str, Any], dict[str, str]], bool] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = float(timeout_s)
        self._post = post

    def _default_post(self, url: str, payload: dict[str, Any],
                      headers: dict[str, str]) -> bool:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = json.loads(response.read().decode("utf-8", "replace") or "{}")
            return str(body.get("status") or "").lower() == "ok" and int(body.get("retcode", 0)) == 0
        except (urllib.error.URLError, TimeoutError, ValueError, TypeError):
            return False

    def _default_post_ex(self, url: str, payload: dict[str, Any],
                         headers: dict[str, str]) -> SendOutcome:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read().decode("utf-8", "replace") or "{}"
                body = json.loads(raw)
            retcode = int(body.get("retcode", 0) or 0)
            ok = str(body.get("status") or "").lower() == "ok" and retcode == 0
            return SendOutcome(ok=ok, retcode=retcode, http_status=200,
                               message=str(body.get("message") or body.get("wording") or "")[:200])
        except urllib.error.HTTPError as exc:
            return SendOutcome(ok=False, retcode=None, http_status=int(exc.code),
                               message=f"HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError) as exc:
            return SendOutcome(ok=False, retcode=None, http_status=None,
                               message=type(exc).__name__)
        except (ValueError, TypeError) as exc:
            return SendOutcome(ok=False, retcode=None, http_status=200,
                               message=f"bad response: {type(exc).__name__}")

    def send_ex(self, event: dict[str, Any], text: str) -> SendOutcome:
        """发一条消息，返回结构化结果（成功/重试/限流/致命 由调用方分类）。"""
        group = event.get("message_type") == "group"
        endpoint = "/send_group_msg" if group else "/send_private_msg"
        payload = {
            ("group_id" if group else "user_id"):
                event.get("group_id") if group else event.get("user_id"),
            "message": [{"type": "text", "data": {"text": str(text)}}],
        }
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self._post is not None:
            ok = bool(self._post(f"{self.base_url}{endpoint}", payload, headers))
            return SendOutcome(ok=ok, retcode=0 if ok else -1, http_status=200 if ok else None)
        return self._default_post_ex(f"{self.base_url}{endpoint}", payload, headers)

    def send(self, event: dict[str, Any], text: str) -> bool:
        group = event.get("message_type") == "group"
        endpoint = "/send_group_msg" if group else "/send_private_msg"
        payload = {
            ("group_id" if group else "user_id"):
                event.get("group_id") if group else event.get("user_id"),
            "message": [{"type": "text", "data": {"text": str(text)}}],
        }
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        post = self._post or self._default_post
        return bool(post(f"{self.base_url}{endpoint}", payload, headers))

    def call(self, action: str, payload: dict[str, Any] | None = None) -> Any:
        name = str(action or "").strip()
        if not _ACTION.fullmatch(name):
            raise OneBotCallError(name, "invalid OneBot action name")
        data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            f"{self.base_url}/{name}", data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = json.loads(
                    response.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read(1000).decode("utf-8", "replace")
            raise OneBotCallError(
                name,
                f"HTTP {exc.code}: {detail}",
                status=int(exc.code),
            ) from exc
        except (urllib.error.URLError, TimeoutError, ValueError, TypeError) as exc:
            raise OneBotCallError(name, str(exc)) from exc
        if not isinstance(body, dict):
            raise OneBotCallError(name, "OneBot response is not an object")
        try:
            retcode = int(body.get("retcode", 0))
        except (TypeError, ValueError):
            retcode = -1
        status = str(body.get("status") or "").lower()
        if retcode != 0 or status not in ("", "ok"):
            message = str(
                body.get("message") or body.get("wording")
                or body.get("msg") or f"OneBot action failed: {status}"
            )
            raise OneBotCallError(name, message, retcode=retcode)
        return body.get("data")


class OneBotBridge:
    def __init__(
        self,
        *,
        reply_fn: Callable[[dict[str, Any]], str],
        sender: Callable[[dict[str, Any], str], bool],
        ingest_fn: Callable[[dict[str, Any]], Any] | None = None,
        group_mode: str = "mention",
        dedupe_ttl_s: float = 300.0,
        configured: bool = False,
    ):
        self.reply_fn = reply_fn
        self.sender = sender
        self.ingest_fn = ingest_fn
        self.group_mode = group_mode
        self.dedupe_ttl_s = max(1.0, float(dedupe_ttl_s))
        self.configured = bool(configured)
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1000)
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats = {
            "accepted": 0, "ignored": 0, "duplicate": 0,
            "replied": 0, "ingested": 0, "failed": 0, "rejected": 0,
        }
        self.last_event: float | None = None
        self.last_reply: str = ""
        self.last_error: str = ""
        self.last_self_id: str = ""
        self.last_sender_name: str = ""

    def start(self) -> "OneBotBridge":
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._loop, name="aiji-onebot", daemon=True,
            )
            self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def submit(self, raw_event: dict[str, Any]) -> dict[str, Any]:
        event = parse_event(
            raw_event,
            group_mode=self.group_mode,
            ingest_unmentioned=self.ingest_fn is not None,
        )
        if event is None:
            self.stats["ignored"] += 1
            return {"status": "ignored"}
        now = time.time()
        key = str(event["message_id"])
        with self._lock:
            for old in [k for k, ts in self._seen.items()
                        if now - ts > self.dedupe_ttl_s]:
                self._seen.pop(old, None)
            if key in self._seen:
                self.stats["duplicate"] += 1
                return {"status": "duplicate", "message_id": key}
            self._seen[key] = now
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            with self._lock:
                self._seen.pop(key, None)
            self.stats["rejected"] += 1
            return {"status": "rejected", "reason": "queue_full"}
        self.stats["accepted"] += 1
        self.last_event = now
        self.last_self_id = event["self_id"]
        self.last_sender_name = event["sender_name"]
        return {"status": "accepted", "message_id": key}

    def _loop(self) -> None:
        while not self._stop.is_set():
            event = self._queue.get()
            if event is None:
                return
            try:
                if not event.get("should_reply", True):
                    if self.ingest_fn is None:
                        continue
                    self.ingest_fn(event)
                    self.stats["ingested"] += 1
                    continue
                reply = str(self.reply_fn(event) or "").strip()
                if not reply:
                    continue
                if self.sender(event, reply):
                    self.stats["replied"] += 1
                    self.last_reply = reply[:300]
                else:
                    self.stats["failed"] += 1
                    self.last_error = "send_failed"
            except Exception as exc:  # noqa: BLE001
                self.stats["failed"] += 1
                self.last_error = f"{type(exc).__name__}: {exc}"

    def snapshot(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "group_mode": self.group_mode,
            "queue_depth": self._queue.qsize(),
            "last_event": self.last_event,
            "last_reply": self.last_reply,
            "last_error": self.last_error,
            "last_self_id": self.last_self_id,
            "last_sender_name": self.last_sender_name,
            **self.stats,
        }


__all__ = [
    "OneBotBridge",
    "OneBotCallError",
    "OneBotClient",
    "parse_event",
]
