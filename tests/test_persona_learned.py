"""persona 的学习例句解析 / 风格块拼装 —— 覆盖率补强（50% → 高）。

这些分支直接决定"模型看到的人设提示词长什么样"，属于高价值低覆盖区：
- `learned_examples`：dict / 二元组 / 字符串里的字面量 / 坏文件 / 缓存
- `style_block`：child 档走成长引擎（不叠学习特征）、其它档叠 traits
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiji.brain import persona  # noqa: E402
from tests._tempdir import make_tmp  # noqa: E402


class LearnedExamplesTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_tmp(self, "persona-learned")
        self.patch = mock.patch.object(persona, "_LEARNED_PATH", self.dir)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        persona._LEARNED.clear()

    def write(self, name: str, payload) -> Path:
        path = self.dir / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_missing_file_returns_empty(self):
        self.assertEqual(persona.learned_examples("nobody"), [])

    def test_dict_items_normalised(self):
        self.write("style1", {"examples": [
            {"user": "在吗", "assistant": "在呀"},
            {"用户": "干嘛", "回应": "玩呢"},
        ]})
        self.assertEqual(
            persona.learned_examples("style1"),
            ["对方：在吗｜你：在呀", "对方：干嘛｜你：玩呢"],
        )

    def test_tuple_items_and_literal_strings(self):
        self.write("style2", {"examples": [
            ["你几岁", "六岁半"],
            "{'user': '喜欢啥', 'assistant': '恐龙'}",
            "['吃啥', '披萨']",
            "就是一句话",
        ]})
        out = persona.learned_examples("style2")
        self.assertEqual(out[0], "对方：你几岁｜你：六岁半")
        self.assertEqual(out[1], "对方：喜欢啥｜你：恐龙")
        self.assertEqual(out[2], "对方：吃啥｜你：披萨")
        self.assertEqual(out[3], "就是一句话")

    def test_broken_json_and_wrong_types_are_ignored(self):
        (self.dir / "bad.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(persona.learned_examples("bad"), [])
        self.write("wrong", {"examples": 5})
        self.assertEqual(persona.learned_examples("wrong"), [])

    def test_at_most_eight_examples(self):
        self.write("many", {"examples": [f"第{i}句" for i in range(20)]})
        self.assertEqual(len(persona.learned_examples("many")), 8)

    def test_cache_is_reused_and_refreshed_on_mtime(self):
        path = self.write("cached", {"examples": ["甲"]})
        self.assertEqual(persona.learned_examples("cached"), ["甲"])
        path.write_text(json.dumps({"examples": ["乙"]}, ensure_ascii=False),
                        encoding="utf-8")
        import os
        os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
        self.assertEqual(persona.learned_examples("cached"), ["乙"])


class StyleBlockTest(unittest.TestCase):
    def setUp(self):
        self.dir = make_tmp(self, "persona-style")
        self.patch = mock.patch.object(persona, "_LEARNED_PATH", self.dir)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_child_uses_growth_book_not_learned_traits(self):
        (self.dir / "child.json").write_text(
            json.dumps({"traits": ["爱讲大道理"], "examples": []}, ensure_ascii=False),
            encoding="utf-8")
        block = persona.style_block("child")
        self.assertIn("玛卡巴卡", block)
        self.assertNotIn("爱讲大道理", block, "儿童档不能被成年语料同化")

    def test_other_style_appends_traits(self):
        (self.dir / "sunba.json").write_text(
            json.dumps({"traits": ["爱自嘲", "阴阳怪气"], "examples": []},
                       ensure_ascii=False), encoding="utf-8")
        block = persona.style_block("sunba")
        self.assertIn("爱自嘲", block)

    def test_unknown_style_is_empty_not_crash(self):
        self.assertEqual(persona.style_block("no_such_style"), "")


if __name__ == "__main__":
    unittest.main()
