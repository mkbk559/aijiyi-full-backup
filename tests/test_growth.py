"""成长引擎 v2：阶段由**学习量**决定，真实时间只负责"事件"和"主动性"。

用户定稿（2026-11-03）：
> 成长绝对不能靠真实时间……成长就按学习阶段来，按照人的小学、初中、高中、大学、
> 工作的阶段，学习到哪，成长到哪

所以这一组测试的两条主轴是：
1. 学分账本：只结算增量、幂等、单调，门槛→阶段映射正确；
2. **反证时间**：把注入的 `now` 拉开一年，阶段和年龄一个字都不许变。
"""
from __future__ import annotations

import inspect
import json
import sqlite3
import time
import unittest
from pathlib import Path
from unittest import mock

from aiji.brain import growth, persona, selfhood
from aiji.right_hand import operations
from aiji.right_hand.config import Settings

TMP = Path(__file__).resolve().parent.parent / ".tmp"
TMP.mkdir(mode=0o777, exist_ok=True)


def workdir() -> Path:
    import uuid

    path = TMP / f"growth-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class FakeDB:
    """只给 `growth.db_counts()` 用的最小库：它只要 COUNT(*) 和会话去重。"""

    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE fact(id INTEGER);
            CREATE TABLE reflection(id INTEGER);
            CREATE TABLE trait(id INTEGER);
            CREATE TABLE turn(id INTEGER, platform TEXT, platform_user_id TEXT,
                              scope TEXT, session TEXT);
            """
        )

    def add(self, table: str, n: int) -> None:
        for _ in range(n):
            if table == "turn":
                self.conn.execute(
                    "INSERT INTO turn(platform, platform_user_id, scope, session) "
                    "VALUES ('local','u','default','s1')")
            else:
                self.conn.execute(f"INSERT INTO {table}(id) VALUES (NULL)")
        self.conn.commit()

    def add_sessions(self, n: int) -> None:
        for i in range(n):
            self.conn.execute(
                "INSERT INTO turn(platform, platform_user_id, scope, session) "
                "VALUES ('local','u','default',?)", (f"s{i}",))
        self.conn.commit()


class GrowthBookTest(unittest.TestCase):
    """学分账本：只结算增量、幂等、单调。"""

    def setUp(self) -> None:
        self.dir = workdir()
        self.path = self.dir / "growth.json"
        self.db = FakeDB()
        patcher = mock.patch.object(growth, "_learn_count", return_value=0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def engine(self, now: float | None = None) -> growth.Growth:
        return growth.Growth(path=self.path, now=now)

    def test_first_sync_sets_baseline_and_earns_nothing(self):
        self.db.add("fact", 50)
        got = self.engine(1000.0).sync(self.db, force=True)
        self.assertEqual(got["xp"], 0.0)          # 历史存量不算"这次学到的"
        self.assertEqual(got["stage"], "primary")
        self.assertEqual(got["baseline"]["fact"], 50.0)

    def test_new_learning_earns_weighted_xp(self):
        self.engine(1000.0).sync(self.db, force=True)
        self.db.add("fact", 10)        # 10 × 1.0
        self.db.add("reflection", 5)   # 5 × 2.0
        self.db.add("trait", 2)        # 2 × 5.0
        self.db.add_sessions(3)        # 3 × 3.0
        self.db.add("turn", 30)        # 30 → 3.0
        got = self.engine(1060.0).sync(self.db, force=True)
        # turn 一列：30 条 + add_sessions 造的 3 条 = 33 → 3.3 学分
        self.assertAlmostEqual(got["xp"], 10 + 10 + 10 + 9 + 3.3, places=1)

    def test_sync_is_idempotent(self):
        self.engine(1000.0).sync(self.db, force=True)
        self.db.add("fact", 7)
        first = self.engine(1060.0).sync(self.db, force=True)["xp"]
        second = self.engine(1120.0).sync(self.db, force=True)["xp"]
        self.assertEqual(first, second)

    def test_sync_never_goes_backwards(self):
        self.engine(1000.0).sync(self.db, force=True)
        self.db.add("fact", 20)
        xp = self.engine(1060.0).sync(self.db, force=True)["xp"]
        # 数据被删（隔离/清理）也不能让学分倒退
        self.db.conn.execute("DELETE FROM fact")
        self.db.conn.commit()
        after = self.engine(1120.0).sync(self.db, force=True)
        self.assertGreaterEqual(after["xp"], xp)

    def test_sync_throttled_without_force(self):
        self.engine(1000.0).sync(self.db, force=True)
        self.db.add("fact", 5)
        engine = self.engine(1010.0)
        self.assertEqual(engine.sync(self.db)["xp"], 0.0)      # 60s 内不重复结算
        self.assertEqual(engine.sync(self.db, force=True)["xp"], 5.0)


class StageThresholdTest(unittest.TestCase):
    def test_stages_are_ordered_and_gapfree_in_xp(self):
        ids = [s.id for s in growth.STAGES]
        self.assertEqual(ids, ["primary", "middle", "high", "university", "work"])
        for prev, cur in zip(growth.STAGES, growth.STAGES[1:]):
            self.assertLess(prev.xp_min, cur.xp_min, cur.id)
        self.assertEqual(growth.STAGES[0].xp_min, 0.0)

    def test_stage_map_boundaries(self):
        cases = {
            0.0: "primary", 1199.9: "primary",
            1200.0: "middle", 3999.9: "middle",
            4000.0: "high", 8999.9: "high",
            9000.0: "university", 15999.9: "university",
            16000.0: "work", 999999.0: "work",
        }
        for xp, expect in cases.items():
            self.assertEqual(growth.stage_for_xp(xp).id, expect, xp)

    def test_scale_shifts_all_thresholds(self):
        self.assertEqual(growth.stage_for_xp(1200.0, scale=2.0).id, "primary")
        self.assertEqual(growth.stage_for_xp(2400.0, scale=2.0).id, "middle")

    def test_age_comes_from_stage_not_calendar(self):
        """同一份数据，时钟差一年，阶段和年龄必须一模一样（这是本轮的核心约束）。"""
        dir_a = workdir()
        path = dir_a / "growth.json"
        db = FakeDB()
        with mock.patch.object(growth, "_learn_count", return_value=0):
            growth.Growth(path=path, now=1000.0).sync(db, force=True)
            db.add("fact", 1500)
            growth.Growth(path=path, now=2000.0).sync(db, force=True)
            a = growth.Growth(path=path, now=2000.0).describe()
            # 同一个文件、晚一年再问：什么都没变
            b = growth.Growth(path=path, now=2000.0 + 365 * 86400).describe()
        self.assertEqual(a["stage"], b["stage"])
        self.assertEqual(a["age"], b["age"])
        self.assertEqual(a["xp"], b["xp"])
        self.assertEqual(a["stage"], "middle")
        self.assertFalse(a["time_based_growth"])
        self.assertEqual(a["age_source"], "learning")

    def test_no_rate_or_birthdriven_math_left(self):
        """v1 的"按天长大"必须彻底消失：没有 rate 常量、状态里没有 rate 字段。"""
        self.assertFalse(hasattr(growth, "DEFAULT_RATE"))
        self.assertFalse(hasattr(growth, "YEAR_DAYS"))
        dir_a = workdir()
        path = dir_a / "growth.json"
        with mock.patch.object(growth, "_learn_count", return_value=0):
            growth.Growth(path=path, now=1234.0).sync(FakeDB(), force=True)
        data = json.loads(path.read_text("utf-8"))
        self.assertNotIn("rate", data)
        self.assertNotIn("start_age", data)
        self.assertEqual(data["schema"], growth.SCHEMA)


class GrowthRenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = workdir()
        self.path = self.dir / "growth.json"
        patcher = mock.patch.object(growth, "_learn_count", return_value=0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, xp: float) -> growth.Growth:
        engine = growth.Growth(path=self.path, now=1000.0)
        data = engine.state()
        data["xp"] = float(xp)
        data["baseline"] = {}
        engine.save(data)
        return engine

    def test_primary_uses_child_base_others_use_growing_base(self):
        primary = self.make(0.0)
        self.assertTrue(primary.block().startswith(persona.CHILD))
        middle = self.make(growth.STAGES[1].xp_min)
        self.assertTrue(middle.block().startswith(persona.GROWING_BASE))
        self.assertIn("初中", middle.block())

    def test_note_mentions_credits_not_days(self):
        middle = self.make(growth.STAGES[1].xp_min)
        block = middle.block()
        self.assertIn("学分", block)
        self.assertIn("不靠时间", block)
        self.assertNotIn("真实天", block)

    def test_few_shot_only_in_primary(self):
        self.assertTrue(self.make(0.0).few_shot())
        self.assertEqual(self.make(growth.STAGES[1].xp_min).few_shot(), [])

    def test_adult_traits_lock_follows_stage(self):
        self.assertFalse(self.make(0.0).unlock_adult_traits())
        self.assertTrue(self.make(growth.STAGES[1].xp_min).unlock_adult_traits())

    def test_age_grows_with_progress_inside_stage(self):
        lo = self.make(growth.STAGES[1].xp_min).age_years()
        hi = self.make(growth.STAGES[2].xp_min - 1).age_years()
        self.assertGreater(hi, lo)
        self.assertGreaterEqual(lo, growth.STAGES[1].age_min)
        self.assertLessEqual(hi, growth.STAGES[1].age_max)

    def test_progress_reports_need_for_next(self):
        engine = self.make(growth.STAGES[1].xp_min + 600)
        prog = engine.progress()
        self.assertEqual(prog["next"], "high")
        self.assertAlmostEqual(prog["need"], growth.STAGES[2].xp_min - (growth.STAGES[1].xp_min + 600))

    def test_set_stage_pins_xp_to_floor(self):
        engine = self.make(0.0)
        data = engine.set_stage("university")
        self.assertEqual(data["stage"], "university")
        self.assertEqual(data["xp"], growth.STAGES[3].xp_min)
        self.assertEqual(growth.Growth(path=self.path).stage().id, "university")

    def test_unknown_stage_rejected(self):
        with self.assertRaises(ValueError):
            self.make(0.0).set_stage("phd")


class LockedStageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = workdir()
        self.path = self.dir / "growth.json"
        patcher = mock.patch.object(growth, "_learn_count", return_value=0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_override_stage_wins_over_xp(self):
        engine = growth.Growth(path=self.path, now=1000.0, override_stage="high")
        self.assertEqual(engine.stage().id, "high")
        self.assertGreaterEqual(engine.age_years(), growth.STAGES[2].age_min)

    def test_env_stage_and_env_age(self):
        with mock.patch.dict("os.environ", {"AIJI_PERSONA_STAGE": "work"}):
            self.assertEqual(growth.Growth(path=self.path).stage().id, "work")
        with mock.patch.dict("os.environ", {"AIJI_PERSONA_AGE": "16"}):
            self.assertEqual(growth.Growth(path=self.path).stage().id, "high")

    def test_chinese_stage_name_accepted(self):
        self.assertEqual(growth.stage_by_id("初中").id, "middle")
        self.assertEqual(growth.stage_by_id("大学").id, "university")

    def test_locked_describe_marks_locked(self):
        engine = growth.Growth(path=self.path, override_stage="middle")
        self.assertTrue(engine.describe()["locked"])
        self.assertFalse(engine.describe()["time_based_growth"])


class SettingsStageTest(unittest.TestCase):
    def test_load_reads_persona_stage_and_age(self):
        dir_a = workdir()
        path = dir_a / "aiji.json"
        path.write_text(json.dumps(
            {"routes": [], "persona": "child", "persona_stage": "high",
             "persona_age": 11.5}), encoding="utf-8")
        settings = Settings.load(path)
        self.assertEqual(settings.persona_stage, "high")
        self.assertEqual(settings.persona_age, 11.5)

    def test_env_stage_used(self):
        with mock.patch.dict("os.environ", {"AIJI_PERSONA_STAGE": "university"}):
            self.assertEqual(Settings.load().persona_stage, "university")

    def test_no_aging_rate_setting_left(self):
        self.assertFalse(hasattr(Settings(), "aging_rate"))

    def test_operations_accepts_stage_and_clears_it(self):
        dir_a = workdir()
        path = dir_a / "aiji.json"
        operations.save_config(path, {"persona_stage": "middle"})
        self.assertEqual(json.loads(path.read_text("utf-8"))["persona_stage"], "middle")
        operations.save_config(path, {"persona_stage": None})
        self.assertIsNone(json.loads(path.read_text("utf-8"))["persona_stage"])

    def test_operations_rejects_age_out_of_range(self):
        dir_a = workdir()
        path = dir_a / "aiji.json"
        with self.assertRaises(ValueError):
            operations.save_config(path, {"persona_age": 999})


class RealTimeIsForEventsAndProactivityTest(unittest.TestCase):
    """成长不看时间，但**事件**和**主动性**必须按真实时间——这两条也要有守卫。"""

    def test_proactive_uses_wallclock_not_growth(self):
        from aiji.endocrine import proactive

        src = inspect.getsource(proactive)
        self.assertIn("time.time()", src)
        self.assertNotIn("growth", src)
        self.assertNotIn("age_years", src)

    def test_life_loop_does_not_scale_by_growth(self):
        from aiji.endocrine import life_loop

        src = inspect.getsource(life_loop)
        self.assertNotIn("growth", src)
        self.assertIn("time.", src)

    def test_being_store_timestamps_are_epoch_seconds(self):
        from aiji.endocrine import being

        src = inspect.getsource(being)
        self.assertIn("time.time()", src)


class LedgerIsolationTest(unittest.TestCase):
    """账本必须跟着自己的库走：一次误调用不能把别人的存量记成"我学到的"。
    （这条 bug 真实发生过：跑全量测试时临时库把生产 growth.json 的基线改成了 1 条事实，
      真实库一结算就把两万多条历史存量算成绩效，人直接跳到"工作"。）
    """

    def test_ledger_lives_next_to_its_db(self):
        dir_a = workdir()
        db = FakeDB()
        db.path = dir_a / "api.db"
        with mock.patch.object(growth, "_learn_count", return_value=0):
            engine = growth.Growth(now=1000.0)
            engine.sync(db, force=True)
        self.assertEqual(engine.path, dir_a / "persona" / "growth.json")
        self.assertTrue(engine.path.exists())
        self.assertNotEqual(engine.path, growth.DEFAULT_PATH)

    def test_prompt_side_sees_the_same_ledger(self):
        """head 结算后，persona 文案必须读同一本账（否则"head 说初中、文案说小学"）。"""
        dir_a = workdir()
        db = FakeDB()
        db.path = dir_a / "api.db"
        db.add("fact", 1300)          # 一次学够 → 进初中
        with mock.patch.object(growth, "_learn_count", return_value=0):
            growth.Growth(now=1000.0).sync(db, force=True)   # 第一次只记基线
            db.add("fact", 1300)                             # 再学 1300
            growth.Growth(now=2000.0).sync(db, force=True)
            self.assertEqual(growth.current().stage().id, "middle")
            self.assertTrue(persona.style_block("child").startswith(persona.GROWING_BASE))
        growth.bind_path(None)        # 别把临时账本泄漏给后面的用例


class KidSafeTraitsStageTest(unittest.TestCase):
    def test_filter_follows_stage(self):
        traits = [{"value": "爱调侃对方", "confidence": 0.9},
                  {"value": "累的时候话变少", "confidence": 0.8}]
        kept_primary = selfhood.kid_safe_traits(traits, "child", stage="primary")
        self.assertEqual([t["value"] for t in kept_primary], ["累的时候话变少"])
        kept_middle = selfhood.kid_safe_traits(traits, "child", stage="middle")
        self.assertEqual(len(kept_middle), 2)

    def test_falls_back_to_age_when_stage_missing(self):
        traits = [{"value": "爱调侃对方"}]
        self.assertEqual(selfhood.kid_safe_traits(traits, "child", age=11.0), [])
        self.assertEqual(len(selfhood.kid_safe_traits(traits, "child", age=13.0)), 1)


class MilestoneTest(unittest.TestCase):
    def test_milestone_written_once_per_stage(self):
        dir_a = workdir()
        path = dir_a / "growth.json"
        events: list[tuple[str, str]] = []

        class FakeBeing:
            def record_event(self, kind, text, **_kwargs):
                events.append((kind, text))

        class FakeMemory:
            being = FakeBeing()

        engine = growth.Growth(path=path, now=time.time())
        with mock.patch.object(growth, "_learn_count", return_value=0):
            engine.sync(FakeDB(), force=True)
        first = engine.write_milestone(FakeMemory())
        second = engine.write_milestone(FakeMemory())
        self.assertEqual(first["stage"], "primary")
        self.assertIsNone(second)          # 同一阶段只留一次痕
        self.assertEqual(len(events), 1)
        self.assertIn("小学", events[0][1])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
