# -*- coding: utf-8 -*-
"""回复长度硬上限（2026-11-03）：生成侧掐 max_tokens，出口侧按平台裁字符。

三条不可退让的性质：
  1. `fit_reply` 永远不返回超过上限的串，且尽量落在句末；
  2. 裁剪不劈开 emoji 的 ZWJ 序列、不劈开 CQ 码/方括号标记；
  3. 真实链路（Head.reply / Heart.pump）里超长回复**被裁**而不是整条丢弃。
"""
import json
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiji.brain.gate import check_reply, fit_reply, reply_char_limit
from aiji.brain.head import Head
from aiji.heart.heart import Heart, OutboxEvent
from aiji.hippocampus.db import DB
from aiji.hippocampus.memory import Memory
from aiji.hippocampus.store import Ident
from aiji.right_hand import params
from aiji.right_hand.config import Settings

TMP = Path(__file__).resolve().parent.parent / ".tmp"


def workdir(tag: str) -> Path:
    d = TMP / f"test_reply_{tag}_{uuid.uuid4().hex[:8]}"
    d.mkdir(mode=0o777, parents=True, exist_ok=True)
    return d


class TestFitReply(unittest.TestCase):
    def test_short_and_exact_untouched(self):
        self.assertEqual(fit_reply("你好呀", 200), "你好呀")
        exact = "x" * 200
        self.assertEqual(fit_reply(exact, 200), exact)

    def test_never_exceeds_limit(self):
        for limit in (5, 12, 80, 200):
            out = fit_reply("这是一句很长的话，用来测试裁剪。" * 40, limit)
            self.assertLessEqual(len(out), limit, f"limit={limit} 越界: {len(out)}")

    def test_cuts_at_sentence_end(self):
        out = fit_reply("第一句。第二句。第三句。第四句。第五句。", 12)
        self.assertLessEqual(len(out), 12)
        self.assertTrue(out.endswith("…"), out)
        self.assertTrue(out[:-1].endswith("。"), out)

    def test_hard_cut_when_no_punctuation(self):
        out = fit_reply("a" * 500, 50)
        self.assertEqual(len(out), 50)
        self.assertTrue(out.endswith("…"))

    def test_does_not_split_zwj_emoji(self):
        family = "👨\u200d👩\u200d👧"
        text = family * 30
        out = fit_reply(text, 7)
        self.assertLessEqual(len(out), 7)
        # 省略号前面不能只剩半个 ZWJ 序列（最后一个字符不能是孤立连接符）
        self.assertFalse(out[:-1].endswith("\u200d"), repr(out))
        self.assertNotIn("\u200d…", out)

    def test_does_not_cut_inside_cq_code(self):
        text = "收到[CQ:face,id=14]了吗" * 20
        out = fit_reply(text, 12)
        self.assertLessEqual(len(out), 12)
        self.assertNotIn("[CQ:face,id", out if out.count("[") == out.count("]") else out[:out.rfind("[")])
        self.assertEqual(out.count("["), out.count("]"), repr(out))

    def test_platform_limits(self):
        self.assertEqual(reply_char_limit("qq"), params.IM_REPLY_CHAR_LIMIT)
        self.assertEqual(reply_char_limit("wechat"), params.IM_REPLY_CHAR_LIMIT)
        self.assertEqual(reply_char_limit("api"), params.MAX_REPLY_CHARS)
        self.assertEqual(reply_char_limit(None), params.MAX_REPLY_CHARS)

    def test_fitted_text_passes_gate(self):
        long_text = "好呀。" * 200
        self.assertFalse(check_reply(long_text).ok)          # 闸门仍然拒超长
        self.assertTrue(check_reply(fit_reply(long_text, 200)).ok)   # 裁完就放行


class TestHeadCap(unittest.TestCase):
    def _memory(self, tag: str, reply_text: str):
        memory = Memory(Settings(
            db_path=workdir(tag) / "h.db", routes=(), debounce_ms=0,
            humanize_min_ms=0, humanize_max_ms=0,
            typing_min_ms=0, typing_max_ms=0,
        ), engines="none")
        seen = {}

        class Pool:
            _states = [object()]

            def chat(self, messages, *, timeout_s=None, **kwargs):
                seen["max_tokens"] = kwargs.get("max_tokens")
                return type("R", (), {
                    "text": reply_text, "route": "fake", "latency_ms": 1.0,
                })()

        memory.pool = Pool()
        return memory, seen

    def test_im_reply_is_trimmed_to_platform_budget(self):
        memory, seen = self._memory("trim", "这是一句很长的回复。" * 100)
        out = Head(memory).reply(
            [{"role": "user", "content": "在吗"}], Ident("qq", "u1", "default"),
            session="private:u1")
        self.assertLessEqual(len(out.text), params.IM_REPLY_CHAR_LIMIT)
        self.assertGreater(len(out.text), 0)
        self.assertEqual(seen["max_tokens"], params.MAX_REPLY_TOKENS)

    def test_default_reply_uses_generous_budget(self):
        memory, seen = self._memory("api", "这是一句很长的回复。" * 100)
        out = Head(memory).reply(
            [{"role": "user", "content": "在吗"}], Ident("api", "u2", "default"),
            session="api:u2")
        self.assertLessEqual(len(out.text), params.MAX_REPLY_CHARS)
        self.assertEqual(seen["max_tokens"], params.MAX_REPLY_TOKENS)

    def test_caller_max_tokens_cannot_exceed_cap(self):
        memory, seen = self._memory("cap", "好的")
        Head(memory).reply(
            [{"role": "user", "content": "在吗"}], Ident("qq", "u3", "default"),
            session="private:u3", max_tokens=99_999)
        self.assertEqual(seen["max_tokens"], params.MAX_REPLY_TOKENS)


class TestHeartCap(unittest.TestCase):
    def test_pump_trims_handler_reply(self):
        db = DB(workdir("heart") / "h.db")
        try:
            heart = Heart(db, handler=lambda ident, ev: "很长的回复。" * 200)
            ident = Ident("qq", "u9", "default")
            heart.enqueue(ident, OutboxEvent(kind="mention", text="在吗"))
            heart.pump()
            rows = db.read("SELECT payload FROM send_queue")
            self.assertTrue(rows, "应该有一条待发送")
            for row in rows:
                payload = json.loads(row[0])
                text = payload.get("text") or ""
                self.assertLessEqual(len(text), params.IM_REPLY_CHAR_LIMIT, text[:40])
                self.assertTrue(text.endswith("…"), text[-6:])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
