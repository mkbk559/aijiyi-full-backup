"""清理脚本：删"确定的垃圾"，保留证据与用户内容。每一步都打印路径，删完给前后对比。

保留（明确不动）：
- `.tmp/research/`（16 个仓库 README 原文，docs 里引作证据）、`.tmp/pdf/`（手册抽取文本）
- `data/`（真库 + 用户克隆音色 477MB + 歌曲）、`voice-engine/`（引擎与模型）
- `.venv/`、`baseline.json`（check_baseline.py 用）、`dist/aiji-0.8.3*`（当前版本产物）

删除（可再生成/无引用）：
- `.dsh-vision-router/`（视觉调用产生的截图与裁剪图）
- `.tmp/` 下除 research/pdf 之外的一切（测试工作目录，4000+ MB）
- 根目录 `aiji-style-*` / `aiji-being-checkup-*` / `aiji-life-loop-*` 空目录、`tmp*`、`torchinductor_*`
- `build/`、`.pytest_cache/`、`.ruff_cache/`、`.coverage`、`aiji.egg-info/`、所有 `__pycache__/`
- `dist/` 里除当前版本(0.8.3)外的旧包

用法： python tools/cleanup_junk.py [--apply]   （默认 dry-run，只打印）
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEEP_IN_TMP = {"research", "pdf"}
KEEP_DIST_PREFIX = "aiji-0.8.3"


def size_of(p: Path) -> int:
    try:
        if p.is_file():
            return p.stat().st_size
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


def collect() -> list[Path]:
    out: list[Path] = []
    for name in (".dsh-vision-router", "build", ".pytest_cache", ".ruff_cache",
                 "aiji.egg-info", "torchinductor_22166"):
        p = ROOT / name
        if p.exists():
            out.append(p)
    cov = ROOT / ".coverage"
    if cov.exists():
        out.append(cov)
    tmp = ROOT / ".tmp"
    if tmp.is_dir():
        out += [p for p in tmp.iterdir() if p.name not in KEEP_IN_TMP]
    for pat in ("aiji-style-*", "aiji-being-checkup-*", "aiji-life-loop-*", "tmp*"):
        out += [p for p in ROOT.glob(pat) if p.is_dir()]
    dist = ROOT / "dist"
    if dist.is_dir():
        out += [p for p in dist.iterdir() if not p.name.startswith(KEEP_DIST_PREFIX)]
    out += [p for p in ROOT.rglob("__pycache__")
            if p.is_dir() and not {".venv", "voice-engine", ".tmp"} & set(p.parts)]
    # 去重 + 去掉"被别的候选包含"的路径（父目录已删就别再列子目录）
    uniq, seen = [], set()
    for p in out:
        s = str(p).lower()
        if s in seen:
            continue
        seen.add(s)
        uniq.append(p)
    return sorted(uniq, key=lambda p: len(str(p)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的删；不加只打印")
    args = ap.parse_args(argv)

    targets = collect()
    total = 0
    freed = 0
    failed: list[tuple[str, str]] = []
    for p in targets:
        if not p.exists():
            continue
        n = size_of(p)
        total += n
        if args.apply:
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                freed += n
                print(f"DEL  {p.relative_to(ROOT)}  ({n/1024/1024:.1f} MB)")
            except OSError as exc:
                failed.append((str(p.relative_to(ROOT)), f"{type(exc).__name__}: {exc}"))
        else:
            print(f"WOULD {p.relative_to(ROOT)}  ({n/1024/1024:.1f} MB)")

    print(f"\n候选 {len(targets)} 项，合计 {total/1024/1024:.1f} MB"
          f"，实际删除 {freed/1024/1024:.1f} MB")
    if failed:
        print(f"\n删不掉的 {len(failed)} 项（多半是 Windows ACL/句柄占用）：")
        for rel, err in failed[:12]:
            print(f"  {rel}: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
