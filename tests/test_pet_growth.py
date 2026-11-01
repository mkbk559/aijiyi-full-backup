"""养成经济规则测试：逐条对齐插件 affinity.js / treats.js 的语义。"""

from __future__ import annotations

import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp"
TMP_ROOT.mkdir(mode=0o777, exist_ok=True)


@contextmanager
def tmpdir(prefix: str = "pet"):
    """沙箱下 `tempfile.TemporaryDirectory()` 建 0o700 目录会写不进去；统一落 .tmp。"""
    path = TMP_ROOT / f"{prefix}-{uuid.uuid4().hex[:8]}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)

from aiji.pet.growth import (
    AFFINITY_RANKS,
    BUILTIN_REMARKS,
    PetStore,
    RemarkPicker,
    affinity_view,
    apply_interaction,
    apply_turn_reward,
    consume_treat,
    empty_affinity,
    empty_treat_ledger,
    next_rank,
    rank_of,
    settle_treat_grants,
)


class TestAffinity(unittest.TestCase):
    def test_rank_boundaries(self):
        self.assertEqual(rank_of(0)[1], "幼鲸")
        self.assertEqual(rank_of(24)[1], "幼鲸")
        self.assertEqual(rank_of(25)[1], "伙伴")
        self.assertEqual(rank_of(49)[1], "伙伴")
        self.assertEqual(rank_of(50)[1], "挚友")
        self.assertEqual(rank_of(80)[1], "深海羁绊")
        self.assertEqual(rank_of(199)[1], "深海羁绊")
        self.assertEqual(rank_of(200)[1], "心有灵犀")
        self.assertEqual(rank_of(10**9)[1], AFFINITY_RANKS[-1][1])

    def test_rank_stars_grow(self):
        stars = [len(r[2]) for r in AFFINITY_RANKS]
        self.assertEqual(stars, sorted(stars))
        self.assertEqual(stars[0], 1)

    def test_next_rank(self):
        self.assertEqual(next_rank(0)[1], "伙伴")
        self.assertEqual(next_rank(24)[1], "伙伴")
        self.assertEqual(next_rank(25)[1], "挚友")
        self.assertIsNone(next_rank(10**9))

    def test_first_pet_always_lands_then_cooldown(self):
        st = empty_affinity()
        st, delta, line, ok = apply_interaction(st, "pet", 1_000_000)
        self.assertTrue(ok)
        self.assertEqual(delta, 1)
        self.assertIn(line, BUILTIN_REMARKS["pet"])
        # 10s 内再摸：拒绝且不加分
        st2, delta2, line2, ok2 = apply_interaction(st, "pet", 1_000_000 + 9_999)
        self.assertFalse(ok2)
        self.assertEqual(delta2, 0)
        self.assertIn(line2, BUILTIN_REMARKS["petCooldown"])
        self.assertEqual(st2["points"], 1)
        # 满 10s：通过
        st3, delta3, _, ok3 = apply_interaction(st, "pet", 1_000_000 + 10_000)
        self.assertTrue(ok3)
        self.assertEqual(st3["points"], 2)

    def test_feed_reward_and_cooldown(self):
        st = empty_affinity()
        st, delta, line, ok = apply_interaction(st, "feed", 5_000_000)
        self.assertTrue(ok)
        self.assertEqual(delta, 5)
        self.assertIn(line, BUILTIN_REMARKS["feed"])
        _, d2, line2, ok2 = apply_interaction(st, "feed", 5_000_000 + 29_999)
        self.assertFalse(ok2)
        self.assertEqual(d2, 0)
        self.assertIn(line2, BUILTIN_REMARKS["feedCooldown"])
        st4, d4, _, ok4 = apply_interaction(st, "feed", 5_000_000 + 30_000)
        self.assertTrue(ok4)
        self.assertEqual(d4, 5)
        self.assertEqual(st4["points"], 10)

    def test_turn_reward(self):
        st = apply_turn_reward(empty_affinity())
        self.assertEqual(st["turns"], 1)
        self.assertEqual(st["points"], 1)

    def test_view_flags_cooldowns(self):
        st, _, _, _ = apply_interaction(empty_affinity(), "pet", 100_000)
        view = affinity_view(st, 100_000 + 1)
        self.assertTrue(view["pet_cooldown"])
        self.assertFalse(view["feed_cooldown"])
        self.assertEqual(view["rank"], "幼鲸")

    def test_points_never_negative(self):
        st = apply_interaction(empty_affinity(), "nonsense", 1)
        self.assertEqual(st[0]["points"], 0)


