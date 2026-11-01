"""Thirty-minute field audit for AIJiYi, MCP, stickers and Agent2API spend."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path


ROOT = Path(r"D:\AIJiYi")
AIJI_DB = ROOT / "data" / "aiji.db"
AGENT_DB = Path(r"C:\Users\22166\.agent2api\agent2api.db")
OUT = ROOT / "data" / "field_audit_30m.jsonl"
SUMMARY = ROOT / "data" / "field_audit_30m_summary.json"
LOG = ROOT / "data" / "field_audit_30m.log"
ERROR_LOG = ROOT / "data" / "field_audit_30m.err.log"
DURATION_S = 30 * 60
INTERVAL_S = 60


def _health() -> dict:
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8787/health", timeout=5,
        ) as response:
            return json.loads(response.read())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _aiji_snapshot() -> dict:
    try:
        conn = sqlite3.connect(f"file:{AIJI_DB.as_posix()}?mode=ro", uri=True)
        roles = dict(conn.execute(
            "SELECT role, COUNT(*) FROM turn WHERE platform='qq' GROUP BY role"
        ).fetchall())
        latest = conn.execute(
            "SELECT MAX(ts) FROM turn WHERE platform='qq' AND role='assistant'"
        ).fetchone()[0]
        dead = conn.execute(
            "SELECT COUNT(*) FROM write_queue WHERE state='failed'"
        ).fetchone()[0]
        conn.close()
        return {
            "qq_user": int(roles.get("user", 0)),
            "qq_assistant": int(roles.get("assistant", 0)),
            "latest_assistant": latest,
            "dead": int(dead),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _agent_snapshot() -> dict:
    try:
        conn = sqlite3.connect(f"file:{AGENT_DB.as_posix()}?mode=ro", uri=True)
        now_ms = int(time.time() * 1000)
        recent = conn.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(SUM(total_tokens), 0),
                COALESCE(SUM(CASE WHEN status = 200 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN status = 408 THEN 1 ELSE 0 END), 0),
                MAX(ts)
            FROM requests
            WHERE ts >= ?
            """,
            (now_ms - 30 * 60 * 1000,),
        ).fetchone()
        daily = conn.execute(
            "SELECT date, requests, successful, tokens "
            "FROM request_daily ORDER BY date DESC LIMIT 1"
        ).fetchone()
        balances = [
            {
                "provider": str(provider),
                "accounts": int(count),
                "remaining": round(float(remaining or 0), 2),
            }
            for provider, count, remaining in conn.execute(
                """
                SELECT a.provider, COUNT(*), COALESCE(SUM(u.remaining), 0)
                FROM accounts a
                LEFT JOIN account_usage_records u ON u.account_id = a.id
                WHERE a.enabled = 1
                GROUP BY a.provider
                ORDER BY a.provider
                """
            )
        ]
        conn.close()
        return {
            "last_30m_requests": int(recent[0]),
            "last_30m_tokens": int(recent[1]),
            "last_30m_200": int(recent[2]),
            "last_30m_408": int(recent[3]),
            "latest_request_ms": recent[4],
            "today": {
                "date": daily[0],
                "requests": int(daily[1]),
                "successful": int(daily[2]),
                "tokens": int(daily[3]),
            } if daily else {},
            "balances": balances,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def sample() -> dict:
    health = _health()
    return {
        "ts": time.time(),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "health": {
            "ok": health.get("ok"),
            "pending": health.get("pending"),
            "queue_depth": health.get("queue_depth"),
            "mcp": health.get("mcp_research"),
            "stickers": health.get("sticker_update"),
            "error": health.get("error"),
        },
        "aiji": _aiji_snapshot(),
        "agent2api": _agent_snapshot(),
    }


def worker() -> None:
    started = time.time()
    deadline = started + DURATION_S
    samples: list[dict] = []
    while time.time() < deadline:
        value = sample()
        samples.append(value)
        with OUT.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")
        time.sleep(min(INTERVAL_S, max(0.0, deadline - time.time())))
    first = samples[0] if samples else {}
    last = samples[-1] if samples else {}
    first_daily = (first.get("agent2api") or {}).get("today") or {}
    last_daily = (last.get("agent2api") or {}).get("today") or {}
    summary = {
        "started_at": started,
        "finished_at": time.time(),
        "samples": len(samples),
        "health_failures": sum(
            1 for item in samples
            if not (item.get("health") or {}).get("ok")
        ),
        "max_pending": max(
            (
                int((item.get("health") or {}).get("pending") or 0)
                for item in samples
            ),
            default=0,
        ),
        "qq_assistant_first": (
            (first.get("aiji") or {}).get("qq_assistant")),
        "qq_assistant_last": (
            (last.get("aiji") or {}).get("qq_assistant")),
        "agent_requests_delta": (
            int(last_daily.get("requests") or 0)
            - int(first_daily.get("requests") or 0)
        ),
        "agent_tokens_delta": (
            int(last_daily.get("tokens") or 0)
            - int(first_daily.get("tokens") or 0)
        ),
        "last_sample": last,
    }
    SUMMARY.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    if "--worker" in sys.argv:
        worker()
        return
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with LOG.open("ab", buffering=0) as stdout, (
        ERROR_LOG.open("ab", buffering=0)
    ) as stderr:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__)), "--worker"],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=flags,
            close_fds=True,
        )
    print(f"AUDIT_PID={process.pid}")
    print(f"OUT={OUT}")
    print(f"SUMMARY={SUMMARY}")


if __name__ == "__main__":
    main()
