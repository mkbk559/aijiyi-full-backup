"""列出待删候选（完整文本），供人工判断是"他的自称"还是"用户的事"。只读。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from aiji.brain.selfhood import _ADULT_TONE_WORDS  # noqa: E402

DB = ROOT / "data" / "aiji.db"
OUT = ROOT / "tools" / "archcheck" / "results" / "persona_residue_candidates.json"
FISH = ("大肥鱼", "鱼缸", "本鱼", "一条鱼", "咕噜", "鱼尾", "吐泡泡")
MACHINE = ("在电脑里", "住在电脑", "我是个程序", "我是程序", "语言模型", "人工智能",
           "虚拟角色", "被造出来", "没有身体", "作为AI", "作为 AI")

con = sqlite3.connect(str(DB))
con.row_factory = sqlite3.Row
out: dict = {}

for table in ("profile", "style"):
    rows = [dict(r) for r in con.execute(f"SELECT rowid AS rid, * FROM {table}")]
    out[table] = []
    for row in rows:
        text = str(row.get("value") or "")
        words = [w for w in FISH + MACHINE if w in text]
        if words:
            out[table].append({k: row.get(k) for k in
                               ("rid", "kind", "key", "value", "confidence", "persona")
                               if k in row} | {"words": words})

rows = [dict(r) for r in con.execute("SELECT rowid AS rid, * FROM trait")]
out["trait_fish"] = [
    {"rid": r["rid"], "value": r["value"], "words": [w for w in FISH if w in str(r["value"])]}
    for r in rows if any(w in str(r.get("value") or "") for w in FISH)
]
out["trait_adult_count"] = sum(
    1 for r in rows if any(w in str(r.get("value") or "") for w in _ADULT_TONE_WORDS))
out["trait_adult_sample"] = [
    {"rid": r["rid"], "value": r["value"][:160]}
    for r in rows if any(w in str(r.get("value") or "") for w in _ADULT_TONE_WORDS)
][:15]
con.close()

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
print(json.dumps(out, ensure_ascii=False, indent=2)[:6000])
