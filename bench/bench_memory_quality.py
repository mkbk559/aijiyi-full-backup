"""记忆质量压测：冲突消解正确率、排序 top-1 命中率、1 跳关联召回命中率。

不测延迟（那是 bench_recall / bench_http 的事），只测"新加的语义行为有没有真的生效"：
  1. reconcile：多值累积 / 同值合并 / 单值覆盖 / 否定失效 / 跨平台隔离；
  2. ranking：重要度、时间衰减、反馈命中三种信号各能改变排序结果；
  3. links：直接命中一条事实后，1 跳邻居能被一起召回。

判定（退出码非 0 即失败）：
  - reconcile_accuracy == 1.0
  - ranking_top1 >= 0.9
  - link_recall >= 0.9
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiji import Memory  # noqa: E402
from aiji.right_hand.config import Settings  # noqa: E402
from aiji.hippocampus.recall import score_fact  # noqa: E402
from aiji.hippocampus.store import FactDraft, Ident, add_fact  # noqa: E402

WX = Ident("wechat", "u1", "default")
DY = Ident("douyin", "u1", "default")


def _fresh_db(root: Path, name: str) -> Memory:
    db = root / f"{name}-{int(time.time() * 1000)}.db"
    return Memory(Settings(db_path=db, routes=(), workers=1))


def check_reconcile(root: Path) -> dict:
    m = _fresh_db(root, "reconcile")
    cases: list[tuple[str, bool]] = []
    c = m.db.conn()
    try:
        with m.db.tx() as c:
            add_fact(c, WX, FactDraft("user", "偏好", "爬山", "用户喜欢爬山", 0.8))
        with m.db.tx() as c:
            add_fact(c, WX, FactDraft("user", "偏好", "游泳", "用户喜欢游泳", 0.8))
        cases.append(("多值偏好累积",
                      m.db.read("SELECT COUNT(*) c FROM fact WHERE status='active'")[0]["c"] == 2))

        with m.db.tx() as c:
            add_fact(c, WX, FactDraft("user", "居住地", "杭州", "用户住在杭州", 0.9))
        with m.db.tx() as c:
            add_fact(c, WX, FactDraft("user", "居住地", "上海", "用户住在上海", 0.9))
        row = m.db.read("SELECT status, valid_to FROM fact WHERE object='杭州'")[0]
        cases.append(("单值属性覆盖且留有效期",
                      row["status"] == "superseded" and row["valid_to"] is not None))

        with m.db.tx() as c:
            add_fact(c, WX, FactDraft("user", "偏好", "爬山", "用户喜欢爬山", 0.9))
        row2 = m.db.read("SELECT COUNT(*) c, MAX(mentions) m FROM fact WHERE object='爬山'")[0]
        cases.append(("同值再说一遍只合并", row2["c"] == 1 and row2["m"] >= 2))

        with m.db.tx() as c:
            add_fact(c, WX, FactDraft("user", "偏好", "游泳", "用户不再喜欢游泳", 0.55,
                                      extra={"negated": True}))
        cases.append(("否定句让旧值失效",
                      m.db.read("SELECT COUNT(*) c FROM fact WHERE object='游泳' AND status='active'"
                                )[0]["c"] == 0))

        with m.db.tx() as c:
            add_fact(c, DY, FactDraft("user", "居住地", "成都", "用户住在成都", 0.9))
        wx_home = m.db.read(
            "SELECT COUNT(*) c FROM fact WHERE platform='wechat' AND predicate='居住地'"
            " AND status='active'")[0]["c"]
        dy_home = m.db.read(
            "SELECT COUNT(*) c FROM fact WHERE platform='douyin' AND predicate='居住地'"
            " AND status='active'")[0]["c"]
        cases.append(("跨平台互不覆盖", wx_home == 1 and dy_home == 1))

        with m.db.tx() as c:
            add_fact(c, WX, FactDraft("user", "偏好", "不存在", "用户不再喜欢不存在", 0.55,
                                      extra={"negated": True}))
        cases.append(("否定无目标不产生垃圾",
                      m.db.read("SELECT COUNT(*) c FROM fact WHERE object='不存在'")[0]["c"] == 0))
    finally:
        m.close()
    passed = sum(1 for _, ok in cases if ok)
    return {"passed": passed, "total": len(cases),
            "accuracy": round(passed / len(cases), 4),
            "cases": [{"case": name, "ok": ok} for name, ok in cases]}


def check_ranking(root: Path) -> dict:
    now = time.time()
    # 1) 重要度：同一天写入，身份事实 vs 备注，查同一个词
    imp_ok = 0
    for i, city in enumerate(["杭州", "北京", "上海", "深圳"] * 5):
        target = _fact_obj(i * 2 + 1, "居住地", city, f"用户住在{city}", 0.9, now, 0.9)
        note = _fact_obj(i * 2 + 2, "备注", city, f"用户备注：{city}", 0.9, now, 0.585)
        imp_ok += int(score_fact(target, 0, now, 0.6) > score_fact(note, 1, now, 0.6))

    # 2) 时间衰减：同权重，一条 1 天前、一条 300 天前
    fresh_ok = 0
    for i in range(20):
        fresh = _fact_obj(100 + i * 2, "技术栈", "跑步机", "项目使用跑步机", 0.8,
                          now - 86400, 0.585)
        stale = _fact_obj(101 + i * 2, "技术栈", "跑步机", "项目使用跑步机", 0.8,
                          now - 300 * 86400, 0.585)
        fresh_ok += int(score_fact(fresh, 0, now, 0.6) > score_fact(stale, 1, now, 0.6))

    # 3) 反馈：同一条事实，被引用过 10 次 vs 0 次
    fb_ok = 0
    for i in range(20):
        plain = _fact_obj(200 + i * 2, "偏好", "摄影", "用户喜欢摄影", 0.8, now, 0.66)
        used = _fact_obj(201 + i * 2, "偏好", "摄影", "用户喜欢摄影", 0.8, now, 0.66,
                         hit_count=10)
        fb_ok += int(score_fact(used, 0, now, 0.6) > score_fact(plain, 1, now, 0.6))

    total = 60  # 三组各 20 条
    top1 = round((imp_ok + fresh_ok + fb_ok) / total, 4)
    return {"n": total, "top1": top1, "detail": {
        "importance": round(imp_ok / 20, 4),
        "decay": round(fresh_ok / 20, 4),
        "feedback": round(fb_ok / 20, 4),
    }}


def _fact_obj(fid: int, predicate: str, obj: str, text: str, conf: float, ts: float,
              importance: float, hit_count: int = 0):
    from aiji.hippocampus.store import Fact
    return Fact(
        id=fid, platform="wechat", platform_user_id="u1", scope="default",
        subject="user", predicate=predicate, object=obj, text=text, confidence=conf,
        status="active", created_at=ts, updated_at=ts, source_kind="heuristic",
        importance=importance, hit_count=hit_count,
    )


CITIES = [
    "杭州", "北京", "上海", "深圳", "成都", "武汉", "西安", "南京", "苏州", "广州",
    "重庆", "天津", "长沙", "郑州", "青岛", "大连", "厦门", "福州", "昆明", "合肥",
    "济南", "沈阳", "哈尔滨", "长春", "石家庄", "太原", "南昌", "贵阳", "南宁", "兰州",
]


def check_links(root: Path, clusters: int = 30) -> dict:
    """每个身份两条事实：一条住址（查询能直接命中），一条工作地（只能靠 1 跳关联带出）。

    查询只带“住哪”：工作地那条不含“住”字，FTS/二字组/单字兜底都直接命中不了它，
    所以它能出现在结果里，就说明关联扩展真的生效了。
    """
    m = _fresh_db(root, "links")
    hits = 0
    users = [Ident("wechat", f"link_u{i}", "default") for i in range(clusters)]
    try:
        for i, who in enumerate(users):
            city = CITIES[i % len(CITIES)]
            with m.db.tx() as c:
                add_fact(c, who, FactDraft("user", "居住地", city, f"用户住在{city}", 0.9))
                add_fact(c, who, FactDraft("user", "职业", f"{city}某公司",
                                           f"用户在{city}上班", 0.85))
        for i, who in enumerate(users):
            city = CITIES[i % len(CITIES)]
            texts = [h.text for h in m.recall("住哪", who, top_k=8).hits]
            hits += int(any(f"住在{city}" in t for t in texts)
                        and any(f"在{city}上班" in t for t in texts))
    finally:
        m.close()
    return {"n": clusters, "hit_rate": round(hits / clusters, 4),
            "links": "每事实最多 3 条近邻（双向）"}


def run(args) -> int:
    root = Path(__file__).resolve().parent.parent / ".tmp"
    root.mkdir(mode=0o777, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root, ignore_cleanup_errors=True) as td:
        r = check_reconcile(Path(td))
        q = check_ranking(Path(td))
        l = check_links(Path(td), args.clusters)
    payload = {
        "reconcile": r,
        "ranking": q,
        "link_recall": l,
        "ok": r["accuracy"] >= 1.0 and q["top1"] >= 0.9 and l["hit_rate"] >= 0.9,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        Path(args.json).write_text(text, "utf-8")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    ap = argparse.ArgumentParser()
    ap.add_argument("--clusters", type=int, default=30)
    ap.add_argument("--json", default=None)
    raise SystemExit(run(ap.parse_args()))
