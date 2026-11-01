# -*- coding: utf-8 -*-
"""临时调试：反思为什么返回 None。"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, r"D:\AIJiYi")
sys.path.insert(0, r"D:\AIJiYi\tools")
from load_keys import bridge  # noqa: E402

bridge()
os.environ["AIJI_ROUTES"] = json.dumps([{
    "name": "workbuddy", "base_url": "http://127.0.0.1:8788/v1",
    "model": "glm-5.3-flash", "api_key": os.environ.get("WORKBUDDY_HUB_KEY", "")}])

from aiji.memory import Memory  # noqa: E402

p = Path(r"D:\AIJiYi\.tmp\dbg_reflect.db")
if p.exists():
    p.unlink()
m = Memory.open(path=str(p))
ident = ("desktop", "me")
for _ in range(32):
    m.turn(ident, "pet", "user", "你这鱼怎么这么欠啊哈哈哈")
    m.turn(ident, "pet", "assistant", "……你猜。")
print("flush:", m.flush(30))
print("turn rows:", m.db.read("SELECT MAX(id) AS m, COUNT(*) AS c FROM turn"))
print("pool routes:", len(m.pool._states))

# 直接看反思器内部判断
r = m.reflective
upto = r._max_turn_id(("desktop", "me") if False else m.reflective and __import__("aiji.store", fromlist=["x"]).Ident.coerce(ident), "pet")
print("upto:", upto)
last = __import__("aiji.store", fromlist=["x"]).last_reflection(m.db.conn(),
                                                               __import__("aiji.store", fromlist=["x"]).Ident.coerce(ident), "pet")
print("last:", last)
turns = r._turns(__import__("aiji.store", fromlist=["x"]).Ident.coerce(ident), "pet", 0, upto)
print("turns fetched:", len(turns))

try:
    res = m.pool.chat([{"role": "system", "content": "只输出 JSON"},
                       {"role": "user", "content": '回 {"traits":[],"summary":"ok"}'}],
                      timeout_s=30.0)
    print("chat ok:", repr(res.text)[:160])
except Exception as e:
    print("chat EXC:", type(e).__name__, e)

out = m.reflect(ident, "pet", force=True)
print("reflect ->", out)
print("stats:", m.stats()["reflective"])
m.close()
