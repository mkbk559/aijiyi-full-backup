"""唱歌队列的状态机与"真会丢任务"的路径（补充 `test_song_service.py`）。

不接入真 separator / ffmpeg（跑一次几十秒且吃 CPU）：
- 队列/状态机用假 profile + 假 `_process`；
- 重启恢复、成品被清掉、id 与文件名不符、坏 JSON 这些直接用 jobs/*.json 构造；
- `_clip_window` 边界值、缓存清理、快照落盘失败后不拖垮队列。

原先 `song_service.py` 覆盖率 47%，漏掉的正是这些分支。
"""
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.heart import song_service as ss  # noqa: E402
from aiji.heart.voice_service import VoiceError, VoiceProfile  # noqa: E402


class FakeVoices:
    """只实现 song_service 用到的那点接口。"""

    def __init__(self, profile=None, error=None):
        self.profile = profile or VoiceProfile(
            id="makabaka", name="玛卡巴卡",
            postprocess={"command": "python fake_rvc.py", "reference": "ref.wav"})
        self.error = error

    def get(self, profile_id=""):  # noqa: ARG002
        if self.error:
            raise self.error
        return self.profile


def make_service(testcase, voices=None, **kwargs) -> ss.SongService:
    root = make_tmp(testcase, "song-queue")
    return ss.SongService(voices or FakeVoices(), data_root=root, **kwargs)


def quiet_worker(testcase):
    """submit 会拉起 worker；按住 _process，避免真跑 ffmpeg/separator。"""
    return mock.patch.object(ss.SongService, "_process",
                             lambda self, job: None)


def drain(service):
    """等 worker 收工。worker 处理完队列会把自己置 None（有竞态），所以判空。"""
    worker = getattr(service, "_worker", None)
    if worker is not None:
        worker.join(timeout=5)


class SubmitTest(unittest.TestCase):
    def test_submit_enqueues_and_persists(self):
        with quiet_worker(self):
            service = make_service(self)
            job = service.submit("https://music.163.com/song?id=123",
                                 max_seconds=999, pitch=99)
            drain(service)
        self.assertEqual(job.pitch, 24, "pitch 夹到 ±24")
        self.assertEqual(job.max_seconds, 180, "max_seconds 夹到 20..180")
        self.assertTrue((service.jobs_root / f"{job.id}.json").is_file())

    def test_empty_source_is_rejected(self):
        service = make_service(self)
        with self.assertRaises(ss.SongError) as ctx:
            service.submit("   ")
        self.assertEqual(ctx.exception.code, "source_required")

    def test_profile_error_is_mapped(self):
        service = make_service(
            self, FakeVoices(error=VoiceError("profile_not_found", "没这个")))
        with self.assertRaises(ss.SongError) as ctx:
            service.submit("123")
        self.assertEqual(ctx.exception.code, "profile_not_found")

    def test_profile_without_converter_is_rejected(self):
        service = make_service(self, FakeVoices(VoiceProfile(id="edge", name="默认")))
        with self.assertRaises(ss.SongError) as ctx:
            service.submit("123")
        self.assertEqual(ctx.exception.code, "voice_converter_not_configured")

    def test_queue_full_at_five(self):
        with quiet_worker(self):
            service = make_service(self)
            for i in range(5):
                service.submit(f"123{i}")
            with self.assertRaises(ss.SongError) as ctx:
                service.submit("999")
            drain(service)
        self.assertEqual(ctx.exception.code, "queue_full")


class LookupTest(unittest.TestCase):
    def test_numeric_id_needs_no_network(self):
        service = make_service(self)
        out = service.lookup("  123456  ")
        self.assertEqual(out["id"], "123456")
        self.assertEqual(out["source"], "netease")
        self.assertIn("id=123456", out["url"])

    def test_empty_query_is_rejected(self):
        service = make_service(self)
        with self.assertRaises(ss.SongError):
            service.lookup("")

    def test_keyword_search_uses_netease_result(self):
        service = make_service(self)
        with mock.patch.object(ss.SongService, "_search_netease",
                               return_value=("42", "某首歌")):
            out = service.lookup("某首歌")
        self.assertEqual((out["id"], out["title"]), ("42", "某首歌"))


class ClipWindowTest(unittest.TestCase):
    def test_max_seconds_is_clamped(self):
        self.assertEqual(ss.SongService._clip_window(1000.0, 5)[1], 20.0)
        self.assertEqual(ss.SongService._clip_window(1000.0, 999)[1], 180.0)

    def test_start_never_runs_past_the_end(self):
        start, length = ss.SongService._clip_window(70.0, 60)
        self.assertAlmostEqual(start + length, 70.0, places=6)
        self.assertGreaterEqual(start, 0.0)


