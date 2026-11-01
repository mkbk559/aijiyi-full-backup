"""只读：6h 审计的回复停滞时长 + QQ 限流错误计数。"""
from __future__ import annotations

import json
import pathlib
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
p = pathlib.Path(r"D:\AIJiYi\data\field_audit_6h.jsonl")
rows = [json.loads(l) for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]

# ---- 回复停滞：latest_assistant 多久没动 ----
stalls = []
last_ts = None
last_assistant = None
start_ts = None
for r in rows:
    ts = r.get("ts")
    cur = (r.get("aiji") or {}).get("latest_assistant")
    if last_assistant is None:
        last_assistant, start_ts = cur, ts
        continue
    if cur == last_assistant:
        if ts - start_ts >= 600:
            stalls.append(round((ts - start_ts) / 60, 1))
    else:
        last_assistant, start_ts = cur, ts
longest = max(stalls) if stalls else 0
print(f"## 回复停滞：≥10 分钟的窗口 {len(stalls)} 段，最长 {longest} 分钟")
if stalls:
    print("   前 10 段（分钟）:", stalls[:10])

# ---- 每条样本的 assistant 增量 ----
inc = []
prev = None
for r in rows:
    cur = (r.get("aiji") or {}).get("qq_assistant")
    if prev is not None and cur is not None:
        inc.append(cur - prev)
    prev = cur
print(f"\n## 每样本（约 60s）新增 assistant：max={max(inc)} avg={sum(inc)/len(inc):.2f}"
      f" 零增量样本={sum(1 for x in inc if x == 0)}/{len(inc)}")

# ---- 今日请求/令牌 ----
a = rows[-1].get("agent2api") or {}
today = a.get("today") or {}
print(f"\n## 今日：requests={today.get('requests')} ok={today.get('successful')}"
      f" tokens={today.get('tokens')}")
print(f"## 30m：requests={a.get('last_30m_requests')} tokens={a.get('last_30m_tokens')}"
      f" 200={a.get('last_30m_200')} 408={a.get('last_30m_408')}")
bal = a.get("balances") or []
low = [b for b in bal if (b.get("remaining") or 0) <= 1000]
print("## 余额告急 provider:", [(b["provider"], b["remaining"]) for b in low])

# ---- 日志里的 QQ 限流 ----
total = 0
for path in [pathlib.Path(r"D:\AIJiYi\data\console.err.log"),
             pathlib.Path(r"D:\AstrBot\astrbot-stable-20261101-183646.out.log")]:
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    n = text.count("每分钟只能发")
    total += n
    print(f"\n## {path.name}: '每分钟只能发' {n} 次, 行数 {text.count(chr(10))}")
print(f"\n## QQ 限流错误合计: {total}")
