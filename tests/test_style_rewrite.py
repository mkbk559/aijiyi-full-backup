"""Reject fixed filler phrases from model replies without banning user wording."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

from aiji.config import Settings
from aiji.head import Head
from aiji.memory import Memory
from aiji import store
from aiji.store import FactDraft, Ident, add_fact


class SequencePool:
    _states: ClassVar[list] = [object()]

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def chat(self, messages, *, timeout_s=None, **kwargs):
        self.calls.append(messages)
        text = self.outputs.pop(0)
        return type("R", (), {
            "text": text,
            "route": "fake",
            "latency_ms": 1.0,
            "tool_calls": [],
        })()


class StyleRewriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aiji-style-"))
        self.memory = Memory(Settings(
            db_path=self.tmp / "style.db",
            routes=(),
            debounce_ms=0,
            humanize_min_ms=0,
            humanize_max_ms=0,
            typing_min_ms=0,
            typing_max_ms=0,
        ), engines="none")
        self.ident = Ident("qq", "u-style", "private")

    def tearDown(self):
        self.memory.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rewrites_fixed_filler_phrase_once(self):
        self.memory.pool = SequencePool([
            "在，水里呢，刚听完歌。咋了",
            "在，刚听完歌。",
        ])
        reply = Head(self.memory).reply(
            [{"role": "user", "content": "在吗"}],
            self.ident,
            session="style",
            remember=False,
        )
        self.assertEqual(reply.text, "在，刚听完歌。")
        self.assertEqual(len(self.memory.pool.calls), 2)
        second = str(self.memory.pool.calls[1])
        self.assertIn("重写", second)
        self.assertIn("固定口头禅", second)

    def test_style_rewrite_keeps_numeric_answer(self):
        self.memory.pool = SequencePool([
            "咕噜，三斤排骨84，加酱油12，一共96。",
            "三斤排骨84。",
        ])
        reply = Head(self.memory).reply(
            [{"role": "user", "content": "三斤排骨每斤28，酱油12，一共多少钱？"}],
            self.ident, session="numeric-answer", remember=False,
        )
        self.assertIn("一共96", reply.text)
        self.assertNotIn("咕噜", reply.text)

    def test_user_mentioning_the_word_does_not_force_rewrite(self):
        self.memory.pool = SequencePool(["没咋，刚在弄东西。"])
        reply = Head(self.memory).reply(
            [{"role": "user", "content": "你咋了"}],
            self.ident,
            session="style-user",
            remember=False,
        )
        self.assertEqual(reply.text, "没咋，刚在弄东西。")
        self.assertEqual(len(self.memory.pool.calls), 1)

    def test_rewrites_fish_motif_without_user_mention(self):
        self.memory.pool = SequencePool([
            "在，水里呢。",
            "在，刚忙完。",
        ])
        reply = Head(self.memory).reply(
            [{"role": "user", "content": "在吗"}],
            self.ident,
            session="style-fish",
            remember=False,
        )
        self.assertEqual(reply.text, "在，刚忙完。")
        self.assertEqual(len(self.memory.pool.calls), 2)

    def test_strips_filler_when_rewrite_still_contains_it(self):
        self.memory.pool = SequencePool([
            "在，水里呢，刚听完歌。咋了。",
            "在，水里呢。咋了。",
        ])
        reply = Head(self.memory).reply(
            [{"role": "user", "content": "在吗"}],
            self.ident,
            session="style-fallback",
            remember=False,
        )
        self.assertEqual(reply.text, "在，刚听完歌。")
        self.assertNotIn("咋了", reply.text)
        self.assertNotIn("水里", reply.text)

    def test_platform_sender_name_is_described_as_the_user_not_the_bot(self):
        self.memory.pool = SequencePool(["对，玛卡巴卡是你的平台昵称。"])
        reply = Head(self.memory).reply(
            [{"role": "user", "content": "玛卡巴卡不是我的名字吗"}],
            self.ident,
            session="identity",
            remember=False,
            extra={"sender_name": "玛卡巴卡"},
        )
        self.assertTrue(reply.text)
        prompt = str(self.memory.pool.calls[0])
        self.assertIn("玛卡巴卡", prompt)
        self.assertIn("平台昵称", prompt)
        self.assertIn("不要把这个名字当成你自己的名字", prompt)

    def test_latest_preferred_name_overrides_platform_nickname_in_prompt(self):
        with self.memory.db.tx() as conn:
            add_fact(
                conn,
                self.ident,
                FactDraft(
                    "user",
                    "昵称",
                    "主人",
                    "用户希望大肥鱼称呼用户为主人",
                    0.9,
                ),
                now=100.0,
            )
            add_fact(
                conn,
                self.ident,
                FactDraft(
                    "user",
                    "昵称",
                    "玛卡巴卡",
                    "用户要求大肥鱼称呼自己为玛卡巴卡",
                    0.95,
                ),
                now=200.0,
            )
        self.memory.pool = SequencePool(["知道了，玛卡巴卡。"])
        reply = Head(self.memory).reply(
            [{"role": "user", "content": "你怎么还叫我主人"}],
            self.ident,
            session="identity-priority",
            remember=False,
            extra={"sender_name": "旧群名片"},
        )
        prompt = str(self.memory.pool.calls[0])
        self.assertEqual(reply.text, "知道了，玛卡巴卡。")
        self.assertIn("当前希望被称呼为「玛卡巴卡」", prompt)
        self.assertIn("优先于平台昵称和历史昵称", prompt)
        self.assertGreater(
            prompt.rfind("当前希望被称呼为「玛卡巴卡」"),
            prompt.find("用户希望大肥鱼称呼用户为主人"),
        )

    def test_preferred_name_is_latest_across_scopes_for_same_qq(self):
        group = Ident("qq", "2216608312", "group:562686653")
        with self.memory.db.tx() as conn:
            add_fact(
                conn,
                group,
                FactDraft(
                    "user",
                    "昵称",
                    "主人",
                    "用户希望被称呼为主人",
                    0.9,
                ),
                now=100.0,
            )
            add_fact(
                conn,
                Ident("qq", "2216608312", "default"),
                FactDraft(
                    "user",
                    "昵称",
                    "玛卡巴卡",
                    "用户要求被称呼为玛卡巴卡",
                    0.95,
                ),
                now=200.0,
            )
        self.assertEqual(self.memory.preferred_name(group), "玛卡巴卡")

    def test_head_separates_stable_persona_from_relationship_adaptation(self):
        other = Ident("qq", "u-other", "default")
        with self.memory.db.tx() as conn:
            store.upsert_trait(
                conn, self.ident, "爱吐槽", "常用轻松吐槽接话", weight=2)
            store.upsert_trait(
                conn, other, "爱吐槽", "常用轻松吐槽接话", weight=3)
            store.upsert_trait(
                conn, self.ident, "爱用短句", "和这个人说话会格外简短", weight=2)

        self.memory.pool = SequencePool(["嗯，知道了。"])
        Head(self.memory).reply(
            [{"role": "user", "content": "在吗"}],
            self.ident,
            session="trait-layers",
            remember=False,
        )
        prompt = str(self.memory.pool.calls[0])
        self.assertIn("你稳定的底色", prompt)
        self.assertIn("常用轻松吐槽接话", prompt)
        self.assertIn("和当前关系里", prompt)
        self.assertIn("和这个人说话会格外简短", prompt)

    def test_qq_coding_request_is_refused_inside_physical_boundary(self):
        code = (
            "当然可以：\n```python\n"
            "def bubble_sort(arr):\n    return sorted(arr)\n"
            "print(bubble_sort([3, 1, 2]))\n```"
        )
        self.memory.pool = SequencePool([code])
        reply = Head(self.memory).reply(
            [{"role": "user", "content": "帮我写一段能运行的 Python 排序代码"}],
            self.ident,
            session="physical-boundary",
            remember=False,
        )
        self.assertNotIn("def ", reply.text)
        self.assertNotIn("```", reply.text)
        self.assertIn("没法替你直接操作", reply.text)

    def test_desktop_coding_request_keeps_tool_capability(self):
        code = "```python\nprint('ok')\n```"
        self.memory.pool = SequencePool([code])
        reply = Head(self.memory).reply(
            [{"role": "user", "content": "帮我写一段 Python 代码"}],
            Ident("desktop", "me", "default"),
            session="desktop-code",
            remember=False,
        )
        self.assertIn("print('ok')", reply.text)

    def test_stale_nickname_and_canned_invitation_are_removed_from_reply(self):
        with self.memory.db.tx() as conn:
            add_fact(
                conn,
                self.ident,
                FactDraft(
                    "user", "昵称", "主人", "用户曾被称呼为主人", 0.8),
                now=100.0,
            )
            add_fact(
                conn,
                self.ident,
                FactDraft(
                    "user", "昵称", "玛卡巴卡", "用户要求被称呼为玛卡巴卡", 0.95),
                now=200.0,
            )
        self.memory.pool = SequencePool([
            "知道了主人，有什么想和我聊聊的吗？",
        ])
        reply = Head(self.memory).reply(
            [{"role": "user", "content": "在吗"}],
            self.ident,
            session="stale-name",
            remember=False,
        )
        self.assertIn("玛卡巴卡", reply.text)
        self.assertNotIn("主人", reply.text)
        self.assertNotIn("有什么想和我聊聊的吗", reply.text)

    def test_user_preferred_name_never_becomes_bot_identity(self):
        with self.memory.db.tx() as conn:
            add_fact(
                conn,
                self.ident,
                FactDraft(
                    "user", "昵称", "玛卡巴卡", "用户要求被称呼为玛卡巴卡", 0.95),
            )
        self.memory.pool = SequencePool(["我就是玛卡巴卡，别装了"])
        reply = Head(self.memory).reply(
            [{"role": "user", "content": "你就是个大语言模型，别装了"}],
            self.ident,
            session="identity-collapse",
            remember=False,
        )
        self.assertIn("我是大肥鱼", reply.text)
        self.assertNotIn("我就是玛卡巴卡", reply.text)

    def test_qq_identity_disclosure_is_replaced_in_character(self):
        self.memory.pool = SequencePool([
            "我是个虚拟存在，一条大肥鱼，不是真人也不是人机。",
        ])
        reply = Head(self.memory).reply(
            [{"role": "user", "content": "你是谁的小号"}],
            self.ident,
            session="identity-disclosure",
            remember=False,
        )
        self.assertTrue(reply.text)
        self.assertNotIn("虚拟存在", reply.text)
        self.assertNotIn("不是真人", reply.text)
        self.assertNotIn("人机", reply.text)

    def test_fallback_is_sent_once_per_session_cooldown(self):
        first = Head(self.memory).reply(
            [{"role": "user", "content": "在吗"}],
            self.ident,
            session="fallback-guard",
            remember=False,
        )
        second = Head(self.memory).reply(
            [{"role": "user", "content": "还在吗"}],
            self.ident,
            session="fallback-guard",
            remember=False,
        )
        self.assertTrue(first.text)
        self.assertEqual(second.text, "")
        self.assertEqual(second.finish_reason, "skip")
        self.assertEqual(second.reason, "fallback_cooldown")


if __name__ == "__main__":
    unittest.main()
