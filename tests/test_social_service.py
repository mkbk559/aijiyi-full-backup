"""QQ 空间与平台分享动作的契约测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from aiji.social_service import SocialService


class SocialServiceTest(unittest.TestCase):
    def test_publish_maps_visibility_images_and_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "media"
            media.mkdir()
            image = media / "post.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\nlocal")
            calls = []

            def call(action, payload):
                calls.append((action, payload))
                return {"tid": "post-123", "time": 1700000000}

            social = SocialService(
                call,
                media_root=media,
                sticker_dir=root / "stickers",
            )
            result = social.publish(
                "今天拍到了一点晚霞",
                images=[str(image)],
                visibility="friends",
            )

            self.assertEqual(calls, [(
                "send_qzone_msg",
                {
                    "content": "今天拍到了一点晚霞",
                    "images": [str(image.resolve())],
                    "ugc_right": 4,
                    "target_uins": [],
                },
            )])
            self.assertEqual(result["tid"], "post-123")
            self.assertEqual(result["action"], "qzone.publish")

    def test_comment_uses_snowluma_tid_and_target_uin(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def call(action, payload):
                calls.append((action, payload))
                return {"comment_id": "comment-9"}

            social = SocialService(
                call,
                media_root=Path(tmp) / "media",
                sticker_dir=Path(tmp) / "stickers",
            )
            result = social.comment(
                "post-123",
                "这张晚霞很好看",
                target_uin="10001",
            )

            self.assertEqual(calls, [(
                "comment_qzone",
                {
                    "tid": "post-123",
                    "target_uin": "10001",
                    "content": "这张晚霞很好看",
                    "images": [],
                },
            )])
            self.assertEqual(result["comment_id"], "comment-9")

    def test_auto_comment_resolves_latest_post_before_commenting(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def call(action, payload):
                calls.append((action, payload))
                if action == "get_qzone_msg_list":
                    return {
                        "total": 2,
                        "msglist": [
                            {"tid": "newest-post", "content": "刚发的"},
                            {"tid": "older-post", "content": "以前发的"},
                        ],
                    }
                return {"comment_id": "comment-auto"}

            social = SocialService(
                call,
                media_root=Path(tmp) / "media",
                sticker_dir=Path(tmp) / "stickers",
            )
            result = social.auto_comment(
                "10001",
                "把晚霞拍得真好",
                post_index=1,
            )

            self.assertEqual(calls, [
                (
                    "get_qzone_msg_list",
                    {"target_uin": "10001", "pos": 0, "num": 2},
                ),
                (
                    "comment_qzone",
                    {
                        "tid": "older-post",
                        "target_uin": "10001",
                        "content": "把晚霞拍得真好",
                        "images": [],
                    },
                ),
            ])
            self.assertEqual(result["comment_id"], "comment-auto")
            self.assertEqual(result["tid"], "older-post")

    def test_avatar_accepts_local_image_inside_media_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "media"
            media.mkdir()
            avatar = media / "avatar.png"
            avatar.write_bytes(b"\x89PNG\r\n\x1a\navatar")
            calls = []

            def call(action, payload):
                calls.append((action, payload))
                return None

            social = SocialService(
                call,
                media_root=media,
                sticker_dir=Path(tmp) / "stickers",
            )
            result = social.set_avatar(str(avatar))

            self.assertEqual(calls, [(
                "set_qq_avatar",
                {"file": str(avatar.resolve())},
            )])
            self.assertEqual(result["action"], "qq.avatar")

    def test_signature_uses_napcat_long_nick_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def call(action, payload):
                calls.append((action, payload))
                return {"result": 0}

            social = SocialService(
                call,
                media_root=Path(tmp) / "media",
                sticker_dir=Path(tmp) / "stickers",
            )
            result = social.set_signature("今天也在好好生活")

            self.assertEqual(calls, [(
                "set_self_longnick",
                {"longNick": "今天也在好好生活"},
            )])
            self.assertEqual(result["signature"], "今天也在好好生活")

    def test_music_share_publishes_netease_link_to_qzone(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def call(action, payload):
                calls.append((action, payload))
                return {"tid": "music-post", "time": 1700000001}

            def lookup(query):
                self.assertEqual(query, "晴天")
                return {
                    "id": "186001",
                    "title": "晴天 - 周杰伦",
                    "url": "https://music.163.com/song?id=186001",
                }

            social = SocialService(
                call,
                media_root=Path(tmp) / "media",
                sticker_dir=Path(tmp) / "stickers",
                song_lookup=lookup,
            )
            result = social.share_music("晴天", caption="想听这首歌")

            self.assertEqual(calls, [(
                "send_qzone_msg",
                {
                    "content": (
                        "想听这首歌\n晴天 - 周杰伦\n"
                        "https://music.163.com/song?id=186001"
                    ),
                    "images": [],
                    "ugc_right": 1,
                    "target_uins": [],
                },
            )])
            self.assertEqual(result["action"], "share.music")
            self.assertEqual(result["tid"], "music-post")

    def test_bilibili_share_normalizes_url_and_publishes_to_qzone(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []

            def call(action, payload):
                calls.append((action, payload))
                return {"tid": "bili-post", "time": 1700000002}

            social = SocialService(
                call,
                media_root=Path(tmp) / "media",
                sticker_dir=Path(tmp) / "stickers",
            )
            result = social.share_bilibili(
                "https://www.bilibili.com/video/BV1xx411c7mD?spm_id_from=333.1",
                title="一个很有意思的视频",
                caption="这个视频值得看看",
            )

            self.assertEqual(calls, [(
                "send_qzone_msg",
                {
                    "content": (
                        "这个视频值得看看\n【B站】一个很有意思的视频\n"
                        "https://www.bilibili.com/video/BV1xx411c7mD"
                    ),
                    "images": [],
                    "ugc_right": 1,
                    "target_uins": [],
                },
            )])
            self.assertEqual(result["action"], "share.bilibili")
            self.assertEqual(result["bvid"], "BV1xx411c7mD")

    def test_snapshot_reports_local_stickers_without_calling_onebot(self):
        with tempfile.TemporaryDirectory() as tmp:
            sticker_dir = Path(tmp) / "stickers"
            sticker_dir.mkdir()
            (sticker_dir / "happy.png").write_bytes(b"\x89PNG\r\n\x1a\nhappy")
            (sticker_dir / "manifest.json").write_text(json.dumps({
                "version": 1,
                "name": "local-pack",
                "stickers": [{
                    "file": "happy.png",
                    "mood": "happy",
                    "description": "开心",
                }],
            }, ensure_ascii=False), encoding="utf-8")
            calls = []
            social = SocialService(
                lambda action, payload: calls.append((action, payload)),
                media_root=Path(tmp) / "media",
                sticker_dir=sticker_dir,
            )

            result = social.snapshot()

            self.assertEqual(calls, [])
            self.assertTrue(result["configured"])
            self.assertEqual(result["stickers"]["count"], 1)
            self.assertEqual(result["stickers"]["moods"], ["happy"])
            self.assertFalse(result["stickers"]["remote_fetch"])
            self.assertEqual(result["stickers"]["source"], "local_manifest")


if __name__ == "__main__":
    unittest.main(verbosity=2)
