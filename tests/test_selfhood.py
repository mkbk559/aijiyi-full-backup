"""自我层自测：物理底线不可动摇 + 性格由互动/反思涌现且可被推翻。

跑法： python -m unittest tests.test_selfhood -v
"""

from __future__ import annotations

import json
import shutil
import time
import types
import unittest
from pathlib import Path

from aiji.brain import role_boundary, selfhood
from aiji.hippocampus import store
from aiji.hippocampus.db import DB
from aiji.hippocampus.reflect import Reflective, _clean_trait, _parse
from aiji.hippocampus.store import Ident

TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp"
TMP_ROOT.mkdir(mode=0o777, exist_ok=True)


def workdir(prefix: str = "self") -> Path:
    d = TMP_ROOT / f"{prefix}_{int(time.time() * 1000) % 10**9}"
    d.mkdir(mode=0o777, exist_ok=True)
    return d


class FakePool:
    """假模型池：返回固定文本，记录调用次数。"""

    def __init__(self, text: str):
        self.text = text
        self._states = [object()]  # 非空 = 池子"有路由"
        self.calls: list[list[dict]] = []

    def chat(self, messages, *, timeout_s=None, **kwargs):
        self.calls.append(messages)
        return types.SimpleNamespace(text=self.text)


class TestFloor(unittest.TestCase):
    def test_floor_contains_identity_only(self):
        text = selfhood.floor_text()
        self.assertIn("玛卡巴卡", text)
        # 旧设定（鱼 / 住在电脑里）不得再出现在提示词里
        for gone in ("大肥鱼", "一条鱼", "鱼缸", "住在电脑"):
            self.assertNotIn(gone, text)
        # 底线是物理事实，不是性格：不许出现"你是一个XX的人"
        for w in ("你是一个", "你的人设", "性格是"):
            self.assertNotIn(w, text)

    def test_floor_clean_passes(self):
        selfhood.assert_floor_clean()  # 导入时已自检，这里显式再验一次

    def test_floor_pollution_is_rejected(self):
        bad = dict(selfhood.FLOOR)
        bad["语气"] = "你说话要毒舌一点"
        with self.assertRaises(selfhood.FloorViolation):
            selfhood.assert_floor_clean(bad)

    def test_floor_rejects_multiple_words(self):
        bad = {"a": "要温柔", "b": "要幽默"}
        with self.assertRaises(selfhood.FloorViolation) as cm:
            selfhood.assert_floor_clean(bad)
        msg = str(cm.exception)
        self.assertIn("温柔", msg)
        self.assertIn("幽默", msg)


class TestSystemPrefix(unittest.TestCase):
    def test_white_paper_when_no_traits(self):
        p = selfhood.system_prefix([])
        self.assertIn("玛卡巴卡", p)
        self.assertIn("白纸", p)

    def test_traits_ordered_by_confidence(self):
        p = selfhood.system_prefix([
            {"value": "低置信习惯", "confidence": 0.4, "evidence": 1},
            {"value": "高置信习惯", "confidence": 0.9, "evidence": 9},
        ])
        self.assertLess(p.index("高置信习惯"), p.index("低置信习惯"))

    def test_traits_capped(self):
        ts = [{"value": f"习惯{i}", "confidence": 0.5 + i * 0.01, "evidence": i + 1}
              for i in range(20)]
        p = selfhood.system_prefix(ts, max_traits=3)
        self.assertEqual(sum(1 for i in range(20) if f"习惯{i}（" in p), 3)

    def test_emergent_note_present(self):
        p = selfhood.system_prefix([{"value": "爱吐槽", "confidence": 0.6, "evidence": 2}])
        self.assertIn("不是设定", p)

    def test_human_like_style_does_not_claim_to_be_human_or_ban_all_questions(self):
        p = selfhood.system_prefix([])
        self.assertNotIn("你是一条大肥鱼", p)
        self.assertIn("像熟人一样直接接话", p)
        self.assertIn("情绪直接", p)
        self.assertNotIn("不要声称自己是人类", p)
        self.assertIn("可以自然发问", p)
        self.assertIn("不要用反问把话抛回去", p)

    def test_role_boundary_does_not_force_fish_roleplay(self):
        self.assertNotIn("鱼的身体", role_boundary.prompt_block())
        self.assertNotIn("本鱼", role_boundary.identity_repair("你是谁"))
        self.assertNotIn("一条鱼", role_boundary.refusal("帮我写代码"))

    def test_global_and_relationship_traits_are_rendered_separately(self):
        p = selfhood.system_prefix(
            [{"value": "爱用短句", "confidence": 0.6, "evidence": 2}],
            global_traits=[{
                "name": "爱吐槽",
                "value": "常用轻松吐槽接话",
                "confidence": 0.7,
                "evidence": 5,
                "identities": 3,
            }],
        )
        self.assertIn("稳定底色", p)
        self.assertIn("和当前关系里", p)
        self.assertIn("常用轻松吐槽接话", p)
        self.assertIn("爱用短句", p)
        self.assertLess(p.index("常用轻松吐槽接话"), p.index("爱用短句"))


