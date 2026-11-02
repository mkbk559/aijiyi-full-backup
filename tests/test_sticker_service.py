"""Online sticker library update contracts."""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from aiji.heart.sticker_service import StickerService, StickerUpdateLoop


_PNG = b"\x89PNG\r\n\x1a\nonline-sticker"


class StickerServiceTest(unittest.TestCase):
    def test_update_keeps_successful_images_when_one_download_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            feed_url = "https://example.com/stickers/manifest.json"
            feed = [
                {"file": "good.png", "name": "好图", "group": "开心"},
                {"file": "bad.png", "name": "超时图", "group": "开心"},
            ]

            def fetch(url, _max_bytes):
                if url == feed_url:
                    return json.dumps(feed, ensure_ascii=False).encode("utf-8")
                if url.endswith("/good.png"):
                    return _PNG
                raise RuntimeError("timed out")

            service = StickerService(
                root,
                feed_url=feed_url,
                fetch=fetch,
                resolver=lambda _host: ["93.184.216.34"],
                timeout_s=1,
            )
            result = service.update()

            self.assertEqual(result["added"], 1)
            self.assertEqual(result["failed"], 1)
            self.assertTrue((root / "good.png").is_file())
            manifest = json.loads((root / "manifest.json").read_text("utf-8"))
            self.assertEqual(
                [item["file"] for item in manifest["stickers"]],
                ["good.png"],
            )

    def test_update_downloads_remote_feed_and_merges_local_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / "local.png"
            local.write_bytes(b"\x89PNG\r\n\x1a\nlocal-sticker")
            (root / "manifest.json").write_text(json.dumps({
                "version": 1,
                "name": "local-pack",
                "stickers": [{
                    "file": "local.png",
                    "mood": "happy",
                    "description": "本地开心",
                }],
            }, ensure_ascii=False), encoding="utf-8")
            feed_url = "https://example.com/stickers/manifest.json"
            feed = [{
                "file": "smile.png",
                "name": "开心",
                "group": "开心",
            }]
            requested = []

            def fetch(url, max_bytes):
                requested.append((url, max_bytes))
                if url == feed_url:
                    return json.dumps(feed, ensure_ascii=False).encode("utf-8")
                if url == "https://example.com/stickers/smile.png":
                    return _PNG
                raise AssertionError(url)

            service = StickerService(
                root,
                feed_url=feed_url,
                fetch=fetch,
                resolver=lambda _host: ["93.184.216.34"],
                max_bytes=1024 * 1024,
            )
            result = service.update()

            self.assertEqual(
                requested,
                [
                    (feed_url, 1024 * 1024),
                    ("https://example.com/stickers/smile.png", 1024 * 1024),
                ],
            )
            self.assertEqual(result["added"], 1)
            self.assertTrue((root / "smile.png").is_file())
            manifest = json.loads((root / "manifest.json").read_text("utf-8"))
            self.assertEqual(
                [item["file"] for item in manifest["stickers"]],
                ["smile.png", "local.png"],
            )
            online = manifest["stickers"][0]
            self.assertEqual(online["mood"], "happy")
            self.assertEqual(online["source"], feed_url)
            self.assertEqual(
                online["sha256"],
                hashlib.sha256(_PNG).hexdigest(),
            )
            state = service.snapshot()
            self.assertTrue(state["auto_update_capable"])
            self.assertEqual(state["last_update"]["status"], "ok")

    def test_invalid_remote_image_keeps_existing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "local.png").write_bytes(_PNG)
            manifest_path = root / "manifest.json"
            original = json.dumps({
                "version": 1,
                "name": "stable-pack",
                "stickers": [{
                    "file": "local.png",
                    "mood": "happy",
                    "description": "keep me",
                }],
            }, ensure_ascii=False).encode("utf-8")
            manifest_path.write_bytes(original)
            feed_url = "https://example.com/stickers/manifest.json"

            def fetch(url, _max_bytes):
                if url == feed_url:
                    return json.dumps([{
                        "file": "broken.png",
                        "group": "开心",
                    }]).encode("utf-8")
                return b"<html>not an image</html>"

            service = StickerService(
                root,
                feed_url=feed_url,
                fetch=fetch,
                resolver=lambda _host: ["93.184.216.34"],
            )
            with self.assertRaisesRegex(Exception, "not a supported image"):
                service.update()

            self.assertEqual(manifest_path.read_bytes(), original)
            self.assertEqual(
                service.snapshot()["last_update"]["status"], "failed")

    def test_import_image_downloads_public_image_into_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            url = "https://cdn.example.com/cat-happy.png"

            def fetch(requested, _max_bytes):
                self.assertEqual(requested, url)
                return _PNG

            service = StickerService(
                root,
                feed_url="",
                fetch=fetch,
                resolver=lambda _host: ["93.184.216.34"],
            )
            result = service.import_image(
                url,
                mood="开心",
                description="联网抓到的开心图",
            )

            self.assertEqual(result["file"], "cat-happy.png")
            self.assertTrue((root / "cat-happy.png").is_file())
            manifest = json.loads((root / "manifest.json").read_text("utf-8"))
            self.assertEqual(manifest["stickers"][0]["mood"], "happy")
            self.assertEqual(
                manifest["stickers"][0]["description"], "联网抓到的开心图")

    def test_import_records_group_chat_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            url = "https://cdn.example.com/group-meme.png"
            service = StickerService(
                root,
                feed_url="",
                fetch=lambda _url, _max_bytes: _PNG,
                resolver=lambda _host: ["93.184.216.34"],
            )

            service.import_image(
                url,
                mood="开心",
                description="群友",
                source="group_chat",
            )

            manifest = json.loads((root / "manifest.json").read_text("utf-8"))
            self.assertEqual(manifest["stickers"][0]["source"], "group_chat")

    def test_import_keeps_distinct_images_with_same_download_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images = [b"\x89PNG\r\n\x1a\nfirst", b"\x89PNG\r\n\x1a\nsecond"]
            service = StickerService(
                root, feed_url="",
                fetch=lambda _url, _max_bytes: images.pop(0),
                resolver=lambda _host: ["93.184.216.34"],
            )
            first = service.import_image("https://cdn.example.com/a/download.png", source="group_chat")
            second = service.import_image("https://cdn.example.com/b/download.png", source="group_chat")
            manifest = json.loads((root / "manifest.json").read_text("utf-8"))
            self.assertNotEqual(first["file"], second["file"])
            self.assertEqual(len(manifest["stickers"]), 2)
            self.assertEqual((root / first["file"]).read_bytes(), b"\x89PNG\r\n\x1a\nfirst")
            self.assertEqual((root / second["file"]).read_bytes(), b"\x89PNG\r\n\x1a\nsecond")

    def test_import_private_network_url_is_rejected_before_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            called = []
            service = StickerService(
                Path(tmp),
                feed_url="",
                fetch=lambda url, _max_bytes: called.append(url) or _PNG,
                resolver=lambda _host: ["127.0.0.1"],
            )
            with self.assertRaisesRegex(Exception, "private network"):
                service.import_image(
                    "http://127.0.0.1/secret.png",
                    mood="happy",
                )
            self.assertEqual(called, [])

    def test_update_loop_repeats_and_stops_cleanly(self):
        calls = []

        class FakeService:
            def update(self):
                calls.append(time.monotonic())
                return {"ok": True}

            def snapshot(self):
                return {"last_update": {"status": "ok"}}

        loop = StickerUpdateLoop(
            FakeService(),
            initial_delay_s=0.01,
            interval_s=0.05,
        ).start()
        deadline = time.monotonic() + 2
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        loop.stop(timeout=1)

        self.assertGreaterEqual(len(calls), 2)
        self.assertFalse(loop.snapshot()["running"])

    def test_concurrent_updates_do_not_download_in_parallel(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed_url = "https://example.com/stickers/manifest.json"
            active = 0
            max_active = 0
            lock = threading.Lock()

            def fetch(url, _max_bytes):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.05)
                with lock:
                    active -= 1
                if url == feed_url:
                    return json.dumps([{
                        "file": "smile.png",
                        "group": "开心",
                    }]).encode("utf-8")
                return _PNG

            service = StickerService(
                Path(tmp),
                feed_url=feed_url,
                fetch=fetch,
                resolver=lambda _host: ["93.184.216.34"],
            )
            threads = [
                threading.Thread(target=service.update)
                for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

            self.assertEqual(max_active, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
