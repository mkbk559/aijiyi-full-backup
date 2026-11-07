"""抖音点赞的离线测试（单独一个文件：主测试文件当时正被另一个会话改，避免互相覆盖）。

钉住的语义：
1. 点完数字变了**但重载回滚** → `like_not_persisted`（2026-11-05 真机就是这么骗人的）；
2. 重载后仍是新数字 → 成功，delta 用落地值算；
3. 找不到点赞按钮 → `selector_missing`；
4. dry_run 不碰浏览器。
"""

from __future__ import annotations

import unittest

from aiji.heart.platforms.base import PlatformError
from aiji.heart.platforms.douyin import DouyinAdapter

VID = "7700882025498021158"


class FakePage:
    def __init__(self, counts):
        self.counts = list(counts)
        self.url_ = ""
        self.clicks = 0

    def goto(self, url, **_):
        self.url_ = url

    def title(self):
        return "抖音"

    def wait_for_timeout(self, ms):
        return None

    def evaluate(self, js):
        js = str(js)
        if "video-player-digg" in js:
            if ".click()" in js:
                self.clicks += 1
                return True
            return self.counts.pop(0) if self.counts else ""
        return []

    def query_selector(self, sel):
        return None


class FakeSession:
    def __init__(self, page: FakePage):
        self.page = page

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def goto(self, url, **_):
        self.page.goto(url)

    def wait(self, ms):
        self.page.wait_for_timeout(ms)

    def url(self):
        return self.page.url_

    def title(self):
        return self.page.title()

    def screenshot(self, path=None):
        return str(path or "")

    def cookie_names(self):
        return set()


def adapter(counts=None, *, dry_run=False):
    page = FakePage(counts or [])
    made = DouyinAdapter(lambda *a, **k: FakeSession(page), dry_run=dry_run)
    return made, page


class DouyinLikeTest(unittest.TestCase):
    def test_state_rolls_back_after_reload_is_not_persisted(self):
        a, page = adapter(["787", "788", "787"])
        with self.assertRaises(PlatformError) as ctx:
            a.like(VID)
        self.assertEqual(ctx.exception.code, "like_not_persisted")
        self.assertEqual(page.clicks, 1)          # 确实点过
        self.assertEqual(page.url_, f"https://www.douyin.com/video/{VID}")

    def test_persisted_like_succeeds(self):
        a, _ = adapter(["787", "788", "788"])
        res = a.like(VID)
        self.assertTrue(res.ok)
        self.assertEqual(res.detail["before"], 787)
        self.assertEqual(res.detail["after"], 788)
        self.assertEqual(res.detail["persisted"], 788)
        self.assertEqual(res.detail["delta"], 1)
        self.assertFalse(res.detail["unliked"])

    def test_persisted_unlike_reports_negative_delta(self):
        a, _ = adapter(["788", "787", "787"])
        res = a.like(VID)
        self.assertEqual(res.detail["delta"], -1)
        self.assertTrue(res.detail["unliked"])

    def test_missing_button(self):
        a, _ = adapter([])
        with self.assertRaises(PlatformError) as ctx:
            a.like(VID)
        self.assertEqual(ctx.exception.code, "selector_missing")

    def test_dry_run_does_not_open_browser(self):
        factory_calls = []

        def factory(*a, **k):
            factory_calls.append(1)
            return FakeSession(FakePage([]))

        a = DouyinAdapter(factory, dry_run=True)
        res = a.like(VID)
        self.assertTrue(res.dry_run)
        self.assertEqual(res.code, "dry_run")
        self.assertEqual(factory_calls, [])

    def test_full_url_is_accepted(self):
        url = f"https://www.douyin.com/video/{VID}"
        a, page = adapter(["10", "11", "11"])
        res = a.like(url)
        self.assertEqual(res.detail["url"], url)
        self.assertEqual(page.url_, url)


if __name__ == "__main__":
    unittest.main()
