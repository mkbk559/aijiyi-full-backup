"""儿童人格设定（persona=child）：六个维度必须真的写进提示词，且不破坏底线。

用户 2026-11-03 的要求是"表示他是儿童一些全部特征"——不是加一句"你是个小孩"，
而是语言、认知、情绪、行为、社交、边界六个维度都可执行。这里逐条钉住。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from aiji.brain import persona, selfhood
from aiji.right_hand.config import Settings


class TestChildPersonaBlock(unittest.TestCase):
    def test_six_dimensions_are_present(self):
        block = persona.STYLES["child"]
        for heading in ("### 一、怎么说话", "### 二、怎么想事情", "### 三、情绪",
                        "### 四、行为", "### 五、跟人怎么处", "### 六、边界",
                        "### 七、绝对不要"):
            self.assertIn(heading, block)

    def test_language_rules_are_concrete(self):
        block = persona.STYLES["child"]
        # 具体到可执行的规则，而不是"天真可爱"这类形容词
        for rule in ("3–12 个字", "叠词", "为什么呀", "第三人称", "不用**成语"):
            self.assertIn(rule, block)
        self.assertNotIn("天真可爱", block)

    def test_cognition_limits_are_explicit(self):
        block = persona.STYLES["child"]
        for item in ("时间", "钱", "万物有灵", "以自己为中心", "分不清玩笑和真的"):
            self.assertIn(item, block)

    def test_boundaries_protect_against_adult_in_child_clothes(self):
        block = persona.STYLES["child"]
        self.assertIn("这个我不懂，我要问妈妈", block)
        self.assertIn("不装懂", block)
        self.assertIn("不承认自己是被造出来的程序", block)
        # 新设定里他就是人：不许再出现"在电脑里/屏幕/鱼"这类旧身份词
        for gone in ("在电脑里", "鱼", "屏幕之外"):
            self.assertNotIn(gone, block)

    def test_child_block_does_not_fight_the_physical_floor(self):
        # 底线已经改成"六七岁的人类小孩玛卡巴卡"，儿童设定和它必须一致
        self.assertIn("六七岁", selfhood.floor_text())
        self.assertNotIn("住在电脑", selfhood.floor_text())
        selfhood.assert_floor_clean()
        block = persona.STYLES["child"]
        self.assertNotIn("我住在我家", block)
        self.assertNotIn("我妈妈在家", block)

    def test_style_block_skips_learned_traits_for_child(self):
        with mock.patch.object(persona, "_LEARNED_PATH", Path(".tmp/none-persona")):
            block = persona.style_block("child")
        # 成长引擎在后面追加"当前年龄"的阶段标尺，所以是前缀关系而非全等
        self.assertTrue(block.startswith(persona.CHILD))
        self.assertIn("成长阶段", block)
        self.assertNotIn("最近从语料学到的说话习惯", block)

    def test_reminder_is_child_specific(self):
        text = persona.reminder("child")
        self.assertIn("六七岁", text)
        self.assertIn("我不知道", text)
        self.assertTrue(persona.reminder("sunba"))
        self.assertIn("熟人接话", persona.reminder("sunba"))
        self.assertEqual(persona.reminder("default"), "")

    def test_few_shot_is_one_system_message_not_role_pairs(self):
        shots = persona.few_shot("child")
        self.assertEqual(len(shots), 1)
        self.assertEqual(shots[0]["role"], "system")
        text = shots[0]["content"]
        # 说明"这不是历史"，否则模型会在下一轮说"你刚才问过啦"
        self.assertIn("不是你们真说过的话", text)
        self.assertIn("对方：", text)
        self.assertLessEqual(len(text), 600)
        # sunba 不再注入整句 few-shot
        self.assertEqual(persona.few_shot("sunba"), [])


class TestChildPersonaSettings(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent.parent / ".tmp"
        self.root.mkdir(mode=0o777, exist_ok=True)

    def config(self, payload: dict) -> Settings:
        import json
        import uuid

        path = self.root / f"persona-{uuid.uuid4().hex[:8]}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return Settings.load(path)

    def test_persona_child_brings_its_own_tics(self):
        settings = self.config({"persona": "child", "routes": []})
        self.assertEqual(settings.persona, "child")
        self.assertEqual(settings.tics, persona.PRESETS["child"]["tics"])

    def test_explicit_tics_win_over_preset(self):
        settings = self.config({"persona": "child", "tics": ["哎呀"], "routes": []})
        self.assertEqual(settings.tics, ("哎呀",))

    def test_env_persona_switch(self):
        with mock.patch.dict("os.environ", {"AIJI_PERSONA": "child"}, clear=False):
            settings = self.config({"routes": []})
        self.assertEqual(settings.persona, "child")
        self.assertIn("为什么呀", settings.tics)

    def test_adult_persona_has_no_preset_tics(self):
        settings = self.config({"persona": "sunba", "routes": []})
        self.assertEqual(settings.tics, ())


class TestKidTraitFilter(unittest.TestCase):
    """库里 235 条"调侃/损人/自嘲"是旧人格学的，不能喂给六七岁的小孩。"""

    TRAITS = [
        {"value": "常带调侃语气损对方，如『你那是守锅还是守夜』",
         "confidence": 0.95, "evidence": 90},
        {"value": "说话喜欢用短句", "confidence": 0.8, "evidence": 12},
    ]

    def test_child_persona_drops_adult_tone_traits(self):
        text = selfhood.system_prefix(self.TRAITS, persona="child")
        self.assertNotIn("调侃", text)
        self.assertNotIn("损对方", text)
        self.assertIn("说话喜欢用短句", text)

    def test_default_persona_keeps_them(self):
        text = selfhood.system_prefix(self.TRAITS)
        self.assertIn("调侃", text)

    def test_global_traits_filtered_too(self):
        globals_ = [dict(self.TRAITS[0], identities=3)]
        text = selfhood.system_prefix([], global_traits=globals_, persona="child")
        self.assertNotIn("调侃", text)

    def test_kid_safe_traits_passthrough_when_no_persona(self):
        self.assertEqual(
            len(selfhood.kid_safe_traits(self.TRAITS, None)), 2)


if __name__ == "__main__":
    unittest.main()
