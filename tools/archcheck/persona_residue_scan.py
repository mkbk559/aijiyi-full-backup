"""盘点"会影响人设的旧数据"：成人语感 trait、旧身份（鱼/电脑）残留、学到的语气语料。

只读，不改库。输出 JSON 到 tools/archcheck/results/persona_residue_scan.json
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from aiji.brain.selfhood import _ADULT_TONE_WORDS  # noqa: E402

DB = ROOT / "data" / "aiji.db"
OUT = ROOT / "tools" / "archcheck" / "results" / "persona_residue_scan.json"

FISH = ("大肥鱼", "鱼缸", "本鱼", "一条鱼", "咕噜", "鱼尾", "吐泡泡")
MACHINE = ("在电脑里", "住在电脑", "我是个程序", "我是程序", "语言模型", "人工智能",
           "虚拟角色", "被造出来", "没有身体", "作为AI", "作为 AI")


def rows(con, sql, args=()):
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def match(text: str) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    adult = [w for w in _ADULT_TONE_WORDS if w in text]
    if adult:
        hits["adult_tone"] = adult
    fish = [w for w in FISH if w in text]
    if fish:
        hits["fish"] = fish
    machine = [w for w in MACHINE if w in text]
    if machine:
        hits["machine"] = machine
    return hits


def main() -> int:
    con = sqlite3.connect(str(DB))
    report: dict = {"db": str(DB), "tables": {}}
    try:
        for table, col in (("trait", "value"), ("profile", "value"),
                           ("style", "value"), ("knowledge", "title")):
            try:
                data = rows(con, f"SELECT rowid AS rid, * FROM {table}")
            except sqlite3.OperationalError as exc:
                report["tables"][table] = {"error": str(exc)}
                continue
            buckets: dict[str, list[dict]] = {"adult_tone": [], "fish": [], "machine": []}
            for row in data:
                text = str(row.get(col) or "")
                for kind, words in match(text).items():
                    buckets[kind].append({"rid": row["rid"], "value": text[:120],
                                          "words": words,
                                          "persona": row.get("persona")})
            report["tables"][table] = {
                "total": len(data),
                **{k: {"n": len(v), "samples": v[:5]} for k, v in buckets.items()},
            }
        # 学到的语气语料
        corpus = []
        pdir = ROOT / "data" / "persona"
        if pdir.exists():
            for path in sorted(pdir.glob("*.json")):
                try:
                    data = json.loads(path.read_text("utf-8"))
                except (OSError, ValueError):
                    corpus.append({"file": path.name, "error": "unreadable"})
                    continue
                traits = data.get("traits") or []
                bad = [t for t in traits if any(w in str(t) for w in _ADULT_TONE_WORDS)]
                corpus.append({"file": path.name, "bytes": path.stat().st_size,
                               "traits": len(traits), "adult_like": len(bad),
                               "sample": bad[:3]})
        report["learned_corpus"] = corpus
        # 人格分区
        report["persona_partitions"] = {}
        for table in ("being_state", "being_event", "being_note", "trait", "style"):
            try:
                report["persona_partitions"][table] = rows(
                    con, f"SELECT persona, COUNT(*) AS n FROM {table} GROUP BY persona")
            except sqlite3.OperationalError:
                pass
    finally:
        con.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