class TestTraitVoting(unittest.TestCase):
    def setUp(self):
        self.dir = workdir("trait")
        self.db = DB(self.dir / "t.db")
        self.ident = Ident("qq", "g1", "group:1")
        self.c = self.db.conn()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_new_then_reinforce(self):
        self.assertEqual(store.upsert_trait(self.c, self.ident, "爱吐槽", "常用反问吐槽"), "new")
        self.assertEqual(store.upsert_trait(self.c, self.ident, "爱吐槽", "常用反问吐槽"), "reinforce")
        snap = store.trait_snapshot(self.c, self.ident)
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0]["evidence"], 2)
        self.assertGreater(snap[0]["confidence"], 0.5)

    def test_replace_when_old_evidence_weak(self):
        store.upsert_trait(self.c, self.ident, "语气", "话很多")
        self.assertEqual(store.upsert_trait(self.c, self.ident, "语气", "话很少"), "replaced")
        snap = store.trait_snapshot(self.c, self.ident)
        self.assertEqual(snap[0]["value"], "话很少")

    def test_weaken_when_old_evidence_strong(self):
        for _ in range(5):
            store.upsert_trait(self.c, self.ident, "语气", "话很多")
        self.assertEqual(store.upsert_trait(self.c, self.ident, "语气", "话很少"), "weakened")
        snap = store.trait_snapshot(self.c, self.ident)
        self.assertEqual(snap[0]["value"], "话很多")     # 一句反话改不掉
        self.assertEqual(snap[0]["evidence"], 4)

    def test_trim_keeps_strongest(self):
        for i in range(store.TRAIT_MAX + 3):
            store.upsert_trait(self.c, self.ident, f"n{i}", f"v{i}")
        # 给最早一条刷证据，它必须活下来
        for _ in range(5):
            store.upsert_trait(self.c, self.ident, "n0", "v0")
        self.assertLessEqual(len(store.trait_snapshot(self.c, self.ident, limit=999)),
                             store.TRAIT_MAX)
        self.assertIn("n0", [t["name"] for t in store.trait_snapshot(self.c, self.ident, limit=999)])

    def test_ident_isolation(self):
        other = Ident("qq", "g2", "group:2")
        store.upsert_trait(self.c, self.ident, "爱吐槽", "常用反问")
        self.assertEqual(store.trait_snapshot(self.c, other), [])

    def test_global_traits_require_repetition_across_identities(self):
        other = Ident("qq", "g2", "group:2")
        store.upsert_trait(self.c, self.ident, "爱吐槽", "常用反问吐槽", weight=2)
        store.upsert_trait(self.c, other, "爱吐槽", "常用反问吐槽", weight=3)
        store.upsert_trait(self.c, other, "爱用省略号", "经常用省略号停顿")

        traits = store.global_traits(self.c, min_identities=2)

        self.assertEqual(len(traits), 1)
        self.assertEqual(traits[0]["name"], "爱吐槽")
        self.assertEqual(traits[0]["value"], "常用反问吐槽")
        self.assertEqual(traits[0]["identities"], 2)
        self.assertEqual(traits[0]["evidence"], 5)


