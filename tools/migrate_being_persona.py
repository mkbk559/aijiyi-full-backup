"""把生命层（being_*）数据从旧人格 sunba 迁到 child，并做备份与前后核对。

背景：人格默认改成 child 后，`being_state/being_event/being_note` 是按 persona 分区的，
库里 1001 条事件 + 30 条笔记全挂在 sunba 下 → 新人格"失忆"（生命层读不到历史）。
这是同一个人换了名字，不是换人，所以迁移而不是清空。

用法：python tools/migrate_being_persona.py [--dry]
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "aiji.db"
OLD, NEW = "sunba", "child"


def counts(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for table in ("being_state", "being_event", "being_note"):
        out[table] = dict(
            (str(k), v) for k, v in conn.execute(
                f"select persona, count(*) from {table} group by persona"))
    return out


def main() -> None:
    dry = "--dry" in __import__("sys").argv
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = DB.with_name(f"aiji.db.bak-before-being-migrate-{stamp}")
    if not dry:
        shutil.copy2(DB, backup)
        print("backup →", backup.name)

    conn = sqlite3.connect(str(DB))
    try:
        before = counts(conn)
        print("BEFORE", json.dumps(before, ensure_ascii=False))
        if dry:
            return
        with conn:
            # 状态一行：child 已有就删旧行（保留新状态），否则改名
            has_child = conn.execute(
                "select count(*) from being_state where persona = ?", (NEW,)).fetchone()[0]
            if has_child:
                conn.execute("delete from being_state where persona = ?", (OLD,))
            else:
                conn.execute("update being_state set persona = ? where persona = ?",
                             (NEW, OLD))
            conn.execute("update being_event set persona = ? where persona = ?", (NEW, OLD))
            conn.execute("update being_note set persona = ? where persona = ?", (NEW, OLD))
            conn.execute("insert into being_note (persona, text, visibility, created_at)"
                         " values (?, ?, 'self', ?)",
                         (NEW, f"人格从 {OLD} 重塑为 {NEW}（六七岁小孩玛卡巴卡），"
                               f"旧生命事件已迁入。", time.time()))
        after = counts(conn)
        print("AFTER ", json.dumps(after, ensure_ascii=False))
        state = conn.execute(
            "select state from being_state where persona = ?", (NEW,)).fetchone()
        if state:
            print("STATE_KEYS", sorted(json.loads(state[0]).keys())[:12])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
