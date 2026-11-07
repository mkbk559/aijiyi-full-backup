"""压缩时间线：一天 24 小时压成一次跑完（用户 2026-11-07 拍板："直接缩短时间线，
测试一天的效果，有 bug 就不断迭代测试修复"）。

做法：把真实时刻 mock 到一天的 10 个关键时点，对每个时点跑真组件
（Head/OneBotBridge/day/growth/curiosity），断言"一个真孩子"的行为。
每个 case 独立 tmp 库，不碰生产 data/aiji.db。

时点表（初中作息）：
  00:41 凌晨被吵（睡）   → 硬闸模板，零模型调用
  07:00 起床（short）    → 正常回，简短
  09:30 上午课（none）   → 提示词带硬规矩
  12:15 午饭（short）    → 正常回
  14:00 下午课（none）   → 提示词带硬规矩
  17:30 放学（normal）   → 正常回，"不知道X"触发好奇心
  19:30 写作业（none）   → 硬规矩
  21:20 自由（normal）   → 正常长聊 + 群复读疲劳
  22:05 写日记           → 带"明天想…"，落 self_memory(kind=plan)
  22:40 睡觉（硬闸）     → 模板
结算：growth.sync 新口径（curiosity_learn/self_goal 加分，turn 0.02）
"""

from __future__ import annotations

import re
import shutil
import time
import uuid
from pathlib import Path
from unittest import mock

from aiji.right_hand.config import Settings
from aiji.hippocampus.memory import Memory
from aiji.brain.head import Head
from aiji.hippocampus.store import Ident
from aiji.brain import curiosity
from aiji.brain import day as day_mod
from aiji.brain import growth as growth_mod

ROOT = Path(__file__).resolve().parent.parent / ".tmp"
DAY = (2026, 11, 10)   # 一个未来的普通周二（避开周末/今天数据）


def workdir(tag: str) -> Path:
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / f"simday-{tag}-{uuid.uuid4().hex[:8]}"
    path.mkdir()
    return path


def at(h: int, m: int) -> float:
    # Windows mktime 需要 9 元组（含星期与 DST 标志，都可 -1 自动推导）
    return time.mktime((*DAY, h, m, 0, 0, 0, -1))


def new_memory(path: Path) -> Memory:
    return Memory(Settings(
        db_path=path / "sim.db", routes=(), debounce_ms=0,
        humanize_min_ms=0, humanize_max_ms=0,
        typing_min_ms=0, typing_max_ms=0,
    ), engines="none")


class FakePool:
    """记账式假模型：记录每次调用的系统提示词，返回一句固定人话。"""

    def __init__(self, reply: str = "嗯嗯，我在呢。"):
        self.calls: list[list[dict]] = []
        self.reply_text = reply
        self._states = [object()]

    def chat(self, messages, *, timeout_s=None, **kwargs):
        self.calls.append(messages)
        return type("R", (), {"text": self.reply_text, "route": "fake",
                              "latency_ms": 1.0})()


def say(memory, pool, when, text, *, platform="qq", user="u1",
        scope="group:1025643092", session="group:1025643092", remember=False):
    ident = Ident(platform, user, scope)
    with mock.patch("aiji.brain.head.time.time", return_value=when):
        return Head(memory).reply(
            [{"role": "user", "content": text}], ident,
            session=session, remember=remember)


def system_text(messages) -> str:
    return "\n".join(str(m.get("content") or "")
                     for m in messages if m.get("role") == "system")


# ---- 全天脚本 ---------------------------------------------------------------

