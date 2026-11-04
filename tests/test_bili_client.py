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
        # 空内容在 dry-run 前就被拒（无需登录态）。空白串要靠 strip 拦——
        # 2026-11-04 教训：strip 被误删时这个用例会带着可用 cred+confirm
        # 摸到真网络；套件的"0 次外网请求"巡检会当场抓住。
        with self.assertRaises(bc.BiliError) as ctx:
            bc.post_comment(1, "   ", bc.BiliCredentials(), state_dir=root)
        self.assertEqual(ctx.exception.code, "empty_message")
        with self.assertRaises(bc.BiliError):
            bc.post_comment(1, "啊" * 1001, self.CRED, confirm=True, state_dir=root)

    def test_dry_run_works_without_login(self):
        # 2026-11-04 设计变化：dry-run 是纯本地预演（含 persona 改写预览），
        # 不要求登录态；真发才要。
        root = pathlib.Path(make_tmp(self, "bili-nologin"))
        result = bc.post_comment(1, "x", bc.BiliCredentials(), state_dir=root)
        self.assertTrue(result.dry_run)
        self.assertIn("[dry-run]", result.message)

    def test_confirm_without_login_fails(self):
        root = pathlib.Path(make_tmp(self, "bili-nologin2"))
        with self.assertRaises(bc.BiliError) as ctx:
            bc.post_comment(1, "x", bc.BiliCredentials(), confirm=True,
                            state_dir=root)
        self.assertEqual(ctx.exception.code, "not_logged_in")


class UploadTest(unittest.TestCase):
    CRED = bc.BiliCredentials(sessdata="s", bili_jct="j", dedeuserid="1")

    def _video(self, tmp: pathlib.Path, size: int) -> pathlib.Path:
        p = tmp / "v.mp4"
        p.write_bytes(b"x" * size)
        return p

    def test_dry_run_needs_no_login(self):
        root = pathlib.Path(make_tmp(self, "bili-up-dry"))
        video = self._video(root, 1000)
        result = bc.upload_video(
            video, "标题", cred=bc.BiliCredentials(), state_dir=root)
        self.assertTrue(result.dry_run)
        self.assertIn("1 个分片", result.message)
        self.assertFalse((root / "bili_actions.jsonl").exists())

    def test_dry_run_reports_multiple_chunks(self):
        root = pathlib.Path(make_tmp(self, "bili-up-chunks"))
        video = self._video(root, bc.CHUNK_SIZE * 2 + 5)
        result = bc.upload_video(
            video, "标题", cred=bc.BiliCredentials(), state_dir=root)
        self.assertIn("3 个分片", result.message)

    def test_missing_file_and_bad_titles(self):
        root = pathlib.Path(make_tmp(self, "bili-up-bad"))
        with self.assertRaises(bc.BiliError) as ctx:
            bc.upload_video(root / "nope.mp4", "t", cred=self.CRED, state_dir=root)
        self.assertEqual(ctx.exception.code, "video_file_missing")
        video = self._video(root, 10)
        with self.assertRaises(bc.BiliError) as ctx:
            bc.upload_video(video, "  ", cred=self.CRED, state_dir=root)
        self.assertEqual(ctx.exception.code, "empty_title")
        with self.assertRaises(bc.BiliError) as ctx:
            bc.upload_video(video, "标" * 81, cred=self.CRED, state_dir=root)
        self.assertEqual(ctx.exception.code, "title_too_long")

    def test_confirm_without_login_is_rejected(self):
        root = pathlib.Path(make_tmp(self, "bili-up-nologin"))
        video = self._video(root, 10)
        with self.assertRaises(bc.BiliError) as ctx:
            bc.upload_video(video, "t", cred=bc.BiliCredentials(),
                            confirm=True, state_dir=root)
        self.assertEqual(ctx.exception.code, "not_logged_in")


class RestyleTest(unittest.TestCase):
    def test_no_pool_and_no_service_returns_original(self):
        # endpoint 指向不可达端口：服务兜底失败 → 原文（heuristic）
        text, source = bc.restyle_comment(
            "这个视频拍得真好", endpoint="http://127.0.0.1:1", timeout_s=0.5)
        self.assertEqual(text, "这个视频拍得真好")
        self.assertEqual(source, "heuristic")

    def test_service_endpoint_rewrites(self):
        class Resp:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return json.dumps(self._payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            # 锁定隔离声明：改写必须独立 session + 不进记忆（excluded #175）
            payload = json.loads(bytes(req.data).decode("utf-8"))
            assert payload.get("session") == "restyle", payload.get("session")
            assert payload.get("aiji", {}).get("remember") is False
            return Resp({"choices": [{"message": {"content": '"哇好好看呀"'}}]})

        with mock.patch.object(bc.urllib.request, "urlopen", fake_urlopen):
            text, source = bc.restyle_comment("好看", endpoint="http://x")
        self.assertEqual(text, "哇好好看呀")
        self.assertEqual(source, "model")

    def test_pool_result_is_stripped(self):
        class Pool:
            def chat(self, messages, timeout_s=30.0):
                return type("R", (), {"text": '"哇好好看呀"'})()
        text, source = bc.restyle_comment("好看", Pool())
        self.assertEqual(text, "哇好好看呀")
        self.assertEqual(source, "model")

    def test_empty_pool_reply_falls_back(self):
        class Pool:
            def chat(self, messages, timeout_s=30.0):
                return type("R", (), {"text": ""})()
        text, source = bc.restyle_comment("好看", Pool())
        self.assertEqual(text, "好看")
        self.assertEqual(source, "heuristic")

    def test_pool_exception_falls_back(self):
        class Pool:
            def chat(self, messages, timeout_s=30.0):
                raise RuntimeError("pool down")
        text, source = bc.restyle_comment("好看", Pool())
        self.assertEqual(text, "好看")
        self.assertEqual(source, "heuristic")

    def test_empty_input_raises(self):
        with self.assertRaises(bc.BiliError):
            bc.restyle_comment("   ")


if __name__ == "__main__":
    unittest.main()
