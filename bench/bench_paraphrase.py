"""阶段 0-②：换说法召回（Recall@1 / Recall@5 / MRR），weighted vs RRF。

为什么需要它：现在没有任何数字能说明"换个说法还找不找得到"。这个 bench 用
**固定语料 + 固定查询**给出可重复的基线，改打分/加事实键/切 RRF 之后跑同一套
对比，才谈得上"变好了"。

两类查询分开报：
  - `para`：换了句式的同义提问（与事实有部分字面重叠）——检索层该解决；
  - `semantic`：完全不含原词（"我住哪儿" → "用户住在杭州"）——需要向量层，
    当前 hashing 索引下基本必然失败，单独统计、不算进主指标。

跑法：
    python bench/bench_paraphrase.py                # weighted 与 rrf 各跑一遍
    python bench/bench_paraphrase.py --mode weighted --no-keys
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiji.hippocampus import store  # noqa: E402
from aiji.hippocampus.db import DB  # noqa: E402
from aiji.hippocampus.recall import Recaller  # noqa: E402
from aiji.hippocampus.store import FactDraft, Ident  # noqa: E402

IDENT = Ident("bench", "para", "default")

# (主语, 谓词, 宾语, 原文, 换说法查询)  —— 查询与原文共享 2-gram，但不是同一句
PARA_CASES: list[tuple[str, str, str, str, str]] = [
    ("user", "居住地", "杭州", "用户住在杭州", "她现在住哪个城市"),
    ("user", "职业", "后端工程师", "用户是后端工程师", "他做什么工作"),
    ("user", "宠物", "橘猫", "用户养了一只橘猫", "家里那只猫是什么品种"),
    ("user", "作息", "凌晨两点睡", "用户习惯凌晨两点睡", "他一般几点睡觉"),
    ("user", "口味", "不吃香菜", "用户不吃香菜", "点菜要注意什么忌口"),
    ("user", "通勤", "坐地铁四号线", "用户坐地铁四号线通勤", "上班怎么过去"),
    ("user", "爱好", "钓鱼", "用户喜欢钓鱼", "周末有什么爱好"),
    ("user", "学历", "计算机硕士", "用户是计算机硕士", "他念的什么专业"),
    ("user", "家乡", "潮汕", "用户老家在潮汕", "他是哪里人"),
    ("user", "项目", "记忆中间件", "用户在做记忆中间件", "最近在忙什么项目"),
    ("user", "目标", "减重十斤", "用户想减重十斤", "他今年的目标是什么"),
    ("user", "设备", "机械键盘", "用户用机械键盘", "他用什么键盘打字"),
]

# 完全不含原词（语义层，当前必然失败，单独统计）
SEMANTIC_CASES: list[tuple[str, str, str, str, str]] = [
    ("user", "居住地", "杭州", "用户住在杭州", "他现在落脚在哪儿"),
    ("user", "口味", "不吃香菜", "用户不吃香菜", "给他点菜要不要放芫荽"),
]


def build_corpus(db: DB, *, with_keys: bool = True) -> dict[int, str]:
    """返回 {fact_id: case_key}，case_key = 原来的查询（便于算命中率）。"""
    ids: dict[int, str] = {}
    with db.tx() as c:
        for subject, pred, obj, text, _q in PARA_CASES + SEMANTIC_CASES:
            fid, _ = store.add_fact(c, IDENT, FactDraft(subject, pred, obj, text, 0.9))
            ids[int(fid)] = text
            if not with_keys:
                c.execute("UPDATE fact SET extra=? WHERE id=?",
                          (json.dumps({"keys": []}, ensure_ascii=False), fid))
    return ids


def run_mode(db: DB, mode: str, *, with_keys: bool = True) -> dict:
    recaller = Recaller(db, budget_ms=200, top_k=8, recent=40, rank_mode=mode)
    recaller.refresh_profile(IDENT)
    out = {"mode": mode, "keys": with_keys, "para": [], "semantic": []}
    for bucket, cases in (("para", PARA_CASES), ("semantic", SEMANTIC_CASES)):
        for _s, _p, _o, text, query in cases:
            res = recaller.recall(query, IDENT, top_k=8)
            got = [h.id for h in res.hits if h.kind in ("fact", "semantic")]
            rank = next((i + 1 for i, h in enumerate(res.hits)
                         if h.kind in ("fact", "semantic") and h.text == text), 0)
            out[bucket].append({
                "query": query, "target": text, "rank": rank,
                "top1": rank == 1, "top5": 0 < rank <= 5,
                "rr": (1.0 / rank) if rank else 0.0,
                "got": got[:5], "took_ms": round(res.took_ms, 2),
            })
    for bucket in ("para", "semantic"):
        rows = out[bucket]
        n = len(rows) or 1
        out[bucket + "_metrics"] = {
            "n": len(rows),
            "recall_at_1": round(sum(r["top1"] for r in rows) / n, 3),
            "recall_at_5": round(sum(r["top5"] for r in rows) / n, 3),
            "mrr": round(sum(r["rr"] for r in rows) / n, 3),
            "p95_ms": round(sorted(r["took_ms"] for r in rows)[max(0, int(0.95 * n) - 1)], 2),
        }
    recaller.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="both", choices=("weighted", "rrf", "both"))
    ap.add_argument("--no-keys", action="store_true", help="清空事实键，测键的净贡献")
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    work = ROOT / ".tmp" / "bench_paraphrase"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(mode=0o777, parents=True, exist_ok=True)
    db = DB(work / "para.db")
    build_corpus(db, with_keys=not args.no_keys)

    modes = ("weighted", "rrf") if args.mode == "both" else (args.mode,)
    report = {"keys": not args.no_keys, "runs": []}
    for mode in modes:
        report["runs"].append(run_mode(db, mode, with_keys=not args.no_keys))

    print(f"{'mode':<10}{'keys':<6}{'R@1':>7}{'R@5':>7}{'MRR':>7}{'p95ms':>8}"
          f"   | 语义层 R@5")
    for r in report["runs"]:
        m, s = r["para_metrics"], r["semantic_metrics"]
        print(f"{r['mode']:<10}{str(r['keys']):<6}{m['recall_at_1']:>7.3f}"
              f"{m['recall_at_5']:>7.3f}{m['mrr']:>7.3f}{m['p95_ms']:>8.1f}"
              f"   | {s['recall_at_5']:.3f}")
    print("\n失败样例（para，rank=0 表示 8 条内没找到）：")
    for r in report["runs"][:1]:
        for row in r["para"]:
            if not row["top5"]:
                print(f"  - Q「{row['query']}」→ 目标「{row['target']}」rank={row['rank']}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
