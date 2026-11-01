"""Audit-only by default; explicitly apply cleanup of known synthetic identities."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


_IDENTITY_TABLES = (
    "account", "fact_link", "fact_log", "fact", "profile", "style", "turn", "summary", "media",
    "injection", "outbox", "send_queue", "heart_state", "event_log",
    "life_card", "trait", "reflection", "write_queue",
)
_KNOWN_TEST_IDS = frozenset((
    "4242", "777", "888", "live-user", "probe2", "smoke_a",
))


def is_test_identity(platform: str, platform_user_id: str) -> bool:
    p = str(platform or "").lower()
    uid = str(platform_user_id or "").strip()
    if p == "qq_user_hist":
        return True
    if p != "qq":
        return False
    if uid in _KNOWN_TEST_IDS:
        return True
    if uid.startswith((
        "99000", "style_", "probe", "learn_", "smoke_", "live-", "sunba_",
    )):
        return True
    if any(marker in uid.lower() for marker in ("probe", "style_check", "style_final", "style_test")):
        return True
    return not bool(re.fullmatch(r"[1-9]\d{5,11}", uid))


def _identities(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for table in _IDENTITY_TABLES:
        try:
            rows = conn.execute(
                f'SELECT DISTINCT platform,platform_user_id FROM "{table}"'
            ).fetchall()
        except sqlite3.DatabaseError:
            continue
        found.update((str(r[0]), str(r[1])) for r in rows)
    return sorted(found)


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    target = path.with_name(
        f"{path.name}.bak-before-test-cleanup-{stamp}-{uuid.uuid4().hex[:6]}")
    source = sqlite3.connect(path)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    return target


def cleanup_test_identities(path: str | Path, *, apply: bool = False) -> dict[str, Any]:
    db_path = Path(path).expanduser().resolve()
    conn = sqlite3.connect(db_path)
    try:
        identities = [
            item for item in _identities(conn)
            if is_test_identity(*item)
        ]
        result: dict[str, Any] = {
            "db": str(db_path),
            "apply": bool(apply),
            "identities": identities,
            "deleted": {},
            "backup": "",
        }
        if not apply or not identities:
            return result
        conn.close()
        result["backup"] = str(_backup(db_path))
        conn = sqlite3.connect(db_path)
        with conn:
            for table in _IDENTITY_TABLES:
                total = 0
                for platform, uid in identities:
                    try:
                        cur = conn.execute(
                            f'DELETE FROM "{table}" WHERE platform=? AND platform_user_id=?',
                            (platform, uid),
                        )
                    except sqlite3.DatabaseError:
                        total = -1
                        break
                    total += max(0, int(cur.rowcount))
                result["deleted"][table] = total
        return result
    finally:
        conn.close()


def fix_legacy_media_scopes(path: str | Path, *, apply: bool = False) -> dict[str, Any]:
    db_path = Path(path).expanduser().resolve()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id,platform,platform_user_id,scope,ref FROM media"
            " WHERE scope='private' ORDER BY id"
        ).fetchall()
        movable = []
        for row in rows:
            exists = conn.execute(
                "SELECT 1 FROM media WHERE platform=? AND platform_user_id=?"
                " AND scope='default' AND ref=? LIMIT 1",
                (row[1], row[2], row[4]),
            ).fetchone()
            if not exists:
                movable.append(row)
        result: dict[str, Any] = {
            "db": str(db_path),
            "apply": bool(apply),
            "candidates": [int(row[0]) for row in movable],
            "updated": 0,
            "backup": "",
        }
        if not apply or not movable:
            return result
        conn.close()
        result["backup"] = str(_backup(db_path))
        conn = sqlite3.connect(db_path)
        with conn:
            for row in movable:
                conn.execute(
                    "UPDATE media SET scope='default' WHERE id=?", (row[0],))
        result["updated"] = len(movable)
        return result
    finally:
        conn.close()


def backfill_account_labels(path: str | Path, *, apply: bool = False) -> dict[str, Any]:
    db_path = Path(path).expanduser().resolve()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT a.platform,a.platform_user_id,a.extra,"
            " (SELECT t.extra FROM turn t"
            "  WHERE t.platform=a.platform"
            "   AND t.platform_user_id=a.platform_user_id"
            "   AND t.role='user'"
            "  ORDER BY t.id DESC LIMIT 1) AS turn_extra"
            " FROM account a"
            " WHERE COALESCE(a.display_name,'')=''"
        ).fetchall()
        updates = []
        for row in rows:
            try:
                turn_extra = json.loads(row["turn_extra"] or "{}")
            except (TypeError, ValueError):
                turn_extra = {}
            name = " ".join(str(turn_extra.get("sender_name") or "").split())[:80]
            if not name:
                continue
            try:
                extra = json.loads(row["extra"] or "{}")
            except (TypeError, ValueError):
                extra = {}
            avatar = str(turn_extra.get("sender_avatar") or "").strip()
            card = " ".join(str(turn_extra.get("sender_card") or "").split())[:80]
            if avatar:
                extra["avatar"] = avatar[:2000]
            if card:
                extra["card"] = card
            updates.append((
                name,
                json.dumps(extra, ensure_ascii=False),
                row["platform"],
                row["platform_user_id"],
            ))
        result: dict[str, Any] = {
            "db": str(db_path),
            "apply": bool(apply),
            "updated": 0,
            "identities": [
                [item[2], item[3]] for item in updates
            ],
            "backup": "",
        }
        if not apply or not updates:
            return result
        conn.close()
        result["backup"] = str(_backup(db_path))
        conn = sqlite3.connect(db_path)
        with conn:
            conn.executemany(
                "UPDATE account SET display_name=?,extra=?,updated_at=?"
                " WHERE platform=? AND platform_user_id=?",
                [
                    (name, extra, time.time(), platform, uid)
                    for name, extra, platform, uid in updates
                ],
            )
        result["updated"] = len(updates)
        return result
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/aiji.db")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--fix-media", action="store_true")
    parser.add_argument("--fix-account-labels", action="store_true")
    args = parser.parse_args()
    result = cleanup_test_identities(args.db, apply=args.apply)
    payload: dict[str, Any] = {"identities": result}
    if args.fix_media:
        payload["media_scopes"] = fix_legacy_media_scopes(
            args.db, apply=args.apply)
    if args.fix_account_labels:
        payload["account_labels"] = backfill_account_labels(
            args.db, apply=args.apply)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
