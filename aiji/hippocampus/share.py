"""跨平台共享记忆（决策见 docs/agent/design/memory-sharing-2026-11-05.md）。

一句话：**一个主人，三个平台外壳**。

- 写：主人自己的每一轮对话，除了写进平台身份，再**镜像**一份到 `SHARED`；
- 读：`SHARED` ∪ 当前平台身份，按文本去重、分数取高；
- 别人（群里陌生人）永远只落平台身份，不进 `SHARED`——隔离语义没变。

为什么用"镜像"而不是"干脆不分平台"：`Ident.where()` 是所有查询唯一的隔离闸门，去掉它
等于把陌生人消息混进主人记忆。镜像只是加法，出问题停掉镜像就回滚。
"""

from __future__ import annotations

from typing import Any, Iterable

from aiji.hippocampus.store import Ident

SHARED = Ident("shared", "owner", "global")

# 哪些平台算"主人自己的入口"。机器人只在这些平台替主人说话。
OWNER_PLATFORMS = ("qq", "douyin", "bilibili", "local")


def is_shared(ident: Ident) -> bool:
    return ident.platform == SHARED.platform


def mirror_ident(ident: Ident) -> Ident | None:
    """需要镜像就返回 SHARED，否则 None（本身已是 shared 就别再镜像）。"""
    if is_shared(ident):
        return None
    if ident.platform not in OWNER_PLATFORMS:
        return None
    return SHARED


def shared_session(ident: Ident, session: str) -> str:
    """共享层里的会话名带来源平台，方便回读时知道"这话是在哪儿说的"。"""
    return f"{ident.platform}:{session}"


def add_turn_pair(conn: Any, store: Any, ident: Ident, session: str, role: str,
                  content: str, **kw: Any) -> None:
    """平台身份写一条 + 共享身份镜像一条（同一事务里，由调用方的 tx 保证原子）。"""
    store.add_turn(conn, ident, session, role, content, **kw)
    mirror = mirror_ident(ident)
    if mirror is None:
        return
    extra = dict(kw.pop("extra", None) or {})
    extra["shared_from"] = ident.platform
    store.add_turn(conn, mirror, shared_session(ident, session), role, content,
                   extra=extra, **kw)


def _hit_key(hit: Any) -> tuple[str, str]:
    return (str(getattr(hit, "kind", "")), str(getattr(hit, "text", ""))[:40])


def recall_union(recaller: Any, query: str, ident: Ident, **kw: Any) -> Any:
    """平台身份 ∪ 共享身份。去重按 (kind, 文本前 40 字)，分数取高，按分数重排。"""
    base = recaller.recall(query, ident, **kw)
    mirror = mirror_ident(ident)
    if mirror is None:
        return base
    try:
        # 共享层要**跨 session** 取：抖音那句存在 `local:douyin:group:测试` 里，
        # 而 QQ 这边问的时候 session 是 `qq:...`，带上 session 就等于自废武功
        # （2026-11-05 真机踩到：union 后仍然 0 条）。
        kw_shared = dict(kw)
        kw_shared["session"] = None
        more = recaller.recall(query, mirror, **kw_shared)
    except Exception as exc:  # noqa: BLE001 - 共享层坏了不能拖垮主链路
        base.degraded.append(f"shared_error:{type(exc).__name__}")
        return base
    merged: dict[tuple[str, str], Any] = {}
    for hit in list(base.hits) + list(more.hits):
        key = _hit_key(hit)
        old = merged.get(key)
        if old is None or float(getattr(hit, "score", 0)) > float(getattr(old, "score", 0)):
            merged[key] = hit
    hits = sorted(merged.values(), key=lambda h: float(getattr(h, "score", 0)),
                  reverse=True)
    base.hits = hits
    base.degraded.append("shared_union")
    return base


def merge_shared_into(items: Iterable[Any]) -> list[Any]:
    """把两批已取回的记忆条目去重合并（给不走 recall 的调用方用）。"""
    seen: set[tuple[str, str]] = set()
    out: list[Any] = []
    for hit in items:
        key = _hit_key(hit)
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out
