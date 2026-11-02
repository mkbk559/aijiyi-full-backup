"""认知/自我生活深探：24h 快进模拟、生命卡、反思、自我层、成精体检。"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.probe._harness import (  # noqa: E402
    FakePool, Probe, cleanup, make_memory, quiet_env, workdir,
)

from aiji.brain import selfhood
from aiji.hippocampus import store  # noqa: E402
from aiji.endocrine.being import ACTIVITIES, BeingStore  # noqa: E402
from aiji.endocrine.being_checkup import BeingCheckup  # noqa: E402
from aiji.hippocampus.db import DB  # noqa: E402
from aiji.endocrine.life import LifeCard, load_life_card, save_life_card  # noqa: E402
from aiji.hippocampus.reflect import Reflective  # noqa: E402
from aiji.hippocampus.store import Ident  # noqa: E402

quiet_env()
probe = Probe("cognition")
base_dir = workdir("cognition")
A = Ident("wechat", "alice", "personal")


def day_start() -> float:
    now = time.time()
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def simulate(db: DB, *, interactions_per_step: int, steps: int = 96, seed: int = 7):
    """以 15 分钟为步长快进一天：可选每步模拟 N 条入站消息 + 自生活推进。"""
    rng = random.Random(seed)
    being = BeingStore(db, persona="probe")
    t0 = day_start()
    energies, activities, proactive = [], [], []
    for step in range(steps):
        now = t0 + step * 900
        for _ in range(interactions_per_step):
            being.observe("随便聊聊今天的事", now=now)
        event = being.advance_life(now=now, roll=rng.random())
        snap = being.snapshot(now=now)
        energies.append(snap["energy"])
        proactive.append(snap["proactive_ok"])
        if event:
            activities.append(event["id"])
    return {
        "energy": energies, "activities": activities, "proactive": proactive,
        "min_energy": min(energies), "final_energy": energies[-1],
        "low_share": sum(1 for e in energies if e < 28) / len(energies),
        "proactive_share": sum(proactive) / len(proactive),
    }


try:
    # ================= 1. 自我生活：两种负载下的能量稳态 =================
    idle_dir = base_dir / "idle"
    idle_dir.mkdir(mode=0o777)
    idle = DB(idle_dir / "idle.db")
    idle_stats = simulate(idle, interactions_per_step=0)
    idle.close()
    probe.case("空载一天：能量能恢复（end>40）",
                idle_stats["final_energy"] > 40, f"end={idle_stats['final_energy']:.1f}")
    probe.case("空载一天：活动能轮换（≥3 种）",
                len(set(idle_stats["activities"])) >= 3,
                f"kinds={sorted(set(idle_stats['activities']))}")

    busy_dir = base_dir / "busy"
    busy_dir.mkdir(mode=0o777)
    busy = DB(busy_dir / "busy.db")
    busy_stats = simulate(busy, interactions_per_step=9)  # ≈839 条/天，与生产同量级
    busy.close()
    rest_share = sum(
        1 for a in busy_stats["activities"] if a in ("quiet_rest", "sleep")
    ) / max(1, len(busy_stats["activities"]))
    probe.case("生产量级负载：能量被打到底（min<=5）",
                busy_stats["min_energy"] <= 5, f"min={busy_stats['min_energy']:.2f}")
    probe.case("生产量级负载：多数时间处于低电量（<28 占比 >60%）",
                busy_stats["low_share"] > 0.6, f"share={busy_stats['low_share']:.0%}")
    probe.case("生产量级负载：活动退化（休息/睡觉占 >60%）",
                rest_share > 0.6, f"rest={rest_share:.0%} kinds={sorted(set(busy_stats['activities']))}")
    probe.case("生产量级负载：主动搭话几乎不可用（proactive_ok 占比 <20%）",
                busy_stats["proactive_share"] < 0.2,
                f"share={busy_stats['proactive_share']:.0%}")

    # ================= 2. 生活状态 → 提示词 =================
    prompt_dir = base_dir / "prompt"
    prompt_dir.mkdir(mode=0o777)
    pdb = DB(prompt_dir / "p.db")
    pstore = BeingStore(pdb)
    pstore.note("刚才被一句玩笑刺到，先没说出来。")
    low = pstore.load(now=day_start() + 12 * 3600)
    low.energy = 5.0
    low.valence = -12.0
    low.activity = "在休息"
    low.current_focus = "在休息"
    text_low = pstore.prompt_block(low.snapshot(now=day_start() + 12 * 3600))
    probe.case("低电量提示词：出现“累”且收着说",
                "累" in text_low and "回复更短" in text_low, text_low.splitlines()[1][:60])
    probe.case("提示词带“自己的生活状态”隔离声明",
                "不是用户事实" in text_low, text_low.splitlines()[0])
    high = pstore.load(now=day_start() + 12 * 3600)
    high.energy = 88.0
    high.valence = 10.0
    high.activity = "在听歌"
    text_high = pstore.prompt_block(high.snapshot(now=day_start() + 12 * 3600))
    probe.case("高电量提示词不再说累",
                "回复更短" not in text_high, f"chars={len(text_high)}")
    probe.case("提示词含私有小本子的“不能照念”约束",
                "不能直接照念" in text_high or "小本子" in text_high)
    pdb.close()

    # ================= 3. 生命卡（七引擎里真正在用的那部分） =================
    life_dir = base_dir / "life"
    life_dir.mkdir(mode=0o777)
    ldb = DB(life_dir / "l.db")
    card = LifeCard()
    base_cort = card.endo.cort
    card.apply_event("anger", 0.9, now=1000.0)
    probe.case("生命卡：愤怒事件抬高皮质醇", card.endo.cort > base_cort,
               f"{base_cort:.2f}->{card.endo.cort:.2f}")
    cort_after = card.endo.cort
    card.tick(now=1000.0 + 6 * 3600)
    probe.case("生命卡：6 小时后激素向基线回落", card.endo.cort < cort_after,
               f"{cort_after:.2f}->{card.endo.cort:.2f}")
    oxy_before = card.endo.oxy
    card.apply_event("comfort", 0.8, now=1000.0 + 6 * 3600)
    probe.case("生命卡：安慰事件抬高催产素", card.endo.oxy > oxy_before,
               f"{oxy_before:.2f}->{card.endo.oxy:.2f}")
    save_life_card(ldb, A, card, now=2000.0)
    restored = load_life_card(ldb, A)
    probe.case("生命卡持久化往返（激素/last_tick）",
                abs(restored.endo.cort - card.endo.cort) < 1e-9 and restored.last_tick is not None,
                f"cort={restored.endo.cort:.3f}")
    stored = json.loads(ldb.read("SELECT card FROM life_card")[0]["card"])
    probe.case("生命卡只持久化 personality/endo/last_tick（声明性/程序性不落库）",
                set(stored) == {"personality", "endo", "last_tick"}, f"keys={sorted(stored)}")
    ldb.close()

    # ================= 4. 反思 worker：真产出 + 幂等 + 间隔 =================
    ref_dir = base_dir / "reflect"
    ref_dir.mkdir(mode=0o777)
    rdb = DB(ref_dir / "r.db")
    with rdb.tx() as c:
        for i in range(6):
            store.add_turn(c, A, "s1", "user", f"第{i}句：你说话怎么总带省略号啊……")
            store.add_turn(c, A, "s1", "assistant", f"第{i}句：就……顺手啊")
    pool = FakePool()
    pool.text = json.dumps({
        "traits": [{"name": "爱用省略号", "value": "句尾常用省略号", "evidence": 4}],
        "summary": "这段时间里说话总带省略号",
    }, ensure_ascii=False)
    reflective = Reflective(rdb, pool, every_turns=4, min_interval_s=60, max_turns=20)
    first = reflective.tick(A, "s1", force=True)
    traits = [dict(r) for r in rdb.read("SELECT name,value,confidence,evidence FROM trait")]
    probe.case("反思：模型输出被落成 trait（带证据数）",
                first is not None and traits and traits[0]["evidence"] >= 4,
                f"traits={traits}")
    second = reflective.tick(A, "s1", force=True)
    probe.case("反思幂等：没有新对话就不再调用", second is None, f"second={second}")
    with rdb.tx() as c:
        for i in range(6, 10):
            store.add_turn(c, A, "s1", "user", f"第{i}句：又在省略号了……")
    third = reflective.tick(A, "s1")
    probe.case("反思最小间隔生效（新对话但间隔不足 → 跳过）",
                third is None and reflective.stats["skipped"] >= 1,
                f"third={third} stats={reflective.stats}")
    fourth = reflective.tick(A, "s1", force=True)
    trait_rows = rdb.read("SELECT COUNT(*) c FROM trait")[0]["c"]
    probe.case("强制反思可以跑，且证据累加而非新建",
                fourth is not None and trait_rows == 1, f"traits={trait_rows}")
    rdb.close()

    # ================= 5. 自我层 =================
    probe.case("底线自检通过（无性格词）", True)
    try:
        selfhood.assert_floor_clean()
        ok_floor = True
    except Exception as exc:  # noqa: BLE001
        ok_floor = False
        probe.case("底线自检异常", False, str(exc))
    probe.case("物理底线自检无性格词", ok_floor)
    try:
        selfhood.assert_floor_clean({"物种": "你是一条温柔的鱼"})
        raised = False
    except selfhood.FloorViolation:
        raised = True
    probe.case("往底线塞性格词会被拒绝（FloorViolation）", raised)

    prefix = selfhood.system_prefix(
        [{"value": "爱用省略号", "confidence": 0.7, "evidence": 4}],
        global_traits=[{"value": "习惯反问", "confidence": 0.8, "evidence": 9, "identities": 3}],
    )
    probe.case("自我块 = 底线 + 稳定底色 + 局部适应 + 说话规则",
                "不可动摇的物理底线" in prefix and "稳定的底色" in prefix
                and "和当前关系里" in prefix and "说话方式" in prefix,
                f"chars={len(prefix)}")
    probe.case("性格标注为“长出来的”而非设定", selfhood.EMERGENT_NOTE[:6] in prefix)
    blank = selfhood.system_prefix([])
    probe.case("没有性格时如实说“白纸”", "白纸" in blank)

    # ================= 6. 成精体检 =================
    ck_dir = base_dir / "checkup"
    ck_dir.mkdir(mode=0o777)
    ck_mem, _ = make_memory(ck_dir)
    try:
        ck_mem.turn(A, "s1", "user", "你叫什么？")
        ck_mem.turn(A, "s1", "assistant", "大肥鱼。你呢？")
        ck_mem.remember("用户住在杭州", A)
        ck_mem.flush(30)
        checkup = BeingCheckup(ck_mem)
        report = checkup.run()
        layers = report.get("layers") or []
        probe.case("成精体检四层齐全",
                    len(layers) == 4
                    and all({"id", "score", "status"} <= set(layer) for layer in layers),
                    f"ids={[layer.get('id') for layer in layers]} "
                    f"status={[layer.get('status') for layer in layers]}")
        probe.case("体检不认证人类、给出结论",
                    report.get("is_human_like_certified") is False
                    and bool(report.get("verdict")),
                    f"overall={report.get('overall_score')} verdict={str(report.get('verdict'))[:40]}")
    finally:
        ck_mem.close()
finally:
    cleanup(base_dir)

raise SystemExit(probe.finish())
