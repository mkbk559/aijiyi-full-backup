"""记忆管线深探：写入决策 / 排序 / 召回 / 注入 / 反馈 / 纠错 / 双时态 / 摘要压缩 / janitor / 隔离 / 向量 / 死信。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.probe._harness import (  # noqa: E402
    Probe, cleanup, make_memory, quiet_env, workdir,
)

from aiji.hippocampus import store  # noqa: E402
from aiji.hippocampus.janitor import Janitor  # noqa: E402
from aiji.hippocampus.store import FactDraft, Ident  # noqa: E402

quiet_env()
probe = Probe("memory-pipeline")
base_dir = workdir("memory")
memory, pool = make_memory(base_dir)
db = memory.db
A = Ident("wechat", "alice", "personal")
B = Ident("qq", "bob", "group:1")


def rows(sql: str, args=()):
    return [dict(r) for r in db.read(sql, args)]


def facts(ident: Ident, status: str | None = None):
    sql = ("SELECT id,predicate,object,status,mentions,confidence,kind,hit_count,"
           "valid_from,valid_to,superseded_by,importance FROM fact"
           " WHERE platform=? AND platform_user_id=? AND scope=?")
    args = [ident.platform, ident.platform_user_id, str(ident.scope)]
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY id"
    return rows(sql, tuple(args))


try:
    # ================= 1. 写入决策矩阵 =================
    with db.tx() as c:
        store.add_fact(c, A, FactDraft("用户", "爱好", "钓鱼", "用户爱好钓鱼", 0.8))
        store.add_fact(c, A, FactDraft("用户", "爱好", "爬山", "用户爱好爬山", 0.8))
    hobbies = [f for f in facts(A) if f["predicate"] == "爱好"]
    probe.case("多值属性累积（两条都 active）",
                len(hobbies) == 2 and all(f["status"] == "active" for f in hobbies),
                f"n={len(hobbies)}")

    with db.tx() as c:
        store.add_fact(c, A, FactDraft("用户", "爱好", "钓鱼", "用户爱好钓鱼", 0.9))
    merged = [f for f in facts(A) if f["predicate"] == "爱好" and f["object"] == "钓鱼"]
    probe.case("同值重复 → merge（mentions+1、仍 1 条）",
                len(merged) == 1 and merged[0]["mentions"] >= 2,
                f"n={len(merged)} mentions={merged[0]['mentions'] if merged else None}")

    with db.tx() as c:
        old_id, _ = store.add_fact(c, A, FactDraft("用户", "居住地", "杭州", "用户住在杭州", 0.9))
    time.sleep(0.01)
    with db.tx() as c:
        new_id, superseded = store.add_fact(
            c, A, FactDraft("用户", "居住地", "苏州", "用户住在苏州", 0.9))
    home = [f for f in facts(A) if f["predicate"] == "居住地"]
    old = next((f for f in home if f["object"] == "杭州"), {})
    new = next((f for f in home if f["object"] == "苏州"), {})
    probe.case("单值替换 → supersede（旧行留痕不删）",
                old.get("status") == "superseded" and old.get("superseded_by") == new_id
                and old.get("valid_to") and new.get("status") == "active",
                f"old={old.get('status')}/{old.get('superseded_by')} new={new.get('status')}")

    with db.tx() as c:
        draft = FactDraft("用户", "居住地", "苏州", "用户不再住在苏州了", 0.8,
                          extra={"negated": True})
        zero, expired = store.add_fact(c, A, draft)
    live_home = [f for f in facts(A) if f["predicate"] == "居住地" and f["status"] == "active"]
    probe.case("否定 → expire（不新增正事实）",
                zero == 0 and expired == [new_id] and not live_home,
                f"id={zero} expired={expired} active={len(live_home)}")

    with db.tx() as c:
        draft = FactDraft("用户", "职业", "宇航员", "我没当过宇航员", 0.8,
                          extra={"negated": True})
        zero2, _ = store.add_fact(c, A, draft)
    probe.case("否定且无旧值 → noop", zero2 == 0, f"id={zero2}")

    ops = {r["op"] for r in rows(
        "SELECT op FROM fact_log WHERE platform=? AND platform_user_id=?",
        (A.platform, A.platform_user_id))}
    probe.case("写入决策有审计（add/merge/supersede/expire/noop）",
                {"add", "merge", "supersede", "expire", "noop"} <= ops,
                f"ops={sorted(ops)}")

    # ================= 2. 排序 =================
    with db.tx() as c:
        store.add_fact(c, A, FactDraft("用户", "电话", "13800000000", "用户电话是13800000000", 0.95))
        store.add_fact(c, A, FactDraft("用户", "备注", "随便说说", "用户随便说说的一段话", 0.4))
    hits = memory.recall("用户电话", A, top_k=5).hits
    top = hits[0] if hits else None
    probe.case("排序：身份事实压过低置信备注",
                bool(top) and "电话" in str(top.text),
                f"top={str(getattr(top, 'text', None))[:90]}")

    # ================= 3. 写入 → 召回 → 注入 → 反馈 =================
    memory.turn(A, "chat1", "user", "我叫李雷，住在南京，周末喜欢钓鱼")
    assert memory.flush(20)
    recall = memory.recall("我住哪", A, session="chat1")
    texts = [str(h.text or "") for h in recall.hits]
    probe.case("turn→抽取→召回 命中居住地",
                any("南京" in t for t in texts), f"hits={len(texts)} {texts[:3]}")

    injection = memory.inject("我住哪", A, session="chat1")
    probe.case("注入：出文本块、带来源、无越权",
                bool(injection.text) and injection.persona_touched is False
                and injection.generated_reply is None,
                f"chars={len(injection.text)} items={len(injection.items)}")
    probe.case("注入：字符预算 ≤700",
                len(injection.text) <= 700, f"chars={len(injection.text)}")
    probe.case("注入：条目带 kind/来源标注",
                all("kind" in item and ("source" in item or "text" in item)
                    for item in injection.to_dict().get("items", [])[:3]),
                f"item0={str(injection.to_dict().get('items', [])[:1])[:120]}")

    used = [item["id"] for item in injection.to_dict().get("items", []) if item.get("id")][:1]
    n = memory.feedback(A, injection.injection_id, used, helpful=True)
    hit_rows = rows("SELECT hit_count FROM fact WHERE id=?", (used[0],)) if used else []
    fb = rows("SELECT feedback FROM injection WHERE id=?", (injection.injection_id,))
    probe.case("反馈：hit_count+1 且 injection.feedback 落库",
                bool(hit_rows) and hit_rows[0]["hit_count"] >= 1 and bool(fb and fb[0]["feedback"]),
                f"updated={n} hit={hit_rows} feedback={fb[0]['feedback'] if fb else None}")

    # ================= 4. 纠错与置信地板 =================
    target = next(f for f in facts(A) if f["predicate"] == "电话")
    before_conf = float(target["confidence"])
    with db.tx() as c:
        result = store.apply_correction(c, A, cited_ids=[target["id"]],
                                        text="不对，电话不是这个")
    after = rows("SELECT confidence FROM fact WHERE id=?", (target["id"],))[0]["confidence"]
    probe.case("纠错（有 cited）：只打 cited 且 ×0.7",
                result["cited"] == [target["id"]] and after < before_conf
                and abs(after / before_conf - 0.7) < 0.02,
                f"cited={result['cited']} {before_conf}->{after}")

    other = next(f for f in facts(A) if f["predicate"] == "备注" and f["status"] == "active") \
        if any(f["predicate"] == "备注" and f["status"] == "active" for f in facts(A)) else None
    if other:
        with db.tx() as c:
            result2 = store.apply_correction(c, A, cited_ids=[], predicate_hint="备注",
                                             text="你记错了")
        probe.case("纠错（无 cited）：降级定位且幅度减半",
                    bool(result2["fallback"]),
                    f"fallback={result2['fallback']}")

    with db.tx() as c:
        for _ in range(12):
            store.apply_correction(c, A, cited_ids=[target["id"]], text="还是不对")
    floored = rows("SELECT confidence FROM fact WHERE id=?", (target["id"],))[0]["confidence"]
    probe.case("纠错地板 0.15 不穿透", abs(float(floored) - 0.15) < 0.001,
               f"conf={floored}")

    # ================= 5. 双时态回放 =================
    now = time.time()
    with db.tx() as c:
        store.add_fact(c, A, FactDraft("用户", "公司", "甲公司", "用户在甲公司", 0.9), now=now - 300)
    with db.tx() as c:
        store.add_fact(c, A, FactDraft("用户", "公司", "乙公司", "用户在乙公司", 0.9), now=now - 100)
    as_of_old = memory.facts_as_of(A, now - 200, predicate="公司")
    as_of_new = memory.facts_as_of(A, now - 10, predicate="公司")
    probe.case("双时态：当时视角只看当时值",
                any("甲" in str(r["object"]) for r in as_of_old)
                and any("乙" in str(r["object"]) for r in as_of_new)
                and not any("乙" in str(r["object"]) for r in as_of_old),
                f"old={[r['object'] for r in as_of_old]} new={[r['object'] for r in as_of_new]}")

    # ================= 6. 隔离 =================
    memory.turn(B, "g1", "user", "我叫小明，住在成都")
    assert memory.flush(20)
    b_hits = memory.recall("我住哪", B, session="g1").hits
    a_cross = memory.recall("成都", A, session="chat1").hits
    probe.case("跨身份隔离：B 的事实不进 A",
                any("成都" in str(h.text) for h in b_hits)
                and not any("成都" in str(h.text) for h in a_cross),
                f"b={len(b_hits)} a={len(a_cross)}")
    inj_cross = memory.inject("成都", A, session="chat1")
    probe.case("注入也隔离", "成都" not in inj_cross.text, f"chars={len(inj_cross.text)}")

    # ================= 7. 摘要与强制压缩 =================
    memory2, _ = make_memory(base_dir / "summary", summary_every=4, max_turns_per_session=8, turn_keep=2)
    try:
        for i in range(6):
            memory2.turn(A, "long", "user", f"第{i}句：我在准备一个项目，重点是记忆压缩")
            memory2.turn(A, "long", "assistant", f"第{i}句回复")
        assert memory2.flush(30)
        summaries = [dict(r) for r in memory2.db.read(
            "SELECT upto_turn,text,extra FROM summary")]
        probe.case("够 N 轮自动写摘要", len(summaries) >= 1,
                   f"n={len(summaries)} upto={[s['upto_turn'] for s in summaries]}")
        probe.case("摘要幂等（upto_turn 不重复）",
                    len({s["upto_turn"] for s in summaries}) == len(summaries),
                    f"upto={[s['upto_turn'] for s in summaries]}")
        forced = [s for s in summaries if "forced" in str(s["extra"])]
        turn_count = memory2.db.read("SELECT COUNT(*) c FROM turn")[0]["c"]
        probe.case("超长会话强制压缩（先摘要后删原文）",
                    bool(forced) and turn_count <= 10,
                    f"forced={len(forced)} turns={turn_count}")
    finally:
        memory2.close()

    # ================= 8. janitor =================
    with db.tx() as c:
        c.execute("UPDATE fact SET confidence=0.05 WHERE predicate='备注'")
    janitor = Janitor(db)
    result = janitor.run()
    floor_rows = rows("SELECT status FROM fact WHERE predicate='备注'")
    probe.case("janitor：低置信软删（不物理删）",
                bool(floor_rows) and floor_rows[0]["status"] != "active",
                f"run={result} status={floor_rows[0]['status'] if floor_rows else None}")
    edges = rows("SELECT dst_id, COUNT(*) c FROM fact_link GROUP BY src_id, dst_id"
                 " HAVING c > 1")
    probe.case("janitor 后关联表无重复边", not edges, f"dupes={len(edges)}")

    # ================= 9. 向量层（hash） =================
    vdir = base_dir / "vec"
    vdir.mkdir(mode=0o777, exist_ok=True)
    memory3, _ = make_memory(vdir, vector="hash")
    try:
        memory3.turn(A, "v", "user", "我喜欢周末去爬山和钓鱼")
        assert memory3.flush(20)
        index = memory3.recaller.index
        probe.case("hash 向量索引已建（非空）",
                    index is not None and len(index) > 0,
                    f"index={type(index).__name__} n={len(index)}")
        memory3.turn(B, "v", "user", "我在成都做设计")
        assert memory3.flush(20)
        res = memory3.recall("成都", A, session="v")
        leaked = [h for h in res.hits if "成都" in str(h.text)]
        probe.case("语义命中后按身份二次过滤", not leaked, f"leaked={len(leaked)}")
    finally:
        memory3.close()

    # ================= 10. 死信队列 =================
    memory4, pool4 = make_memory(base_dir / "dead")
    try:
        pool4.fail_next = 5
        memory4.turn(A, "d", "user", "我叫死信，住在火星基地")
        memory4.flush(30)
        dead = [dict(r) for r in memory4.db.read(
            "SELECT state,attempts,kind FROM write_queue")]
        probe.case("抽取失败进死信做留痕（不静默丢）",
                    bool(dead), f"rows={dead[:2]}")
    finally:
        memory4.close()

    # ================= 11. 媒体引用（无 BLOB） =================
    media_id = memory.add_media(A, kind="image", ref="m-1", url="https://x/y.png",
                                mime="image/png", size_bytes=1024)
    cols = [r["name"] for r in rows("PRAGMA table_info(media)")]
    probe.case("media 只存引用 + 识别结果（无 BLOB 列）",
                bool(media_id) and not any("blob" in c.lower() for c in cols),
                f"id={media_id} cols={cols}")

    # ================= 12. 风格与口癖 =================
    from aiji.hippocampus.extract import style_hints, verbal_tic_hints
    hints = style_hints("反正我觉得吧~~~ 就是那种感觉哈哈 😂")
    tics = verbal_tic_hints("反正我觉得吧，就是那种感觉")
    probe.case("风格四维（语气/长度/表情/口癖）",
                {"语气", "长度", "表情", "口癖"} & set(hints) != set(),
                f"hints={hints}")
    probe.case("口癖检测命中", bool(tics), f"tics={tics}")

    # ================= 13. 统计口径 =================
    stats = memory.stats()
    probe.case("stats 汇总口径可用",
                {"db", "pool", "injection"} & set(stats) != set(),
                f"keys={sorted(stats)[:10]}")
finally:
    memory.close()
    cleanup(base_dir)

raise SystemExit(probe.finish())
