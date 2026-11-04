"""listen_video / look_video / upload_video / check_login 的离线编排测试。

网络与子进程全部 mock；锁的是**编排逻辑**（调用的参数、错误映射、降级顺序）。
真机链路见 e2e 归档：101541（listen）、101958（look）、103849（全家桶）、
112257（restyle）。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aiji.senses import bili_client as bc  # noqa: E402
from aiji.senses import video_ingest as vi  # noqa: E402
from tests._tempdir import make_tmp  # noqa: E402


def ok_proc(stdout: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=0,
                                       stdout=stdout, stderr="")


class ListenVideoTest(unittest.TestCase):
    def _patch_asr(self, text="你好", conf=0.9):
        patcher = mock.patch(
            "aiji.hippocampus.real_engines.transcribe_wav_bytes",
            return_value=(text, conf))
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_happy_path_downloads_converts_transcribes(self):
        calls = {}

        def fake_run(args, timeout=None, **kw):
            calls.setdefault("runs", []).append(args)
            if "-f" in args:  # yt-dlp
                workdir = pathlib.Path(args[args.index("-o") + 1]).parent
                (workdir / "audio.m4a").write_bytes(b"audio")
                return ok_proc()
            # ffmpeg：最后一个参数是输出 wav
            pathlib.Path(args[-1]).write_bytes(b"wav")
            return ok_proc()

        with mock.patch.object(vi.subprocess, "run", side_effect=fake_run), \
                self._patch_asr("台词", 0.88):
            text, conf = vi.listen_video("https://x/v", seconds=90.0)
        self.assertEqual(text, "台词")
        self.assertEqual(conf, 0.88)
        ytdlp = calls["runs"][0]
        self.assertIn("bestaudio", ytdlp)
        ffmpeg = calls["runs"][1]
        self.assertIn("16000", ffmpeg)

    def test_download_failure_raises_domain_error(self):
        proc = subprocess.CompletedProcess(args=[], returncode=1, stdout="",
                                           stderr="ERROR: some failure")
        with mock.patch.object(vi.subprocess, "run", return_value=proc):
            with self.assertRaises(vi.VideoIngestError) as ctx:
                vi.listen_video("https://x")
        self.assertEqual(ctx.exception.code, "audio_download_failed")

    def test_no_audio_file_after_download(self):
        def fake_run(args, timeout=None, **kw):
            return ok_proc()  # 成功但没写文件

        with mock.patch.object(vi.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(vi.VideoIngestError) as ctx:
                vi.listen_video("https://x")
        self.assertEqual(ctx.exception.code, "audio_missing")

    def test_ffmpeg_missing_is_actionable(self):
        def fake_run(args, timeout=None, **kw):
            if "-f" in args:
                workdir = pathlib.Path(args[args.index("-o") + 1]).parent
                (workdir / "audio.m4a").write_bytes(b"audio")
                return ok_proc()
            raise FileNotFoundError("ffmpeg")

        with mock.patch.object(vi.subprocess, "run", side_effect=fake_run), \
                self._patch_asr():
            with self.assertRaises(vi.VideoIngestError) as ctx:
                vi.listen_video("https://x")
        self.assertEqual(ctx.exception.code, "ffmpeg_missing")
        self.assertIn("PATH", str(ctx.exception))

    def test_cookies_forwarded_to_ytdlp(self):
        seen = {}

        def fake_run(args, timeout=None, **kw):
            if "-f" in args:  # yt-dlp 调用
                seen["ytdlp"] = args
                workdir = pathlib.Path(args[args.index("-o") + 1]).parent
                (workdir / "audio.m4a").write_bytes(b"a")
                return ok_proc()
            pathlib.Path(args[-1]).write_bytes(b"w")
            return ok_proc()

        with mock.patch.object(vi.subprocess, "run", side_effect=fake_run), \
                self._patch_asr():
            vi.listen_video("https://x", cookies_from_browser="edge")
        self.assertIn("edge", seen["ytdlp"])
        self.assertIn("--cookies-from-browser", seen["ytdlp"])


class LookVideoTest(unittest.TestCase):
    def _patch_ocr(self, text="标题板\n标题板\nbilibili", conf=0.7):
        patcher = mock.patch(
            "aiji.hippocampus.real_engines.ocr_images",
            return_value=(text, conf))
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_frames_are_deduped_and_joined(self):
        def fake_run(args, timeout=None, **kw):
            if "-f" in args:
                workdir = pathlib.Path(args[args.index("-o") + 1]).parent
                (workdir / "video.mp4").write_bytes(b"v")
                return ok_proc()
            # ffmpeg 抽帧：写 10 帧
            out = pathlib.Path(args[-1])
            workdir = out.parent
            for i in range(10):
                (workdir / f"grab_{i:03d}.jpg").write_bytes(b"j")
            return ok_proc()

        with mock.patch.object(vi.subprocess, "run", side_effect=fake_run), \
                self._patch_ocr("标题板\n标题板\nbilibili\n", 0.7):
            text, conf = vi.look_video("https://x", frames=4)
        self.assertIn("标题板", text)
        self.assertNotIn("标题板\n标题板", text, "重复行要被去掉")
        self.assertIn("bilibili", text)

    def test_grabbed_frames_capped(self):
        n_ocr = {}

        def fake_run(args, timeout=None, **kw):
            if "-f" in args:
                workdir = pathlib.Path(args[args.index("-o") + 1]).parent
                (workdir / "video.mp4").write_bytes(b"v")
                return ok_proc()
            workdir = pathlib.Path(args[-1]).parent
            for i in range(20):
                (workdir / f"grab_{i:03d}.jpg").write_bytes(b"j")
            return ok_proc()

        def fake_ocr(blobs):
            n_ocr["n"] = len(blobs)
            return ("x", 0.5)

        with mock.patch.object(vi.subprocess, "run", side_effect=fake_run), \
                mock.patch("aiji.hippocampus.real_engines.ocr_images",
                           side_effect=fake_ocr):
            vi.look_video("https://x", frames=4)
        self.assertEqual(n_ocr["n"], 4, "frames 参数要限制 OCR 的帧数")

    def test_video_download_failure(self):
        proc = subprocess.CompletedProcess(args=[], returncode=1, stdout="",
                                           stderr="ERROR: boom")
        with mock.patch.object(vi.subprocess, "run", return_value=proc):
            with self.assertRaises(vi.VideoIngestError) as ctx:
                vi.look_video("https://x")
        self.assertEqual(ctx.exception.code, "video_download_failed")

    def test_no_frames_extracted(self):
        def fake_run(args, timeout=None, **kw):
            if "-f" in args:
                workdir = pathlib.Path(args[args.index("-o") + 1]).parent
                (workdir / "video.mp4").write_bytes(b"v")
                return ok_proc()
            return ok_proc()  # ffmpeg 没产出帧

        with mock.patch.object(vi.subprocess, "run", side_effect=fake_run), \
                self._patch_ocr():
            with self.assertRaises(vi.VideoIngestError) as ctx:
                vi.look_video("https://x")
        self.assertEqual(ctx.exception.code, "frames_missing")


class CheckLoginTest(unittest.TestCase):
    def test_no_sessdata_means_not_logged_in(self):
        info = bc.check_login(bc.BiliCredentials())
        self.assertFalse(info["is_login"])

    def test_parses_nav_payload(self):
        payload = {"data": {"isLogin": True, "uname": "玛卡巴卡",
                            "mid": 42, "level_info": {"current_level": 3}}}

        class Resp:
            def read(self):
                return json.dumps(payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch.object(bc.urllib.request, "urlopen",
                               return_value=Resp()):
            info = bc.check_login(bc.BiliCredentials(sessdata="s"))
        self.assertTrue(info["is_login"])
        self.assertEqual(info["uname"], "玛卡巴卡")
        self.assertEqual(info["mid"], 42)
        self.assertEqual(info["level"], 3)

    def test_http_error_maps_to_domain_error(self):
        import urllib.error

        with mock.patch.object(
                bc.urllib.request, "urlopen",
                side_effect=urllib.error.HTTPError(
                    "u", 412, "Precondition Failed", None, None)):
            with self.assertRaises(bc.BiliError) as ctx:
                bc.check_login(bc.BiliCredentials(sessdata="s"))
        self.assertEqual(ctx.exception.code, "nav_http_error")


class ResolveAidTest(unittest.TestCase):
    def test_av_number_passthrough(self):
        with mock.patch("aiji.senses.video_ingest.fetch_video_info") as fake:
            fake.return_value = vi.VideoInfo(
                webpage_url="https://b23.tv/av123", video_id="123",
                title="t", uploader="u", duration_s=1.0)
            self.assertEqual(bc.resolve_aid("https://x/av123"), 123)

    def test_bv_converted(self):
        with mock.patch("aiji.senses.video_ingest.fetch_video_info") as fake:
            fake.return_value = vi.VideoInfo(
                webpage_url="https://b23.tv/BV1GJ411x7h7",
                video_id="BV1GJ411x7h7", title="t", uploader="u", duration_s=1.0)
            self.assertEqual(bc.resolve_aid("https://x/BV1GJ411x7h7"), 80433022)

    def test_garbage_id_raises(self):
        with mock.patch("aiji.senses.video_ingest.fetch_video_info") as fake:
            fake.return_value = vi.VideoInfo(
                webpage_url="https://x", video_id="!!!", title="t",
                uploader="u", duration_s=1.0)
            with self.assertRaises(bc.BiliError) as ctx:
                bc.resolve_aid("https://x")
        self.assertEqual(ctx.exception.code, "bad_video_id")


class UploadOrchestrationTest(unittest.TestCase):
    """upload_video 真传路径：mock urlopen，锁调用顺序与错误映射。"""

    CRED = bc.BiliCredentials(sessdata="s", bili_jct="j", dedeuserid="1")

    def _video(self, tmp: pathlib.Path, size: int = 10) -> pathlib.Path:
        p = tmp / "v.mp4"
        p.write_bytes(b"x" * size)
        return p

    def _fake_urlopen_factory(self, responses: list, record: list):
        """按序返回 responses 里的 JSON；record 记录 (url, method, data)。"""

        class Resp:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return json.dumps(self._payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        state = {"i": 0}

        def fake(req, timeout=None):
            record.append((req.full_url, req.get_method(),
                           (req.data or b"")[:60].decode("utf-8", "replace")))
            payload = responses[min(state["i"], len(responses) - 1)]
            state["i"] += 1
            return Resp(payload)

        return fake

    def test_full_upload_flow_calls_in_order(self):
        root = pathlib.Path(make_tmp(self, "up-orch"))
        video = self._video(root, 100)
        record: list = []
        responses = [
            # 1) preupload
            {"upos_uri": "upos/v1/abc", "auth": "AUTH", "biz_id": 777,
             "endpoints": ["//upos.example.com"]},
            # 2) chunk PUT
            {"OK": 1},
            # 3) complete
            {"OK": 1, "ok": 1},
            # 4) submit
            {"code": 0, "data": {"aid": 12345}},
        ]
        fake = self._fake_urlopen_factory(responses, record)
        with mock.patch.object(bc.urllib.request, "urlopen", fake):
            result = bc.upload_video(video, "标题", cred=self.CRED,
                                     confirm=True, state_dir=root)
        self.assertTrue(result.ok)
        self.assertEqual(result.aid, 12345)
        urls = [r[0] for r in record]
        self.assertIn("preupload", urls[0])
        self.assertEqual(record[1][1], "PUT", "分片要 PUT")
        self.assertIn("partNumber=1", record[1][0])
        self.assertEqual(record[2][1], "POST", "complete 要 POST")

    def test_preupload_failure_is_actionable(self):
        root = pathlib.Path(make_tmp(self, "up-pre"))
        video = self._video(root)
        record: list = []
        fake = self._fake_urlopen_factory([{}], record)  # 缺 upos_uri/auth
        with mock.patch.object(bc.urllib.request, "urlopen", fake):
            with self.assertRaises(bc.BiliError) as ctx:
                bc.upload_video(video, "t", cred=self.CRED, confirm=True,
                                state_dir=root)
        self.assertEqual(ctx.exception.code, "preupload_failed")

    def test_chunk_rejection_maps_to_error(self):
        root = pathlib.Path(make_tmp(self, "up-chunk"))
        video = self._video(root, 20)
        record: list = []
        responses = [
            {"upos_uri": "upos/v1/abc", "auth": "AUTH", "biz_id": 1,
             "endpoints": ["//upos.example.com"]},
            {"OK": 0},  # 分片被拒
        ]
        fake = self._fake_urlopen_factory(responses, record)
        with mock.patch.object(bc.urllib.request, "urlopen", fake):
            with self.assertRaises(bc.BiliError) as ctx:
                bc.upload_video(video, "t", cred=self.CRED, confirm=True,
                                state_dir=root)
        self.assertEqual(ctx.exception.code, "chunk_upload_rejected")

    def test_submit_rejection_is_logged_and_raised(self):
        root = pathlib.Path(make_tmp(self, "up-submit"))
        video = self._video(root)
        record: list = []
        responses = [
            {"upos_uri": "u", "auth": "A", "biz_id": 1,
             "endpoints": ["//e.example.com"]},
            {"OK": 1},
            {"OK": 1, "ok": 1},
            {"code": -111, "message": "稿件审核中"},
        ]
        fake = self._fake_urlopen_factory(responses, record)
        with mock.patch.object(bc.urllib.request, "urlopen", fake):
            with self.assertRaises(bc.BiliError) as ctx:
                bc.upload_video(video, "t", cred=self.CRED, confirm=True,
                                state_dir=root)
        self.assertEqual(ctx.exception.code, "submit_rejected")
        # 被拒也留痕
        log = (root / "bili_actions.jsonl").read_text(encoding="utf-8")
        entry = json.loads(log.strip().splitlines()[-1])
        self.assertEqual(entry["action"], "upload")
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["code"], -111)


if __name__ == "__main__":
    unittest.main()