class TestTreats(unittest.TestCase):
    def test_first_settlement_writes_time_anchor(self):
        ledger, gained = settle_treat_grants(empty_treat_ledger(), 0, 1_000_000)
        self.assertEqual(gained, 0)
        self.assertEqual(ledger["last_grant_at"], 1_000_000)  # 防死锁

    def test_time_source_every_5_hours(self):
        ledger = {"treats": 0, "last_grant_at": 1_000_000, "turns_at_last_grant": 0}
        five_h = 300 * 60_000
        ledger2, gained = settle_treat_grants(ledger, 0, 1_000_000 + five_h)
        self.assertEqual(gained, 1)
        self.assertEqual(ledger2["treats"], 1)
        # 未满下一个 5 小时：不变
        ledger3, g3 = settle_treat_grants(ledger2, 0, 1_000_000 + five_h + 1)
        self.assertEqual(g3, 0)
        self.assertIs(ledger3, ledger2)

    def test_work_source_every_30_turns(self):
        ledger = {"treats": 0, "last_grant_at": 1_000_000, "turns_at_last_grant": 0}
        ledger2, gained = settle_treat_grants(ledger, 29, 1_000_000)
        self.assertEqual(gained, 0)
        ledger3, gained3 = settle_treat_grants(ledger, 30, 1_000_000)
        self.assertEqual(gained3, 1)
        self.assertEqual(ledger3["turns_at_last_grant"], 30)
        # 再来 30 轮 → 又一条
        _, gained4 = settle_treat_grants(ledger3, 60, 1_000_000)
        self.assertEqual(gained4, 1)

    def test_both_sources_independent(self):
        ledger = {"treats": 0, "last_grant_at": 1_000_000, "turns_at_last_grant": 0}
        _, gained = settle_treat_grants(ledger, 30, 1_000_000 + 300 * 60_000)
        self.assertEqual(gained, 2)

    def test_cap(self):
        ledger = {"treats": 20, "last_grant_at": 1_000_000, "turns_at_last_grant": 0}
        ledger2, gained = settle_treat_grants(ledger, 300, 1_000_000 + 10 * 300 * 60_000)
        self.assertEqual(ledger2["treats"], 20)

    def test_consume(self):
        ok, led = consume_treat({"treats": 1, "last_grant_at": 0, "turns_at_last_grant": 0})
        self.assertTrue(ok)
        self.assertEqual(led["treats"], 0)
        ok2, led2 = consume_treat(led)
        self.assertFalse(ok2)
        self.assertEqual(led2["treats"], 0)


class TestRemarkPicker(unittest.TestCase):
    def test_round_robin(self):
        p = RemarkPicker()
        first = [p.pick("feed") for _ in range(3)]
        self.assertEqual(first, list(BUILTIN_REMARKS["feed"][:3]))
        # 循环回到开头
        for _ in range(len(BUILTIN_REMARKS["feed"]) - 3):
            p.pick("feed")
        self.assertEqual(p.pick("feed"), BUILTIN_REMARKS["feed"][0])

    def test_slots_independent(self):
        p = RemarkPicker()
        a = p.pick("pet")
        b = p.pick("feed")
        self.assertEqual(a, BUILTIN_REMARKS["pet"][0])
        self.assertEqual(b, BUILTIN_REMARKS["feed"][0])

    def test_custom_override(self):
        p = RemarkPicker({"feed": ["只有这一句"]})
        self.assertEqual(p.pick("feed"), "只有这一句")
        self.assertEqual(p.pick("pet"), BUILTIN_REMARKS["pet"][0])