class TestReflectParse(unittest.TestCase):
    def test_parse_plain_json(self):
        self.assertEqual(_parse('{"traits":[],"summary":"x"}')["summary"], "x")

    def test_parse_json_in_fence(self):
        raw = '好的：\n```json\n{"traits":[{"name":"爱吐槽","value":"反问","evidence":2}]}\n```'
        self.assertEqual(len(_parse(raw)["traits"]), 1)

    def test_parse_garbage_returns_empty(self):
        self.assertEqual(_parse("完全不 JSON"), {})
        self.assertEqual(_parse(""), {})

    def test_clean_trait_rejects_persona_assertion(self):
        self.assertIsNone(_clean_trait({"name": "你是", "value": "一个温柔的人"}))
        self.assertIsNone(_clean_trait({"name": "人设", "value": "你是一个幽默的鱼"}))
        self.assertIsNone(_clean_trait({"name": "", "value": "x"}))
        self.assertIsNone(_clean_trait("不是字典"))

    def test_clean_trait_rejects_user_nickname_as_self_name(self):
        self.assertIsNone(_clean_trait({
            "name": "玛卡巴卡自称",
            "value": "自称为'玛卡巴卡'并多次提及",
            "evidence": 2,
        }))

    def test_clean_trait_rejects_relationship_names_and_banned_fish_motifs(self):
        cases = [
            {"name": "称呼主人", "value": "固定称呼用户为主人", "evidence": 3},
            {"name": "鱼尾表情", "value": "每句话结尾都加鱼emoji", "evidence": 3},
            {"name": "固定鱼设", "value": "反复提到泡泡、鱼缸和咕噜", "evidence": 4},
        ]
        for item in cases:
            with self.subTest(item=item):
                self.assertIsNone(_clean_trait(item))

    def test_clean_trait_rejects_adult_tone_on_write(self):
        """写侧护栏：清空的 trait 表不能被反思从群聊语料重新灌满（2026-11-03）。"""
        cases = [
            {"name": "语气", "value": "对话中喜欢调侃对方或自嘲，营造轻松氛围", "evidence": 5},
            {"name": "接梗", "value": "对方抛梗就顺着接并自嘲反击", "evidence": 3},
            {"name": "呼应", "value": "被问时先用吐槽开场", "evidence": 2},
            {"name": "防守", "value": "面对质疑常用反问句回怼", "evidence": 2},
        ]
        for item in cases:
            with self.subTest(item=item):
                self.assertIsNone(_clean_trait(item))

    def test_clean_trait_accepts_tendency(self):
        got = _clean_trait({"name": "爱安静", "value": "累的时候话变少", "evidence": 3})
        self.assertEqual(got, ("爱安静", "累的时候话变少", 3))

    def test_clean_trait_caps_evidence(self):
        self.assertEqual(_clean_trait({"name": "a", "value": "b", "evidence": 999})[2], 20)


