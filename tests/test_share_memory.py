"""跨平台共享记忆的离线测试（对应 docs/agent/design/memory-sharing-2026-11-05.md）。

盯着三件事：
1. 主人自己的话会镜像到 shared；
2. 非主人（群里陌生人）**不会**进 shared；
3. 读的时候 shared ∪ 平台身份，去重且分数取高。
"""

from __future__ import annotations

import unittest

from aiji.hippocampus import share
from aiji.hippocampus.recall import Hit, RecallResult
from aiji.hippocampus.store import Ident


class FakeStore:
    def __init__(self):
        self.turns = []

    def add_turn(self, conn, ident, session, role, content, **kw):  # noqa: ARG002
        self.turns.append({"ident": ident, "session": session, "role": role,
                           "content": content, "extra": kw.get("extra") or {}})


class MirrorTest(unittest.TestCase):
    def test_owner_platforms_mirror(self):
        for platform in ("qq", "douyin", "bilibili"):
            mirror = share.mirror_ident(Ident(platform, "u1"))
            self.assertIsNotNone(mirror, platform)
            self.assertEqual(mirror.platform, "shared")

    def test_shared_ident_does_not_mirror_again(self):
        self.assertIsNone(share.mirror_ident(share.SHARED))

    def test_unknown_platform_is_not_mirrored(self):
        self.assertIsNone(share.mirror_ident(Ident("stranger_bot", "x")))

    def test_add_turn_pair_writes_both(self):
        st = FakeStore()
        ident = Ident("douyin", "玛卡巴卡的小推车")
        share.add_turn_pair(None, st, ident, "group:测试", "user", "我在抖音说的话")
        self.assertEqual(len(st.turns), 2)
        self.assertEqual(st.turns[0]["ident"], ident)
        self.assertEqual(st.turns[1]["ident"].platform, "shared")
        self.assertEqual(st.turns[1]["content"], "我在抖音说的话")
        self.assertEqual(st.turns[1]["extra"]["shared_from"], "douyin")
        # 共享层的会话名带来源，回读时知道话是在哪儿说的
        self.assertEqual(st.turns[1]["session"], "douyin:group:测试")


class FakeRecaller:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def recall(self, query, ident, **kw):  # noqa: ARG002
        self.calls.append(ident.platform)
        hits = self.mapping.get(ident.platform, [])
        return RecallResult(hits=list(hits), took_ms=1.0, budget_ms=100)


class RecallUnionTest(unittest.TestCase):
    def test_union_merges_and_dedupes(self):
        rec = FakeRecaller({
            "qq": [Hit(kind="turn", id=1, text="在 QQ 说的话", score=1.0, meta={})],
            "shared": [
                Hit(kind="turn", id=2, text="在抖音说的话", score=0.8, meta={}),
                Hit(kind="turn", id=1, text="在 QQ 说的话", score=0.9, meta={}),
            ],
        })
        res = share.recall_union(rec, "我说了什么", Ident("qq", "2216608312"))
        texts = [h.text for h in res.hits]
        self.assertIn("在抖音说的话", texts)          # 别的平台的话能被想起来
        self.assertEqual(texts.count("在 QQ 说的话"), 1)  # 去重
        self.assertEqual(res.hits[0].text, "在 QQ 说的话")  # 分数取高后仍排第一
        self.assertIn("shared_union", res.degraded)

    def test_shared_error_does_not_break_recall(self):
        class Boom(FakeRecaller):
            def recall(self, query, ident, **kw):
                if ident.platform == "shared":
                    raise RuntimeError("boom")
                return super().recall(query, ident, **kw)

        rec = Boom({"qq": [Hit(kind="turn", id=1, text="本体", score=1.0, meta={})]})
        res = share.recall_union(rec, "q", Ident("qq", "u"))
        self.assertEqual([h.text for h in res.hits], ["本体"])
        self.assertTrue(any(d.startswith("shared_error") for d in res.degraded))


if __name__ == "__main__":
    unittest.main()
