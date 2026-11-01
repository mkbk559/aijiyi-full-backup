"""P2-1：巩固后自检（20 条固定探针，anda-brain self-test 思路）。

巩固/代谢每次改动都可能"悄悄变差"——不是崩，是记不住、或者乱记。这个脚本用
20 条固定问题给出 4 个可判定的数字，改完就跑一遍（也能挂 CI）：

  hit@5     语料里有的，是否被注进 prompt
  abstain   语料里没有的，是否安静（注入块为空，不硬凑）
  stale     200 天前的老事实，是否被标成"可能过时"（tier=stale，而不是被删）
  leak      另一个身份的隐私，是否漏进来（必须 0）

注意：与 `bench_memory_quality.py` 分工不同——那个测冲突消解/排序/关联，
这个测"代谢层上线后记忆还准不准"。

跑法： python bench/bench_self_test.py [--json out.json]
退出码：0 = 全过阈值；1 = 有回归（阈值见 GATES，按 2026-11-02 实测基线下调一档）
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiji.config import Settings  # noqa: E402
from aiji.memory import Memory  # noqa: E402
from aiji.store import FactDraft, Ident, add_fact  # noqa: E402

ME = Ident("selfcheck", "me", "default")
OTHER = Ident("selfcheck", "other", "default")
DAY = 86400.0

# (谓词, 宾语, 原文, 提问, 期望关键词) —— 词面有重叠：检索层该解决
ANSWERABLE = [
    ("居住地", "成都", "用户住在成都", "我现在住在哪", "成都"),
    ("职业", "数据分析师", "用户是数据分析师", "我是数据分析师吗", "数据分析师"),
    ("宠物", "布偶猫", "用户养了布偶猫", "我养的布偶猫", "布偶猫"),
    ("口味", "不吃辣", "用户不吃辣", "我吃辣吗", "不吃辣"),
    ("作息", "晚上十一点睡", "用户晚上十一点睡", "我晚上几点睡", "十一点"),
    ("家乡", "洛阳", "用户老家在洛阳", "我老家在哪", "洛阳"),
    ("爱好", "爬山", "用户喜欢爬山", "周末我喜欢做什么", "爬山"),
    ("目标", "考过雅思", "用户想考过雅思", "我想考过雅思", "雅思"),
]

# 换了说法、词面不重叠：需要语义层（当前 hashing 索引做不到，只报数、不设门槛）
SEMANTIC = [
    ("居住地", "成都", "用户住在成都", "我现在落脚在哪儿", "成都"),
    ("职业", "数据分析师", "用户是数据分析师", "我是干什么的", "数据分析师"),
    ("宠物", "布偶猫", "用户养了布偶猫", "我家那只猫是什么品种", "布偶猫"),
    ("目标", "考过雅思", "用户想考过雅思", "我今年的目标", "雅思"),
]

# 极短提问（只能靠单字命中）：这是"开弃答门"会误伤的那一类，单独报数，
# 也是 inject_min_rel 默认关掉的理由（2026-11-02 实测：开 0.15 时这 3 条全空）。
SHORT = [
    ("居住地", "成都", "用户住在成都", "我住哪", "成都"),
    ("家乡", "洛阳", "用户老家在洛阳", "我老家", "洛阳"),
    ("爱好", "爬山", "用户喜欢爬山", "爱好", "爬山"),
]

# 语料里完全没有的主题：应当安静（弃答），不要硬凑
# 注意：不能选与语料有共同实词的（"我最喜欢的电影"含"喜欢"、"门禁密码"含"公司"，
# 那属于"检索到了但不该当答案"，不是弃答场景——2026-11-02 第一次跑就踩了这个坑）。
ABSTAIN = [
    "我的车牌号是多少", "我二舅叫什么", "我上个月体检结果",
    "我房贷还剩多少", "我的护照什么时候到期", "我表姐家的狗叫什么",
]

# 老事实：要标成"可能过时"，但仍能查到（查询保留与原文的字面重叠，否则测的是检索不是档位）
STALE = [
    ("手机号", "13800000000", "用户手机号是13800000000", "我的手机号是多少", "13800000000"),
    ("公司", "老东家A公司", "用户在老东家A公司上班", "我在哪家公司上班", "老东家A公司"),
    ("住址", "老城区B路", "用户住在老城区B路", "我住在哪条路", "老城区B路"),
]

# 另一个身份的事实：绝不能出现在我的注入块里
OTHER_FACTS = [
    ("居住地", "乌鲁木齐", "对方住在乌鲁木齐"),
    ("职业", "牙医", "对方是牙医"),
    ("宠物", "蜥蜴", "对方养了蜥蜴"),
]

# 门槛：按 2026-11-02 实测基线定（不追求漂亮，追求"退步就红"）
# 实测：hit@5 1.0 / abstain 0.5 / stale 1.0 / leak 0 / semantic 0.25 / short 0.667
GATES = {"hit_at_5": 0.875, "abstain": 0.45, "stale": 0.90, "leak": 0}


def build(m: Memory) -> None:
    old = time.time() - 200 * DAY
    with m.db.tx() as c:
        for pred, obj, text, _q, _kw in ANSWERABLE:
            add_fact(c, ME, FactDraft("user", pred, obj, text, 0.9))
        for pred, obj, text, _q, _kw in STALE:
            fid, _ = add_fact(c, ME, FactDraft("user", pred, obj, text, 0.85))
            c.execute("UPDATE fact SET updated_at=? WHERE id=?", (old, fid))
        for pred, obj, text in OTHER_FACTS:
            add_fact(c, OTHER, FactDraft("user", pred, obj, text, 0.9))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    ap.add_argument("--gate", type=float, default=None,
                    help="临时打开弃答门（如 0.15），用来看它对 hit@5/abstain 的取舍")
    args = ap.parse_args(argv)

    work = ROOT / ".tmp" / "bench_self_test"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(mode=0o777, parents=True, exist_ok=True)
    m = Memory(Settings(db_path=work / "q.db", routes=()), engines="auto")
    if args.gate is not None:
        m.injector.min_rel = float(args.gate)
    rows: list[dict] = []
    try:
        build(m)
        m.recaller.refresh_profile(ME)
        m.recaller.refresh_profile(OTHER)

        for _p, _o, text, query, kw in ANSWERABLE:
            inj = m.inject(query, ME)
            hit = kw in inj.text
            rows.append({"kind": "answerable", "q": query, "ok": hit,
                         "detail": "命中" if hit else f"未命中（{text}）"})

        for _p, _o, text, query, kw in SEMANTIC:
            inj = m.inject(query, ME)
            hit = kw in inj.text
            rows.append({"kind": "semantic", "q": query, "ok": hit,
                         "detail": "命中" if hit else f"未命中（{text}，需要语义层）"})

        for _p, _o, text, query, kw in SHORT:
            inj = m.inject(query, ME)
            hit = kw in inj.text
            rows.append({"kind": "short", "q": query, "ok": hit,
                         "detail": "命中" if hit else f"未命中（{text}，单字命中被弃答门挡了）"})

        for query in ABSTAIN:
            inj = m.inject(query, ME)
            ok = inj.text.strip() == ""
            rows.append({"kind": "abstain", "q": query, "ok": ok,
                         "detail": "安静" if ok else f"硬凑了 {len(inj.items)} 条"})

        for _p, _o, text, query, kw in STALE:
            inj = m.inject(query, ME)
            tiers = {i.tier for i in inj.items}
            ok = kw in inj.text and "stale" in tiers
            rows.append({"kind": "stale", "q": query, "ok": ok,
                         "detail": f"tiers={sorted(tiers)}"})

        for _p, _o, text in OTHER_FACTS:
            leaked = text in m.inject(text, ME).text
            rows.append({"kind": "leak", "q": text, "ok": not leaked,
                         "detail": "串号！" if leaked else "隔离正常"})
    finally:
        m.close()

    def rate(kind: str) -> float:
        sub = [r for r in rows if r["kind"] == kind]
        return round(sum(r["ok"] for r in sub) / len(sub), 3) if sub else 1.0

    metrics = {
        "hit_at_5": rate("answerable"),
        "abstain": rate("abstain"),
        "stale": rate("stale"),
        "leak": sum(0 if r["ok"] else 1 for r in rows if r["kind"] == "leak"),
        "semantic": rate("semantic"),
        "short": rate("short"),
        "total": len(rows),
        "passed": sum(r["ok"] for r in rows),
    }
    gates = {
        "hit_at_5": metrics["hit_at_5"] >= GATES["hit_at_5"],
        "abstain": metrics["abstain"] >= GATES["abstain"],
        "stale": metrics["stale"] >= GATES["stale"],
        "leak": metrics["leak"] <= GATES["leak"],
    }
    report = {"metrics": metrics, "gates": gates, "thresholds": GATES, "rows": rows}
    print(json.dumps({"metrics": metrics, "gates": gates}, ensure_ascii=False, indent=2))
    for r in rows:
        print(f"  [{'PASS' if r['ok'] else 'FAIL'}] {r['kind']:<10} {r['q']} — {r['detail']}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    ok = all(gates.values())
    print("\nALL GATES PASS" if ok else "\nREGRESSION")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