class TestPetStore(unittest.TestCase):
    def _store(self, tmp: Path) -> PetStore:
        return PetStore(tmp / "pet_state.json")

    def test_feed_needs_treat(self):
        with tmpdir() as d:
            s = self._store(Path(d))
            delta, line, ok = s.feed(1_000_000)
            self.assertFalse(ok)
            self.assertIn(line, BUILTIN_REMARKS["noTreats"])
            self.assertEqual(s.affinity["points"], 0)

    def test_feed_success_consumes_one(self):
        with tmpdir() as d:
            s = self._store(Path(d))
            s.treats = {"treats": 2, "last_grant_at": 1, "turns_at_last_grant": 0}
            delta, line, ok = s.feed(1_000_000)
            self.assertTrue(ok)
            self.assertEqual(delta, 5)
            self.assertEqual(s.treats["treats"], 1)
            self.assertIn(line, BUILTIN_REMARKS["feed"])

    def test_cooldown_does_not_burn_treat(self):
        with tmpdir() as d:
            s = self._store(Path(d))
            s.treats = {"treats": 2, "last_grant_at": 1, "turns_at_last_grant": 0}
            s.feed(1_000_000)
            before = s.treats["treats"]
            _, line, ok = s.feed(1_000_000 + 1_000)   # 冷却中
            self.assertFalse(ok)
            self.assertEqual(s.treats["treats"], before)  # 不白扣
            self.assertIn(line, BUILTIN_REMARKS["feedCooldown"])

    def test_persist_and_reload(self):
        with tmpdir() as d:
            s = self._store(Path(d))
            s.treats = {"treats": 1, "last_grant_at": 1, "turns_at_last_grant": 0}
            s.pet(1_000_000)
            s.rename("小胖鱼")
            s2 = PetStore(Path(d) / "pet_state.json")
            self.assertEqual(s2.name, "小胖鱼")
            self.assertEqual(s2.affinity["points"], 1)
            self.assertEqual(s2.affinity["pets"], 1)

    def test_turn_reward_and_treat_from_turns(self):
        with tmpdir() as d:
            s = self._store(Path(d))
            s.settle(1_000_000)
            for _ in range(30):
                s.reward_turn()
            snap = s.snapshot(1_000_000)
            self.assertEqual(snap["affinity"]["turns"], 30)
            self.assertEqual(snap["affinity"]["points"], 30)
            self.assertEqual(snap["treats"], 1)   # 30 轮 → 1 条鱼干

    def test_two_store_instances_do_not_overwrite_each_others_updates(self):
        with tmpdir() as d:
            path = Path(d) / "pet_state.json"
            first = PetStore(path)
            second = PetStore(path)

            first.reward_turn()
            second.reward_turn()

            restored = PetStore(path)
            self.assertEqual(restored.affinity["turns"], 2)
            self.assertEqual(restored.affinity["points"], 2)

    def test_multiple_pets_share_one_file_without_sharing_state(self):
        with tmpdir() as d:
            path = Path(d) / "pet_state.json"
            try:
                blue = PetStore(path, pet_id="blue", name="蓝鲸")
                white = PetStore(path, pet_id="white", name="白鲸")
            except TypeError as exc:
                self.fail(f"PetStore must support pet_id: {exc}")

            blue.reward_turn()
            blue.rename("小蓝")
            white.reward_turn()
            white.reward_turn()

            blue_again = PetStore(path, pet_id="blue")
            white_again = PetStore(path, pet_id="white")
            self.assertEqual(blue_again.name, "小蓝")
            self.assertEqual(blue_again.affinity["turns"], 1)
            self.assertEqual(white_again.name, "白鲸")
            self.assertEqual(white_again.affinity["turns"], 2)


if __name__ == "__main__":
    unittest.main()
