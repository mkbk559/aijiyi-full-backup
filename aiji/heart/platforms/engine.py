"""平台层编排：把适配器动作接到记忆与账本上。

两件事：
1. **刷视频学习**：`feed()` 拿到一批内容 → 压成一段文本 → 走同一条记忆抽取管线
   （不新开管线：这样"刷到的"和"聊到的"在同一个库里，注入时能互相引用）。
2. **续火花**：账本里"昨天还连着、今天还没互动"的 peer → 让 brain 生成一句真实由头
   → 发出去 → 记账。发送失败**不记账**（账本只记真的发生了的事）。
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable, Iterable

from aiji.heart.platforms.base import Adapter, PlatformError
from aiji.heart.platforms.streak import StreakBook, day_of

MAX_TEXT = 4000


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def feed_text(platform: str, rows: Iterable[dict[str, Any]], *,
              topic: str = "") -> str:
    """把一批内容压成学习文本。字段名统一到 title/desc/text。"""
    lines = []
    head = f"在{platform}刷到这些内容" + (f"（{topic}）" if topic else "") + "："
    lines.append(head)
    n = 0
    for row in rows:
        n += 1
        parts = [
            _clean(row.get("title") or ""),
            _clean(row.get("text") or ""),
            _clean(row.get("desc") or ""),
        ]
        body = "；".join(p for p in parts if p)
        owner = _clean(row.get("owner") or "")
        url = _clean(row.get("url") or "")
        line = f"{n}. {body or url}"
        if owner:
            line += f"（{owner}）"
        lines.append(line[:400])
    return "\n".join(lines)


def _feed_kwargs(adapter: Adapter, kwargs: dict[str, Any]) -> dict[str, Any]:
    """只把这台适配器真认的参数传下去（B 站没有 keyword，抖音才有）。"""
    try:
        params = inspect.signature(adapter.feed).parameters  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        return {}
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return dict(kwargs)
    return {k: v for k, v in kwargs.items() if k in params}


def learn_feed(adapter: Adapter, memory: Any, ident: Any, *,
               limit: int = 8, topic: str = "", flush_timeout: float = 20.0,
               **feed_kwargs: Any) -> dict[str, Any]:
    """刷一批内容并写进记忆。返回 {ok, count, text_chars, flushed, items}。"""
    rows = adapter.feed(limit, **_feed_kwargs(adapter, feed_kwargs))  # type: ignore[attr-defined]
    text = feed_text(adapter.name, rows, topic=topic)
    if not rows:
        return {"ok": False, "code": "empty_feed", "count": 0, "items": []}
    ref = f"{adapter.name}:{day_of()}"
    memory.ingest(ident, kind="link", ref=ref, text=text[:MAX_TEXT],
                  url=_clean(rows[0].get("url") or ""), session="learn-feed",
                  extra={"source": f"{adapter.name}_feed", "count": len(rows),
                         "topic": topic, "learn": True})
    flushed = memory.flush(flush_timeout)
    return {
        "ok": True, "count": len(rows), "text_chars": len(text),
        "flushed": bool(flushed), "ref": ref,
        "items": [{k: row.get(k) for k in ("title", "text", "url", "video_id", "bvid")}
                  for row in rows],
    }


def streak_sweep(adapter: Adapter, book: StreakBook, *,
                 platform: str, peers: Iterable[str],
                 message_for: Callable[[str, dict[str, Any]], str],
                 limit: int = 5, dry_run: bool = True) -> dict[str, Any]:
    """给"今天该续"的 peer 发消息。

    `message_for(peer, row) -> str` 负责"说什么"（brain/persona 在外面）；
    这里只管挑人、发、记账。dry-run 时**不记账**，因为什么都没发生。
    """
    day = day_of(book.clock())
    due = {str(r["peer"]): r for r in book.due(platform, day=day, limit=200)}
    sent, planned, failed = [], [], []
    for peer in peers:
        peer = str(peer)
        if peer not in due:
            continue
        row = due[peer]
        if len(planned) + len(sent) >= limit:
            break
        try:
            text = str(message_for(peer, row) or "").strip()
        except Exception as exc:  # noqa: BLE001 - 生成失败不该拖垮整轮
            failed.append({"peer": peer, "code": "message_failed",
                           "error": str(exc)[:200]})
            continue
        if not text:
            failed.append({"peer": peer, "code": "empty_message"})
            continue
        try:
            res = adapter.dm(peer, text, dry_run=dry_run)  # type: ignore[attr-defined]
        except PlatformError as exc:
            failed.append({"peer": peer, "code": exc.code, "error": exc.message})
            continue
        item = {"peer": peer, "text": text, "days_before": row["days"],
                "dry_run": bool(getattr(res, "dry_run", False))}
        if getattr(res, "dry_run", False):
            planned.append(item)
            continue
        touched = book.touch(platform, peer, day=day)
        item["days"] = touched["days"]
        sent.append(item)
    return {"ok": not failed, "day": day, "sent": sent, "planned": planned,
            "failed": failed, "dry_run": bool(dry_run)}
