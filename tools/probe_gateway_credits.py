"""一次性探针：从 workbuddy 反代的 dashboard 页面里找出它自己用的 JSON 接口，
只为确认"反代积分"能不能自动读到（找得到就不用手填；找不到就退回静态配置）。

产物：.tmp/probe_gw/paths.txt（接口清单）+ 每个候选接口的首 200 字响应。
不联网、不改任何东西。
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / ".tmp" / "probe_gw"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:8788"

with urllib.request.urlopen(BASE + "/dashboard", timeout=8) as r:
    html = r.read().decode("utf-8", "replace")

paths = sorted(set(re.findall(r"""['"](/api/[A-Za-z0-9_\-/.{}$]{2,60})['"]""", html)))
(OUT / "paths.txt").write_text("\n".join(paths), "utf-8")
print(f"paths={len(paths)}")

# 名字里带 credit/quota/account/summary/usage 的才值得试
interesting = [p for p in paths if re.search(r"credit|quota|account|summary|usage|balance|stat", p, re.I)]
print("interesting:", interesting[:20])
report = {}
for p in interesting[:12]:
    url = BASE + re.sub(r"\{[^}]*\}", "1", p)
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            body = r.read(400).decode("utf-8", "replace")
        report[url] = {"status": r.status, "body": body[:200]}
    except Exception as exc:  # noqa: BLE001
        report[url] = {"error": f"{type(exc).__name__}: {exc}"[:120]}
(OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2)[:1200])
