"""Low-overhead field-test monitor for AIJiYi + AstrBot + NapCat."""

from __future__ import annotations

import json
import socket
import sqlite3
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "aiji.db"
AGENT_DB = Path(r"C:\Users\22166\.agent2api\agent2api.db")
OUT = ROOT / "data" / "field_monitor.jsonl"
LOG_DIR = Path(r"D:\AstrBot")
PORTS = (6099, 6199, 6185, 8787)
ERROR_PATTERNS = (
    "Failed to send",
    "AIJiYi 群消息入库失败",
    "AIJiYi 调用失败",
    "retcode=1200",
    "ActionFailed",
)


def health() -> dict:
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:8787/health", timeout=5
        ) as response:
            return json.loads(response.read())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def db_snapshot() -> dict:
    try:
        conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS turns,
                MAX(ts) AS latest,
                SUM(role = 'assistant') AS assistant_turns,
                MAX(CASE WHEN role = 'assistant' THEN ts END) AS latest_assistant,
                MAX(CASE WHEN role = 'user' THEN ts END) AS latest_user
            FROM turn
            WHERE platform = 'qq'
            """
        ).fetchone()
        dead = conn.execute(
            "SELECT COUNT(*) FROM write_queue WHERE state='failed'"
        ).fetchone()[0]
        conn.close()
        now = time.time()
        latest_assistant = row["latest_assistant"]
        latest_user = row["latest_user"]
        reply_stalled = bool(
            latest_user is not None
            and latest_user >= now - 600
            and (
                latest_assistant is None
                or latest_assistant < now - 600
            )
        )
        return {
            "qq_turns": int(row["turns"]),
            "latest_turn": row["latest"],
            "qq_assistant_turns": int(row["assistant_turns"] or 0),
            "latest_assistant_turn": latest_assistant,
            "reply_stalled": reply_stalled,
            "dead": dead,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def agent_snapshot() -> dict:
    try:
        conn = sqlite3.connect(f"file:{AGENT_DB.as_posix()}?mode=ro", uri=True)
        now_ms = int(time.time() * 1000)
        recent = conn.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(SUM(total_tokens), 0),
                COALESCE(SUM(CASE WHEN status = 200 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN status = 408 THEN 1 ELSE 0 END), 0)
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


def latest_log() -> Path | None:
    files = sorted(
        (
            path
            for pattern in (
                "astrbot-fieldtest-*.out.log",
                "astrbot-stable-*.out.log",
            )
            for path in LOG_DIR.glob(pattern)
        ),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )
    return files[0] if files else None


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def read_log_increment(path: Path, offset: int = 0) -> str:
    try:
        stream = path.open("rb")
    except OSError:
        return ""
    try:
        stream.seek(max(0, int(offset)))
        raw = stream.read()
    finally:
        stream.close()
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def read_log(path: Path) -> str:
    return read_log_increment(path, 0)


def main() -> None:
    last_log_size = 0
    last_log_path: Path | None = None
    previous_pending = None
    while True:
        current = time.time()
        h = health()
        d = db_snapshot()
        a = agent_snapshot()
        log_path = latest_log()
        try:
            size = log_path.stat().st_size if log_path else 0
        except OSError:
            size = 0
        if log_path and (log_path != last_log_path or size != last_log_size):
            offset = (
                last_log_size
                if log_path == last_log_path and size >= last_log_size
                else 0
            )
            text = read_log_increment(log_path, offset)
        else:
            text = ""
        last_log_path = log_path
        last_log_size = size
        ports = {str(port): port_open(port) for port in PORTS}
        errors = []
        if text:
            for line in text.splitlines():
                if any(marker in line for marker in ERROR_PATTERNS):
                    errors.append(line[-500:])
        pending = h.get("pending")
        mcp = h.get("mcp_research")
        sticker_update = h.get("sticker_update")
        alert = []
        if not h.get("ok"):
            alert.append("aiji_health_failed")
        if not ports["8787"]:
            alert.append("aiji_port_down")
        if not ports["6199"]:
            alert.append("onebot_disconnected")
        if not ports["6185"]:
            alert.append("astrbot_down")
        if not ports["6099"]:
            alert.append("napcat_webui_down")
        if isinstance(pending, int) and pending >= 80:
            alert.append("writer_backlog_high")
        if (
            isinstance(pending, int)
            and isinstance(previous_pending, int)
            and pending > previous_pending + 20
        ):
            alert.append("writer_backlog_rising")
        if d.get("reply_stalled"):
            alert.append("assistant_reply_stalled")
        if h.get("ok") and mcp is None:
            alert.append("mcp_research_unavailable")
        if isinstance(mcp, dict):
            if not mcp.get("running"):
                alert.append("mcp_research_loop_down")
            if mcp.get("last_error"):
                alert.append("mcp_research_error")
        if isinstance(sticker_update, dict) and sticker_update.get("failures"):
            alert.append("sticker_update_failed")
        if isinstance(a, dict) and not a.get("error"):
            requests_30m = int(a.get("last_30m_requests") or 0)
            failed_30m = int(a.get("last_30m_408") or 0)
            tokens_30m = int(a.get("last_30m_tokens") or 0)
            if tokens_30m >= 200_000:
                alert.append("agent2api_token_rate_high")
            if requests_30m >= 20 and failed_30m / requests_30m >= 0.35:
                alert.append("agent2api_timeout_rate_high")
        elif isinstance(a, dict) and a.get("error"):
            alert.append("agent2api_usage_unavailable")
        if errors:
            alert.append("transport_or_model_errors")
        payload = {
            "ts": current,
            "health": {
                "ok": h.get("ok"),
                "pending": pending,
                "queue_depth": h.get("queue_depth"),
                "facts": h.get("facts"),
                "pool": h.get("pool"),
                "sticker_update": sticker_update,
                "mcp_research": mcp,
                "error": h.get("error"),
            },
            "db": d,
            "agent2api": a,
            "ports": ports,
            "log": str(log_path) if log_path else "",
            "alerts": alert,
            "log_errors": errors[-8:],
        }
        with OUT.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        previous_pending = pending if isinstance(pending, int) else None
        time.sleep(15)


if __name__ == "__main__":
    main()
