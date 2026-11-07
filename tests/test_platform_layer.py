"""三平台社交层离线测试：闸门/账本/编排/三个适配器。

全离线、零外网：
- QQ 用假 `call`（记录调用了哪个 OneBot action、payload 长什么样）；
- B 站用 `mock.patch` 顶掉 `bili_client` 的函数；
- 抖音用假 page（`evaluate` 返回卡片、`query_selector` 按选择器回落），
  这样 DOM 选择器的**顺序回退逻辑**也在测试里。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# 必须在 import registry 之前设：平台层默认会把动作写进 data/platform_actions.jsonl，
# 测试跑一遍就往生产账本里塞几行假动作（2026-11-05 真发生过）。见 excluded#209。
os.environ["AIJI_ACTION_LOG"] = str(
    pathlib.Path(__file__).resolve().parent.parent / ".tmp" / "platform_actions_test.jsonl")

from tests._tempdir import make_tmp  # noqa: E402
from aiji.heart.platforms import (  # noqa: E402
    Guard,
    PlatformError,
    StreakBook,
    feed_text,
    learn_feed,
    matrix,
    streak_sweep,
    summary_lines,
)
from aiji.heart.platforms.bilibili import BiliAdapter  # noqa: E402
from aiji.heart.platforms.douyin import DouyinAdapter, learn_blob  # noqa: E402
from aiji.heart.platforms.engine import MAX_TEXT  # noqa: E402
from aiji.heart.platforms.qq import QQAdapter  # noqa: E402
from aiji.heart.platforms.registry import build  # noqa: E402
from aiji.heart.platforms.streak import day_of, observe_turns  # noqa: E402
from aiji.hippocampus.db import DB  # noqa: E402


def _db(case: unittest.TestCase, name: str = "streak") -> DB:
    root = pathlib.Path(make_tmp(case, f"aiji-{name}-"))
    return DB(root / "a.db")


class GuardTest(unittest.TestCase):
    def test_rate_limit_raises(self):
        g = Guard(min_interval=60.0, clock=lambda: 1000.0)
        g.check("qq", "dm", "1")          # 没记录，放行
        g.log({"platform": "qq", "action": "dm", "key": "1", "ok": True})
        with self.assertRaises(PlatformError) as ctx:
            g.check("qq", "dm", "1")
        self.assertEqual(ctx.exception.code, "rate_limited")
        self.assertEqual(ctx.exception.status, 429)

    def test_different_key_is_independent(self):
        g = Guard(min_interval=60.0, clock=lambda: 1000.0)
        g.log({"platform": "qq", "action": "dm", "key": "1", "ok": True})
        g.check("qq", "dm", "2")          # 换个人，不该被拦
        self._ok(g)

    def test_empty_key_falls_back_to_action_level(self):
        g = Guard(min_interval=60.0, clock=lambda: 1000.0)
        g.log({"platform": "bilibili", "action": "comment", "key": "av1", "ok": True})
        with self.assertRaises(PlatformError):
            g.check("bilibili", "comment", "")

    def test_daily_cap(self):
        g = Guard(min_interval=0.0, daily_cap=2, clock=lambda: 1000.0)
        for _ in range(2):
            g.check("douyin", "comment", "x")
            g.log({"platform": "douyin", "action": "comment", "key": "x", "ok": True})
        with self.assertRaises(PlatformError) as ctx:
            g.check("douyin", "comment", "x")
        self.assertEqual(ctx.exception.code, "daily_cap")

    def test_counter_survives_new_process(self):
        """CLI 是一次性进程：计数必须从留痕文件里读回来。"""
        root = pathlib.Path(make_tmp(self, "aiji-guard-"))
        log = root / "actions.jsonl"
        g1 = Guard(log_path=log, min_interval=60.0, clock=lambda: 1000.0)
        g1.log({"platform": "qq", "action": "dm", "key": "9", "ok": True})
        self.assertTrue(log.exists())
        g2 = Guard(log_path=log, min_interval=60.0, clock=lambda: 1000.0)
        with self.assertRaises(PlatformError):
            g2.check("qq", "dm", "9")

    def test_bad_lines_do_not_break_load(self):
        root = pathlib.Path(make_tmp(self, "aiji-guard2-"))
        log = root / "a.jsonl"
        log.write_text("not json\n{\"platform\":\"qq\"}\n", encoding="utf-8")
        g = Guard(log_path=log, min_interval=1.0, clock=lambda: 1000.0)
        g.check("qq", "dm", "1")

    def _ok(self, g: Guard) -> None:
        g.check("qq", "dm", "2")


class StreakTest(unittest.TestCase):
    def setUp(self):
        self.db = _db(self)
        self.clock = [1_700_000_000.0]        # 固定时钟
        self.book = StreakBook(self.db, clock=lambda: self.clock[0])

    def tearDown(self):
        self.db.close()

    def _day(self, offset: int = 0) -> str:
        return day_of(self.clock[0] + offset * 86400)

    def test_first_touch_counts_one(self):
        out = self.book.touch("qq", "42", day=self._day())
        self.assertEqual(out["days"], 1)
        self.assertTrue(out["extended"])

    def test_same_day_is_idempotent(self):
        self.book.touch("qq", "42", day=self._day())
        again = self.book.touch("qq", "42", day=self._day())
        self.assertEqual(again["days"], 1)
        self.assertFalse(again["extended"])

    def test_consecutive_days_increment(self):
        self.book.touch("qq", "42", day=self._day(0))
        out = self.book.touch("qq", "42", day=self._day(1))
        self.assertEqual(out["days"], 2)
        self.assertTrue(out["extended"])

    def test_gap_resets_but_keeps_best(self):
        for i in range(3):
            self.book.touch("qq", "42", day=self._day(i))
        out = self.book.touch("qq", "42", day=self._day(5))   # 断了 2 天
        self.assertEqual(out["days"], 1)
        self.assertEqual(out["best"], 3)
        self.assertFalse(out["extended"])

    def test_status_marks_broken_streak_dead(self):
        self.book.touch("qq", "42", day=self._day(-5))
        row = self.book.status("qq", day=self._day())[0]
        self.assertFalse(row["alive"])
        self.assertEqual(row["effective_days"], 0)

    def test_due_lists_only_alive_untouched(self):
        self.book.touch("qq", "a", day=self._day(-1))   # 昨天，今天该续
        self.book.touch("qq", "b", day=self._day())     # 今天已经聊过
        self.book.touch("qq", "c", day=self._day(-9))   # 早断了
        due = self.book.due("qq", day=self._day())
        self.assertEqual([r["peer"] for r in due], ["a"])

    def test_summary_counts(self):
        self.book.touch("qq", "a", day=self._day(-1))
        s = self.book.summary(day=self._day())
        self.assertEqual(s["tracked"], 1)
        self.assertEqual(s["due"], 1)
        self.assertEqual(s["best"], 1)

    def test_empty_peer_rejected(self):
        with self.assertRaises(PlatformError):
            self.book.touch("qq", "  ")

    def test_observe_turns_backfills_from_db(self):
        t = self.clock[0]
        with self.db.tx() as c:
            for peer, ts in (("42", t - 3600), ("43", t - 86400 * 2)):
                c.execute(
                    "INSERT INTO turn(platform, platform_user_id, scope, session, role,"
                    " content, extra, ts) VALUES('qq', ?, 'default', 's', 'user', 'hi',"
                    " '{}', ?)", (peer, ts))
        out = observe_turns(self.book, self.db, platform="qq", days=3, now=t)
        self.assertEqual(out["peers"], 2)
        self.assertGreaterEqual(out["days_recorded"], 2)


class QQAdapterTest(unittest.TestCase):
    def _adapter(self, dry_run: bool = True, guard: Guard | None = None):
        calls: list[tuple[str, dict]] = []

        def call(action, payload=None):
            calls.append((action, dict(payload or {})))
            if action == "get_login_info":
                return {"user_id": 2684827447, "nickname": "小肥鱼"}
            if action == "get_friend_list":
                return [{"user_id": 2216608312, "nickname": "玛卡巴卡", "remark": "主人"}]
            if action == "get_group_list":
                return [{"group_id": 849663082, "group_name": "造梦西游3交流群",
                         "member_count": 100}]
            if action in ("send_private_msg", "send_group_msg"):
                return {"message_id": 12345}
            if action == "send_like":
                return {}
            if action == "get_friend_msg_history":
                return {"messages": [{"time": 1, "sender": {"user_id": 1},
                                      "message": [{"type": "text",
                                                   "data": {"text": "在吗"}}]}]}
            raise RuntimeError(f"Connection refused ({action})")

        return QQAdapter(call, dry_run=dry_run, guard=guard), calls

    def test_probe_online(self):
        a, _ = self._adapter()
        self.assertTrue(a.probe()["online"])
        self.assertEqual(a.probe()["self_id"], "2684827447")

    def test_dry_run_does_not_call_send(self):
        a, calls = self._adapter(dry_run=True)
        res = a.dm("2216608312", "在吗")
        self.assertTrue(res.dry_run)
        self.assertEqual(res.code, "dry_run")
        self.assertEqual([c[0] for c in calls], [])

    def test_real_dm_payload_shape(self):
        a, calls = self._adapter(dry_run=False)
        res = a.dm("2216608312", "在吗")
        self.assertFalse(res.dry_run)
        self.assertEqual(res.detail["message_id"], "12345")
        action, payload = calls[-1]
        self.assertEqual(action, "send_private_msg")
        self.assertEqual(payload["user_id"], 2216608312)
        self.assertEqual(payload["message"][0]["data"]["text"], "在吗")

    def test_group_payload_shape(self):
        a, calls = self._adapter(dry_run=False)
        a.group(849663082, "晚上好")
        action, payload = calls[-1]
        self.assertEqual(action, "send_group_msg")
        self.assertEqual(payload["group_id"], 849663082)

    def test_empty_text_rejected_before_any_call(self):
        a, calls = self._adapter(dry_run=False)
        with self.assertRaises(PlatformError) as ctx:
            a.dm("1", "   ")
        self.assertEqual(ctx.exception.code, "empty_text")
        self.assertEqual(calls, [])

    def test_transport_error_is_classified(self):
        def call(action, payload=None):
            raise RuntimeError("Connection refused")

        a = QQAdapter(call, dry_run=False)
        with self.assertRaises(PlatformError) as ctx:
            a.dm("1", "hi")
        self.assertEqual(ctx.exception.code, "transport_down")
        self.assertFalse(a.probe()["online"])

    def test_probe_reports_offline_without_raising(self):
        def call(action, payload=None):
            raise RuntimeError("[WinError 10061] 目标计算机积极拒绝")

        state = QQAdapter(call).probe()
        self.assertFalse(state["online"])
        self.assertEqual(state["code"], "transport_down")

    def test_friends_and_groups(self):
        a, _ = self._adapter()
        self.assertEqual(a.friends()[0]["peer"], "2216608312")
        self.assertEqual(a.groups()[0]["name"], "造梦西游3交流群")

    def test_history_flattens_message_array(self):
        a, _ = self._adapter()
        rows = a.history("friend", "1", count=5)
        self.assertEqual(rows[0]["text"], "在吗")

    def test_like_real(self):
        a, calls = self._adapter(dry_run=False)
        a.like("2216608312", times=3)
        self.assertEqual(calls[-1], ("send_like",
                                     {"user_id": 2216608312, "times": 3}))

    def test_create_group_unsupported_when_action_missing(self):
        def call(action, payload=None):
            raise RuntimeError("unsupported action: create_group")

        a = QQAdapter(call, dry_run=False)
        with self.assertRaises(PlatformError) as ctx:
            a.group_create("小群", ["1", "2"])
        self.assertEqual(ctx.exception.code, "unsupported")

    def test_guard_blocks_second_send(self):
        g = Guard(min_interval=999.0)
        a, calls = self._adapter(dry_run=False, guard=g)
        a.dm("1", "hi")
        with self.assertRaises(PlatformError) as ctx:
            a.dm("1", "hi again")
        self.assertEqual(ctx.exception.code, "rate_limited")
        self.assertEqual([c[0] for c in calls].count("send_private_msg"), 1)


class BiliAdapterTest(unittest.TestCase):
    def _cred(self):
        from aiji.senses.bili_client import BiliCredentials
        return BiliCredentials(sessdata="s", bili_jct="j", dedeuserid="42")

    def test_feed_wraps_ranking(self):
        a = BiliAdapter(self._cred())
        with mock.patch("aiji.senses.bili_client.ranking",
                        return_value=[{"bvid": "BV1", "title": "标题"}]):
            rows = a.feed(3)
        self.assertEqual(rows[0]["title"], "标题")

    def test_feed_error_becomes_platform_error(self):
        from aiji.senses.bili_client import BiliError
        a = BiliAdapter(self._cred())
        with mock.patch("aiji.senses.bili_client.ranking",
                        side_effect=BiliError("ranking_failed", "boom")):
            with self.assertRaises(PlatformError) as ctx:
                a.feed(3)
        self.assertEqual(ctx.exception.code, "ranking_failed")

    def test_comment_dry_run_does_not_need_login(self):
        a = BiliAdapter(None, cred_loader=lambda: None, dry_run=True)
        with mock.patch("aiji.senses.bili_client.resolve_aid", return_value=80433022):
            res = a.comment("https://www.bilibili.com/video/BV1GJ411x7h7", "好看")
        self.assertTrue(res.dry_run)
        self.assertEqual(res.detail["aid"], 80433022)
        self.assertEqual(a.login_state()["is_login"], False)

    def test_comment_real_without_login_says_needs_login(self):
        a = BiliAdapter(None, cred_loader=lambda: None, dry_run=False)
        with mock.patch("aiji.senses.bili_client.resolve_aid", return_value=1):
            with self.assertRaises(PlatformError) as ctx:
                a.comment("https://www.bilibili.com/video/BV1", "x")
        self.assertEqual(ctx.exception.code, "needs_login")

    def test_dm_dry_run(self):
        a = BiliAdapter(self._cred(), dry_run=True)
        res = a.dm("123", "在吗")
        self.assertTrue(res.dry_run)
        self.assertEqual(res.detail["peer"], "123")

    def test_dm_real_passes_confirm_and_logs(self):
        root = pathlib.Path(make_tmp(self, "aiji-bili-dm-"))
        a = BiliAdapter(self._cred(), dry_run=False, state_dir=root,
                        guard=Guard(min_interval=0.0, log_path=root / "act.jsonl"))
        with mock.patch("aiji.senses.bili_client.send_dm") as send:
            send.return_value = mock.Mock(ok=True, rpid=None)
            res = a.dm("123", "在吗")
        self.assertFalse(res.dry_run)
        self.assertEqual(send.call_args.kwargs["confirm"], True)
        log = (root / "act.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(json.loads(log[-1])["action"], "dm")

    def test_capabilities_are_honest(self):
        caps = BiliAdapter(self._cred()).capabilities()
        self.assertEqual(caps["dm"], "experimental")   # 写路径未真验证
        self.assertEqual(caps["comment"], "ok")        # 2026-11-04 真发过
        self.assertEqual(caps["group"], "unsupported")  # B 站没有群聊


class FakeElement:
    def __init__(self, name: str, sink: dict, *, label: str = ""):
        self.name = name
        self.sink = sink
        self.label = label

    def click(self, **_kw):
        # 真实实现里常带 force=/timeout=，这里一口吞掉
        self.sink.setdefault("clicked", []).append(self.name)

    def evaluate(self, js, *args):
        # 点开会话后校验"活动项"：默认成功，sink["wrong_target"]=True 可模拟点歪
        if "className" in str(js):
            if self.sink.get("wrong_target"):
                return "conversationConversationItemwrapper"
            return ("conversationConversationItemwrapper "
                    "conversationConversationItemcurConversation")
        return "" 

    def fill(self, text):
        self.sink["filled"] = text

    def inner_text(self):
        return self.label or self.name


class FakePage:
    def __init__(self, *, cards=None, verify=False, selectors=(), sink=None,
                 conversations=()):
        self.cards = cards or []
        self.verify = verify
        self.selectors = set(selectors)
        self.sink = sink if sink is not None else {}
        self.conversations = list(conversations)
        self.url_ = ""
        self.scrolls = 0

    def query_selector_all(self, _sel):
        return [FakeElement(f"conv:{c}", self.sink, label=c)
                for c in self.conversations]

    def goto(self, url, **_):
        self.url_ = url

    def title(self):
        return "验证中...中间页" if self.verify else "抖音"

    def wait_for_timeout(self, ms):
        self.sink["waited"] = self.sink.get("waited", 0) + ms

    def screenshot(self, path, **_):
        pathlib.Path(path).write_bytes(b"png")

    def evaluate(self, js, *args):
        js = str(js)
        if "rc-verifycenter" in js:
            # 验证页探测：默认"没被拦"
            return {"verify": bool(self.sink.get("verify", False)),
                    "title": "video", "has_video": True, "iframe_visible": False}
        if "comment/publish" in js:
            # 页面上下文 fetch 发评论：默认给一条带 cid 的成功响应
            body = self.sink.get("publish_body")
            if body is None:
                body = '{"comment":{"cid":"7704571082514711306","status":7,"text":"x"}}'
            self.sink["posted"] = True
            return {"status": 200, "len": len(body), "text": body}
        if "window.scrollBy" in js:
            self.scrolls += 1
            return None
        if "public-DraftEditor-content" in str(js):
            # 读回 draft.js 里已经打进去的字（用来判断键盘事件有没有生效）
            return self.sink.get("typed", "")
        if "includes(" in str(js):
            # 回读校验（评论 / 私信 / 群聊）：默认"发出去了"，
            # 用例里把 sink["publish_ok"] 设成 False 可模拟 2026-11-05 真机那种假成功。
            return bool(self.sink.get("publish_ok", True))
        if "(sel)" in str(js):
            # 读输入框里已经打进去的字（`(sel) => document.querySelector(sel).innerText`）
            return self.sink.get("typed", "")
        return list(self.cards)

    def query_selector(self, sel):
        return FakeElement(sel, self.sink) if sel in self.selectors else None

    @property
    def keyboard(self):
        page = self

        class _Keyboard:
            def type(self, text, delay=0):
                page.sink["filled"] = text
                page.sink["typed"] = text

            def press(self, key):
                page.sink["pressed"] = key

        return _Keyboard()


class FakeSession:
    def __init__(self, page):
        self.page = page

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def goto(self, url, **_):
        self.page.goto(url)

    def wait(self, ms):
        self.page.wait_for_timeout(ms)

    def title(self):
        return self.page.title()

    def url(self):
        return self.page.url_

    def cookie_names(self):
        return set(self.page.sink.get("cookies", ()))

    def screenshot(self, path):
        return str(path)


class DouyinAdapterTest(unittest.TestCase):
    def _adapter(self, page: FakePage, *, dry_run: bool = True, guard=None):
        factory = mock.Mock(side_effect=lambda *a, **k: FakeSession(page))
        return DouyinAdapter(factory, dry_run=dry_run, guard=guard), factory

    def test_feed_collects_and_dedupes(self):
        cards = [
            {"video_id": "111", "url": "https://www.douyin.com/video/111", "text": "第一条"},
            {"video_id": "111", "url": "https://www.douyin.com/video/111", "text": "重复"},
            {"video_id": "222", "url": "https://www.douyin.com/video/222", "text": "第二条"},
        ]
        page = FakePage(cards=cards)
        a, _ = self._adapter(page)
        rows = a.feed(2)
        self.assertEqual([r["video_id"] for r in rows], ["111", "222"])

    def test_feed_raises_on_verify_page(self):
        a, _ = self._adapter(FakePage(verify=True))
        with self.assertRaises(PlatformError) as ctx:
            a.feed(3)
        self.assertEqual(ctx.exception.code, "verify_page")
        self.assertEqual(ctx.exception.status, 403)

    def test_feed_non_verify_title(self):
        a, _ = self._adapter(FakePage(cards=[{"video_id": "9",
                                              "url": "u", "text": "t"}]))
        self.assertEqual(len(a.feed(1)), 1)

    def test_comment_dry_run_opens_no_browser(self):
        page = FakePage()
        a, factory = self._adapter(page)
        res = a.comment("https://www.douyin.com/video/111", "好看")
        self.assertTrue(res.dry_run)
        factory.assert_not_called()

    def test_comment_uses_page_fetch_and_returns_cid(self):
        """发评论走"页面上下文 fetch"：服务端给了 cid 就算发出去了。"""
        page = FakePage()
        adapter, _ = self._adapter(page, dry_run=False)
        res = adapter.comment("https://www.douyin.com/video/7704003749563370752",
                              "看着挺专业的")
        self.assertTrue(res.ok)
        self.assertEqual(res.detail["cid"], "7704571082514711306")
        self.assertTrue(page.sink.get("posted"))

    def test_comment_empty_body_is_unverified(self):
        """2026-11-05 真机：账号没过验证时抖音回 200 + 0 字节，必须如实报失败。"""
        page = FakePage()
        page.sink["publish_body"] = ""
        adapter, _ = self._adapter(page, dry_run=False)
        with self.assertRaises(PlatformError) as ctx:
            adapter.comment("https://www.douyin.com/video/7704003749563370752", "随便说点")
        self.assertEqual(ctx.exception.code, "unverified")
        self.assertIn("0 字节", ctx.exception.message)

    def test_comment_requires_human_verify_when_blocked(self):
        """验证码页（可见 + 没有 video）时直接报 verify_required，别去硬发。"""
        page = FakePage()
        page.sink["verify"] = True
        adapter, _ = self._adapter(page, dry_run=False)
        with self.assertRaises(PlatformError) as ctx:
            adapter.comment("https://www.douyin.com/video/7704003749563370752", "随便说点")
        self.assertEqual(ctx.exception.code, "verify_required")

    def test_comment_rejects_bad_url(self):
        adapter, _ = self._adapter(FakePage(), dry_run=False)
        with self.assertRaises(PlatformError) as ctx:
            adapter.comment("https://www.douyin.com/user/abc", "随便说点")
        self.assertEqual(ctx.exception.code, "bad_url")

    def test_dm_dry_run(self):
        a, factory = self._adapter(FakePage())
        res = a.dm("小明", "在吗")
        self.assertTrue(res.dry_run)
        factory.assert_not_called()

    def test_dm_peer_not_found(self):
        # 会话列表里有别人、没有"查无此人" → peer_not_found，并把现有会话名列出来
        page = FakePage(conversations=["玖玖大王万岁 (66) | 刚刚 | 还有派解说"])
        a, _ = self._adapter(page, dry_run=False)
        with self.assertRaises(PlatformError) as ctx:
            a.dm("查无此人", "在吗")
        self.assertEqual(ctx.exception.code, "peer_not_found")
        self.assertIn("玖玖大王万岁", ctx.exception.hint)

    def test_group_send_verifies_by_readback(self):
        from aiji.heart.platforms.douyin import CHAT_EDITOR

        page = FakePage(conversations=["玖玖大王万岁 (66)"],
                        selectors={CHAT_EDITOR})
        page.sink["publish_ok"] = True
        a, _ = self._adapter(page, dry_run=False)
        res = a.group("玖玖大王万岁", "大家好呀")
        self.assertTrue(res.detail["sent"])
        self.assertTrue(res.detail["verified"])

    def test_login_state_without_cookies(self):
        page = FakePage()
        page.sink["cookies"] = []
        a, _ = self._adapter(page)
        self.assertFalse(a.login_state()["is_login"])

    def test_login_state_with_sessionid(self):
        page = FakePage()
        page.sink["cookies"] = ["sessionid", "ttwid"]
        a, _ = self._adapter(page)
        self.assertTrue(a.login_state()["is_login"])

    def test_capabilities_are_honest(self):
        caps = DouyinAdapter().capabilities()
        # 2026-11-05 真机：/chat 会话列表 + .messageEditori + 回车，群里发完回读到了
        self.assertEqual(caps["group"], "ok")
        self.assertEqual(caps["dm"], "ok")
        # 2026-11-05 真机：/hot 热点榜无需登录就有卡片，feed 因此从 experimental 升到 ok
        self.assertEqual(caps["feed"], "ok")
        # 2026-11-05 14:3x 真机：页面上下文 fetch 发评论拿到 cid，
        # 独立回读三条都在评论区（excluded#225）→ 从 experimental 升到 ok
        self.assertEqual(caps["comment"], "ok")

    def test_learn_blob_compacts(self):
        text = learn_blob([{"text": "  第一 条  "}, {"url": "u2"}], topic="猫")
        self.assertIn("猫", text)
        self.assertIn("1. 第一 条", text)
        self.assertIn("u2", text)


class EngineTest(unittest.TestCase):
    class FakeAdapter:
        name = "bilibili"

        def __init__(self, rows=None, fail=None):
            self.rows = rows or []
            self.fail = fail
            self.sent = []

        def feed(self, limit=8, **_):
            return self.rows[:limit]

        def dm(self, peer, text, dry_run=None):
            if self.fail:
                raise PlatformError(self.fail, "boom")
            self.sent.append((peer, text, dry_run))
            return mock.Mock(dry_run=dry_run)

    class FakeMemory:
        def __init__(self):
            self.ingests = []
            self.flushes = 0

        def ingest(self, ident, **kw):
            self.ingests.append((ident, kw))

        def flush(self, timeout=0):
            self.flushes += 1
            return True

    def test_feed_text_shape(self):
        text = feed_text("bilibili", [{"title": "T", "desc": "D", "owner": "UP"}])
        self.assertIn("在bilibili刷到这些内容", text)
        self.assertIn("T；D（UP）", text)

    def test_learn_feed_writes_one_ingest(self):
        a = self.FakeAdapter(rows=[{"title": "标题", "url": "u"}])
        mem = self.FakeMemory()
        out = learn_feed(a, mem, "ident", limit=1)
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 1)
        self.assertEqual(mem.flushes, 1)
        ident, kw = mem.ingests[0]
        self.assertEqual(kw["kind"], "link")
        self.assertEqual(kw["extra"]["source"], "bilibili_feed")

    def test_learn_feed_truncates(self):
        long = [{"title": "x" * 300} for _ in range(40)]
        a = self.FakeAdapter(rows=long)
        mem = self.FakeMemory()
        learn_feed(a, mem, "i", limit=40)
        self.assertLessEqual(len(mem.ingests[0][1]["text"]), MAX_TEXT)

    def test_learn_feed_empty(self):
        out = learn_feed(self.FakeAdapter(), self.FakeMemory(), "i")
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "empty_feed")

    def test_streak_sweep_sends_only_due(self):
        db = _db(self, "sweep")
        try:
            clock = [1_700_000_000.0]
            book = StreakBook(db, clock=lambda: clock[0])
            book.touch("qq", "a", day=day_of(clock[0] - 86400))
            book.touch("qq", "b", day=day_of(clock[0]))
            a = self.FakeAdapter()
            out = streak_sweep(a, book, platform="qq", peers=["a", "b"],
                               message_for=lambda p, r: f"hi {p}", dry_run=False)
            self.assertEqual([s["peer"] for s in out["sent"]], ["a"])
            self.assertEqual(a.sent[0][:2], ("a", "hi a"))
            self.assertEqual(book.status("qq")[0]["days"], 2)
        finally:
            db.close()

    def test_streak_sweep_dry_run_does_not_touch(self):
        db = _db(self, "sweep2")
        try:
            clock = [1_700_000_000.0]
            book = StreakBook(db, clock=lambda: clock[0])
            book.touch("qq", "a", day=day_of(clock[0] - 86400))
            out = streak_sweep(self.FakeAdapter(), book, platform="qq",
                               peers=["a"], message_for=lambda p, r: "hi",
                               dry_run=True)
            self.assertEqual(len(out["planned"]), 1)
            self.assertEqual(out["sent"], [])
            row = book.status("qq")[0]
            self.assertFalse(row["touched_today"])
        finally:
            db.close()

    def test_streak_sweep_failure_not_recorded(self):
        db = _db(self, "sweep3")
        try:
            clock = [1_700_000_000.0]
            book = StreakBook(db, clock=lambda: clock[0])
            book.touch("qq", "a", day=day_of(clock[0] - 86400))
            a = self.FakeAdapter(fail="transport_down")
            out = streak_sweep(a, book, platform="qq", peers=["a"],
                               message_for=lambda p, r: "hi", dry_run=False)
            self.assertFalse(out["ok"])
            self.assertEqual(out["failed"][0]["code"], "transport_down")
            self.assertFalse(book.status("qq")[0]["touched_today"])
        finally:
            db.close()

    def test_streak_sweep_empty_message_counts_as_failure(self):
        db = _db(self, "sweep4")
        try:
            clock = [1_700_000_000.0]
            book = StreakBook(db, clock=lambda: clock[0])
            book.touch("qq", "a", day=day_of(clock[0] - 86400))
            out = streak_sweep(self.FakeAdapter(), book, platform="qq",
                               peers=["a"], message_for=lambda p, r: "  ",
                               dry_run=False)
            self.assertEqual(out["failed"][0]["code"], "empty_message")
        finally:
            db.close()


class RegistryTest(unittest.TestCase):
    def test_build_gives_three_platforms(self):
        adapters = build(onebot=lambda a, p=None: {}, dry_run=True)
        self.assertEqual(set(adapters), {"qq", "bilibili", "douyin"})

    def test_matrix_static_has_all_actions(self):
        data = matrix(build(onebot=lambda a, p=None: {}))
        self.assertEqual(set(data["platforms"]), {"qq", "bilibili", "douyin"})
        for action in ("dm", "group", "comment", "feed", "streak", "group_create"):
            self.assertIn(action, data["actions"])

    def test_matrix_live_qq_status(self):
        data = matrix(build(onebot=lambda a, p=None: {"user_id": 1}), live=True)
        self.assertTrue(data["platforms"]["qq"]["status"]["online"])

    def test_summary_lines_render_table(self):
        lines = summary_lines(matrix(build(onebot=lambda a, p=None: {})))
        self.assertIn("qq", lines[0])
        self.assertTrue(any(line.startswith("dm") for line in lines))

    def test_build_without_onebot_reports_transport_down(self):
        # log_path 必须指到 .tmp：不留神就会把测试的行写进生产账本
        # data/platform_actions.jsonl（2026-11-05 就被污染过，还被当成"神秘调用者"查了半天）。
        log = pathlib.Path(".tmp") / "platform_actions_test.jsonl"
        adapter = build(runtime={}, dry_run=False, only=("qq",),
                        log_path=log)["qq"]
        with self.assertRaises(PlatformError) as ctx:
            adapter.dm("1", "hi")
        self.assertEqual(ctx.exception.code, "transport_down")
        self.assertTrue(log.exists(), "测试自己该写的日志要落在这里")
        self.assertIn("transport_down", log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
