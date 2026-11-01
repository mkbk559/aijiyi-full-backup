"""只读：6 小时现场审计 jsonl 的分布统计（不写任何文件）。"""
from __future__ import annotations

import json
import pathlib
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")
p = pathlib.Path(r"D:\AIJiYi\data\field_audit_6h.jsonl")
rows = []
for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        rows.append(json.loads(line))
    except ValueError:
        pass
print("samples:", len(rows))
if not rows:
    raise SystemExit(0)

print("first:", rows[0].get("time"), "last:", rows[-1].get("time"))

# 告警分布
alerts = Counter()
for r in rows:
    for a in r.get("alerts", []) or []:
        alerts[a.get("code") or a.get("kind") or str(a)] += 1
print("\n## alerts")
for k, v in alerts.most_common():
    print(f"  {k:32s} {v}  ({v/len(rows):.0%})")

# pending / queue_depth
pend = [(r.get("health") or {}).get("pending", 0) or 0 for r in rows]
qd = [(r.get("health") or {}).get("queue_depth", 0) or 0 for r in rows]
print("\n## pending: max", max(pend), "avg", round(sum(pend)/len(pend), 1))
print("## queue_depth: max", max(qd), "avg", round(sum(qd)/len(qd), 1))
print("## pending>20 的样本:", sum(1 for x in pend if x > 20))

# 408 / 请求
ratios = []
for r in rows:
    a = r.get("agent2api") or {}
    req = a.get("last_30m_requests") or 0
    to = a.get("last_30m_408") or 0
    if req:
        ratios.append((to / req, req, to))
if ratios:
    worst = max(ratios)
    print(f"\n## agent2api 30m 408 比例: avg={sum(x[0] for x in ratios)/len(ratios):.1%}"
          f" max={worst[0]:.1%} (408={worst[2]}/{worst[1]})")
    print("## 408 比例 >20% 的样本:", sum(1 for x in ratios if x[0] > 0.2))

# 回复停滞
stalled = [r for r in rows if any((a.get("code") == "assistant_reply_stalled")
                                   for a in (r.get("alerts") or []))]
print("\n## assistant_reply_stalled 样本:", len(stalled),
      f"({len(stalled)/len(rows):.0%})")
if stalled:
    print("   first:", stalled[0].get("time"), "last:", stalled[-1].get("time"))

# QQ 速率错误
qq_err = 0
for r in rows:
    txt = json.dumps(r.get("errors") or [], ensure_ascii=False)
    qq_err += txt.count("每分钟只能发")
print("\n## 日志里 '每分钟只能发5条' 出现次数:", qq_err)
