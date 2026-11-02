"""校验当前状态 vs baseline.json：参数漂移 + 绿率回归。

用法：python tools/check_baseline.py [--quick]
- 参数漂移：params.py 当前值 vs baseline.params 快照，不一致即 DRIFT（列出）
- 绿率：重跑 S1/S2/S3（--quick 时只跑 S3 + 1 个全角色 seed）比对 baseline 绿率
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiji.right_hand import params


def run_sim(args: list[str]) -> tuple[int, str]:
    r = subprocess.run([sys.executable, "tools/sim_platforms.py", *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(ROOT))
    tail = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else ""
    return r.returncode, tail


def green_of(report_tag: str) -> tuple[int, int]:
    p = ROOT / ".tmp" / f"sim-report-{report_tag}.json"
    if not p.exists():
        return 0, 0
    asserts = json.loads(p.read_text(encoding="utf-8")).get("assertions", [])
    return sum(1 for a in asserts if a.get("ok")), len(asserts)


def main() -> int:
    quick = "--quick" in sys.argv
    bp = ROOT / "baseline.json"
    if not bp.exists():
        print("!! baseline.json 不存在，先跑 tools/make_baseline.py")
        return 2
    base = json.loads(bp.read_text(encoding="utf-8"))

    fail = 0
    # 1) 参数漂移
    snap = params.snapshot()
    for name, old in base.get("params", {}).items():
        cur = snap.get(name)
        if cur is None or cur["value"] != old["value"]:
            print(f"  [DRIFT] {name}: baseline={old['value']} current={cur and cur['value']}")
            fail += 1
    print(f"参数快照：{len(snap)} 项，漂移 {fail}")

    # 2) 场景绿率
    runs = ([("S3", ["--rounds", "60", "--seed", "7", "--roles", "杠精", "--tag", "C_S3"])]
            if quick else
            [("S1", ["--rounds", "40", "--seed", "7", "--roles", "刷屏怪", "--tag", "C_S1"]),
             ("S2", ["--rounds", "40", "--seed", "7", "--roles", "小明,小刚", "--tag", "C_S2"]),
             ("S3", ["--rounds", "60", "--seed", "7", "--roles", "杠精", "--tag", "C_S3"])])
    for tag, args in runs:
        rc, tail = run_sim(args)
        ok, n = green_of(f"C_{tag}")
        base_ok, base_n = green_of(tag)  # baseline 的报告还在 .tmp 里
        status = "OK" if rc == 0 else "FAIL"
        print(f"  [{status}] {tag}: {ok}/{n}（baseline: {base_ok}/{base_n}）:: {tail}")
        if rc != 0:
            fail += 1

    print(f"\ncheck_baseline: {'PASS' if fail == 0 else f'{fail} 项漂移/失败'}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
