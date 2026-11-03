"""删旧备份：只留"能回滚当下状态"的那几份。

规则（2026-11-03 定）：
- `data/backups/aiji.db.bak-before-persona-purge-*`：**只留最早的一份**——那是最干净的
  全量状态，后面的几份都是它的子集（每轮清理各备一次，逐轮变小）。
- 其它 `aiji.db.bak-*`：只留最近 KEEP 份，其余删掉。

用法：python tools/prune_backups.py [--keep 2] [--apply]
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUPS = ROOT / "data" / "backups"
KEEP_PREFIX = "aiji.db.bak-before-persona-purge-"


def plan(keep: int) -> tuple[list[Path], list[Path]]:
    files = sorted(BACKUPS.glob("aiji.db.bak-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    purge = [p for p in files if p.name.startswith(KEEP_PREFIX)]
    others = [p for p in files if not p.name.startswith(KEEP_PREFIX)]
    # 清理备份按时间升序，最早的（=清理前、最全）那份留，其余是子集
    purge_keep = [min(purge, key=lambda p: p.stat().st_mtime)] if purge else []
    purge_drop = [p for p in purge if p not in purge_keep]
    drop = others[keep:] + purge_drop
    return others[:keep] + purge_keep, drop


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=2)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    keep, drop = plan(args.keep)
    freed = sum(p.stat().st_size for p in drop)
    print(f"保留 {len(keep)} 份 / 删除 {len(drop)} 份 / 回收 {freed / 1e6:.1f} MB")
    for p in keep:
        print("  keep", p.name, f"{p.stat().st_size / 1e6:.1f} MB")
    for p in drop:
        print("  drop", p.name, f"{p.stat().st_size / 1e6:.1f} MB")
        if args.apply:
            p.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
