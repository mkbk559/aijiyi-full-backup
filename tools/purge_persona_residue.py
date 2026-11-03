"""清除"会影响人设"的旧数据（可回滚）。

用户 2026-11-03：「还有之前影响人设的全部删除」。
判定口径（本脚本实现的就是这个口径）：

1. `trait.value` 命中成年语感词表（`selfhood._ADULT_TONE_WORDS`：调侃/自嘲/损对方/阴阳/毒舌…）
   → 删。这是旧人格（孙吧抽象话）时期学的说话习惯，喂给小孩会立刻变成"大人装小孩"。
2. `trait/profile/style.value` 命中旧身份词（大肥鱼/本鱼/一条鱼/鱼缸/咕噜/鱼尾/吐泡泡/
   在电脑里/我是程序/语言模型/人工智能/虚拟角色/没有身体…）→ 删。旧名字和"住在电脑里"
   出现在任何注入文本里，模型都可能重新认领。
3. `being_*` 里 persona='sunba' 的行 → 删（默认人格已改成 child，这些是迁移漏网）。
4. `data/persona/sunba.json`（学到的成年语气语料）→ 删。

保全：先把整库备份到 `data/backups/`，再把每一条被删记录写进
`tools/archcheck/results/persona_purge_audit.json`，最后 VACUUM 收空间。

用法：python tools/purge_persona_residue.py [--apply]（默认 dry-run，只统计不删）
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aiji.brain.selfhood import _ADULT_TONE_WORDS  # noqa: E402

DB = ROOT / "data" / "aiji.db"
BACKUPS = ROOT / "data" / "backups"
AUDIT = ROOT / "tools" / "archcheck" / "results" / "persona_purge_audit.json"
CORPUS = ROOT / "data" / "persona" / "sunba.json"

FISH = ("大肥鱼", "鱼缸", "本鱼", "一条鱼", "咕噜", "鱼尾", "吐泡泡", "小鱼干", "鱼片")
MACHINE = ("在电脑里", "住在电脑", "我是个程序", "我是程序", "语言模型", "人工智能",
           "虚拟角色", "被造出来", "没有身体", "作为AI", "作为 AI")
# 第二轮补的：旧人格（孙吧抽象话）特有的"互联网成年人姿态"。这些词不在
# `_ADULT_TONE_WORDS` 里，但同样是六七岁小孩不可能有的表达习惯。
EXTRA_ADULT = ("吐槽", "回怼", "怼", "阴阳", "抽象", "玩梗", "杠精", "冷笑", "反问")


def hit(text: str) -> list[str]:
    """返回命中的词（空列表=保留）。"""
    value = str(text or "")
    words = [w for w in _ADULT_TONE_WORDS if w in value]
    words += [w for w in EXTRA_ADULT if w in value]
    words += [w for w in FISH if w in value]
    words += [w for w in MACHINE if w in value]
    return words


def main() -> int:
    apply = "--apply" in sys.argv
    wipe_traits = "--all-traits" in sys.argv
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    plan: dict[str, list[dict]] = {"trait": [], "profile": [], "style": [], "being": []}
    try:
        for table in ("trait", "profile", "style"):
            for row in con.execute(f"SELECT rowid AS rid, * FROM {table}"):
                data = dict(row)
                words = hit(data.get("value"))
                if words:
                    plan[table].append({"rid": data["rid"], "value": data.get("value"),
                                        "words": sorted(set(words)), "row": data})
        for table in ("being_state", "being_note", "being_event"):
            for row in con.execute(f"SELECT rowid AS rid, * FROM {table} WHERE persona='sunba'"):
                data = dict(row)
                plan["being"].append({"table": table, "rid": data["rid"], "row": data})
    finally:
        con.close()

    summary = {
        "apply": apply,
        "all_traits": wipe_traits,
        "counts": {k: len(v) for k, v in plan.items()},
        "corpus_deleted": CORPUS.exists(),
        "backup": None,
        "deleted_at": time.time(),
    }
    if not apply:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        AUDIT.write_text(json.dumps({"summary": summary, "plan": plan},
                                    ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = BACKUPS / f"aiji.db.bak-before-persona-purge-{stamp}"
    con = sqlite3.connect(str(DB))
    try:
        con.execute("PRAGMA wal_checkpoint(FULL)")
        con.close()
        shutil.copy2(DB, backup)
        wal = DB.with_suffix(".db-wal")
        if wal.exists():
            shutil.copy2(wal, backup.with_suffix(".db-wal"))
        summary["backup"] = str(backup)
        summary["backup_bytes"] = backup.stat().st_size

        con = sqlite3.connect(str(DB))
        removed = {}
        if wipe_traits:
            # 全量清零：trait 是"你长成了什么样"（旧人格的口吻与意象），
            # 留着任何一条都会把六七岁的人设往孙吧抽象话上拽。
            plan["trait"] = [
                {"rid": row[0], "value": row[1], "words": ["--all-traits"], "row": {}}
                for row in con.execute("SELECT rowid, value FROM trait")
            ]
            summary["counts"]["trait"] = len(plan["trait"])
        for table in ("trait", "profile", "style"):
            rids = [r["rid"] for r in plan[table]]
            for start in range(0, len(rids), 500):
                chunk = rids[start:start + 500]
                marks = ",".join("?" * len(chunk))
                cur = con.execute(f"DELETE FROM {table} WHERE rowid IN ({marks})", chunk)
                removed[table] = removed.get(table, 0) + cur.rowcount
        for item in plan["being"]:
            cur = con.execute(f"DELETE FROM {item['table']} WHERE rowid = ?", (item["rid"],))
            removed[item["table"]] = removed.get(item["table"], 0) + cur.rowcount
        con.commit()
        con.execute("VACUUM")
        con.close()
        summary["removed"] = removed
        if CORPUS.exists():
            CORPUS.unlink()
        summary["db_bytes_after"] = DB.stat().st_size
    except Exception as exc:  # noqa: BLE001 - 出错必须留下现场
        summary["error"] = f"{type(exc).__name__}: {exc}"
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        AUDIT.write_text(json.dumps({"summary": summary, "plan": plan},
                                    ensure_ascii=False, indent=2), "utf-8")
        raise
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps({"summary": summary, "plan": plan},
                                ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
