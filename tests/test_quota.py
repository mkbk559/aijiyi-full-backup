"""主动配额（= 反代积分）与自我记忆进注入块的回归测试。

两个都是"没数字就没法判断"的东西，所以这里全部用**构造出来的积分/单价**断言算式，
再用一个假网关（可注入 opener）证明"取不到就退回硬上限"，绝不联网。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiji.right_hand import params  # noqa: E402
from aiji.right_hand.config import Settings  # noqa: E402
from aiji.hippocampus.memory import Memory  # noqa: E402
from aiji.brain.quota import CreditProvider, parse_credits, record_send, sends_last_24h
from aiji.hippocampus.store import FactDraft, Ident, add_fact  # noqa: E402

ME = Ident("quota", "me", "default")


class _Resp:
    """最小可用的 urlopen 返回值：能 read()、有 headers。"""

    def __init__(self, payload, headers: dict | None = None):
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def read(self, _n: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(payload, headers: dict | None = None):
    def _open(_req, timeout=None):  # noqa: ARG001
        return _Resp(payload, headers)
    return _open


class TestParseCredits(unittest.TestCase):
    def test_real_gateway_formats(self):
        # 本机实测两种写法：agent2api 的 "x0.5 credits"、纯数字
        self.assertEqual(parse_credits("x0.5 credits"), 0.5)
        self.assertEqual(parse_credits(0.5), 0.5)
        self.assertEqual(parse_credits("12.5"), 12.5)
        self.assertEqual(parse_credits("1,234 credits"), 1234.0)
        self.assertIsNone(parse_credits(None))
        self.assertIsNone(parse_credits(""))
        self.assertIsNone(parse_credits(True))  # bool 不算数字


class TestCreditProvider(unittest.TestCase):
    def test_no_source_falls_back_to_daily_cap(self):
        p = CreditProvider()
        st = p.state(daily_cap=3)
        self.assertIsNone(st.remaining)
        self.assertEqual(st.allowed_24h, 3)
        self.assertEqual(st.source, "credits:unknown/cost:default")

    def test_remaining_from_url_and_cost_from_models(self):
        # 余额 10 积分，单条 0.5 → 10*0.8/0.5 = 16 次，被防刷屏上限截断
        p = CreditProvider(
            url="http://gw/balance", json_path="data.credits",
            models_url="http://gw/v1/models", model="m1", reserve=0.2,
            opener=lambda req, timeout=None: (
                _Resp({"data": {"credits": 10}} if "balance" in req.full_url
                      else {"data": [{"id": "m1", "credits": "x0.5 credits"}]})
            ),
        )
        self.assertEqual(p.remaining()[0], 10.0)
        self.assertEqual(p.cost_per_message()[0], 0.5)
        self.assertEqual(p.state(max_cap=5).allowed_24h, 5)      # 封顶生效
        self.assertEqual(p.state().allowed_24h, params.PROACTIVE_QUOTA_MAX)  # 不传就按 8

    def test_credits_file_is_a_source(self):
        import json as _json
        work = ROOT / ".tmp" / "test_credits_file"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(mode=0o777, parents=True, exist_ok=True)
        f = work / "credits.json"
        f.write_text(_json.dumps({"credits": 4, "at": 1_800_000_000.0}), encoding="utf-8")
        p = CreditProvider(
            file_path=str(f), models_url="http://gw/v1/models", model="m1",
            opener=_opener({"data": [{"id": "m1", "credits": 1}]}),
        )
        remaining, source = p.remaining(now=1_800_000_000.0 + 3600)
        self.assertEqual(remaining, 4.0)
        self.assertEqual(source, "file(1.0h)")   # 带"我填了多久"，便于判断是否过期
        self.assertEqual(p.state().allowed_24h, 3)  # 4*0.8/1 = 3

    def test_tiny_balance_starves_proactive(self):
        # 1.25 积分、单条 1 → 1.25*0.8=1.0 → 只够 1 次
        p = CreditProvider(
            url="http://gw/b", models_url="http://gw/v1/models", model="m1",
            opener=lambda req, timeout=None: (
                _Resp({"credits": 1.25} if "/b" in req.full_url
                      else {"data": [{"id": "m1", "credits": 1}]})
            ),
        )
        self.assertEqual(p.state(daily_cap=3).allowed_24h, 1)

    def test_zero_balance_blocks_everything(self):
        p = CreditProvider(url="http://gw/b", opener=_opener({"credits": 0}))
        self.assertEqual(p.state(daily_cap=3).allowed_24h, 0)

    def test_gateway_error_does_not_raise(self):
        def boom(_req, timeout=None):
            raise OSError("gateway down")
        p = CreditProvider(url="http://gw/b", opener=boom)
        st = p.state(daily_cap=3)
        self.assertEqual(st.allowed_24h, 3)          # 回退硬上限
        self.assertIn("url(error)", st.source)

    def test_header_hint_beats_static(self):
        p = CreditProvider(static=100.0)
        p.note_headers({"x-credits-remaining": "42"})
        self.assertEqual(p.remaining()[0], 42.0)
        self.assertEqual(p.remaining()[1], "header")

    def test_cache_avoids_second_fetch(self):
        calls = {"n": 0}

        def counting(_req, timeout=None):
            calls["n"] += 1
            return _Resp({"credits": 5})
        p = CreditProvider(url="http://gw/b", opener=counting, cache_s=60.0)
        p.remaining()
        p.remaining()
        self.assertEqual(calls["n"], 1)


class TestProactiveWindow(unittest.TestCase):
    def setUp(self):
        self.work = ROOT / ".tmp" / "test_quota"
        shutil.rmtree(self.work, ignore_errors=True)
        self.work.mkdir(mode=0o777, parents=True, exist_ok=True)
        self.m = Memory(Settings(db_path=self.work / "q.db", routes=()), engines="auto")

    def tearDown(self):
        self.m.close()

    def test_window_counts_only_last_24h(self):
        now = 1_800_000_000.0
        record_send(self.m.heart, ME, now)
        record_send(self.m.heart, ME, now - 90000)
        self.assertEqual(sends_last_24h(self.m.heart, ME, now), 1)

    def test_quota_ok_follows_credit_derived_cap(self):
        now = 1_800_000_000.0
        # 积分只够 1 次 → 发过 1 次后就不许再主动
        self.m.credits = CreditProvider(
            url="http://gw/b", models_url="http://gw/v1/models", model="m1",
            opener=lambda req, timeout=None: (
                _Resp({"credits": 1.25} if "/b" in req.full_url
                      else {"data": [{"id": "m1", "credits": 1}]})
            ),
        )
        self.assertTrue(self.m.proactive_quota_ok(ME, now=now))
        self.assertEqual(self.m.proactive_spend(ME, now=now), 1)
        self.assertFalse(self.m.proactive_quota_ok(ME, now=now + 60))

    def test_unknown_credits_keeps_old_behaviour(self):
        now = 1_800_000_000.0
        self.m.credits = CreditProvider()  # 没有任何来源
        for _ in range(params.PROACTIVE_QUOTA_24H):
            self.assertTrue(self.m.proactive_quota_ok(ME, now=now))
            self.m.proactive_spend(ME, now=now)
        self.assertFalse(self.m.proactive_quota_ok(ME, now=now))


class TestSelfMemoryInjection(unittest.TestCase):
    def setUp(self):
        self.work = ROOT / ".tmp" / "test_selfmem"
        shutil.rmtree(self.work, ignore_errors=True)
        self.work.mkdir(mode=0o777, parents=True, exist_ok=True)
        self.m = Memory(Settings(db_path=self.work / "s.db", routes=()), engines="auto")

    def tearDown(self):
        self.m.close()

    def test_related_self_memory_enters_block(self):
        self.m.remember_self("我不喜欢被叫老师", kind="pref")
        inj = self.m.inject("你还记得我不喜欢什么称呼吗", ME)
        self.assertIn("我不喜欢被叫老师", inj.text)
        self.assertTrue(any(i.kind == "self" for i in inj.items))

    def test_unrelated_self_memory_stays_out(self):
        self.m.remember_self("我不喜欢被叫老师", kind="pref")
        inj = self.m.inject("成都今天天气如何", ME)
        self.assertNotIn("我不喜欢被叫老师", inj.text)


if __name__ == "__main__":
    unittest.main()
