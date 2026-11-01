# -*- coding: utf-8 -*-
"""桌宠端到端：看桌面 → 聊天 → 攒对话 → 反思涌现性格。

跑法: python tools/pet_smoke.py
真机：走本机 workbuddy 网关（已实测连通）。
"""
import io
import json
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = ROOT / ".tmp"
WORK.mkdir(mode=0o777, exist_ok=True)
db = WORK / "pet_smoke.db"
if db.exists():
    db.unlink()

sys.path.insert(0, str(ROOT / "tools"))
from tools.load_keys import bridge  # noqa: E402
bridge()
os.environ["AIJI_ROUTES"] = json.dumps([
    {"name": "workbuddy", "base_url": "http://127.0.0.1:8788/v1",
     "model": "glm-5.3-flash", "api_key": os.environ.get("WORKBUDDY_HUB_KEY", "")},
])

from aiji.memory import Memory  # noqa: E402
from aiji.pet.brain import FishBrain  # noqa: E402
from aiji.pet import desk  # noqa: E402

IDENT = ("desktop", "me")
m = Memory.open(path=str(db))

# ---- 0. 无模型时是否诚实（显式 routes=()，不受 AIJI_ROUTES 影响）----
from aiji.config import Settings  # noqa: E402

m0 = Memory(Settings(db_path=WORK / "pet_nomodel.db", routes=()), engines="none")
b0 = FishBrain(m0, IDENT)
r0 = b0.reply("在吗")
print(f"[无模型] {r0['text']}  (ok={r0['ok']}, reason={r0['reason']})")
assert r0["ok"] is False and r0["reason"] == "no_model", r0
m0.close()

brain = FishBrain(m, IDENT, "pet")

# ---- 1. 看桌面 ----
lk = brain.look()
print(f"\n[看桌面] {lk['took_ms']}ms screen={lk['screen']} windows={len(lk['windows'])} "
      f"ocr_conf={lk['ocr_conf']}")
print("  窗口:", (lk["windows"] or [])[:4])
print("  文字:", (lk["text"] or "")[:160] or "(屏上没读出字)")
assert lk["screen"], "截屏失败"

# ---- 2. 聊天（真模型）----
r1 = brain.reply("你是谁？")
print(f"\n[聊天] ({r1['took_ms']}ms) {r1['text']}")
assert r1["ok"], r1

# ---- 3. 让它评论看到的桌面 ----
r2 = brain.comment_on(lk)
print(f"\n[看桌面评论] {r2['text'][:200]}")

# ---- 4. 攒一段"群聊"互动，再让反思 Worker 归纳 ----
# 直接写 turn 表（不走异步 writer）：writer 每条 turn 都会触发模型抽取，
# 攒 60+ 条会把池子占满、flush 超时，测的就不是反思了。真实场景是渐进入库。
CHAT = [
    "你这鱼怎么这么欠啊哈哈哈",
    "笑死，你说话好毒",
    "别贫了，问你个正经的",
    "你也太会吐槽了吧",
    "行行行，你说得对（并不）",
    "你怎么老是反问啊",
    "哈哈哈哈哈被你怼了",
    "算了算了说不过你",
]
from aiji import store as _store  # noqa: E402

with m.db.tx() as c:
    for i in range(40):
        t = CHAT[i % len(CHAT)]
        _store.add_turn(c, __import__("aiji.store", fromlist=["Ident"]).Ident.coerce(IDENT),
                        "pet", "user", t)
        _store.add_turn(c, __import__("aiji.store", fromlist=["Ident"]).Ident.coerce(IDENT),
                        "pet", "assistant", "……你猜。")

out = brain.reflect(force=True)
print(f"\n[反思] {json.dumps(out, ensure_ascii=False) if out else 'None（没攒够/失败）'}")
print(f"[反思统计] {m.stats()['reflective']}")

portrait = m.self_portrait(IDENT)
print(f"\n[自画像] 底线 {len(portrait['floor'])} 条 / 涌现性格 {len(portrait['traits'])} 条")
for t in portrait["traits"]:
    print(f"  · {t['value']}  (证据{t['evidence']} 置信{t['confidence']:.2f})")
print("\n---- 提示词开头 ----")
print(portrait["prefix"][:700])

m.close()
ok = bool(portrait["traits"])
print(f"\n== 结果：{'PASS（性格确实从互动里长出来了）' if ok else 'FAIL（没长出性格）'} ==")
sys.exit(0 if ok else 1)
