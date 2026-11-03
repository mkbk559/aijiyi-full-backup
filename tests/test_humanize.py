"""拟人节奏（`brain/humanize.py`）——79% → 全覆盖。

只对 IM 平台生效（控制台/HTTP API 不能被拖慢），且延迟有上下界：
这些是"给人聊天手感"的规则，错的方向是**把等待加到不该等的地方**，
所以重点测"什么时候返回 0"和"档位边界"。rng 注入 + 不真 sleep。
"""
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aiji.brain.humanize import IM_PLATFORMS, HumanPacer  # noqa: E402


class MidRng:
    """每次取区间中点：跨调用比较才不会受随机抽样干扰。"""

    def randint(self, low, high):
        return (low + high) // 2


def pacer(**kwargs) -> HumanPacer:
    return HumanPacer(rng=MidRng(), **kwargs)


class PlanMsTest(unittest.TestCase):
    def test_non_im_platform_never_delays(self):
        p = pacer()
        for platform in ("", "console", "web", "api", "openai"):
            self.assertEqual(p.plan_ms("你好", platform), 0.0, platform)

    def test_every_im_platform_delays(self):
        p = pacer()
        for platform in IM_PLATFORMS:
            self.assertGreater(p.plan_ms("你好", platform), 0.0, platform)

    def test_platform_is_case_insensitive(self):
        p = pacer()
        self.assertGreater(p.plan_ms("你好", "QQ"), 0.0)

    def test_urgent_is_much_faster_than_normal(self):
        p = pacer()
        normal = p.plan_ms("你好呀", "qq", idle_s=10, urgent=False)
        urgent = p.plan_ms("你好呀", "qq", idle_s=10, urgent=True)
        self.assertLess(urgent, normal)

    def test_idle_tiers_increase_with_idle_time(self):
        p = pacer()
        hot = p.plan_ms("同一句话", "qq", idle_s=10)
        warm = p.plan_ms("同一句话", "qq", idle_s=600)
        cold = p.plan_ms("同一句话", "qq", idle_s=7200)
        self.assertLess(hot, warm)
        self.assertLess(warm, cold)

    def test_low_energy_slows_down_high_energy_speeds_up(self):
        p = pacer()  # 中点抽样，只差在能量乘子上
        low = p.plan_ms("你好", "qq", energy=10)
        mid = p.plan_ms("你好", "qq", energy=60)
        high = p.plan_ms("你好", "qq", energy=95)
        self.assertGreater(low, mid)
        self.assertLess(high, mid)

    def test_longer_text_waits_longer_until_cap(self):
        p = pacer(typing_min_ms=100, typing_max_ms=400)
        short = p.plan_ms("嗨", "qq", idle_s=10)
        long = p.plan_ms("很长" * 100, "qq", idle_s=10)
        self.assertGreater(long, short)
        self.assertLessEqual(long, 3000 + 400, "打字停顿要封顶")

    def test_bounds_are_sane(self):
        p = pacer(min_ms=2000, max_ms=500)
        self.assertEqual(p.max_ms, 2000, "max 不能小于 min")
        self.assertEqual(p.plan_ms("x", "qq", idle_s=7200), p.plan_ms("x", "qq", idle_s=7200))


class WaitTest(unittest.TestCase):
    def test_wait_sleeps_and_counts(self):
        p = pacer()
        # 注意作用域：`patch("...humanize.time.sleep")` 会改到**共享的 time 模块**，
        # 别的测试线程（如 mcp_research 的 time.sleep(0.2) 轮询）全被记进 mock，
        # v35 全量跑时就是被这个坑的。改成只换 humanize 自己的 time 绑定。
        with mock.patch("aiji.brain.humanize.time") as time_mod:
            delay = p.wait("你好", "qq", idle_s=10)
        self.assertGreater(delay, 0)
        time_mod.sleep.assert_called_once()
        self.assertEqual(p.stats["delayed"], 1)
        self.assertAlmostEqual(p.stats["waited_ms"], delay)

    def test_non_im_platform_does_not_sleep(self):
        p = pacer()
        with mock.patch("aiji.brain.humanize.time") as time_mod:
            self.assertEqual(p.wait("你好", "console"), 0.0)
        time_mod.sleep.assert_not_called()
        self.assertEqual(p.stats["delayed"], 0)


if __name__ == "__main__":
    unittest.main()