class RestartRecoveryTest(unittest.TestCase):
    def _write_job(self, service, job: dict) -> None:
        (service.jobs_root / f"{job['id']}.json").write_text(
            json.dumps(job, ensure_ascii=False), encoding="utf-8")

    def test_queued_job_becomes_failed_on_restart(self):
        service = make_service(self)
        self._write_job(service, {"id": "abc", "source": "123",
                                  "profile_id": "makabaka", "pitch": 0,
                                  "max_seconds": 60,
                                  "status": "queued", "stage": "排队"})
        reloaded = ss.SongService(service.voices, data_root=service.root.parent)
        job = reloaded.get("abc")
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error_code, "interrupted")
        self.assertIn("restart", job.error)

    def test_done_job_loses_output(self):
        service = make_service(self)
        self._write_job(service, {"id": "done1", "source": "123",
                                  "profile_id": "makabaka", "pitch": 0,
                                  "max_seconds": 60, "status": "done",
                                  "output": str(service.root / "nope.wav")})
        reloaded = ss.SongService(service.voices, data_root=service.root.parent)
        self.assertEqual(reloaded.get("done1").error_code, "output_missing")

    def test_broken_job_file_is_ignored(self):
        service = make_service(self)
        (service.jobs_root / "bad.json").write_text("{不是 json", encoding="utf-8")
        reloaded = ss.SongService(service.voices, data_root=service.root.parent)
        with self.assertRaises(ss.SongError):
            reloaded.get("bad")

    def test_job_id_must_match_filename(self):
        # 文件名 other.json，里面写的 id 却是 mismatch → 目录被手工改过，判定不可信
        service = make_service(self)
        (service.jobs_root / "other.json").write_text(
            json.dumps({"id": "mismatch", "source": "1", "profile_id": "makabaka",
                        "pitch": 0, "max_seconds": 60, "status": "queued"}),
            encoding="utf-8")
        reloaded = ss.SongService(service.voices, data_root=service.root.parent)
        with self.assertRaises(ss.SongError):
            reloaded.get("mismatch")


class GetAndAudioPathTest(unittest.TestCase):
    def test_unknown_job(self):
        service = make_service(self)
        with self.assertRaises(ss.SongError) as ctx:
            service.get("nope")
        self.assertEqual(ctx.exception.code, "job_not_found")

    def test_job_not_ready(self):
        with quiet_worker(self):
            service = make_service(self)
            job = service.submit("123")
            drain(service)
        with self.assertRaises(ss.SongError) as ctx:
            service.audio_path(job.id)
        self.assertEqual(ctx.exception.code, "job_not_ready")

    def test_audio_path_outside_root_is_refused(self):
        with quiet_worker(self):
            service = make_service(self)
            job = service.get(service.submit("123").id)
            drain(service)
        job.status = "done"
        job.output = r"C:\Windows\win.ini"
        with self.assertRaises(ss.SongError) as ctx:
            service.audio_path(job.id)
        self.assertEqual(ctx.exception.code, "output_missing")

    def test_audio_path_returns_existing_file(self):
        with quiet_worker(self):
            service = make_service(self)
            out = service.root / "ok.wav"
            out.write_bytes(b"RIFF")
            job = service.get(service.submit("123").id)
            drain(service)
        job.status = "done"
        job.output = str(out)
        self.assertEqual(service.audio_path(job.id), out.resolve())


class SnapshotAndCacheTest(unittest.TestCase):
    def test_snapshot_shape(self):
        with quiet_worker(self):
            service = make_service(self)
            snap = service.snapshot()
            drain(service)
        self.assertIn("ready", snap)
        self.assertEqual(snap["jobs"], [])
        self.assertEqual(snap["queue"], 0)

    def test_ready_requires_separator_and_model(self):
        service = make_service(self, separator="definitely-not-a-separator-xyz")
        self.assertFalse(service.ready())

    def test_prune_cache_keeps_newest(self):
        service = make_service(self)
        for i in range(4):
            path = service.cache_root / f"{i}.mp3"
            path.write_bytes(b"x")
            os.utime(path, (1_000 + i, 1_000 + i))
        service._prune_cache(keep=2)
        self.assertEqual(len(list(service.cache_root.glob("*.mp3"))), 2)

    def test_persist_failure_does_not_raise(self):
        service = make_service(self)
        job = ss.SongJob(id="x", source="1", profile_id="p", pitch=0,
                         max_seconds=60)
        with mock.patch.object(pathlib.Path, "write_text",
                               side_effect=OSError("磁盘满")):
            service._persist(job)
        self.assertGreaterEqual(int(getattr(service, "_persist_errors", 0)), 1)


if __name__ == "__main__":
    unittest.main()