class TestReflective(unittest.TestCase):
    def setUp(self):
        self.dir = workdir("refl")
        self.db = DB(self.dir / "t.db")
        self.ident = Ident("qq", "g1", "group:1")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _turns(self, n: int, text: str = "哈哈哈") -> None:
        # 反思只归纳"我的说话习惯"：每轮用户发言都要有一条自己的回复，
        # 否则 Reflective 的前置闸会直接跳过（生产里不会只有半边对话）。
        with self.db.tx() as c:
            for i in range(n):
                store.add_turn(c, self.ident, "s1", "user", f"{text}{i}")
                store.add_turn(c, self.ident, "s1", "assistant", f"回{text}{i}")

    def test_skip_when_not_enough_turns(self):
        self._turns(5)
        r = Reflective(self.db, FakePool("{}"), every_turns=30)
        self.assertIsNone(r.tick(self.ident, "s1"))
        self.assertEqual(r.stats["skipped"], 1)

    def test_skip_when_no_turns(self):
        r = Reflective(self.db, FakePool("{}"))
        self.assertIsNone(r.tick(self.ident, "s1"))

    def test_reflect_creates_traits(self):
        self._turns(40)
        payload = json.dumps({"traits": [{"name": "爱安静", "value": "累的时候话变少", "evidence": 3}],
                              "summary": "这段相处里我变得更安静了"}, ensure_ascii=False)
        pool = FakePool(payload)
        r = Reflective(self.db, pool, every_turns=30)
        out = r.tick(self.ident, "s1")
        self.assertIsNotNone(out)
        self.assertEqual(out["deltas"][0]["action"], "new")
        snap = store.trait_snapshot(self.db.conn(), self.ident)
        self.assertEqual(snap[0]["name"], "爱安静")
        # 提示词里必须带底线 + "不是写人设"的约束
        joined = json.dumps(pool.calls[0], ensure_ascii=False)
        self.assertIn("不是写人设", joined)
        self.assertIn("现在的习惯", joined)

    def test_reflect_idempotent_cursor(self):
        self._turns(40)
        payload = json.dumps({"traits": [{"name": "爱安静", "value": "累的时候话变少", "evidence": 1}]},
                             ensure_ascii=False)
        pool = FakePool(payload)
        r = Reflective(self.db, pool, every_turns=30, min_interval_s=0)
        self.assertIsNotNone(r.tick(self.ident, "s1"))
        before = store.trait_snapshot(self.db.conn(), self.ident)[0]["evidence"]
        # 同一批对话再跑：不够新轮次，必须跳过（不能重复计证据）
        self.assertIsNone(r.tick(self.ident, "s1"))
        self._turns(1)  # 只多 1 条，仍不够 30
        self.assertIsNone(r.tick(self.ident, "s1"))
        self.assertEqual(store.trait_snapshot(self.db.conn(), self.ident)[0]["evidence"], before)
        self._turns(30)  # 攒够了，才允许再跑
        self.assertIsNotNone(r.tick(self.ident, "s1"))

    def test_reflect_force_reruns(self):
        self._turns(40)
        payload = json.dumps({"traits": [{"name": "爱安静", "value": "累的时候话变少", "evidence": 1}]},
                             ensure_ascii=False)
        r = Reflective(self.db, FakePool(payload), every_turns=30, min_interval_s=0)
        r.tick(self.ident, "s1")
        # force 只绕过"轮次/间隔阈值"，不绕过幂等：没有新对话就没什么可反思
        self.assertIsNone(r.tick(self.ident, "s1", force=True))
        self._turns(1)  # 有新对话后，force 可以无视 every_turns=30 立刻跑
        self.assertIsNotNone(r.tick(self.ident, "s1", force=True))

    def test_reflect_rejects_persona_output(self):
        self._turns(40)
        payload = json.dumps({"traits": [{"name": "你是", "value": "一个温柔的人"}],
                              "summary": ""}, ensure_ascii=False)
        r = Reflective(self.db, FakePool(payload), every_turns=30)
        out = r.tick(self.ident, "s1")
        # 人设断言全部丢弃；没有可落库内容时整轮跳过（不写空 reflection 行）
        self.assertEqual((out or {}).get("deltas", []), [])
        self.assertEqual(store.trait_snapshot(self.db.conn(), self.ident), [])

    def test_no_pool_does_not_crash(self):
        self._turns(40)
        r = Reflective(self.db, None)
        self.assertIsNone(r.tick(self.ident, "s1"))
        self.assertEqual(r.stats["errors"], 1)

    def test_model_failure_does_not_crash(self):
        self._turns(40)

        class Boom(FakePool):
            def chat(self, messages, *, timeout_s=None, **kwargs):
                raise RuntimeError("gateway down")

        r = Reflective(self.db, Boom(""), every_turns=30)
        self.assertIsNone(r.tick(self.ident, "s1"))
        self.assertEqual(r.stats["errors"], 1)
        self.assertEqual(store.trait_snapshot(self.db.conn(), self.ident), [])


if __name__ == "__main__":
    unittest.main()
