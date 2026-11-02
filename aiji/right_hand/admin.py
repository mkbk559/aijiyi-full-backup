"""控制台只读数据面：把分散在内核表里的状态整理成 UI 可直接消费的 JSON。"""

from __future__ import annotations

import json
import importlib.util
from dataclasses import asdict
from typing import Any

from aiji.hippocampus import store
from aiji.hippocampus.db import DB
from aiji.hippocampus.store import Ident


def _json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) and value else default
    except (TypeError, ValueError):
        return default


def _fact_row(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "kind": row["kind"] or "fact",
        "subject": row["subject"],
        "predicate": row["predicate"],
        "object": row["object"],
        "text": row["text"],
        "confidence": float(row["confidence"] or 0),
        "importance": float(row["importance"] or 0),
        "mentions": int(row["mentions"] or 1),
        "hit_count": int(row["hit_count"] or 0),
        "status": row["status"],
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
        "updated_at": row["updated_at"],
        "extra": _json(row["extra"], {}),
    }


def memory_library(db: DB, ident: Ident, *, query: str = "",
                   limit: int = 200) -> dict[str, Any]:
    ident = Ident.coerce(ident)
    if query.strip():
        items = [asdict(fact) for fact in store.search_facts(
            db.conn(), ident, query.strip(), limit=limit,
        )]
    else:
        w, args = ident.where()
        rows = db.read(
            f"SELECT * FROM fact WHERE {w} AND status='active'"
            " ORDER BY updated_at DESC LIMIT ?",
            (*args, int(limit)),
        )
        items = [_fact_row(row) for row in rows]
    return {"items": items, "query": query, "identity": {
        "platform": ident.platform,
        "platform_user_id": ident.platform_user_id,
        "scope": ident.scope,
    }}


def memory_graph(db: DB, ident: Ident, *, limit: int = 400) -> dict[str, Any]:
    ident = Ident.coerce(ident)
    w, args = ident.where("f")
    rows = db.read(
        f"SELECT f.* FROM fact f WHERE {w} AND f.status='active'"
        " ORDER BY f.importance DESC, f.updated_at DESC LIMIT ?",
        (*args, int(limit)),
    )
    nodes = [{
        "id": int(row["id"]),
        "text": row["text"],
        "kind": row["kind"] or "fact",
        "predicate": row["predicate"],
        "confidence": float(row["confidence"] or 0),
        "importance": float(row["importance"] or 0),
    } for row in rows]
    ids = {node["id"] for node in nodes}
    lw, largs = ident.where("l")
    edge_rows = db.read(
        f"SELECT l.src_id,l.dst_id,l.weight,l.reason FROM fact_link l"
        f" WHERE {lw} ORDER BY l.weight DESC LIMIT ?",
        (*largs, max(1, int(limit) * 4)),
    )
    edges = [{
        "source": int(row["src_id"]),
        "target": int(row["dst_id"]),
        "weight": float(row["weight"] or 0),
        "reason": row["reason"] or "",
    } for row in edge_rows
        if int(row["src_id"]) in ids and int(row["dst_id"]) in ids]
    return {"nodes": nodes, "edges": edges}


def _status_counts(db: DB, table: str, column: str = "status") -> dict[str, int]:
    rows = db.read(f"SELECT {column} AS state, COUNT(*) AS n FROM {table} GROUP BY {column}")
    return {str(row["state"]): int(row["n"]) for row in rows}


def scheduler_snapshot(db: DB, ident: Ident | None, *, limit: int = 20) -> dict[str, Any]:
    if ident is None:
        w, args = "1=1", ()
    else:
        w, args = Ident.coerce(ident).where()
    recent_outbox = [{
        "id": int(row["id"]),
        "kind": row["kind"],
        "priority": int(row["priority"]),
        "status": row["status"],
        "attempts": int(row["attempts"]),
        "updated_at": row["updated_at"],
        "payload": _json(row["payload"], {}),
    } for row in db.read(
        f"SELECT * FROM outbox WHERE {w} ORDER BY id DESC LIMIT ?",
        (*args, int(limit)),
    )]
    recent_send = [{
        "id": int(row["id"]),
        "kind": row["kind"],
        "priority": int(row["priority"]),
        "status": row["status"],
        "attempts": int(row["attempts"]),
        "created_at": row["created_at"],
        "payload": _json(row["payload"], {}),
    } for row in db.read(
        f"SELECT * FROM send_queue WHERE {w} ORDER BY id DESC LIMIT ?",
        (*args, int(limit)),
    )]
    return {
        "outbox": _status_counts(db, "outbox"),
        "send_queue": _status_counts(db, "send_queue"),
        "write_queue": _status_counts(db, "write_queue", "state"),
        "recent_outbox": recent_outbox,
        "recent_send": recent_send,
    }


