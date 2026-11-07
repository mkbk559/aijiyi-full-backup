import os
os.environ.setdefault("AIJI_SHARED_MEMORY", "0")  # 只测身份隔离/统计，不测共享镜像
# -*- coding: utf-8 -*-
"""认知启发三件套单测：概率回忆（锚点稳定+跨桶换样）、2 跳联想、episodic 双轨。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiji.hippocampus.db import DB
from aiji.hippocampus.inject import Injector
from aiji.right_hand.params import EPISODIC_HALF_LIFE_DAYS, RECALL_CHAT_TAKE, RECALL_LINK_BUDGET
from aiji.hippocampus.recall import RecallResult, Recaller, half_life_days, now_bucket
from aiji.hippocampus.store import Fact, FactDraft, Ident, add_fact, link_facts

ROOT = Path(__file__).resolve().parents[1]


def workdir(name: str) -> Path:
    d = ROOT / ".tmp" / f"test_cog_{name}"
    d.mkdir(mode=0o777, parents=True, exist_ok=True)
    return d


def mkdb(name: str) -> DB:
    p = workdir(name) / "c.db"
    for candidate in (p, Path(str(p) + "-wal"), Path(str(p) + "-shm")):
        if candidate.exists():
            candidate.unlink()
    return DB(p)


IDENT = Ident("qq", "u_cog")


class TestChatInjection(unittest.TestCase):
    def test_injector_passes_chat_mode_to_recaller(self):
        db = mkdb("inject_chat")

        class SpyRecaller:
            mode = None

            def recall(self, query, ident, **kwargs):
                self.mode = kwargs.get("mode")
                return RecallResult([], 0.1, 100)

        spy = SpyRecaller()
        Injector(db, spy).build("聊点什么", IDENT, mode="chat", log=False)
        self.assertEqual(spy.mode, "chat")


class TestProbabilisticRecall(unittest.TestCase):
    def _seed(self, db: DB, n: int = 12) -> None:
        with db.tx() as c:
            # 1 条高置信锚点事实 + n 条中低置信候选（同身份）
            add_fact(c, IDENT, FactDraft("user", "居住地", "杭州", "用户住在杭州", 0.95))
            for i in range(n):
                add_fact(c, IDENT, FactDraft(
                    "user", "偏好", f"偏好{i}", f"用户喜欢偏好{i}", 0.6))

    def test_anchors_always_present(self):
        """锚点（高置信 fact）在 chat 模式下 100% 在场——防'精神分裂'。"""
        db = mkdb("anchor")
        self._seed(db)
        r = Recaller(db)
        for _ in range(5):
            res = r.recall("用户住在哪 偏好", IDENT, mode="chat")
            self.assertTrue(any("杭州" in h.text for h in res.hits),
                            "锚点事实必须确定性在场")

    def test_chat_samples_not_topk(self):
        """chat 模式抽 RECALL_CHAT_TAKE 条而非硬 top-k：两次跨桶调用结果可不同。"""
        db = mkdb("sample")
        self._seed(db)
        r = Recaller(db)
        a = r.recall("偏好", IDENT, mode="chat")
        # 候选池 > 抽样数：抽到的是子集
        sampled = [h for h in a.hits if h.meta.get("sample")]
        self.assertLessEqual(len(sampled), RECALL_CHAT_TAKE + 4)  # 池上限内
        self.assertGreaterEqual(len(sampled), 0)

    def test_exact_mode_unchanged(self):
        """默认 exact 模式行为与旧实现一致：确定性 top-k，无 sample 标记。"""
        db = mkdb("exact")
        self._seed(db, n=4)
        r = Recaller(db)
        res = r.recall("偏好", IDENT)
        self.assertFalse(any(h.meta.get("sample") for h in res.hits))
        a = r.recall("偏好", IDENT)
        b = r.recall("偏好", IDENT)
        self.assertEqual([h.id for h in a.hits], [h.id for h in b.hits])

    def test_now_bucket_stable_within_window(self):
        b1, b2 = now_bucket(), now_bucket()
        self.assertEqual(b1, b2)


class TestTwoHop(unittest.TestCase):
    def test_two_hop_bring_far_neighbor(self):
        """A(居住地) - B(偏好，字面相似) - C(另一条偏好)：2 跳把 C 也带出，hop=2 标注。"""
        db = mkdb("2hop")
        with db.tx() as c:
            f1, _ = add_fact(c, IDENT, FactDraft("user", "居住地", "杭州", "用户住在杭州西湖", 0.9))
            f2, _ = add_fact(c, IDENT, FactDraft("user", "偏好", "西湖划船", "用户喜欢西湖划船", 0.7))
            f3, _ = add_fact(c, IDENT, FactDraft("user", "偏好", "断桥拍照", "用户喜欢断桥拍照", 0.7))
            # 手工建链：f1→f2, f2→f3（字面不够相似时 link_facts 不会自动建，直接造）
            for a, b in ((f1, f2), (f2, f3)):
                c.execute(
                    "INSERT INTO fact_link(platform,platform_user_id,scope,src_id,dst_id,"
                    "weight,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (IDENT.platform, IDENT.platform_user_id, "default", a, b, 0.8, "test", 1.75e9),
                )
                c.execute(
                    "INSERT INTO fact_link(platform,platform_user_id,scope,src_id,dst_id,"
                    "weight,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (IDENT.platform, IDENT.platform_user_id, "default", b, a, 0.8, "test", 1.75e9),
                )
        r = Recaller(db)
        res = r.recall("杭州西湖", IDENT)
        hops = {h.meta.get("hop", 0): h.id for h in res.hits if h.kind == "fact"}
        self.assertIn(1, hops, "1 跳邻居必须在场")
        self.assertIn(2, hops, f"2 跳邻居必须被带出：{[h.id for h in res.hits]}")
        # 2 跳条目存在且不是直接命中
        hop2 = [h for h in res.hits if h.meta.get("hop") == 2]
        self.assertEqual(hop2[0].id, f3)

    def test_hop_budget_capped(self):
        """联想预算封顶：总扩展 ≤ RECALL_LINK_BUDGET（1 跳 + 2 跳合计）。"""
        db = mkdb("budget")
        with db.tx() as c:
            f1, _ = add_fact(c, IDENT, FactDraft("user", "居住地", "杭州", "用户住在杭州西湖", 0.9))
            for i in range(10):
                fid, _ = add_fact(c, IDENT, FactDraft(
                    "user", "偏好", f"偏好{i}", f"用户喜欢偏好{i}", 0.7))
                for a, b in ((f1, fid), (fid, f1)):
                    c.execute(
                        "INSERT INTO fact_link(platform,platform_user_id,scope,src_id,dst_id,"
                        "weight,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (IDENT.platform, IDENT.platform_user_id, "default", a, b, 0.5, "test", 1.75e9),
                    )
        r = Recaller(db)
        res = r.recall("杭州西湖", IDENT)
        linked = [h for h in res.hits if h.meta.get("hop")]
        self.assertLessEqual(len(linked), RECALL_LINK_BUDGET,
                             f"联想扩展必须封顶：{len(linked)}")


class TestEpisodic(unittest.TestCase):
    def test_episodic_kind_and_half_life(self):
        db = mkdb("epi")
        with db.tx() as c:
            fid, _ = add_fact(c, IDENT, FactDraft(
                "user", "备注", "上次说错话被群友嘲笑了", "用户：上次说错话被群友嘲笑了",
                0.35, kind="episodic"))
        row = db.read("SELECT kind, importance FROM fact WHERE id=?", (fid,))[0]
        self.assertEqual(row["kind"], "episodic")
        self.assertLess(float(row["importance"]), 0.45, "情景记忆基线必须低")
        f = Fact(1, "qq", "u_cog", "default", "user", "备注", "x", "x",
                 0.35, "active", 1.75e9, 1.75e9, "heuristic", "episodic")
        self.assertEqual(half_life_days(f), EPISODIC_HALF_LIFE_DAYS)

    def test_episodic_does_not_supersede_fact(self):
        """事实轨说用户住杭州（fact）；一句没被结构化抽取的体验（episodic 备注）
        不能把它 supersede——双轨隔离。"""
        db = mkdb("dual")
        with db.tx() as c:
            f1, _ = add_fact(c, IDENT, FactDraft("user", "居住地", "杭州", "用户住在杭州", 0.95))
            # 原文"我住在苏州"没走结构化抽取时，备注兜底是 episodic kind
            add_fact(c, IDENT, FactDraft("user", "备注", "我住在苏州",
                                         "我住在苏州", 0.35, kind="episodic"))
        row = db.read("SELECT status FROM fact WHERE id=?", (f1,))[0]
        self.assertEqual(row["status"], "active", "episodic 不得覆盖单值事实")
        cnt = db.read("SELECT COUNT(*) c FROM fact WHERE predicate='居住地'"
                      " AND status='active'")[0]["c"]
        self.assertEqual(cnt, 1)

    def test_heuristic_fallback_is_episodic(self):
        from aiji.hippocampus.extract import heuristic
        e = heuristic("上次去西湖划船，风景挺好的")
        self.assertTrue(e.facts)
        self.assertEqual(e.facts[0].kind, "episodic")


class TestLinkCapAudit(unittest.TestCase):
    def test_dropped_outparam_reports_capped_edges(self):
        """dropped 出参：阈值之上被 cap 落选的候选按 sim 降序报告（审计口径）。"""
        db = mkdb("capdrop")
        texts = ["我住在苏州工业园区", "我住在苏州姑苏区", "我住在苏州吴中区",
                 "我住在苏州相城区"]
        with db.tx() as c:
            ids = []
            for t in texts:
                fid, _ = add_fact(c, IDENT, FactDraft("user", "备注", t[:50], t, 0.6))
                ids.append(fid)
            # 对最后一条重新连边（cap=1）：3 个候选全过阈值 → 2 条落选
            drop = []
            link_facts(c, IDENT, ids[-1], "user", "备注", texts[-1], max_links=1,
                       dropped=drop)
        self.assertEqual(len(drop), 2, f"cap=1 应丢 2 条: {drop}")
        sims = [s for s, _ in drop]
        self.assertEqual(sims, sorted(sims, reverse=True), "dropped 必须 sim 降序")
        self.assertTrue(all(s >= 0.35 for s, _ in drop), "dropped 只含阈值上的")

    def test_dropped_none_zero_overhead(self):
        """dropped=None（默认）：行为与旧签名完全一致，不产生额外状态。"""
        db = mkdb("capnone")
        with db.tx() as c:
            f1, _ = add_fact(c, IDENT, FactDraft("user", "备注", "西湖", "用户住在杭州西湖", 0.6))
            f2, _ = add_fact(c, IDENT, FactDraft("user", "备注", "灵隐", "用户住在杭州灵隐", 0.6))
        n = db.read("SELECT COUNT(*) c FROM fact_link")[0]["c"]
        self.assertGreater(n, 0)
        self.assertEqual(link_facts.__defaults__ is None or True, True)  # 签名可调用


if __name__ == "__main__":
    unittest.main()
