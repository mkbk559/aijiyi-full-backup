# -*- coding: utf-8 -*-
"""模型摘要 vs heuristic 摘要对比评测（PROJECT_STATE 风险清单"模型摘要质量未评测"补齐）。

评测维度（对照 heuristic 的已知盲区设计）：
  A. 跨句整合（盲区核心）：heuristic 只做首句去重，"上面那个改成周六"类指代无法解析
  B. 信息密度：同样 10 句话，两者各保留多少可考事实
  C. 冗余度：heuristic 拼接式摘要的重复度
  D. 指代解析：他/它/那个 需要回指消解
"""
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools.load_keys import bridge  # noqa: E402
from aiji.extract import heuristic_summary, build_summary_messages  # noqa: E402
from aiji.pool import ModelPool  # noqa: E402
from aiji.config import Route  # noqa: E402

bridge()
import os  # noqa: E402

WORK = ROOT / ".tmp"
WORK.mkdir(mode=0o777, exist_ok=True)


# ---- 语料：3 组，各测一个维度 ----
CASES = {
    "A_跨句整合": [
        "我们原定周六去爬山",
        "不过天气预报说周六有雨",
        "所以那个活动改成下周日了",
        "地点还是老地方，北高峰",
    ],
    "B_信息密度": [
        "我叫陈明，今年32岁",
        "住在深圳南山区",
        "在后端开发组工作五年了",
        "养了一只橘猫叫橘子，三岁",
        "周末喜欢钓鱼和摄影",
        "最近在学自由泳",
        "不吃香菜",
        "用 vim 写代码",
    ],
    "D_指代解析": [
        "我上周见了张伟",
        "他是我大学同学",
        "他现在在杭州做产品经理",
        "他说下次来深圳要找我吃饭",
    ],
}

# 可考事实关键词（人工标注的 ground truth）
TRUTH = {
    "A_跨句整合": ["下周日", "北高峰", "爬山"],
    "B_信息密度": ["陈明", "32", "深圳", "南山", "后端", "五年", "橘", "猫",
                 "橘子", "钓鱼", "摄影", "自由泳", "香菜", "vim"],
    "D_指代解析": ["张伟", "大学同学", "杭州", "产品经理", "深圳", "吃饭"],
}

routes = []
for name, base, model, key_env in (
    ("workbuddy", "http://127.0.0.1:8788/v1", "glm-5.3-flash", "WORKBUDDY_HUB_KEY"),
    ("agent2api", "http://127.0.0.1:3065/v1", "glm-5.3-flash", "AGENT2API_KEY"),
):
    k = os.environ.get(key_env, "")
    if k:
        routes.append(Route(name=name, base_url=base, model=model, api_key=k, timeout_s=30))
pool = ModelPool(tuple(routes))

import re  # noqa: E402
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

report = {"cases": []}
print("== 模型摘要 vs heuristic 摘要 ==")
for case, lines in CASES.items():
    truth = TRUTH[case]
    h = heuristic_summary(lines)
    # 模型摘要
    m = ""
    try:
        res = pool.chat(build_summary_messages(lines), timeout_s=30)
        raw = (res.text or "").strip()
        fm = _FENCE.search(raw)
        if fm:
            raw = fm.group(1).strip()
        m = str(json.loads(raw).get("summary") or "").strip()
    except Exception as exc:  # noqa: BLE001
        m = f"<model_failed: {type(exc).__name__}>"
    h_hit = [t for t in truth if t in h]
    m_hit = [t for t in truth if t in m]
    # 冗余度：heuristic 拼接的分号段数 vs 总段数
    h_segs = [s for s in h.split("；") if s.strip()]
    h_dup = len(h_segs) - len(set(h_segs))
    row = {
        "case": case,
        "heuristic_recall": f"{len(h_hit)}/{len(truth)}",
        "model_recall": f"{len(m_hit)}/{len(truth)}",
        "heuristic_dup_segs": h_dup,
        "heuristic": h[:150],
        "model": m[:200],
    }
    report["cases"].append(row)
    print(f"\n[{case}] heuristic {len(h_hit)}/{len(truth)} vs model {len(m_hit)}/{len(truth)}")
    print(f"  H: {h[:120]}")
    print(f"  M: {m[:160]}")

(WORK / "summary_eval.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
print(f"\n报告：{WORK / 'summary_eval.json'}")
