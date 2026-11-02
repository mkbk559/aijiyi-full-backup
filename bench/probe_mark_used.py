"""阶段 0-③：读→强化闭环探针（`mark_used` 到底有没有接上）。

背景（2026-11-02 基线）：`store.mark_used()` 全仓 **0 个调用方**，
所以 `hit_count/last_used_at` 永远是 0/NULL，FSRS 的稳定性没有输入——
这就是"冻结层"落地前必须先修的那一环。

探针断言的是**现在的行为**（应该全部 PASS）：
  ① 注入一次 → hit_count+1、last_used_at 落值、stability 变大；
  ② 1 小时内再注入 → 不再加（冷却，防一轮刷分）；
  ③ 把 last_used_at 改到 2 天前再注入 → 再加一次，且用跨天增量。

跑法： python bench/probe_mark_used.py   （退出码 0=全 PASS，1=有 FAIL）
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiji.right_hand import params  # noqa: E402
from aiji.right_hand.config import Settings  # noqa: E402
from aiji.hippocampus.memory import Memory  # noqa: E402
from aiji.hippocampus.store import FactDraft, Ident, add_fact  # noqa: E402

IDENT = Ident("probe", "reinforce", "default")


def main() -> int:
    work = ROOT / ".tmp" / "probe_mark_used"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(mode=0o777, parents=True, exist_ok=True)
    m = Memory(Settings(db_path=work / "probe.db", routes=()), engines="auto")
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: object = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    try:
        with m.db.tx() as c:
            fid, _ = add_fact(c, IDENT, FactDraft("user", "居住地", "杭州", "用户住在杭州", 0.9))
        m.recaller.refresh_profile(IDENT)

        def row() -> dict:
            return dict(m.db.read(
                "SELECT hit_count,last_used_at,stability FROM fact WHERE id=?", (fid,))[0])

        inj = m.inject("我住哪", IDENT)
        r1 = row()
        check("① 注入产生留痕", inj.injection_id > 0, inj.injection_id)
        check("① hit_count +1", r1["hit_count"] == 1, r1["hit_count"])
        check("① last_used_at 落值", r1["last_used_at"] is not None, r1["last_used_at"])
        check("① stability 变大",
              float(r1["stability"]) > params.STABILITY_INIT_DAYS, r1["stability"])

        m.inject("我住哪", IDENT)
        r2 = row()
        check("② 1 小时内不重复计分", r2["hit_count"] == 1, r2["hit_count"])

        two_days_ago = time.time() - 2 * 86400
        with m.db.tx() as c:
            c.execute("UPDATE fact SET last_used_at=? WHERE id=?", (two_days_ago, fid))
        m.inject("我住哪", IDENT)
        r3 = row()
        expected = min(params.STABILITY_MAX_DAYS,
                       float(r1["stability"]) * (1 + params.FSRS_SPACED_GAIN))
        check("③ 隔天再想起 +1", r3["hit_count"] == 2, r3["hit_count"])
        check("③ 用跨天增量（+20%）",
              abs(float(r3["stability"]) - expected) < 1e-6,
              {"got": r3["stability"], "expected": round(expected, 6)})
    finally:
        m.close()

    failed = [c for c in checks if not c["ok"]]
    print(json.dumps({"passed": len(checks) - len(failed), "failed": len(failed),
                      "checks": checks}, ensure_ascii=False, indent=2))
    print("\nPASS" if not failed else f"\nFAIL x{len(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
