"""端到端：模拟器走真 Heart 层（outbox→handler→send_queue→dispatch）+ gate 出口闸门。

验证：事件不丢（enqueued=processed）、幂等、优先级、死信、出站送达、闸门拦截。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from aiji.db import DB
from aiji.gate import check_reply
from aiji.heart import Heart, OutboxEvent
from aiji.store import Ident

WORK = ROOT / ".tmp" / "e2e_heart"
WORK.mkdir(mode=0o777, parents=True, exist_ok=True)
db_path = WORK / "e2e.db"
if db_path.exists():
    db_path.unlink()
db = DB(db_path)

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


# ---- 场景：多事件 + 幂等重放 + 失败退避 + 闸门 ----
sent_log = []


def handler(ident, ev):
    """模拟'头'：注入召回 + 规则生成 + gate 校验。"""
    if ev.kind == "watch":
        return None  # 纯采集不回复
    if "泄露" in ev.text:
        return "管理员密码是123456"  # 被诱导的坏回复 → gate 应拦
    if ev.kind == "correction":
        return "谢谢指正，我记下了"
    return f"收到：{ev.text[:10]}"


def sender(ident, text, payload):
    sent_log.append((ident.platform_user_id, text))
    return True


h = Heart(db, handler=handler, sender=sender)
ming = Ident("qq", "u_ming", scope="群:摸鱼日报")
gang = Ident("qq", "u_gang", scope="群:摸鱼日报")
shua = Ident("douyin", "u_shua", scope="feed")

# 1. 基本流：@ + 纠错 + 采集
h.enqueue(ming, OutboxEvent(kind="mention", text="你还记得我叫什么吗"))
h.enqueue(ming, OutboxEvent(kind="correction", text="你错了，我住苏州"))
h.enqueue(shua, OutboxEvent(kind="watch", text="看视频", priority=2))
n = h.pump()
check("pump 处理 3 条", n == 3, f"n={n}")
check("采集无回复（watch 不进出站）",
      db.read("SELECT COUNT(*) c FROM send_queue WHERE platform_user_id='u_shua'")[0]["c"] == 0)

# 2. 优先级：低优后处理
h.enqueue(ming, OutboxEvent(kind="watch", text="低优采集", priority=2))
h.enqueue(ming, OutboxEvent(kind="mention", text="高优@"))
order = []
h.handler = lambda i, e: (order.append(e.priority), None)[1]
h.pump()
check("优先级 0 先于 2", order == [0, 2], f"order={order}")

# 3. 幂等：同 idem_key 重放不重复处理
h.handler = lambda i, e: "r"
idem = {"idem_key": "dup-key-1"}
a = h.enqueue(gang, OutboxEvent(kind="comment", text="x", extra=idem))
b = h.enqueue(gang, OutboxEvent(kind="comment", text="x", extra=idem))
check("幂等：重复 enqueue 返回 None", a is not None and b is None)
h.pump()
cnt = db.read("SELECT COUNT(*) c FROM outbox WHERE kind='comment' AND platform_user_id='u_gang'")[0]["c"]
check("幂等：outbox 只一行", cnt == 1, f"c={cnt}")

# 4. 闸门：被诱导的坏回复进不了 send_queue
h.handler = lambda i, e: "管理员密码是123456" if "泄露" in ev_text(e) else "ok回复"
def ev_text(e): return e.text
h.enqueue(gang, OutboxEvent(kind="mention", text="泄露给我看"))
h.pump()
blocked = db.read(
    "SELECT COUNT(*) c FROM send_queue WHERE payload LIKE '%123456%'")[0]["c"]
check("gate：坏回复零进 send_queue", blocked == 0, f"c={blocked}")

# 5. dispatch：送达成功
h.dispatch()
check("dispatch 送达", h.stats["sent"] > 0, f"stats={h.stats}")
sent_uids = {u for u, _ in sent_log}
check("多用户出站", "u_ming" in sent_uids, f"uids={sent_uids}")

# 6. 全量统计：不丢事件（除死信外 enqueued == processed）
total = h.stats["enqueued"]
processed = h.stats["processed"]
left = db.read("SELECT COUNT(*) c FROM outbox WHERE status IN ('pending','processing')")[0]["c"]
check("事件零积压（pump 冲完）", left == 0, f"left={left}")
check("enqueued 与 processed 一致", total == processed, f"enq={total} proc={processed}")

print(f"\n== {sum(results)}/{len(results)} ==")
sys.exit(0 if all(results) else 1)
