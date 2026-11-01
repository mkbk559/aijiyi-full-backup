"""Octop Gateway → AIJiYi 桥接。

Octop 负责 QQ/飞书/微信等平台协议，AijiGatewayClient 只负责把归一化消息
POST 到 AIJiYi 的 /v1/gateway/messages，再把回复交回平台。
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


def inbound_payload(message: Any) -> dict[str, Any]:
    metadata = dict(getattr(message, "metadata", {}) or {})
    subject = getattr(message, "channel_subject", None)
    subject_id = str(getattr(subject, "subject_id", "") or "")
    chat_type = str(
        getattr(subject, "chat_type", "") or metadata.get("chat_type") or ""
    ).lower()
    group = chat_type in ("group", "guild", "channel")
    sender = str(
        metadata.get("sender_id") or metadata.get("user_openid")
        or subject_id or "unknown"
    )
    session = str(
        getattr(message, "channel_session_id", "") or subject_id or sender
    )
    text = str(getattr(message, "text", "") or "").strip()
    if not text and getattr(message, "has_media", False):
        text = "[用户发送了媒体消息]"
    return {
        "platform": str(getattr(message, "channel_type", "") or "im"),
        "platform_user_id": sender,
        "scope": f"group:{subject_id}" if group and subject_id else "default",
        "session": session,
        "content": text,
        "debounce": True,
    }


class AijiGatewayClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8787",
        *,
        api_key: str = "",
        timeout_s: float = 180.0,
        post: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = float(timeout_s)
        self._post = post

    async def _default_post(self, url: str, *, json: dict[str, Any],
                            headers: dict[str, str]) -> dict[str, Any]:
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=self.timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=json, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                return data if isinstance(data, dict) else {}

    async def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        post = self._post or self._default_post
        return await post(
            f"{self.base_url}/v1/gateway/messages",
            json=payload,
            headers=headers,
        )


def make_processor(client: AijiGatewayClient):
    """创建 Octop 兼容的 async generator；Octop 类型在运行时才导入。"""
    async def processor(message: Any) -> AsyncIterator[Any]:
        from octop_gateway import MessageEvent

        payload = inbound_payload(message)
        if not payload["content"]:
            yield MessageEvent.text("这条消息没有可识别的文字。")
            yield MessageEvent.completed()
            return
        try:
            result = await client.chat(payload)
            text = str(result.get("text") or "").strip()
            if not text:
                text = "（吐了个泡泡）这次没接上话，稍后再试。"
        except Exception as exc:  # noqa: BLE001
            text = f"（吐了个泡泡）本地 AIJiYi 暂时没连上：{type(exc).__name__}"
        yield MessageEvent.text(text)
        yield MessageEvent.completed()

    return processor


async def run_qq(client: AijiGatewayClient, *, sandbox: bool = False) -> None:
    from octop_gateway import ChannelManager, FileSystemMediaBackend
    from octop_gateway.channels.qq import QQConfig

    app_id = os.environ.get("QQ_APP_ID", "").strip()
    token = os.environ.get("QQ_TOKEN", "").strip()
    secret = os.environ.get("QQ_SECRET", "").strip()
    if not app_id or not secret:
        raise SystemExit("QQ_APP_ID and QQ_SECRET are required (QQ_TOKEN is optional)")

    media = FileSystemMediaBackend(".tmp/octop-media")
    manager = ChannelManager(
        processor=make_processor(client),
        workers_per_channel=4,
        media_backend=media,
    )
    await manager.start()
    try:
        channel_id = await manager.add_qq_channel(QQConfig(
            app_id=app_id, token=token, secret=secret, sandbox=sandbox,
        ))
        print(f"qq bridge online channel_id={channel_id}", flush=True)
        await asyncio.Event().wait()
    finally:
        await manager.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Octop Gateway to AIJiYi bridge")
    parser.add_argument("--core-url", default=os.environ.get(
        "AIJI_CORE_URL", "http://127.0.0.1:8787"))
    parser.add_argument("--api-key", default=os.environ.get("AIJI_API_KEY", ""))
    parser.add_argument(
        "--sandbox", action="store_true",
        default=os.environ.get("QQ_SANDBOX", "").lower() in ("1", "true", "yes", "on"),
    )
    args = parser.parse_args(argv)
    client = AijiGatewayClient(args.core_url, api_key=args.api_key)
    try:
        asyncio.run(run_qq(client, sandbox=args.sandbox))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["AijiGatewayClient", "inbound_payload", "main", "make_processor", "run_qq"]
