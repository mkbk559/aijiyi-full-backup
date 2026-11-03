"""QQ 空间/分享动作的边角：可见性、路径闸门、失败分类。

`tests/test_social_service.py` 覆盖了发布/评论/分享的主路径；这里补的是
"出错时返回什么"——这些方法直接把结果喂给 console 和网关，
错误码写错就会出现"假成功"（明明没发出去却回 ok）。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.heart.social_service import SocialError, SocialService  # noqa: E402


def png(path: pathlib.Path, name: str = "a.png") -> pathlib.Path:
    target = path / name
    target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    return target


class Base(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(make_tmp(self, "social"))
        self.calls: list[tuple[str, dict]] = []

    def service(self, *, reply=None, **kwargs) -> SocialService:
        def call(action, payload):
            self.calls.append((action, payload))
            if isinstance(reply, Exception):
                raise reply
            return reply if reply is not None else {"tid": "T1", "time": 1700000000}

        return SocialService(call, media_root=self.root, **kwargs)


class SocialErrorTest(unittest.TestCase):
    def test_fields_default_message_to_code(self):
        err = SocialError("boom")
        self.assertEqual((err.code, err.message, err.status), ("boom", "boom", 400))
        err = SocialError("bad", "说人话", status=503)
        self.assertEqual((str(err), err.status), ("说人话", 503))


class PublishGuardsTest(Base):
    def test_include_visibility_requires_targets(self):
        with self.assertRaises(SocialError) as ctx:
            self.service().publish("你好", visibility="include")
        self.assertEqual(ctx.exception.code, "target_uins_required")
        self.assertEqual(self.calls, [], "校验没过就不该调 QQ")

    def test_visibility_accepts_numbers_and_names(self):
        service = self.service()
        for value, rights in (("public", 1), ("friends", 4), ("self", 64), ("64", 64)):
            self.calls.clear()
            service.publish("你好", visibility=value)
            self.assertEqual(self.calls[0][1]["ugc_right"], rights, value)

    def test_unknown_visibility_is_rejected(self):
        with self.assertRaises(SocialError) as ctx:
            self.service().publish("你好", visibility="全世界")
        self.assertEqual(ctx.exception.code, "invalid_visibility")

    def test_empty_content_without_images_is_rejected(self):
        with self.assertRaises(SocialError) as ctx:
            self.service().publish("   ")
        self.assertEqual(ctx.exception.code, "content_required")

    def test_too_long_content_is_rejected(self):
        with self.assertRaises(SocialError) as ctx:
            self.service().publish("字" * 2001)
        self.assertEqual(ctx.exception.code, "content_too_long")

    def test_invalid_qq_list_is_rejected(self):
        with self.assertRaises(SocialError) as ctx:
            self.service().publish("转给朋友", visibility="include",
                                   target_uins=["abc"])
        self.assertEqual(ctx.exception.code, "invalid_qq")


class MediaGateTest(Base):
    def test_http_url_passes_through(self):
        service = self.service()
        service.publish("图", images=["https://example.com/a.png"])
        self.assertEqual(self.calls[0][1]["images"], ["https://example.com/a.png"])

    def test_relative_path_resolves_under_media_root(self):
        png(self.root)
        service = self.service()
        service.publish("图", images=["a.png"])
        self.assertTrue(self.calls[0][1]["images"][0].endswith("a.png"))

    def test_path_outside_roots_is_forbidden(self):
        outside = pathlib.Path(make_tmp(self, "outside"))
        png(outside, "b.png")
        with self.assertRaises(SocialError) as ctx:
            self.service().publish("图", images=[str(outside / "b.png")])
        self.assertEqual(ctx.exception.code, "media_path_forbidden")

    def test_sticker_dir_is_an_allowed_root(self):
        stickers = pathlib.Path(make_tmp(self, "stickers"))
        png(stickers, "s.png")
        service = self.service(sticker_dir=stickers)
        service.publish("图", images=[str(stickers / "s.png")])
        self.assertTrue(self.calls[0][1]["images"][0].endswith("s.png"))

    def test_unsupported_suffix_and_missing_file(self):
        (self.root / "note.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(SocialError) as ctx:
            self.service().publish("图", images=["note.txt"])
        self.assertEqual(ctx.exception.code, "unsupported_image")
        with self.assertRaises(SocialError) as ctx:
            self.service().publish("图", images=["nope.png"])
        self.assertEqual(ctx.exception.code, "media_missing")


class CommentTest(Base):
    def test_empty_tid_is_rejected(self):
        with self.assertRaises(SocialError) as ctx:
            self.service().comment("  ", "说两句")
        self.assertEqual(ctx.exception.code, "tid_required")

    def test_comment_with_target_uin(self):
        service = self.service(reply={"comment_id": "C9"})
        result = service.comment("T1", "好看", target_uin="12345")
        self.assertEqual(result["comment_id"], "C9")
        self.assertEqual(self.calls[0][1]["target_uin"], "12345")


class AutoCommentTest(Base):
    def test_post_index_bounds(self):
        for bad in (-1, 100):
            with self.assertRaises(SocialError) as ctx:
                self.service().auto_comment("12345", "赞", post_index=bad)
            self.assertEqual(ctx.exception.code, "invalid_post_index")

    def test_missing_post_list_is_404(self):
        with self.assertRaises(SocialError) as ctx:
            self.service(reply={"msglist": []}).auto_comment("12345", "赞")
        self.assertEqual((ctx.exception.code, ctx.exception.status),
                         ("qzone_post_not_found", 404))

    def test_entry_without_tid_is_404(self):
        with self.assertRaises(SocialError) as ctx:
            self.service(reply={"msglist": [{}]}).auto_comment("12345", "赞")
        self.assertEqual(ctx.exception.status, 404)
        self.assertIn("usable tid", ctx.exception.message)

    def test_success_marks_action_and_index(self):
        reply = {"msglist": [{"tid": "T7"}]}
        service = self.service(reply=reply)
        result = service.auto_comment("12345", "赞", post_index=0)
        self.assertEqual(result["action"], "qzone.auto_comment")
        self.assertEqual(result["post_index"], 0)
        self.assertEqual(result["tid"], "T7")


class ProfileActionsTest(Base):
    def test_set_avatar_uses_media_gate(self):
        png(self.root, "avatar.png")
        result = self.service().set_avatar("avatar.png")
        self.assertEqual(result["action"], "qq.avatar")
        self.assertEqual(self.calls[0][0], "set_qq_avatar")

    def test_signature_length_limit(self):
        with self.assertRaises(SocialError) as ctx:
            self.service().set_signature("签" * 201)
        self.assertEqual(ctx.exception.code, "signature_too_long")

    def test_signature_normalizes_whitespace(self):
        result = self.service().set_signature("第一行\r\n\r\n\r\n第二行")
        self.assertEqual(result["signature"], "第一行\n\n第二行")


class ShareTest(Base):
    def test_numeric_query_becomes_netease_link(self):
        service = self.service()
        result = service.share_music("1234567")
        self.assertEqual(result["song_id"], "1234567")
        self.assertEqual(result["url"], "https://music.163.com/song?id=1234567")
        self.assertIn("网易云 1234567", self.calls[0][1]["content"])

    def test_lookup_result_wins(self):
        service = self.service(song_lookup=lambda q: {
            "id": "88", "title": "晚风", "url": "https://m/88"})
        result = service.share_music("晚风")
        self.assertEqual((result["song_id"], result["title"]), ("88", "晚风"))

    def test_unresolvable_song_is_rejected(self):
        with self.assertRaises(SocialError) as ctx:
            self.service(song_lookup=lambda q: {}).share_music("找不到的歌")
        self.assertEqual(ctx.exception.code, "song_not_found")

    def test_bilibili_bv_id(self):
        result = self.service().share_bilibili("看看 BV1xx411c7mD 这个")
        self.assertEqual(result["bvid"], "BV1xx411c7mD")
        self.assertEqual(result["url"],
                         "https://www.bilibili.com/video/BV1xx411c7mD")
        self.assertIn("【B站】B站视频", self.calls[0][1]["content"])

    def test_bilibili_av_id_and_url_forms(self):
        service = self.service()
        self.assertEqual(service.share_bilibili("av12345")["url"],
                         "https://www.bilibili.com/video/av12345")
        result = service.share_bilibili("https://m.bilibili.com/video/BV1zz411c7mQ?p=2",
                                        title="我的标题")
        self.assertEqual(result["bvid"], "BV1zz411c7mQ")
        self.assertEqual(result["title"], "我的标题")

    def test_bilibili_url_with_av_segment_has_no_bvid(self):
        service = self.service()
        result = service.share_bilibili("https://www.bilibili.com/video/av999")
        self.assertEqual(result["bvid"], "", "av 不是 BV 号")
        self.assertEqual(result["url"], "https://www.bilibili.com/video/av999")

    def test_bilibili_garbage_is_rejected(self):
        with self.assertRaises(SocialError) as ctx:
            self.service().share_bilibili("这不是链接")
        self.assertEqual(ctx.exception.code, "invalid_bilibili_url")


class InvokeTest(Base):
    def test_unconfigured_service_is_503(self):
        service = SocialService(None, media_root=self.root)
        with self.assertRaises(SocialError) as ctx:
            service.publish("你好")
        self.assertEqual((ctx.exception.code, ctx.exception.status),
                         ("social_not_configured", 503))
        self.assertFalse(service.snapshot()["configured"])

    def test_social_error_passes_through(self):
        service = self.service(reply=SocialError("policy", "不许发"))
        with self.assertRaises(SocialError) as ctx:
            service.publish("你好")
        self.assertEqual(ctx.exception.code, "policy")

    def test_other_exceptions_become_502(self):
        service = self.service(reply=RuntimeError("NapCat 挂了"))
        with self.assertRaises(SocialError) as ctx:
            service.publish("你好")
        self.assertEqual((ctx.exception.code, ctx.exception.status),
                         ("onebot_call_failed", 502))
        self.assertIn("NapCat 挂了", ctx.exception.message)


class StickerSnapshotTest(Base):
    def test_callback_result_wins(self):
        service = self.service(sticker_status=lambda: {"count": 3, "name": "鲸鱼"})
        self.assertEqual(service.snapshot()["stickers"]["count"], 3)

    def test_callback_failure_falls_back_to_manifest(self):
        def boom():
            raise RuntimeError("读不到")
        service = self.service(sticker_status=boom)
        self.assertEqual(service.snapshot()["stickers"]["source"], "local_manifest")

    def test_no_sticker_dir_returns_empty_summary(self):
        stickers = self.service().snapshot()["stickers"]
        self.assertEqual((stickers["count"], stickers["dir"]), (0, ""))
        self.assertFalse(stickers["remote_fetch"])

    def test_manifest_parsing_and_filtering(self):
        self.sticker_dir = pathlib.Path(make_tmp(self, "stickers-many"))
        png(self.sticker_dir, "ok.png")
        (self.sticker_dir / "bad.txt").write_text("x", encoding="utf-8")
        manifest = {
            "name": "鲸鱼包",
            "stickers": [
                {"file": "ok.png", "mood": "开心"},
                {"file": "bad.txt", "mood": "开心"},
                {"file": "missing.png", "mood": "困"},
                {"file": "../escape.png", "mood": "坏"},
                "不是对象",
            ],
        }
        (self.sticker_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        service = self.service(sticker_dir=self.sticker_dir)
        stickers = service.snapshot()["stickers"]
        self.assertEqual(stickers["name"], "鲸鱼包")
        self.assertEqual(stickers["count"], 1, "只有真存在、在目录内、图片后缀的算数")
        self.assertEqual(stickers["moods"], ["开心"])

    def test_broken_manifest_shapes_fall_back(self):
        self.sticker_dir = pathlib.Path(make_tmp(self, "stickers-bad"))
        service = self.service(sticker_dir=self.sticker_dir)
        (self.sticker_dir / "manifest.json").write_text("{坏掉的json", encoding="utf-8")
        self.assertEqual(service.snapshot()["stickers"]["count"], 0)
        (self.sticker_dir / "manifest.json").write_text("[1,2,3]", encoding="utf-8")
        self.assertEqual(service.snapshot()["stickers"]["count"], 0)
        (self.sticker_dir / "manifest.json").write_text(
            json.dumps({"stickers": "不是列表"}), encoding="utf-8")
        self.assertEqual(service.snapshot()["stickers"]["count"], 0)


if __name__ == "__main__":
    unittest.main()
