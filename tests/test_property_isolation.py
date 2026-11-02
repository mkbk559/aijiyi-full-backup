# -*- coding: utf-8 -*-
"""属性测试：身份隔离（本仓第一大风险是"一次漏写 platform 条件 = 串号事故"）。

用 hypothesis 生成任意平台名/用户 id/scope（含中文、emoji、引号、% _ 等），断言：
  1. `Ident.where()` 的占位符数量永远等于参数个数（不许手工拼串）；
  2. 生成的 SQL 里永远不出现平台名/用户 id 的字面量（杜绝字符串插值）；
  3. 真落库后，用 A 的身份查不到 B 的任何事实（端到端串号检查）。
hypothesis 未安装时整套跳过（不影响其他人跑单测）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiji.hippocampus.db import DB
from aiji.hippocampus.store import FactDraft, Ident, add_fact

try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    HAS_HYPOTHESIS = True
except ImportError:  # pragma: no cover
    HAS_HYPOTHESIS = False

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".tmp" / "test_property"


def text_strategy():
    return st.text(min_size=1, max_size=24)


def fact_text_strategy():
    """事实正文/对象：必须是 `strip()` 后非空的可见字符。

    hypothesis 第一版给了 `'\\r'`——被 `add_fact` 的"三元组不可为空"挡下（那是**正确**的校验），
    说明这条属性测试真的能挖到东西；这里把生成域收窄到"合法但千奇百怪"的输入。
    """
    visible = st.characters(blacklist_categories=("Cc", "Cs", "Zl", "Zp"))
    return st.text(alphabet=visible, min_size=1, max_size=24).filter(lambda s: bool(s.strip()))



@unittest.skipUnless(HAS_HYPOTHESIS, "未安装 hypothesis：pip install hypothesis")
class TestIdentWhere(unittest.TestCase):
    @settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(platform=text_strategy(), user=text_strategy(), scope=st.one_of(st.none(), st.just("*"), text_strategy()))
    def test_where_is_parameterized(self, platform, user, scope):
        ident = Ident(platform, user, scope)
        sql, args = ident.where()
        self.assertEqual(sql.count("?"), len(args), f"占位符与参数不匹配: {sql} / {args}")
        expected = "platform=? AND platform_user_id=?"
        if scope not in (None, "*"):
            expected += " AND scope=?"
        self.assertEqual(sql, expected, "SQL 形状变了：说明有拼接或漏了条件")
        self.assertEqual(args[0], platform)
        self.assertEqual(args[1], user)

    @settings(max_examples=100, deadline=None)
    @given(platform=text_strategy(), user=text_strategy())
    def test_where_has_alias_prefix(self, platform, user):
        sql, args = Ident(platform, user, "s1").where("f")
        self.assertTrue(sql.startswith("f.platform=?"))
        self.assertEqual(len(args), 3)


@unittest.skipUnless(HAS_HYPOTHESIS, "未安装 hypothesis：pip install hypothesis")
class TestIsolationEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(parents=True, exist_ok=True)

    def _fresh_db(self, name: str) -> DB:
        p = WORK / f"{name}.db"
        for c in (p, Path(str(p) + "-wal"), Path(str(p) + "-shm")):
            if c.exists():
                c.unlink()
        return DB(p)

    @settings(max_examples=15, deadline=None,
              suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
    @given(pa=text_strategy(), ua=text_strategy(), pb=text_strategy(), ub=text_strategy(),
           obj=fact_text_strategy())
    def test_two_idents_never_see_each_other(self, pa, ua, pb, ub, obj):
        # 同一个 (platform, user) 组合时本来就该看到，跳过这种退化情形
        if (pa, ua) == (pb, ub):
            return
        db = self._fresh_db(f"iso_{abs(hash((pa, ua, pb, ub, obj))) % 10**8}")
        try:
            a, b = Ident(pa, ua), Ident(pb, ub)
            with db.tx() as c:
                add_fact(c, a, FactDraft(subject="我", predicate="住在", object=obj, text=f"A 住在 {obj}"))
                add_fact(c, b, FactDraft(subject="我", predicate="住在", object=obj, text=f"B 住在 {obj}"))

            sel_a, args_a = a.where()
            sel_b, args_b = b.where()
            rows_a = db.read(f"SELECT platform, platform_user_id, text FROM fact WHERE {sel_a}", args_a)
            rows_b = db.read(f"SELECT platform, platform_user_id, text FROM fact WHERE {sel_b}", args_b)

            self.assertTrue(rows_a, "A 自己的事实应该查得到")
            for row in rows_a:
                self.assertEqual((row[0], row[1]), (pa, ua), "A 查到了别人的行")
            for row in rows_b:
                self.assertEqual((row[0], row[1]), (pb, ub), "B 查到了别人的行")
            self.assertFalse({(r[0], r[1]) for r in rows_a} & {(r[0], r[1]) for r in rows_b})
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
