"""bili_client 离线测试：BV↔av 算法、cookie 解析、dry-run 护栏、限速、留痕。

不联网：真发路径用假 urlopen；限速/留痕用临时 state_dir。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.senses import bili_client as bc  # noqa: E402


class Bv2AvTest(unittest.TestCase):
    def test_known_pair(self):
        # BV1GJ411x7h7 ↔ av80433022（用官方 view API 实测核对，归档 105242-check-aid）
        self.assertEqual(bc.bv_to_av("BV1GJ411x7h7"), 80433022)

    def test_bad_bvid(self):
        with self.assertRaises(bc.BiliError):
            bc.bv_to_av("av97630895")
        with self.assertRaises(bc.BiliError):
            bc.bv_to_av("BV1!illegal")


class CookieJarTest(unittest.TestCase):
    JAR = (
        "# Netscape HTTP Cookie File\n"
        ".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tabc,def\n"
        ".bilibili.com\tTRUE\t/\tTRUE\t0\tbili_jct\tcsrf123\n"
        ".bilibili.com\tTRUE\t/\tTRUE\t0\tDedeUserID\t42\n"
    )

    def _jar(self, tmp: pathlib.Path) -> pathlib.Path:
        p = tmp / "cookies.txt"
        p.write_text(self.JAR, encoding="utf-8")
        return p

    def test_parse(self):
        root = pathlib.Path(make_tmp(self, "bili-jar"))
        cred = bc.parse_cookie_jar(self._jar(root))
        self.assertEqual(cred.sessdata, "abc,def")
        self.assertEqual(cred.bili_jct, "csrf123")
        self.assertEqual(cred.dedeuserid, "42")
        self.assertTrue(cred.usable)

    def test_missing_file(self):
        with self.assertRaises(bc.BiliError) as ctx:
            bc.parse_cookie_jar(pathlib.Path("Z:/nope.txt"))
        self.assertEqual(ctx.exception.code, "cookie_file_missing")

    def test_not_logged_in_jar(self):
        root = pathlib.Path(make_tmp(self, "bili-jar2"))
        p = root / "c.txt"
        p.write_text(".b.com\tTRUE\t/\tTRUE\t0\tfoo\tbar\n", encoding="utf-8")
        with self.assertRaises(bc.BiliError) as ctx:
            bc.parse_cookie_jar(p)
        self.assertEqual(ctx.exception.code, "cookie_not_logged_in")

    def test_cookie_header(self):
        cred = bc.BiliCredentials(sessdata="s", bili_jct="j", dedeuserid="1")
        self.assertEqual(cred.cookie_header(), "SESSDATA=s; DedeUserID=1; bili_jct=j")


class PostCommentTest(unittest.TestCase):
    CRED = bc.BiliCredentials(sessdata="s", bili_jct="j", dedeuserid="1")

    def test_dry_run_is_the_default(self):
        root = pathlib.Path(make_tmp(self, "bili-dry"))
        result = bc.post_comment(97630895, "玛卡巴卡看了这个视频！",
                                 self.CRED, state_dir=root)
        self.assertTrue(result.dry_run)
        self.assertFalse(result.ok)
        self.assertFalse((root / "bili_actions.jsonl").exists(),
                         "dry-run 不许留痕")

    def test_confirm_requires_confirmation_to_post(self):
        root = pathlib.Path(make_tmp(self, "bili-post"))
        captured = {}

        def fake_urlopen(req, timeout=15):
            captured["url"] = req.full_url
            captured["data"] = req.data.decode("utf-8")
            captured["cookie"] = req.headers.get("Cookie")
            resp = mock.Mock()
            resp.read.return_value = json.dumps(
                {"code": 0, "data": {"reply": {"rpid": 777}}}).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: False
            return resp

        with mock.patch.object(bc.urllib.request, "urlopen", fake_urlopen):
            result = bc.post_comment(97630895, "你好呀", self.CRED,
                                     confirm=True, state_dir=root,
                                     now=1000.0)
        self.assertTrue(result.ok)
        self.assertEqual(result.rpid, 777)
        self.assertIn("/x/v2/reply/add", captured["url"])
        self.assertIn("oid=97630895", captured["data"])
        self.assertIn("csrf=j", captured["data"])
        self.assertIn("SESSDATA=s", captured["cookie"])
        log = (root / "bili_actions.jsonl").read_text(encoding="utf-8")
        entry = json.loads(log.strip().splitlines()[-1])
        self.assertEqual(entry["action"], "comment")
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["rpid"], 777)

    def test_rate_limit_blocks_second_post_within_60s(self):
        root = pathlib.Path(make_tmp(self, "bili-rate"))
        (root / "bili_actions.jsonl").write_text(
            json.dumps({"ts": 1000.0, "action": "comment", "ok": True}) + "\n",
            encoding="utf-8")
        with self.assertRaises(bc.BiliError) as ctx:
            bc.post_comment(1, "太快了", self.CRED, confirm=True,
                            state_dir=root, now=1030.0)
        self.assertEqual(ctx.exception.code, "rate_limited")
        # 超过 60s 就放行（真发路径会打 API——这里 mock 掉）
        def fake_urlopen(req, timeout=15):
            resp = mock.Mock()
            resp.read.return_value = b'{"code": 0, "data": {"reply": {"rpid": 1}}}'
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: False
            return resp
        with mock.patch.object(bc.urllib.request, "urlopen", fake_urlopen):
            result = bc.post_comment(1, "过会儿再发", self.CRED, confirm=True,
                                     state_dir=root, now=1100.0)
        self.assertTrue(result.ok)

    def test_rejection_raises_with_site_message(self):
        root = pathlib.Path(make_tmp(self, "bili-reject"))

        def fake_urlopen(req, timeout=15):
            resp = mock.Mock()
            resp.read.return_value = '{"code": -403, "message": "请求被拦截"}'.encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = lambda s, *a: False
            return resp

        with mock.patch.object(bc.urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(bc.BiliError) as ctx:
                bc.post_comment(1, "x", self.CRED, confirm=True, state_dir=root)
        self.assertEqual(ctx.exception.code, "comment_rejected")
        self.assertIn("-403", str(ctx.exception))
        # 被拒也要留痕（审计需要知道试过什么）
        log = (root / "bili_actions.jsonl").read_text(encoding="utf-8")
        entry = json.loads(log.strip().splitlines()[-1])
        self.assertFalse(entry["ok"])

    def test_empty_and_too_long_messages(self):
        root = pathlib.Path(make_tmp(self, "bili-msg"))
        with self.assertRaises(bc.BiliError):
            bc.post_comment(1, "   ", self.CRED, confirm=True, state_dir=root)
        with self.assertRaises(bc.BiliError):
            bc.post_comment(1, "啊" * 1001, self.CRED, confirm=True, state_dir=root)

    def test_without_login_even_dry_run_fails(self):
        root = pathlib.Path(make_tmp(self, "bili-nologin"))
        with self.assertRaises(bc.BiliError) as ctx:
            bc.post_comment(1, "x", bc.BiliCredentials(), state_dir=root)
        self.assertEqual(ctx.exception.code, "not_logged_in")


if __name__ == "__main__":
    unittest.main()
