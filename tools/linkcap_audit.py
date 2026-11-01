# -*- coding: utf-8 -*-
"""LINK_CAP 触顶丢边审计（用户点名的数据判定：cap=3 丢的是噪声还是语义）。

方法：回放混合语料（3 个语义簇 + 噪声），每次 add_fact 时收集 link_facts 的
dropped 出参——"过阈值却落选"的边才是 cap 的真实代价（阈值下被滤的是设计内噪声）。
输出：丢边分布（sim 分桶）、样例、cap 敏感度（cap=3 vs 6 vs 10 的保留差）。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiji import store  # noqa: E402
from aiji.db import DB
from aiji.store import FactDraft, Ident, add_fact  # noqa: E402

WORK = ROOT / ".tmp"
WORK.mkdir(mode=0o777, exist_ok=True)

# 语料：3 个语义簇（簇内句式相似、2-gram 高重叠）+ 簇间噪声
CORPUS = [
    # 簇 A：居住地-苏州（句式相近）
    "我住在苏州工业园区", "我住在苏州姑苏区", "我住在苏州吴中区",
    "我住在苏州相城区", "我住在苏州虎丘区",
    # 簇 B：周末活动-爬山（动作相似）
    "我周末喜欢去爬山", "我周末喜欢去骑车", "我周末喜欢去钓鱼",
    "我周末喜欢去露营", "我周末喜欢去跑步",
    # 簇 C：工作-后端（主题相似）
    "我工作用 python 写后端", "我工作用 go 写后端", "我工作用 java 写后端",
    "我工作用 rust 写后端", "我工作用 node 写后端",
    # 噪声：互不相关
    "今天天气不错", "刚看完一部电影", "早饭吃了包子", "我家猫叫团子",
    "晚上想吃火锅", "手机该充电了",
]


def run(cap: int, tag: str) -> dict:
    p = WORK / f"linkcap_{tag}.db"
    if p.exists():
        p.unlink()
    db = DB(p)
    ident = Ident("qq", "u_audit")
    dropped_all: list[tuple[str, str, float]] = []   # (src_text, dst_text, sim)
    kept_edges = 0
    real_link = store.link_facts
    with db.tx() as c:
        for text in CORPUS:
            # add_fact 内部会自动 link（cap 固定 3）——先置空，由审计统一按 cap 调用，
            # 保证 kept/dropped 来自同一次真实调用（否则 INSERT OR IGNORE 全忽略，kept 失真）
            store.link_facts = lambda *a, **k: 0
            try:
                fid, _ = add_fact(c, ident, FactDraft("user", "备注", text[:50], text, 0.6))
            finally:
                store.link_facts = real_link
            drop: list[tuple[float, str]] = []
            n = real_link(c, ident, fid, "user", "备注", text, max_links=cap,
                          dropped=drop)
            kept_edges += n // 2  # 双向边，去半
            for s, dt in drop:
                dropped_all.append((text, dt, s))
    db.close()
    p.unlink(missing_ok=True)
    return {"kept": kept_edges, "dropped": dropped_all}


def classify(sim: float) -> str:
    """粗分类：≥0.5 明显语义近邻；0.35–0.5 边缘（可能噪声）。"""
    return "semantic" if sim >= 0.5 else "borderline"


def main() -> None:
    print("== LINK_CAP 丢边审计 ==")
    results: dict[int, dict] = {}
    for cap in (3, 6, 10):
        results[cap] = run(cap, f"cap{cap}")

    base = results[3]
    print(f"\ncap=3（现状）：保留边 {base['kept']}，触顶丢边 {len(base['dropped'])}")
    if base["dropped"]:
        sem = [d for d in base["dropped"] if classify(d[2]) == "semantic"]
        print(f"  其中 semantic(≥0.5): {len(sem)}，borderline(0.35-0.5): "
              f"{len(base['dropped']) - len(sem)}")
        # sim 分桶
        buckets = {"0.5+": 0, "0.45-0.5": 0, "0.4-0.45": 0, "0.35-0.4": 0}
        for _, _, s in base["dropped"]:
            k = "0.5+" if s >= 0.5 else ("0.45-0.5" if s >= 0.45 else
                                         ("0.4-0.45" if s >= 0.4 else "0.35-0.4"))
            buckets[k] += 1
        print(f"  sim 分桶: {buckets}")
        print("  样例（前 8 条，src → 被丢的 dst [sim]）:")
        for src, dst, s in base["dropped"][:8]:
            print(f"    {s:.2f}  {src[:22]} → {dst[:22]}")
    for cap in (6, 10):
        extra_kept = results[cap]["kept"] - base["kept"]
        extra_dropped = len(results[cap]["dropped"])
        print(f"cap={cap}: 保留边 {results[cap]['kept']}（+{extra_kept}），"
              f"仍触顶丢 {extra_dropped}")

    # 结论判据（数据说话）
    sem_n = sum(1 for d in base["dropped"] if classify(d[2]) == "semantic")
    total = len(base["dropped"])
    if total == 0:
        print("\n[结论] cap=3 在该语料上无触顶丢失——cap 足够，不动")
    elif sem_n / total >= 0.5 and total >= 5:
        print(f"\n[结论] 触顶丢失以语义边为主（{sem_n}/{total} ≥50%）——cap=3 偏紧，"
              f"建议上调（数据支撑，改 params.LINK_CAP 需另跑回归）")
    else:
        print(f"\n[结论] 触顶丢失以边缘边为主（{total - sem_n}/{total}）——cap=3 "
              f"丢的多是 borderline，现值合理")


if __name__ == "__main__":
    main()
