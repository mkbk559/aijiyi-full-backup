# -*- coding: utf-8 -*-
"""vectors 层单测：hashing 基础 + HybridIndex 双路（mock chat，确定性）。"""

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiji.vectors import (
    HybridIndex,
    LLM_DIMS,
    BruteForceIndex,
    _parse_dims,
    cosine,
    hashing_embed,
)


def mkdb(tag: str):
    from aiji.db import DB
    p = Path(__file__).parent.parent / ".tmp" / f"vectors-{tag}.db"
    p.parent.mkdir(mode=0o777, exist_ok=True)
    if p.exists():
        p.unlink()
    return DB(p)


class TestHashing(unittest.TestCase):
    def test_norm_and_determinism(self):
        a = hashing_embed("用户住在杭州西湖区")
        b = hashing_embed("用户住在杭州西湖区")
        self.assertEqual(a, b)
        n = sum(x * x for x in a) ** 0.5
        self.assertAlmostEqual(n, 1.0, places=5)

    def test_same_text_high_sim(self):
        a = hashing_embed("我住在苏州工业园区")
        b = hashing_embed("我住在苏州工业园区")
        self.assertGreater(cosine(a, b), 0.99)


class TestParseDims(unittest.TestCase):
    def test_valid(self):
        v = _parse_dims("[0.9, 0.8, 0.1, 0.0, 0.2, 0.3, 0.5, 0.1, 0.4, 0.2]")
        self.assertEqual(len(v), len(LLM_DIMS))
        self.assertTrue(all(0.0 <= x <= 1.0 for x in v))

    def test_fence_and_clamp(self):
        v = _parse_dims("```json\n[1.5, -0.2, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]\n```")
        self.assertIsNotNone(v)
        self.assertEqual(v[0], 1.0)  # 越界钳制
        self.assertEqual(v[1], 0.0)

    def test_garbage(self):
        self.assertIsNone(_parse_dims("我不知道你在说什么"))
        self.assertIsNone(_parse_dims("[0.1, 0.2]"))  # 长度不对
        self.assertIsNone(_parse_dims("[a,b,c,d,e,f,g,h,i,j]"))


def _fake_chat(scores_by_text: dict[str, list[float]]):
    """按文本前 6 字匹配返回预设维度分的 mock。"""
    def chat(messages):
        t = messages[-1]["content"]
        for k, v in scores_by_text.items():
            if k in t:
                return "[" + ",".join(f"{x:.2f}" for x in v) + "]"
        return "[" + ",".join("0.1" for _ in LLM_DIMS) + "]"
    return chat


class TestHybridIndex(unittest.TestCase):
    # 维度顺序：身份 居住 职业 兴趣 人际 时间 偏好 情绪 知识 计划
    SC = {
        "住在深圳南山": [0.2, 0.95, 0.1, 0.0, 0.0, 0.1, 0.0, 0.0, 0.3, 0.0],
        "后端开发五年": [0.1, 0.0, 0.95, 0.0, 0.0, 0.1, 0.0, 0.0, 0.3, 0.0],
        "养猫叫橘子":   [0.2, 0.1, 0.0, 0.4, 0.0, 0.1, 0.2, 0.3, 0.2, 0.0],
    }

    def test_semantic_route_wins_on_paraphrase(self):
        """query「住哪」与居住 fact 零字面重叠，语义路应命中。"""
        idx = HybridIndex(chat=_fake_chat({
            "住在深圳南山": self.SC["住在深圳南山"],
            # query 打分：居住维度高
        }))
        idx._chat = _fake_chat(dict(self.SC, **{"文本：他在哪个城市住": [0.1, 0.9, 0.0, 0.0, 0.1, 0.1, 0.0, 0.0, 0.2, 0.0]}))
        idx.upsert(1, "住在深圳南山区", {"text": "x"})
        idx.upsert(2, "后端开发五年", {"text": "y"})
        top = idx.search("他在哪个城市住", k=2)
        self.assertEqual(top[0][0], 1)
        self.assertGreater(top[0][1], 0.8)

    def test_hashing_fallback_when_chat_fails(self):
        """chat 一直失败 → 每条 fact vd=None，纯 hashing 行为 = BruteForceIndex。"""
        def bad_chat(messages):
            raise RuntimeError("pool down")
        idx = HybridIndex(chat=bad_chat)
        ref = BruteForceIndex()
        for fid, text in ((1, "用户住在杭州"), (2, "养猫叫橘子")):
            idx.upsert(fid, text, {"text": text})
            ref.upsert(fid, text, {"text": text})
        a = idx.search("用户住在杭州", k=2)
        b = ref.search("用户住在杭州", k=2)
        self.assertEqual([x[0] for x in a], [x[0] for x in b])
        self.assertAlmostEqual(a[0][1], b[0][1], places=6)

    def test_dim_vector_normalization(self):
        """语义 cosine 已归一化：全零 query 不产生除零/NaN。"""
        idx = HybridIndex(chat=_fake_chat(self.SC))
        idx._chat = _fake_chat(dict(self.SC, **{"zzz": [0.0] * 10}))
        idx.upsert(1, "住在深圳南山", {"text": "x"})
        top = idx.search("zzz", k=1)  # query 维度全零 → 只剩 hashing 路
        self.assertEqual(len(top), 1)
        for _fid, s, _m in top:
            self.assertFalse(s != s, "cosine 产出 NaN")

    def test_slow_semantic_upsert_does_not_block_search(self):
        entered = threading.Event()
        release = threading.Event()

        def chat(messages):
            text = messages[-1]["content"]
            if "slow-fact" in text:
                entered.set()
                release.wait(2.0)
            return "[" + ",".join("0.1" for _ in LLM_DIMS) + "]"

        idx = HybridIndex(chat=chat)
        worker = threading.Thread(
            target=idx.upsert,
            args=(1, "slow-fact", {"text": "slow-fact"}),
            daemon=True,
        )
        worker.start()
        self.assertTrue(entered.wait(1.0), "upsert 未进入慢语义计算")

        started = time.perf_counter()
        try:
            idx.search("fast-query", k=1)
        finally:
            release.set()
            worker.join(timeout=2.0)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.5, "搜索被慢语义写入锁住")


if __name__ == "__main__":
    unittest.main()
