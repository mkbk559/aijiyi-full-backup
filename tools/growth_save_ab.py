"""A/B：成长账本 save() 的并发安全（旧写法 vs 现在的写法）。

旧写法 = 固定临时名 `growth.tmp` + 直接 replace、无锁无重试（2026-11-04 修之前的样子）。
新写法 = `Growth.save()`。
两边跑同一份并发负载：8 线程 × 15 次保存，看抛不抛 WinError 32/13。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aiji.brain import growth as growth_mod  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1] / ".tmp" / "growth-ab"
THREADS, ROUNDS = 8, 15


def _old_save(path: pathlib.Path, data: dict) -> None:
    """修复前的 save()：固定临时名、无锁、无重试。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    tmp.replace(path)


def hammer(save, path: pathlib.Path) -> list[str]:
    errors: list[str] = []
    barrier = threading.Barrier(THREADS)

    def worker(index: int) -> None:
        barrier.wait()
        for round_no in range(ROUNDS):
            try:
                save(path, {"schema": 2, "xp": float(index * 100 + round_no)})
            except BaseException as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    return errors


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    print(f"pid={os.getpid()} threads={THREADS} rounds={ROUNDS}")

    old_path = ROOT / "old" / "growth.json"
    old_errors = hammer(_old_save, old_path)
    print(f"[old] 固定临时名 + 无锁: errors={len(old_errors)}")
    for line in old_errors[:3]:
        print(f"   {line}")

    new_path = ROOT / "new" / "growth.json"
    ledger = growth_mod.Growth(new_path)
    new_errors = hammer(lambda p, d: ledger.save(d), new_path)
    print(f"[new] 唯一临时名 + 锁 + 重试: errors={len(new_errors)}")
    for line in new_errors[:3]:
        print(f"   {line}")

    ok = json.loads(new_path.read_text(encoding="utf-8"))["schema"] == 2
    print(f"new 落盘完整 JSON: {ok}")
    leftovers_old = sorted(p.name for p in old_path.parent.glob("*.tmp"))
    leftovers_new = sorted(p.name for p in new_path.parent.glob("*.tmp"))
    print(f"残留临时文件 old={leftovers_old} new={leftovers_new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
