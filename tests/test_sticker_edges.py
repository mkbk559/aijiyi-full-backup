"""表情包服务的边界：URL 守卫、下载上限、格式识别、失败快照、更新循环。

这个模块会**从网上下东西并写进本地目录**，所以三类边界最要紧：
1. SSRF：私网/环回/链路本地/保留地址一律拒；带账号密码的 URL 拒；解析失败按 502 报
2. 大小：`Content-Length` 超限要提前拒；`read(max+1)` 后仍要再判一次（对端可能撒谎）
3. 失败要留痕：`.update_state.json` 记下失败码，下次 `/api/stickers` 能看出来
另外 `_image_suffix` 是按**魔术字节**判类型，不信文件后缀——这直接决定落盘文件能不能被用。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.heart import sticker_service as st  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\npayload"
JPG = b"\xff\xd8\xff\xe0payload"
GIF = b"GIF89apayload"
WEBP = b"RIFF" + b"\x00" * 4 + b"WEBPpayload"
BMP = b"BMpayload"
PUBLIC = ["93.184.216.34"]


def service(root: pathlib.Path, *, fetch=None, **kwargs) -> st.StickerService:
    return st.StickerService(
        root,
        feed_url=kwargs.pop("feed_url", "https://example.com/feed.json"),
        fetch=fetch,
        resolver=lambda _host: PUBLIC,
        timeout_s=1,
        **kwargs,
    )


class HelperTest(unittest.TestCase):
    def test_mood_mapping_and_fallback(self):
        self.assertEqual(st._mood(""), "misc")
        self.assertEqual(st._mood("今天很开心"), "happy")
        self.assertEqual(st._mood("无语"), "speechless")
        self.assertEqual(st._mood("Angry Mode"), "angry-mode",
                         "英文不做中文关键词映射，只做 slug 化")
        self.assertEqual(st._mood("自定义 标签!"), "misc", "中文无命中时洗成空→misc")
        self.assertEqual(st._mood("custom tag!"), "custom-tag")

    def test_safe_filename(self):
        self.assertEqual(st._safe_filename("dir/好图.PNG"), "好图.PNG")
        self.assertEqual(st._safe_filename("a b?.png"), "a-b-.png")
        self.assertEqual(st._safe_filename("图", require_suffix=False), "图")
        self.assertEqual(st._safe_filename("a.exe", require_suffix=False), "a.exe")
        for bad in ("", "noext", "shell.exe", "../x.png/../"):
            with self.assertRaises(st.StickerError) as ctx:
                st._safe_filename(bad)
            self.assertEqual(ctx.exception.code, "invalid_sticker_filename")

    def test_image_suffix_by_magic_bytes(self):
        self.assertEqual(st._image_suffix(PNG), ".png")
        self.assertEqual(st._image_suffix(JPG), ".jpg")
        self.assertEqual(st._image_suffix(GIF), ".gif")
        self.assertEqual(st._image_suffix(WEBP), ".webp")
        self.assertEqual(st._image_suffix(BMP), ".bmp")
        with self.assertRaises(st.StickerError) as ctx:
            st._image_suffix(b"RIFF\x00\x00\x00\x00AVI ")
        self.assertEqual(ctx.exception.code, "invalid_image")

    def test_atomic_writes_leave_no_temp_files(self):
        root = pathlib.Path(make_tmp(self, "atomic"))
        st._atomic_bytes(root / "deep" / "a.png", PNG)
        st._atomic_json(root / "b.json", {"k": 1})
        self.assertEqual((root / "deep" / "a.png").read_bytes(), PNG)
        self.assertEqual(json.loads((root / "b.json").read_text("utf-8")), {"k": 1})
        self.assertEqual(
            [p.name for p in root.rglob("*.tmp")], [], "临时文件必须被 rename 掉")


class UrlGuardTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(make_tmp(self, "guard"))
        self.svc = service(self.dir, fetch=lambda url, n: PNG)

    def test_scheme_and_credentials(self):
        for url, code in (("ftp://e.com/a.png", "invalid_url"),
                          ("file:///c:/a.png", "invalid_url"),
                          ("https://user:pw@e.com/a.png", "invalid_url"),
                          ("https:///a.png", "invalid_url")):
            with self.subTest(url=url):
                with self.assertRaises(st.StickerError) as ctx:
                    self.svc._validate_public_url(url)
                self.assertEqual(ctx.exception.code, code)

    def test_private_ranges_are_rejected(self):
        blocked = ["127.0.0.1", "10.1.2.3", "192.168.1.1", "172.16.0.9",
                   "169.254.1.1", "224.0.0.1", "240.0.0.1", "0.0.0.0", "::1",
                   "fd00::1"]
        for ip in blocked:
            with self.subTest(ip=ip):
                svc = st.StickerService(
                    self.dir, feed_url="https://e.com/f.json",
                    fetch=lambda url, n: PNG, resolver=lambda _h, ip=ip: [ip])
                with self.assertRaises(st.StickerError) as ctx:
                    svc._validate_public_url("https://e.com/a.png")
                self.assertEqual(ctx.exception.code, "private_url")

    def test_dns_failure_and_empty_answer(self):
        raising = st.StickerService(
            self.dir, feed_url="https://e.com/f.json",
            fetch=lambda url, n: PNG,
            resolver=mock.Mock(side_effect=OSError("没有 DNS")))
        with self.assertRaises(st.StickerError) as ctx:
            raising._validate_public_url("https://e.com/a.png")
        self.assertEqual((ctx.exception.code, ctx.exception.status), ("dns_failed", 502))

        empty = st.StickerService(
            self.dir, feed_url="https://e.com/f.json",
            fetch=lambda url, n: PNG, resolver=lambda _h: [])
        with self.assertRaises(st.StickerError) as ctx:
            empty._validate_public_url("https://e.com/a.png")
        self.assertEqual(ctx.exception.code, "dns_failed")

    def test_non_ip_answers_are_skipped(self):
        svc = st.StickerService(
            self.dir, feed_url="https://e.com/f.json",
            fetch=lambda url, n: PNG,
            resolver=lambda _h: ["不是ip", "93.184.216.34"])
        svc._validate_public_url("https://e.com/a.png")  # 不抛

    def test_resolve_host_dedupes(self):
        rows = [(2, 1, 6, "", ("93.184.216.34", 0)),
                (2, 1, 6, "", ("93.184.216.34", 0)),
                (2, 1, 6, "", ("142.250.1.1", 0))]
        with mock.patch.object(st.socket, "getaddrinfo", return_value=rows):
            self.assertEqual(st.StickerService._resolve_host("e.com"),
                             ["142.250.1.1", "93.184.216.34"])


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(make_tmp(self, "dl"))

    def test_non_bytes_downloader(self):
        svc = service(self.dir, fetch=lambda url, n: "不是 bytes")
        with self.assertRaises(st.StickerError) as ctx:
            svc._get("https://e.com/a.png")
        self.assertEqual(ctx.exception.code, "invalid_download")

    def test_oversize_download_is_rejected(self):
        big = b"x" * (65 * 1024)
        svc = service(self.dir, fetch=lambda url, n: big, max_bytes=64 * 1024)
        with self.assertRaises(st.StickerError) as ctx:
            svc._get("https://e.com/a.png")
        self.assertEqual(ctx.exception.code, "download_too_large",
                         "注入的下载器超限也要拦")

    def test_default_fetch_honours_content_length(self):
        svc = service(self.dir, max_bytes=64 * 1024)
        response = mock.MagicMock()
        response.headers = {"Content-Length": str(9 * 1024 * 1024)}
        response.geturl.return_value = "https://e.com/a.png"
        response.__enter__.return_value = response
        with mock.patch.object(st.net, "urlopen", return_value=response):
            with self.assertRaises(st.StickerError) as ctx:
                svc._get("https://e.com/a.png")
        self.assertEqual(ctx.exception.code, "download_too_large")

    def test_default_fetch_rechecks_size_after_read(self):
        svc = service(self.dir, max_bytes=64 * 1024)
        response = mock.MagicMock()
        response.headers = {}                       # 对端没给 Content-Length
        response.geturl.return_value = "https://e.com/a.png"
        response.read.return_value = b"x" * (65 * 1024)
        response.__enter__.return_value = response
        with mock.patch.object(st.net, "urlopen", return_value=response):
            with self.assertRaises(st.StickerError) as ctx:
                svc._get("https://e.com/a.png")
        self.assertEqual(ctx.exception.code, "download_too_large")

    def test_default_fetch_success_path(self):
        svc = service(self.dir, max_bytes=64 * 1024)
        response = mock.MagicMock()
        response.headers = {}                       # 没给 Content-Length
        response.geturl.return_value = "https://e.com/a.png"
        response.read.return_value = PNG
        response.__enter__.return_value = response
        with mock.patch.object(st.net, "urlopen", return_value=response):
            self.assertEqual(svc._get("https://e.com/a.png"), PNG)

    def test_default_fetch_wraps_network_errors(self):
        svc = service(self.dir, max_bytes=64 * 1024)
        with mock.patch.object(st.net, "urlopen", side_effect=OSError("连接被重置")):
            with self.assertRaises(st.StickerError) as ctx:
                svc._get("https://e.com/a.png")
        self.assertEqual((ctx.exception.code, ctx.exception.status),
                         ("download_failed", 502))

    def test_redirect_target_is_revalidated(self):
        """重定向到私网也必须拦住——只在入口查一次是不够的。"""
        svc = st.StickerService(
            self.dir, feed_url="https://e.com/f.json",
            resolver=lambda host: (["127.0.0.1"] if host == "localhost"
                                   else PUBLIC))
        response = mock.MagicMock()
        response.headers = {"Content-Length": "1"}
        response.geturl.return_value = "http://localhost/a.png"
        response.read.return_value = PNG
        response.__enter__.return_value = response
        with mock.patch.object(st.net, "urlopen", return_value=response):
            with self.assertRaises(st.StickerError) as ctx:
                svc._get("https://e.com/a.png")
        self.assertEqual(ctx.exception.code, "private_url")


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(make_tmp(self, "upd"))

    def test_feed_not_configured(self):
        svc = st.StickerService(self.dir, feed_url="", fetch=lambda u, n: PNG,
                                resolver=lambda _h: PUBLIC)
        with self.assertRaises(st.StickerError) as ctx:
            svc.update()
        self.assertEqual((ctx.exception.code, ctx.exception.status),
                         ("sticker_feed_not_configured", 503))
        # "没配置" 是配置错误不是运行失败：不写失败快照，免得每次都刷一条
        self.assertFalse((self.dir / ".update_state.json").exists())

    def test_feed_without_stickers_list(self):
        svc = service(self.dir, fetch=lambda u, n: b'{"name": "x"}')
        with self.assertRaises(st.StickerError) as ctx:
            svc.update()
        self.assertEqual(ctx.exception.code, "invalid_feed")

    def test_feed_that_is_not_json_is_wrapped(self):
        svc = service(self.dir, fetch=lambda u, n: b"<html>")
        with self.assertRaises(st.StickerError) as ctx:
            svc.update()
        self.assertEqual((ctx.exception.code, ctx.exception.status),
                         ("sticker_update_failed", 502))
        state = json.loads((self.dir / ".update_state.json").read_text("utf-8"))
        self.assertEqual(state["error_code"], "sticker_update_failed")

    def test_unnamed_images_are_pulled_from_relative_urls(self):
        feed = [{"file": "子目录/开心.png", "group": "开心"}]
        seen = []

        def fetch(url, _n):
            seen.append(url)
            return json.dumps(feed, ensure_ascii=False).encode("utf-8") \
                if url.endswith("feed.json") else PNG

        result = service(self.dir, fetch=fetch).update()
        self.assertEqual(result["added"], 1)
        self.assertEqual(len(seen), 2)
        self.assertTrue(seen[1].startswith("https://example.com/"),
                        f"相对 file 要拼成绝对 URL，实际 {seen[1]}")

    def test_missing_suffix_is_replaced_by_detected_format(self):
        feed = [{"file": "图", "url": "https://e.com/raw"}]
        result = service(self.dir, fetch=lambda u, n: (
            json.dumps(feed).encode("utf-8") if u.endswith("feed.json") else JPG)).update()
        self.assertEqual(result["added"], 1)
        files = [p.name for p in self.dir.glob("*.jpg")]
        self.assertEqual(files, ["图.jpg"], "后缀要按魔术字节补上")

    def test_unchanged_image_is_not_counted(self):
        feed = [{"file": "a.png"}]
        svc = service(self.dir, fetch=lambda u, n: (
            json.dumps(feed).encode("utf-8") if u.endswith("feed.json") else PNG))
        first = svc.update()
        second = svc.update()
        self.assertEqual((first["added"], first["updated"]), (1, 0))
        self.assertEqual((second["added"], second["updated"]), (0, 0),
                         "同样的字节不该算新增或更新")
        self.assertEqual(second["total"], 1)

    def test_changed_image_counts_as_updated(self):
        state = {"payload": PNG}
        feed = [{"file": "a.png"}]

        def fetch(url, _n):
            if url.endswith("feed.json"):
                return json.dumps(feed).encode("utf-8")
            return state["payload"]

        svc = service(self.dir, fetch=fetch)
        svc.update()
        state["payload"] = PNG + b"v2"
        result = svc.update()
        self.assertEqual((result["added"], result["updated"]), (0, 1))

    def test_non_dict_items_are_skipped(self):
        feed = ["字符串", 42, {"file": "a.png"}]
        result = service(self.dir, fetch=lambda u, n: (
            json.dumps(feed).encode("utf-8") if u.endswith("feed.json") else PNG)).update()
        self.assertEqual(result["total"], 1)

    def test_local_items_survive_when_absent_from_feed(self):
        (self.dir / "mine.png").write_bytes(PNG)
        (self.dir / "manifest.json").write_text(json.dumps({
            "version": 2, "name": "本地",
            "stickers": [{"file": "mine.png", "mood": "happy"},
                         {"file": "gone.png", "mood": "sad"}],
        }), encoding="utf-8")
        feed = [{"file": "remote.png", "group": "开心"}]
        result = service(self.dir, fetch=lambda u, n: (
            json.dumps(feed).encode("utf-8") if u.endswith("feed.json") else PNG)).update()
        self.assertEqual(result["preserved"], 1, "只保留文件还在的那些")
        manifest = json.loads((self.dir / "manifest.json").read_text("utf-8"))
        self.assertEqual([i["file"] for i in manifest["stickers"]],
                         ["remote.png", "mine.png"])
        self.assertEqual(manifest["name"], "aiji-online", "feed 没名字时用默认名")

    def test_too_many_failures_break_early(self):
        feed = [{"file": f"{i}.png"} for i in range(20)]

        def fetch(url, _n):
            if url.endswith("feed.json"):
                return json.dumps(feed).encode("utf-8")
            raise OSError("全挂")

        result = service(self.dir, fetch=fetch, max_items=20).update()
        self.assertEqual(result["failed"], 8, "失败 8 张就停，别把整轮拖死")
        self.assertTrue(result["partial"])
        self.assertEqual(len(result["failures"]), 8)
        self.assertEqual(result["total"], 0)

    def test_max_items_caps_the_feed(self):
        feed = [{"file": f"{i}.png"} for i in range(10)]
        result = service(self.dir, max_items=3, fetch=lambda u, n: (
            json.dumps(feed).encode("utf-8") if u.endswith("feed.json") else PNG)).update()
        self.assertEqual(result["total"], 3)

    def test_failed_state_write_is_swallowed(self):
        svc = service(self.dir, fetch=lambda u, n: b"<html>")
        with mock.patch.object(st, "_atomic_json", side_effect=OSError("盘满了")):
            with self.assertLogs("aiji.heart.sticker_service", "WARNING"):
                with self.assertRaises(st.StickerError):
                    svc.update()


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(make_tmp(self, "man"))

    def test_snapshot_counts_only_files_that_exist(self):
        (self.dir / "a.png").write_bytes(PNG)
        (self.dir / "manifest.json").write_text(json.dumps({
            "name": "我的", "stickers": [
                {"file": "a.png", "mood": "happy"},
                {"file": "missing.png", "mood": "sad"},
                {"file": "evil.exe", "mood": "sad"},
                "不是字典",
            ]}), encoding="utf-8")
        snap = service(self.dir).snapshot()
        self.assertEqual(snap["count"], 1)
        self.assertEqual(snap["name"], "我的")
        self.assertEqual(snap["moods"], ["happy"])
        self.assertEqual(snap["source"], "online_feed+local_manifest")

    def test_broken_manifest_and_state_are_treated_as_empty(self):
        (self.dir / "manifest.json").write_text("{坏", encoding="utf-8")
        (self.dir / ".update_state.json").write_text("[]", encoding="utf-8")
        snap = service(self.dir).snapshot()
        self.assertEqual(snap["count"], 0)
        self.assertEqual(snap["name"], "")
        self.assertEqual(snap["last_update"], {})

    def test_no_feed_means_not_auto_updating(self):
        svc = st.StickerService(self.dir, feed_url="", resolver=lambda _h: PUBLIC)
        snap = svc.snapshot()
        self.assertFalse(snap["auto_update_capable"])
        self.assertEqual(snap["feed_url"], "")


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(make_tmp(self, "imp"))

    def test_import_uses_hash_name_when_path_has_no_stem(self):
        svc = service(self.dir, fetch=lambda u, n: PNG)
        result = svc.import_image("https://e.com/")
        expected = hashlib.sha256(PNG).hexdigest()[:16]
        self.assertEqual(result["file"], f"{expected}.png")

    def test_import_overwrites_same_name_different_bytes(self):
        payloads = {"first": PNG, "second": PNG + b"v2"}
        current = {"value": "first"}
        svc = service(self.dir, fetch=lambda u, n: payloads[current["value"]])
        first = svc.import_image("https://e.com/x.png")
        current["value"] = "second"
        second = svc.import_image("https://e.com/x.png")
        self.assertEqual(first["file"], "x.png")
        self.assertNotEqual(second["file"], "x.png",
                            "同名不同字节要加哈希后缀，别互相覆盖")
        self.assertEqual([i["file"] for i in json.loads(
            (self.dir / "manifest.json").read_text("utf-8"))["stickers"]],
            [second["file"], "x.png"])

    def test_import_replaces_same_file_entry(self):
        svc = service(self.dir, fetch=lambda u, n: PNG)
        svc.import_image("https://e.com/x.png", mood="开心", description="第一次")
        out = svc.import_image("https://e.com/x.png", mood="生气", description="第二次")
        stickers = json.loads((self.dir / "manifest.json").read_text("utf-8"))["stickers"]
        self.assertEqual(len(stickers), 1, "同一个文件只能有一条记录")
        self.assertEqual(stickers[0]["mood"], "angry")
        self.assertEqual(out["mood"], "angry")

    def test_import_default_source_is_manual(self):
        svc = service(self.dir, fetch=lambda u, n: PNG)
        svc.import_image("https://e.com/x.png", source="someone")
        stickers = json.loads((self.dir / "manifest.json").read_text("utf-8"))["stickers"]
        self.assertEqual(stickers[0]["source"], "manual_import")


class LoopTest(unittest.TestCase):
    def test_start_is_idempotent_and_snapshot_reports_state(self):
        class Service:
            def __init__(self):
                self.calls = 0

            def update(self):
                self.calls += 1
                return {"ok": True, "added": self.calls}

            def snapshot(self):
                return {"count": 0}

        svc = Service()
        loop = st.StickerUpdateLoop(svc, initial_delay_s=0, interval_s=0.05)
        loop.start()
        loop.start()
        for _ in range(200):
            if svc.calls >= 2:
                break
            threading.Event().wait(0.01)
        snap = loop.snapshot()
        loop.stop()
        self.assertGreaterEqual(snap["runs"], 2)
        self.assertEqual(snap["service"], {"count": 0})
        self.assertFalse(loop.snapshot()["running"])

    def test_loop_records_failures(self):
        class Boom:
            def update(self):
                raise RuntimeError("喂过期了")

        loop = st.StickerUpdateLoop(Boom(), initial_delay_s=0, interval_s=5)
        loop.start()
        for _ in range(200):
            if loop.snapshot()["failures"]:
                break
            threading.Event().wait(0.01)
        loop.stop()
        snap = loop.snapshot()
        self.assertEqual(snap["failures"], 1)
        self.assertIn("喂过期了", snap["last_error"])
        self.assertFalse(snap["last_update"]["ok"])

    def test_service_without_snapshot(self):
        loop = st.StickerUpdateLoop(types.SimpleNamespace(), initial_delay_s=60,
                                    interval_s=60)
        self.assertEqual(loop.snapshot()["service"], {})
        self.assertEqual(loop.snapshot()["initial_delay_s"], 60.0)

    def test_stop_during_initial_delay(self):
        """首轮延迟里被叫停：线程要立刻退出，而不是等满 60 秒。"""
        loop = st.StickerUpdateLoop(types.SimpleNamespace(), initial_delay_s=30)
        loop.start()
        loop.stop(timeout=5)
        self.assertFalse(loop.snapshot()["running"])
        self.assertEqual(loop.snapshot()["runs"], 0)

    def test_stop_before_start(self):
        loop = st.StickerUpdateLoop(types.SimpleNamespace(), initial_delay_s=60)
        loop.stop()
        self.assertEqual(loop.snapshot()["running"], False)
        self.assertEqual(loop.stopped_at, 0.0)


if __name__ == "__main__":
    unittest.main()
