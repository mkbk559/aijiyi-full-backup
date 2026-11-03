"""审计补充：人格切换后的语义冲突（being/trait）+ /ingest 空 body 是否写脏数据。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "aiji.db"

ADULT = ("阴阳", "吐槽", "损", "毒舌", "抽象", "玩梗", "冷嘲", "阴阳怪气", "抬杠",
         "嘴臭", "阴阳人", "装傻", "自嘲", "脏话", "骂", "羞辱", "刻薄", "挖苦")


def main() -> None:
    conn = sqlite3.connect(str(DB))
    out: dict = {}

    # 1) 涌现性格里有多少条与"六七岁小孩"冲突
    rows = conn.execute("select value, confidence, evidence from trait").fetchall()
    hits = [r for r in rows if any(w in (r[0] or "") for w in ADULT)]
    out["trait_total"] = len(rows)
    out["trait_adult_like"] = len(hits)
    out["trait_adult_samples"] = [
        {"value": r[0], "conf": round(float(r[1] or 0), 2), "evidence": r[2]}
        for r in sorted(hits, key=lambda x: -(x[2] or 0))[:12]
    ]

    # 2) 生命层按 persona 分区的行（切换后是否孤儿）
    for table in ("being_state", "being_event", "being_note"):
        try:
            cols = [c[1] for c in conn.execute(f"pragma table_info({table})")]
            out[f"{table}_cols"] = cols
            out[f"{table}_by_persona"] = dict(
                (str(k), v) for k, v in conn.execute(
                    f"select persona, count(*) from {table} group by persona"))
        except sqlite3.Error as exc:
            out[f"{table}_error"] = str(exc)

    # 3) 空 ingest 是否写脏数据：近 10 分钟有没有空文本轮次/事实
    latest = conn.execute("select max(ts) from turn").fetchone()[0]
    out["latest_turn_at"] = latest
    empty_turns = conn.execute(
        "select count(*) from turn where trim(coalesce(content,'')) = ''").fetchone()[0]
    out["empty_turns"] = empty_turns
    empty_facts = conn.execute(
        "select count(*) from fact where trim(coalesce(object,'')) = ''").fetchone()[0]
    out["empty_facts"] = empty_facts

    # 4) 库里还有没有旧身份词（事实/画像/风格）
    legacy = {}
    for table, col in (("fact", "value"), ("fact", "obj"), ("profile", "value"),
                       ("style", "value"), ("trait", "value")):
        try:
            n = conn.execute(
                f"select count(*) from {table} where {col} like '%大肥鱼%' "
                f"or {col} like '%鱼缸%' or {col} like '%本鱼%'").fetchone()[0]
            legacy[f"{table}.{col}"] = n
        except sqlite3.Error:
            continue
    out["legacy_identity_rows"] = legacy

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


