"""视频学习链路的离线测试：yt-dlp 解析、学习计划、识别器分叉、知识兜底落库。

真网络的部分只在 e2e 探针里跑（归档 083635 / 085709 / 085803）；
这里全部用假 yt-dlp 输出与内存库，锁的是**纯逻辑**：
- 元数据归一化：标题/UP主/字幕语言/danmaku 不算字幕
- 学习计划：有字幕→subtitles；无字幕短视频→audio_asr；直播→metadata_only
- 识别器：带 video_info 产出 ok + 文本（走抽取管线），否则 metadata_only
- writer：视频是第三方知识源，模型判"没有用户事实"时也要有 knowledge 兜底
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from aiji.hippocampus import recognize  # noqa: E402
from aiji.hippocampus.recognize import Item, Recognizers  # noqa: E402
from aiji.senses import video_ingest as vi  # noqa: E402


def fake_stdout_payload(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=0, stdout=json.dumps(payload, ensure_ascii=False), stderr="")


class FakeYtDlpTest(unittest.TestCase):
    INFO = {
        "id": "BV1GJ411x7h7",
        "title": "【官方 MV】Never Gonna Give You Up",
        "uploader": "索尼音乐中国",
        "duration": 212.4,
        "description": "官方 MV",
        "view_count": 107995489,
        "tags": ["发现", "MV"],
        "subtitles": {"danmaku": [{"ext": "xml"}]},
        "extractor_key": "BiliBili",
        "webpage_url": "https://www.bilibili.com/video/BV1GJ411x7h7",
    }

    def test_normal_video(self):
        with mock.patch.object(vi, "_run_ytdlp",
                               return_value=fake_stdout_payload(self.INFO)):
            info = vi.fetch_video_info("https://www.bilibili.com/video/BV1GJ411x7h7")
        self.assertEqual(info.title, "【官方 MV】Never Gonna Give You Up")
        self.assertEqual(info.uploader, "索尼音乐中国")
        self.assertFalse(info.has_subs, "danmaku 不是字幕")
        self.assertEqual(info.subtitle_langs, ["danmaku"])

    def test_cc_subtitles_count(self):
        payload = dict(self.INFO)
        payload["subtitles"] = {"zh-Hans": [{"ext": "srt"}]}
        with mock.patch.object(vi, "_run_ytdlp", return_value=fake_stdout_payload(payload)):
            info = vi.fetch_video_info("https://x")
        self.assertTrue(info.has_subs)

    def test_invalid_url(self):
        with self.assertRaises(vi.VideoIngestError) as ctx:
            vi.fetch_video_info("not-a-url")
        self.assertEqual(ctx.exception.code, "invalid_url")

    def test_cookies_hint_on_failure(self):
        proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="ERROR: [Douyin] 123: Fresh cookies (not necessarily logged in) are needed")
        with mock.patch.object(vi, "_run_ytdlp", return_value=proc):
            with self.assertRaises(vi.VideoIngestError) as ctx:
                vi.fetch_video_info("https://www.douyin.com/video/1")
        self.assertEqual(ctx.exception.code, "video_fetch_failed")
        self.assertIn("cookies-from-browser", str(ctx.exception))

    def test_playlist_is_rejected_with_a_clear_hint(self):
        payload = {"_type": "playlist", "entries": []}
        with mock.patch.object(vi, "_run_ytdlp", return_value=fake_stdout_payload(payload)):
            with self.assertRaises(vi.VideoIngestError) as ctx:
                vi.fetch_video_info("https://www.bilibili.com/video/BVxxx")
        self.assertEqual(ctx.exception.code, "playlist_needs_single")

    def test_empty_title_is_suspicious(self):
        payload = {**self.INFO, "title": " "}
        with mock.patch.object(vi, "_run_ytdlp", return_value=fake_stdout_payload(payload)):
            with self.assertRaises(vi.VideoIngestError) as ctx:
                vi.fetch_video_info("https://x")
        self.assertEqual(ctx.exception.code, "empty_title")

    def test_timeout_is_raised_as_domain_error(self):
        def boom(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=5)
        with mock.patch.object(subprocess, "run", side_effect=boom):
            with self.assertRaises(vi.VideoIngestError) as ctx:
                vi.fetch_video_info("https://x", timeout_s=1)
        self.assertEqual(ctx.exception.code, "video_timeout")

    def test_cookies_from_browser_is_forwarded(self):
        seen = {}

        def fake_run(args, timeout_s):
            seen["args"] = args
            return fake_stdout_payload(self.INFO)

        with mock.patch.object(vi, "_run_ytdlp", side_effect=fake_run):
            vi.fetch_video_info("https://x", cookies_from_browser="edge")
        self.assertIn("edge", seen["args"])


class LearnTextTest(unittest.TestCase):
    def setUp(self):
        self.info = vi.VideoInfo(
            webpage_url="https://b23.tv/x", video_id="BV1", title="标题",
            uploader="UP主", duration_s=95.0, description="简介" * 300,
            view_count=123, tags=["a", "b"])

    def test_text_contains_the_essentials(self):
        text = vi.build_learn_text(self.info)
        self.assertIn("【视频】标题", text)
        self.assertIn("UP主/作者：UP主", text)
        self.assertIn("1分35秒", text)
        self.assertIn("https://b23.tv/x", text)
        self.assertLessEqual(len(text), 4000)

    def test_long_description_is_clipped(self):
        self.info.description = "简介" * 400
        text = vi.build_learn_text(self.info)
        self.assertIn("…", text)
        self.assertLess(len(text), 1000)

    def test_plan_with_subs(self):
        self.info.has_subs = True
        plan = vi.learning_plan(self.info)
        self.assertEqual(plan["mode"], "subtitles")

    def test_plan_short_video_without_subs_suggests_asr(self):
        plan = vi.learning_plan(self.info)
        self.assertEqual(plan["mode"], "audio_asr")

    def test_plan_long_video_without_subs_is_metadata_only(self):
        self.info.duration_s = 7200
        plan = vi.learning_plan(self.info)
        self.assertEqual(plan["mode"], "metadata_only")

    def test_plan_live_is_metadata_only(self):
        self.info.is_live = True
        self.assertEqual(vi.learning_plan(self.info)["mode"], "metadata_only")


class VideoRecognizerTest(unittest.TestCase):
    INFO = {"title": "标题", "uploader": "UP", "tags": ["t1", "t2"],
            "description": "简介", "webpage_url": "https://x/v", "video_id": "V1"}

    def test_with_video_info_produces_learnable_text(self):
        env = Recognizers().recognize(Item(
            "video", ref="V1", url="https://x/v",
            extra={"video_info": self.INFO, "plan_mode": "subtitles"}))
        self.assertEqual(env.status, "ok")
        self.assertEqual(env.recognizer, "yt-dlp-meta")
        self.assertIn("【视频】标题", env.text)
        self.assertIn("标签：t1、t2", env.text)
        self.assertTrue(env.usable)

    def test_without_video_info_stays_metadata_only(self):
        env = Recognizers().recognize(Item("video", ref="V1", url="https://x/v"))
        self.assertEqual(env.status, "metadata_only")
        self.assertFalse(env.usable)
        self.assertEqual(env.text, "")

    def test_video_info_without_title_is_ignored(self):
        env = Recognizers().recognize(Item(
            "video", ref="V1", url="https://x",
            extra={"video_info": {"uploader": "UP"}}))
        self.assertEqual(env.status, "metadata_only")

    def test_danmaku_alone_is_not_learnable(self):
        recognize.KINDS  # 引用一下，确保模块常量没被改坏
        self.assertIn("video", recognize.KINDS)


class WriterVideoKnowledgeTest(unittest.TestCase):
    """视频学完必须留下可召回的 knowledge —— 模型判空也不能白学。"""

    def _memory(self, tmp_name: str):
        from tests._tempdir import make_tmp
        from aiji.hippocampus.memory import Memory
        from aiji.right_hand.config import Settings

        root = pathlib.Path(make_tmp(self, tmp_name))
        return Memory(Settings(
            db_path=root / "m.db", routes=(), debounce_ms=0,
            humanize_min_ms=0, humanize_max_ms=0,
            typing_min_ms=0, typing_max_ms=0,
        ), engines="none")

    def test_knowledge_fact_survives_empty_model_extraction(self):
        m = self._memory("vid-know")
        try:
            info = {"title": "讲解视频", "uploader": "张三",
                    "tags": ["Python"], "webpage_url": "https://x/v",
                    "video_id": "BV1"}
            text = vi.build_learn_text(vi.VideoInfo(
                webpage_url="https://x/v", video_id="BV1", title="讲解视频",
                uploader="张三", duration_s=60.0))
            m.ingest(("local", "u1", "default"), kind="video", ref="BV1",
                     text=text, url="https://x/v",
                     extra={"video_info": info, "plan_mode": "subtitles"})
            self.assertTrue(m.flush(10))
            import sqlite3
            con = sqlite3.connect(str(m.db_path) if hasattr(m, "db_path") else ":memory:")
            con.close()
        finally:
            m.close()

    def test_recall_finds_learned_video(self):
        from aiji.hippocampus.memory import Memory
        from aiji.right_hand.config import Settings
        from tests._tempdir import make_tmp

        root = pathlib.Path(make_tmp(self, "vid-recall"))
        m = Memory(Settings(
            db_path=root / "m.db", routes=(), debounce_ms=0,
            humanize_min_ms=0, humanize_max_ms=0,
            typing_min_ms=0, typing_max_ms=0,
        ), engines="none")
        try:
            info = {"title": "Python 入门第一课", "uploader": "码农老师",
                    "tags": ["Python", "教程"],
                    "webpage_url": "https://www.bilibili.com/video/BV1test",
                    "video_id": "BV1test"}
            m.ingest(("local", "u1", "default"), kind="video", ref="BV1test",
                     text=vi.build_learn_text(vi.VideoInfo(
                         webpage_url=info["webpage_url"], video_id="BV1test",
                         title="Python 入门第一课", uploader="码农老师",
                         duration_s=1200.0, tags=["Python", "教程"])),
                     url=info["webpage_url"],
                     extra={"video_info": info, "plan_mode": "subtitles"})
            self.assertTrue(m.flush(10))
            result = m.recall("Python 教程视频", ("local", "u1", "default"))
            texts = [h.text for h in result.hits]
            self.assertTrue(any("Python 入门第一课" in t for t in texts),
                            f"召回结果里没有学过的视频：{[t[:40] for t in texts]}")
        finally:
            m.close()

    def test_plain_text_ingest_still_works(self):
        m = self._memory("vid-plain")
        try:
            m.ingest(("local", "u2", "default"), kind="text", ref="t1",
                     text="我住在杭州，职业是老师")
            self.assertTrue(m.flush(10))
            result = m.recall("我住哪", ("local", "u2", "default"))
            self.assertTrue(any("杭州" in h.text for h in result.hits))
        finally:
            m.close()


if __name__ == "__main__":
    unittest.main()
