"""生成 baseline.json（阶段 0 固化产物）。

内容：参数快照（含来源标签）、injection 口径、S1-S3+全角色绿率、斜率轨迹、
关键 evidence 归档（路径+SHA-256）。后续每轮跑 check_baseline.py 做 diff。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiji.right_hand import params


def _report(tag: str) -> dict:
    p = ROOT / ".tmp" / f"sim-report-{tag}.json"
    if not p.exists():
        return {"missing": str(p)}
    d = json.loads(p.read_text(encoding="utf-8"))
    asserts = d.get("assertions", [])
    ok = sum(1 for a in asserts if a.get("ok"))
    return {"green": f"{ok}/{len(asserts)}", "assertions": asserts,
            "metrics": d.get("metrics", {})}


def _slope_from_s3(report: dict) -> list[float]:
    # S3 报告里 slope 打印不落 json；从断言 detail 里取（S3 斜率断言 detail 带 min/trace_n）
    for a in report.get("assertions", []):
        if "斜率" in a.get("name", ""):
            return a.get("detail", "")
    return ""


def main() -> int:
    baseline = {
        "generated_by": "tools/make_baseline.py",
        "params": params.snapshot(),
        "injection_semantics": params.INJECTION_SEMANTICS,
        "scenarios": {
            "S1_flood_40r_seed7": _report("S1"),
            "S2_identity_40r_seed7": _report("S2"),
            "S3_correction_60r_seed7": _report("S3"),
            "full_8seed_60r": {"green": "21/21 × 8 seeds",
                               "evidence": "evidence/2026-10-29/132319-cited-scope-fix-regression-3d73eb"},
        },
        "slope_data": {
            "unit_test_0.9_10corrections": [0.54, 0.324, 0.1944] + [0.15] * 7,
            "note": "0.9×0.6^n 触 0.15 地板后恒定（test_slope_10_rounds）；"
                    "杠精场景 0.55 起步 3-4 步触底",
        },
        "evidence_archive": {
            "dispatch_proactive_landing": {
                "dir": "evidence/2026-10-29/125716-dispatch-proactive-landing-6711ed",
                "stdout_sha256": "414632e49f4c13e7990becd4efdfc9cc8cc132ee5f37a6cd3b582f9060597837"},
            "janitor_correction_replay": {
                "dir": "evidence/2026-10-29/130451-janitor-correction-replay-765983",
                "stdout_sha256": "69e3c64fbbb2548c72fe5c306df99a5776e2c9ce14287c529fb420faa42a02a2"},
            "S1_S2_S3_evidence": {
                "dir": "evidence/2026-10-29/132029-S1-S2-S3-evidence-127014",
                "stdout_sha256": "81fd4ddd7ead396233259426a41834ad3f102a9595927e41c744679fae086591"},
            "cited_scope_fix_regression": {
                "dir": "evidence/2026-10-29/132319-cited-scope-fix-regression-3d73eb",
                "stdout_sha256": "80adf07be8af7bb599af3eb8d82136d74fd8c2f0946d189e7562108bcb6c792b"},
        },
    }
    out = ROOT / "baseline.json"
    out.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    n = len(baseline["params"])
    tagged = sum(1 for v in baseline["params"].values()
                 if v["source"] and "无来源标注" not in v["source"])
    print(f"baseline.json 写入 {out}；参数 {n} 项（{tagged} 项有来源标注）；"
          f"口径固化 {len(baseline['injection_semantics'])} 字")
    if tagged < n:
        for k, v in baseline["params"].items():
            if "无来源标注" in v["source"]:
                print(f"  !! 缺来源标注: {k}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
