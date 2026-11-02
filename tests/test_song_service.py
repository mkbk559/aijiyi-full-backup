"""Song queue and safety boundary regressions."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aiji.heart.song_service import SongError, SongService
from aiji.heart.voice_service import VoiceRegistry


def _service(root: Path) -> SongService:
    voices = VoiceRegistry(root / "voices")
    profile_dir = root / "voices" / "dafeiyu"
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.json").write_text(json.dumps({
        "name": "Dafeiyu",
        "provider": "edge",
        "postprocess": {"command": ["converter", "{input}", "{output}"]},
    }), encoding="utf-8")
    return SongService(voices, data_root=root)


class SongServiceTest(unittest.TestCase):
    def test_clip_window_uses_middle_section_for_long_song(self):
        start, length = SongService._clip_window(300.0, 60)
        self.assertEqual(start, 90.0)
        self.assertEqual(length, 60.0)

    def test_clip_window_keeps_short_song_intact(self):
        self.assertEqual(
            SongService._clip_window(42.0, 60), (0.0, 42.0))

    def test_submit_requires_a_configured_voice_converter(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = SongService(
                VoiceRegistry(Path(tmp) / "voices"), data_root=tmp)
            with self.assertRaises(SongError) as caught:
                service.submit("晴天", profile_id="edge-default")
            self.assertEqual(
                caught.exception.code, "voice_converter_not_configured")

    def test_submit_queues_bounded_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = _service(Path(tmp))
            with mock.patch.object(service, "_ensure_worker", lambda: None):
                job = service.submit(
                    "晴天", profile_id="dafeiyu", pitch=99)
            self.assertEqual(job.status, "queued")
            self.assertEqual(job.pitch, 24)
            self.assertEqual(job.max_seconds, 60)
            self.assertEqual(service.get(job.id).source, "晴天")
            self.assertEqual(service.snapshot()["queue"], 1)

    def test_http_voice_converter_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            voices = VoiceRegistry(root / "voices")
            profile_dir = root / "voices" / "dafeiyu"
            profile_dir.mkdir(parents=True)
            (profile_dir / "profile.json").write_text(json.dumps({
                "name": "Dafeiyu",
                "provider": "edge",
                "postprocess": {
                    "url": "http://127.0.0.1:9880/convert",
                    "reference": "dafeiyu/reference.wav",
                },
            }), encoding="utf-8")
            service = SongService(voices, data_root=root)
            with mock.patch.object(service, "_ensure_worker", lambda: None):
                job = service.submit("晴天", profile_id="dafeiyu")
            self.assertEqual(job.status, "queued")

    def test_lookup_returns_share_ready_song_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = _service(Path(tmp))
            with mock.patch.object(
                service,
                "_search_netease",
                return_value=("186001", "晴天 - 周杰伦"),
            ):
                info = service.lookup("晴天")
            self.assertEqual(info, {
                "id": "186001",
                "title": "晴天 - 周杰伦",
                "url": "https://music.163.com/song?id=186001",
                "source": "netease",
            })

    def test_completed_job_survives_service_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = _service(root)
            output = service.cache_root / "abc123.mp3"
            output.write_bytes(b"audio")
            job = service.submit("晴天", profile_id="dafeiyu")
            job.status = "done"
            job.stage = "完成"
            job.output = str(output)
            service._persist(job)

            restarted = SongService(service.voices, data_root=root)
            loaded = restarted.get(job.id)
            self.assertEqual(loaded.status, "done")
            self.assertEqual(restarted.audio_path(job.id), output)

    def test_local_song_outside_music_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = _service(root)
            outside = root / "outside.wav"
            outside.write_bytes(b"not audio")
            with self.assertRaises(SongError) as caught:
                service._resolve_source(str(outside), root / "work")
            self.assertEqual(
                caught.exception.code, "local_path_forbidden")

    def test_private_song_url_is_rejected_before_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = _service(Path(tmp))
            with mock.patch.object(service, "_http_bytes") as fetch:
                with self.assertRaises(SongError) as caught:
                    service._download(
                        "http://127.0.0.1/music.mp3",
                        Path(tmp) / "song.mp3",
                    )
            self.assertEqual(caught.exception.code, "private_url")
            fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
