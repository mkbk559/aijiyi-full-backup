"""v0.3 端到端演示：学习 → 总结 → 注入 → 反馈，全程走真实代码路径。

    python -X utf8 _demo_v3.py
"""

import json
import pathlib
import sqlite3
import sys
import time

sys.path.insert(0, r"D:\AIJiYi")

from aiji import Memory  # noqa: E402
from aiji.right_hand.config import Settings  # noqa: E402

DB = pathlib.Path(r"D:\AIJiYi\.tmp\v3demo.db")
for s in ("", "-wal", "-shm"):
    pathlib.Path(str(DB) + s).unlink(missing_ok=True)

WX = ("wechat", "u1")

# OCR/ASR 引擎由调用方注入；这里给一个"假引擎"证明管线通，不代表识别质量。
m = Memory(
    Settings(db_path=DB, routes=(), summary_every=4, inject_chars=700),
    ocr=lambda item: ("发票金额 1280 元，开票日期 2026-10-28", 0.82),
)

print("=== 1. 学习：文本 → 事实/知识/关系/风格 ===")
m.turn(WX, "s1", "user", "我叫李雷，住在杭州，张伟是我的同事，项目Apollo的技术栈是Go，我说话比较随意呀~~")
print("flush:", m.flush(20))

print("\n=== 2. 学习：非文本输入（同一管线，不按类型分叉）===")
m.ingest(WX, kind="emoji", text="今天好累😭😭", session="s1")
m.ingest(WX, kind="image", ref="msg-7781", url="https://cdn.example/fapiao.jpg", session="s1")
m.ingest(WX, kind="voice", ref="v-9", url="https://cdn.example/9.ogg", session="s1")  # 没 ASR 引擎
m.ingest(WX, kind="video", ref="vid-1", url="https://cdn.example/v.mp4",
         size_bytes=9_000_000, session="s1", extra={"duration_s": 12})
print("flush:", m.flush(20))
for row in m.media(WX, "s1"):
    print(json.dumps({k: row[k] for k in ("kind", "ref", "status", "recognizer", "confidence", "text")},
                     ensure_ascii=False))

print("\n=== 3. 总结：轮次够多后自动出摘要 ===")
for i in range(4):
    m.turn(WX, "s1", "user", f"再补一句第{i}条：我下个月要搬家到滨江")
m.flush(20)
print("summary:", json.dumps(m.summary(WX, "s1"), ensure_ascii=False))

print("\n=== 4. 召回（100ms 硬预算）===")
res = m.recall("我住哪", WX, session="s1")
print(json.dumps({"took_ms": round(res.took_ms, 2), "degraded": res.degraded,
                  "hits": [h.text for h in res.hits]}, ensure_ascii=False))

print("\n=== 5. 注入：软引导块（带来源标注，不生成回复，不改人格）===")
inj = m.inject("我住哪", WX, session="s1")
print(inj.text)
print(json.dumps({"injection_id": inj.injection_id, "items": len(inj.items),
                  "chars": len(inj.text), "took_ms": round(inj.took_ms, 2),
                  "dropped": inj.dropped, "soft": inj.soft,
                  "persona_touched": inj.persona_touched,
                  "generated_reply": inj.generated_reply}, ensure_ascii=False))

print("\n=== 6. 反馈：主模型用了哪几条 ===")
used = [i.id for i in inj.items if i.kind in ("fact", "knowledge", "relation") and i.id][:2]
print("updated:", m.feedback(WX, inj.injection_id, used, helpful=True, note="主模型引用了居住地"))

print("\n=== 7. 汇总 ===")
st = m.stats()
print(json.dumps({"store": st["store"], "writer": {k: st["writer"][k] for k in
                  ("submitted", "processed", "ingested", "unrecognized", "summaries",
                   "summary_model", "evicted", "heuristic")},
                  "injection": st["injection"]}, ensure_ascii=False, indent=2))
print("style:", json.dumps(m.style(WX), ensure_ascii=False))
m.close()

print("\n=== 8. schema v4 ===")
c = sqlite3.connect(str(DB))
c.row_factory = sqlite3.Row
for t in ("fact", "fact_link", "fact_log", "media", "style", "injection", "summary"):
    cols = [f"{r['name']}:{r['type']}" + (" PK" if r["pk"] else "") for r in c.execute(f"pragma table_info({t})")]
    n = c.execute(f"select count(*) from {t}").fetchone()[0]
    print(f"{t}({n}): " + " | ".join(cols))
print("user_version:", c.execute("pragma user_version").fetchone()[0])
c.close()