def models_snapshot(routes: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = []
    for route in routes:
        item = dict(route)
        item.setdefault("route", item.get("name", ""))
        normalized.append(item)
    now = [route for route in normalized if float(route.get("cooldown_s") or 0) <= 0]
    return {
        "routes": normalized,
        "total": len(normalized),
        "active": len(now),
        "cooldown": len(normalized) - len(now),
    }


def recent_turns_snapshot(db: DB, ident: Ident, session: str | None = None,
                          limit: int = 40) -> dict[str, Any]:
    ident = Ident.coerce(ident)
    if session:
        items = store.recent_turns(db.conn(), ident, session, limit)
    else:
        w, args = ident.where()
        items = [dict(row) for row in db.read(
            f"SELECT id,role,content,session,ts,extra FROM turn WHERE {w}"
            " ORDER BY id DESC LIMIT ?",
            (*args, int(limit)),
        )]
        for item in items:
            item["extra"] = _json(item.get("extra"), {})
    return {"items": items}


def injection_snapshot(db: DB, ident: Ident, *, limit: int = 30) -> dict[str, Any]:
    ident = Ident.coerce(ident)
    w, args = ident.where()
    rows = db.read(
        f"SELECT id,session,query,item_ids,prompt,feedback,created_at FROM injection"
        f" WHERE {w} ORDER BY id DESC LIMIT ?",
        (*args, int(limit)),
    )
    return {"items": [{
        "id": int(row["id"]),
        "session": row["session"],
        "query": row["query"],
        "item_ids": _json(row["item_ids"], []),
        "prompt": row["prompt"],
        "feedback": _json(row["feedback"], None),
        "created_at": row["created_at"],
    } for row in rows]}


def proactive_snapshot(db: DB) -> dict[str, Any]:
    rows = db.read(
        "SELECT platform,platform_user_id,scope,state_key,day_bucket,value,updated_at"
        " FROM heart_state ORDER BY updated_at DESC LIMIT 200"
    )
    by_identity: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["platform"], row["platform_user_id"], row["scope"])
        item = by_identity.setdefault(key, {
            "platform": key[0],
            "platform_user_id": key[1],
            "scope": key[2],
            "state": {},
        })
        item["state"][row["state_key"]] = _json(row["value"], row["value"])
        item["updated_at"] = row["updated_at"]
    return {"items": list(by_identity.values())}


def capabilities_snapshot(memory: Any) -> dict[str, Any]:
    from aiji.hippocampus import real_engines
    routes = [{
        "name": route.name,
        "model": route.model,
        "base_url": route.base_url,
        "key_configured": bool(route.api_key),
    } for route in memory.settings.routes]
    onebot = {
        **memory.onebot.snapshot(),
        "api": memory.settings.onebot_api,
        "webui": memory.settings.onebot_webui,
    }
    return {
        "ocr": bool(real_engines.HAS_OCR),
        "asr": {
            "funasr": bool(real_engines.HAS_FUNASR),
            "whisper": bool(real_engines.HAS_ASR),
            "model": memory.settings.asr_model,
        },
        "openai_api": True,
        "websocket": memory.presence.port,
        "gateway_webhook": True,
        "octop_gateway": bool(importlib.util.find_spec("octop_gateway")),
        "onebot": onebot,
        "platforms": memory.stats()["store"]["platforms"],
        "settings": {
            **memory.settings.describe(),
            "routes": routes,
        },
    }


__all__ = [
    "capabilities_snapshot", "injection_snapshot", "memory_graph",
    "memory_library", "models_snapshot", "proactive_snapshot",
    "recent_turns_snapshot", "scheduler_snapshot",
]