def test_full_day_simulation():
    path = workdir("full")
    memory = new_memory(path)
    pool = FakePool()
    memory.pool = pool
    issues: list[str] = []
    try:
        # 00:41 凌晨被吵 → 硬闸
        r = say(memory, pool, at(0, 41), "别睡！起来嗨！")
        if pool.calls:
            issues.append("00:41 sleep gate: 模型仍被调用")
        if "睡了" not in r.text:
            issues.append(f"00:41 sleep gate: 回复不是模板 ({r.text[:30]!r})")
        if r.reason != "day_gate:sleep":
            issues.append(f"00:41 sleep gate: reason={r.reason!r}")

        # 07:00 起床 → 正常（short）
        n0 = len(pool.calls)
        r = say(memory, pool, at(7, 0), "早呀，吃早饭没")
        if len(pool.calls) != n0 + 1:
            issues.append("07:00 起床段: 模型没被调用")

        # 09:30 上午课 → 硬规矩
        r = say(memory, pool, at(9, 30), "在吗，帮我看看这道题")
        if "硬规矩" not in system_text(pool.calls[-1]):
            issues.append("09:30 上课段: 提示词没带硬规矩")

        # 12:15 午饭 → 正常
        r = say(memory, pool, at(12, 15), "中午吃啥")
        if not r.text:
            issues.append("12:15 午饭段: 没回复")

        # 17:30 放学 + 好奇心触发（回"我不知道"→ 学习）
        curiosity._recent_learns.clear()
        curiosity._seen_topics.clear()
        n0 = len(pool.calls)
        r = say(memory, pool, at(17, 30), "变声器是什么呀？",
                remember=True)
        if len(pool.calls) != n0 + 1:
            issues.append("17:30 放学段: 模型没被调用")
        # wants_to_learn 由 head 的 B1 钩子触发——直接验证钩子判定
        topic = curiosity.wants_to_learn("我不太懂呢", "变声器是什么呀？")
        if topic != "变声器":
            issues.append(f"17:30 好奇心: 主题提取错 ({topic!r})")

        # 19:30 写作业 → 硬规矩
        r = say(memory, pool, at(19, 30), "数学写完没")
        if "硬规矩" not in system_text(pool.calls[-1]):
            issues.append("19:30 作业段: 提示词没带硬规矩")

        # 21:20 自由 + 群复读疲劳（A5）+ 日记→plan 链
        from aiji.throat.bridge.onebot import OneBotBridge

        replied, ingested = [], []

        def reply_fn(ev):
            replied.append(ev)
            return "嗯嗯"

        def sender(ev, text):
            return True

        def ingest_fn(ev):
            ingested.append(ev)

        bridge = OneBotBridge(reply_fn=reply_fn, sender=sender,
                              ingest_fn=ingest_fn,
                              group_mode="mention", configured=True).start()

        def group_event(content, msg_id, who="复读机"):
            return {"post_type": "message", "message_type": "group",
                    "message_id": msg_id, "self_id": "2684827447",
                    "user_id": "10086", "group_id": 849663082,
                    "sender": {"card": who},
                    "message": [{"type": "at", "data": {"qq": "2684827447"}},
                                {"type": "text", "data": {"text": content}}]}

        for i in range(4):
            bridge.submit(group_event("你们看这个视频", 500 + i))
        _drain_bridge(bridge)
        bridge.stop(timeout=5.0)
        if bridge.stats["replied"] != 2 or bridge.stats["repeat_fatigued"] != 2:
            issues.append(
                f"21:20 复读疲劳: replied={bridge.stats['replied']} "
                f"fatigued={bridge.stats['repeat_fatigued']} (期望 2/2)")
        if len(ingested) != 2:
            issues.append(f"21:20 复读疲劳: 疲劳条没入记忆 ({len(ingested)})")

        diary = ("今天聊了变声器的事，我没弄明白。"
                 "明天想把变声器的原理弄明白，搞懂它怎么变声音的。")
        plan = day_mod.extract_tomorrow_plan(diary)
        if "变声器" not in plan:
            issues.append(f"21:20 日记: plan 抠取失败 ({plan!r})")
        pid = memory.remember_self(plan, kind="plan", importance=0.7)
        rows = memory.self_memory(scope=None, limit=10)
        if not any(str(row.get("kind")) == "plan" for row in rows):
            issues.append("21:20 self_memory: plan 没落库")

        # A2 主动闸：凌晨不该主动开口
        from aiji.endocrine.being import BeingState

        b = BeingState()
        if b.can_proactive(now=at(1, 30)):
            issues.append("A2 主动闸: 凌晨 1:30 还允许主动")
        if not b.can_proactive(now=at(21, 0)):
            issues.append("A2 主动闸: 21:00 不允许主动（默认能量下应允许）")
        # 22:30 后也拦（作息表 sleep 段）
        if b.can_proactive(now=at(23, 30)):
            issues.append("A2 主动闸: 23:30 还允许主动")

        # 22:40 睡觉 → 硬闸
        n0 = len(pool.calls)
        r = say(memory, pool, at(22, 41), "睡了吗")
        if pool.calls and len(pool.calls) != n0:
            issues.append("22:40 sleep gate: 模型仍被调用")
        if "睡了" not in r.text:
            issues.append(f"22:40 sleep gate: 回复不是模板 ({r.text[:30]!r})")

        # ---- 成长结算（新口径）----
        # 模拟当天学到的证据：fact + curiosity 学习 + self_goal
        # sync 首跑只记基线不涨分（历史存量不算"学到的"），所以要两次：
        # 先基线，再插当天新证据，再结算。
        with memory.db.tx() as c:
            c.execute(
                "INSERT INTO fact(subject, predicate, object, text, confidence,"
                " status, source, source_kind, created_at, updated_at,"
                " platform, platform_user_id, scope, extra)"
                " VALUES ('x','y','z','基线占位', 0.9, 'active',"
                " 'test', 'senses', ?, ?, 'qq','u1','group:1', '{}')",
                (at(0, 5), at(0, 5)))
        memory_growth_sync(path)             # 基线
        with memory.db.tx() as c:
            c.execute(
                "INSERT INTO fact(subject, predicate, object, text, confidence,"
                " status, source, source_kind, created_at, updated_at,"
                " platform, platform_user_id, scope, extra)"
                " VALUES ('x','y','z','我查过「变声器」：...', 0.9, 'active',"
                " 'test', 'senses', ?, ?, 'qq','u1','group:1',"
                " '{\"source\": \"curiosity\"}')",
                (at(18, 0), at(18, 0)))
        got = memory_growth_sync(path)
        if got["xp"] <= 0:
            issues.append(f"结算: 新证据没涨学分 ({got.get('last_detail')})")
        detail = got.get("last_detail") or {}
        if detail.get("curiosity_learn", 0) < 1:
            issues.append(f"结算: curiosity_learn 没计入 ({detail})")

        assert not issues, "全天模拟发现的问题:\n" + "\n".join(issues)
    finally:
        memory.close()
        shutil.rmtree(path, ignore_errors=True)


def _drain_bridge(bridge, *, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if bridge._queue.qsize() == 0 and \
                bridge.stats["replied"] + bridge.stats["ingested"] + \
                bridge.stats["failed"] >= bridge.stats["accepted"]:
            time.sleep(0.05)
            return
        time.sleep(0.02)


def memory_growth_sync(path: Path) -> dict:
    """对当天库跑一次成长结算（B5 新口径）。"""
    g = growth_mod.Growth(path=path / "growth.json")
    return g.sync(path / "sim.db", force=True)


def test_growth_new_evidence_weights():
    """B5 口径的独立验证：curiosity_learn 3 分/条、self_goal 4 分/条、turn 0.02。"""
    path = workdir("growth")
    try:
        import sqlite3
        db = path / "g.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE fact(id INTEGER PRIMARY KEY, extra TEXT)")
        con.execute("CREATE TABLE reflection(id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE trait(x TEXT)")
        con.execute("CREATE TABLE turn(x TEXT)")
        con.execute("CREATE TABLE self_memory(id INTEGER PRIMARY KEY,"
                    " kind TEXT, status TEXT)")
        con.execute("INSERT INTO fact(extra) VALUES"
                    " ('{\"source\": \"curiosity\"}')")
        con.execute("INSERT INTO self_memory(kind, status) VALUES"
                    " ('plan', 'active')")
        con.execute("INSERT INTO turn(x) VALUES ('a')")
        con.commit()
        con.close()
        with mock.patch.object(growth_mod, "_learn_count", return_value=0):
            g = growth_mod.Growth(path=path / "growth.json")
            g.sync(db, force=True)          # baseline
            con = sqlite3.connect(db)
            con.execute("INSERT INTO fact(extra) VALUES"
                        " ('{\"source\": \"curiosity\"}')")
            con.execute("INSERT INTO self_memory(kind, status) VALUES"
                        " ('plan', 'active')")
            for _ in range(100):
                con.execute("INSERT INTO turn(x) VALUES ('a')")
            con.commit()
            con.close()
            got = g.sync(db, force=True)
        detail = got.get("last_detail") or {}
        if detail.get("curiosity_learn", 0) != 1:
            raise AssertionError(f"curiosity_learn 增量错: {detail}")
        if detail.get("self_goal", 0) != 1:
            raise AssertionError(f"self_goal 增量错: {detail}")
        # fact 增量行 1.0 + curiosity_learn 3.0 + self_goal 4.0 + turn 100×0.02=2.0 → 10.0
        if abs(got["last_gain"] - 10.0) > 0.01:
            raise AssertionError(f"总学分错: got={got['last_gain']}, 期望 10.0")
    finally:
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    import unittest
    unittest.main()
