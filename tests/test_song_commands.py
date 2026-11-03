"""唱歌服务的 ffmpeg/separator 命令层与下载防护（`song_service.py` 62% → 全绿）。

不真跑 ffmpeg / separator / 下载：全部用假 `_run`、假 `_http_bytes`、
假 `voices.convert_audio`。重点：
- `_run` 的三种失败要转成 `SongError`（tool_missing / tool_timeout / tool_failed），
  并把 stderr 尾部带出来；
- `_download` 的三道闸（私网、太小、返回的是 HTML 页）；
- `_separate` 只认 "(vocals)" 和 "(instrumental)/(other)" 命名的产物，缺一个就报
  separation_failed；
- `_process` 全流程的**命令拼装**（对 ffmpeg 的两段调用、对 separator 的参数）
  以及产物缺失时的 empty_output。
"""
import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.heart import song_service as ss  # noqa: E402
from aiji.heart.song_service import SongError  # noqa: E402
from tests.test_song_queue import make_service  # noqa: E402


class ConverterVoices:
    """带 convert_audio 的假注册表：转换产物直接落一个 wav。"""

    def __init__(self, error=None):
        self.error = error
        self.converted = []

    def get(self, profile_id=""):  # noqa: ARG002
        if self.error:
            raise self.error
        return type("P", (), {"id": profile_id, "name": "假声线"})()

    def convert_audio(self, vocals, target, **kwargs):  # noqa: ARG002
        self.converted.append((vocals, target, kwargs))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"RIFF" + b"c" * 2048)


class RunTest(unittest.TestCase):
    def test_missing_executable(self):
        def boom(command, **kwargs):  # noqa: ARG001
            raise FileNotFoundError("no such file")

        with mock.patch.object(ss.subprocess, "run", side_effect=boom):
            with self.assertRaises(SongError) as ctx:
                ss.SongService._run(["gone.exe", "-x"], timeout=5)
        self.assertEqual(ctx.exception.code, "tool_missing")
        self.assertIn("gone.exe", ctx.exception.message)

    def test_timeout(self):
        def boom(command, **kwargs):  # noqa: ARG001
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1)

        with mock.patch.object(ss.subprocess, "run", side_effect=boom):
            with self.assertRaises(SongError) as ctx:
                ss.SongService._run(["ffmpeg"], timeout=1)
        self.assertEqual(ctx.exception.code, "tool_timeout")
        self.assertIn("timed out", ctx.exception.message)

    def test_nonzero_exit_carries_stderr_tail(self):
        result = mock.Mock(returncode=1, stdout="", stderr="E" * 3000 + "\n真实原因")
        with mock.patch.object(ss.subprocess, "run", return_value=result):
            with self.assertRaises(SongError) as ctx:
                ss.SongService._run(["ffmpeg", "-i"], timeout=5)
        self.assertEqual(ctx.exception.code, "tool_failed")
        self.assertIn("真实原因", ctx.exception.message)
        self.assertLessEqual(len(ctx.exception.message), 1100, "stderr 截尾到 1000")

    def test_env_is_merged_with_utf8(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured.update(kwargs)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(ss.subprocess, "run", side_effect=fake_run):
            ss.SongService._run(["x"], timeout=5, env={"EXTRA": "1"})
        self.assertEqual(captured["env"]["EXTRA"], "1")
        self.assertEqual(captured["env"]["PYTHONUTF8"], "1")


class DownloadGuardsTest(unittest.TestCase):
    def setUp(self):
        self.service = make_service(self)
        # _download 先做 SSRF 校验（会真 DNS）；这里只测下载的后几道闸，
        # 校验本身由 test_net_guard 覆盖。
        self.guard = mock.patch.object(
            ss, "validate_public_http_url",
            return_value=__import__("urllib.parse").parse.urlparse(
                "https://ok.example/a.mp3"))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_private_url_is_refused_without_fetching(self):
        self.guard.stop()  # 这个用例要测真实的 SSRF 校验，别用 no-op
        with mock.patch.object(self.service, "_http_bytes") as fetch:
            with self.assertRaises(SongError) as ctx:
                self.service._download("http://127.0.0.1/x", pathlib.Path("t.mp3"))
        self.assertEqual(ctx.exception.code, "private_url")
        fetch.assert_not_called()

    def test_tiny_body_is_rejected(self):
        with mock.patch.object(self.service, "_http_bytes", return_value=b"xx"):
            with self.assertRaises(SongError) as ctx:
                self.service._download("https://ok.example/a.mp3", pathlib.Path("t.mp3"))
        self.assertEqual(ctx.exception.code, "download_too_small")

    def test_html_page_is_rejected(self):
        with mock.patch.object(
                self.service, "_http_bytes",
                return_value=b"<html>" + b"p" * 5000):
            with self.assertRaises(SongError) as ctx:
                self.service._download("https://ok.example/a", pathlib.Path("t.mp3"))
        self.assertEqual(ctx.exception.code, "song_unavailable")

    def test_good_body_lands_on_disk(self):
        body = b"ID3" + b"a" * 8192
        target = pathlib.Path(make_tmp(self, "dl")) / "song.mp3"
        with mock.patch.object(self.service, "_http_bytes", return_value=body):
            out = self.service._download("https://ok.example/a.mp3", target)
        self.assertEqual(out, target)
        self.assertEqual(target.read_bytes(), body)


class HttpBytesTest(unittest.TestCase):
    def setUp(self):
        self.service = make_service(self)

    def _ctx(self, chunks, limit_mb=1):
        self.service.max_download_bytes = limit_mb * 1024 * 1024
        response = mock.Mock()
        response.__enter__ = lambda s: s
        response.__exit__ = lambda s, *a: False
        response.read = lambda size=-1: next(chunks, b"")
        return mock.patch.object(ss.net, "urlopen", return_value=response)

    def test_normal_stream_is_concatenated(self):
        with self._ctx(iter([b"ab", b"cd"])):
            self.assertEqual(
                self.service._http_bytes("https://x/y", headers={}, timeout=5), b"abcd")

    def test_oversize_is_aborted(self):
        with self._ctx(iter([b"x" * 1024 * 1024, b"y" * 1024])):
            with self.assertRaises(SongError) as ctx:
                self.service._http_bytes("https://x/y", headers={}, timeout=5)
        self.assertEqual(ctx.exception.code, "download_too_large")

    def test_http_error_is_wrapped(self):
        import urllib.error
        with mock.patch.object(
                ss.net, "urlopen",
                side_effect=urllib.error.HTTPError("u", 404, "nf", {}, None)):
            with self.assertRaises(SongError) as ctx:
                self.service._http_bytes("https://x/y", headers={}, timeout=5)
        self.assertEqual(ctx.exception.code, "download_failed")
        self.assertIn("404", ctx.exception.message)

    def test_generic_network_failure_is_wrapped(self):
        with mock.patch.object(ss.net, "urlopen", side_effect=OSError("no route")):
            with self.assertRaises(SongError) as ctx:
                self.service._http_bytes("https://x/y", headers={}, timeout=5)
        self.assertEqual(ctx.exception.code, "download_failed")
        self.assertIn("no route", ctx.exception.message)


class ProbeDurationTest(unittest.TestCase):
    def setUp(self):
        self.service = make_service(self)

    def test_valid_duration_is_parsed(self):
        result = mock.Mock(returncode=0, stdout="123.456\n")
        with mock.patch.object(ss.subprocess, "run", return_value=result):
            self.assertEqual(self.service._probe_duration(pathlib.Path("x.wav")), 123.456)

    def test_garbage_output_is_zero(self):
        result = mock.Mock(returncode=0, stdout="N/A")
        with mock.patch.object(ss.subprocess, "run", return_value=result):
            self.assertEqual(self.service._probe_duration(pathlib.Path("x.wav")), 0.0)

    def test_subprocess_failure_is_zero(self):
        with mock.patch.object(
                ss.subprocess, "run",
                side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=1)):
            self.assertEqual(self.service._probe_duration(pathlib.Path("x.wav")), 0.0)


class SeparateTest(unittest.TestCase):
    def setUp(self):
        self.service = make_service(self)

    def _make_wavs(self, work: pathlib.Path, names):
        out = work / "separated"
        paths = []
        for name in names:
            path = out / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"RIFF")
            paths.append(path)
        return out

    def test_vocals_and_instrumental_are_picked(self):
        work = pathlib.Path(make_tmp(self, "sep"))
        out = self._make_wavs(work, ["clip_(vocals).wav", "clip_(instrumental).wav"])
        with mock.patch.object(self.service, "_run"):
            vocals, instrumental = self.service._separate(work / "clip.wav", out)
        self.assertIn("vocals", vocals.name)
        self.assertIn("instrumental", instrumental.name)

    def test_other_stem_counts_as_instrumental(self):
        work = pathlib.Path(make_tmp(self, "sep"))
        out = self._make_wavs(work, ["clip_(vocals).wav", "clip_(other).wav"])
        with mock.patch.object(self.service, "_run"):
            _, instrumental = self.service._separate(work / "clip.wav", out)
        self.assertIn("other", instrumental.name)

    def test_missing_vocals_is_separation_failure(self):
        work = pathlib.Path(make_tmp(self, "sep"))
        out = self._make_wavs(work, ["clip_(instrumental).wav"])
        with mock.patch.object(self.service, "_run"):
            with self.assertRaises(SongError) as ctx:
                self.service._separate(work / "clip.wav", out)
        self.assertEqual(ctx.exception.code, "separation_failed")


class ProcessPipelineTest(unittest.TestCase):
    """用假 `_run` / 假 converter 驱动 `_process` 全流程，验证命令拼装。"""

    def _job(self, service, source="114514"):
        job = ss.SongJob(id="job1", source=source, profile_id="makabaka",
                         pitch=2, max_seconds=45)
        service._jobs[job.id] = job
        return job

    def test_full_pipeline_assembles_the_right_commands(self):
        service = make_service(self, voices=ConverterVoices())
        job = self._job(service)
        commands = []

        def fake_run(command, **kwargs):
            commands.append(list(command))
            target = pathlib.Path(command[-1])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"RIFF" + b"p" * 2048)
            return mock.Mock(returncode=0, stdout="", stderr="")

        # 探测时长 20 秒 → 截取窗口 (0, 20)
        with mock.patch.object(service, "_resolve_source",
                               return_value=(pathlib.Path("song.mp3"), "测试歌")), \
             mock.patch.object(service, "_probe_duration", return_value=20.0), \
             mock.patch.object(service, "_run", side_effect=fake_run), \
             mock.patch.object(service, "_separate",
                               return_value=(pathlib.Path("vocals.wav"),
                                             pathlib.Path("instr.wav"))), \
             mock.patch.object(service.voices, "convert_audio") as convert, \
             mock.patch.object(service, "_mix"), \
             mock.patch.object(service, "_prune_cache"):
            convert.return_value = None
            service._process(job)
        self.assertEqual(job.status, "done")
        self.assertEqual(job.stage, "完成")
        ffmpeg_calls = [c for c in commands if c and "ffmpeg" in c[0]]
        self.assertEqual(len(ffmpeg_calls), 2, "截取一段、成条一段，各一次 ffmpeg")
        clip, fade = ffmpeg_calls
        self.assertIn("-ss", clip)
        self.assertIn("pcm_s16le", clip)
        self.assertIn("-b:a", fade, "成条那段要压 128k")
        self.assertTrue(any("afade" in arg for arg in fade), "成条要有淡入淡出")
        convert.assert_called_once()

    def test_unreadable_audio_aborts_before_any_command(self):
        service = make_service(self, voices=ConverterVoices())
        job = self._job(service, source=str(
            pathlib.Path(make_tmp(self, "proc")) / "local.wav"))
        with mock.patch.object(service, "_resolve_source",
                               return_value=(pathlib.Path("x.wav"), "x")), \
             mock.patch.object(service, "_probe_duration", return_value=0.0), \
             mock.patch.object(service, "_run") as run:
            with self.assertRaises(SongError) as ctx:
                service._process(job)
        self.assertEqual(ctx.exception.code, "invalid_audio")
        run.assert_not_called()

    def test_empty_final_output_is_reported(self):
        service = make_service(self, voices=ConverterVoices())
        job = self._job(service)

        def fake_run(command, **kwargs):  # noqa: ARG001
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(service, "_resolve_source",
                               return_value=(pathlib.Path("song.mp3"), "x")), \
             mock.patch.object(service, "_probe_duration", return_value=20.0), \
             mock.patch.object(service, "_run", side_effect=fake_run), \
             mock.patch.object(service, "_separate",
                               return_value=(pathlib.Path("v.wav"),
                                             pathlib.Path("i.wav"))), \
             mock.patch.object(service.voices, "convert_audio"), \
             mock.patch.object(service, "_mix"):
            # 不写产物文件 → 最后一道 ffmpeg 之后 output 不存在
            with self.assertRaises(SongError) as ctx:
                service._process(job)
        self.assertEqual(ctx.exception.code, "empty_output")



class MixTest(unittest.TestCase):
    def test_mix_assembles_amix_filter(self):
        service = make_service(self)
        commands = []

        def fake_run(command, **kwargs):
            commands.append(list(command))
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(service, "_run", side_effect=fake_run):
            service._mix(pathlib.Path("v.wav"), pathlib.Path("i.wav"),
                         pathlib.Path("out.wav"))
        cmd = commands[0]
        self.assertIn("-filter_complex", cmd)
        joined = " ".join(cmd)
        self.assertIn("amix=inputs=2", joined)
        self.assertIn("volume=1.0", joined, "人声满、伴奏 0.8")
        self.assertIn("volume=0.8", joined)


class SearchNeteaseTest(unittest.TestCase):
    def setUp(self):
        self.service = make_service(self)

    def test_song_and_artist_are_joined(self):
        payload = {"result": {"songs": [
            {"id": 123, "name": " 稻香 ",
             "artists": [{"name": "周杰伦"}, {"name": ""}]},
        ]}}
        with mock.patch.object(self.service, "_http_bytes",
                               return_value=json.dumps(payload).encode("utf-8")):
            self.assertEqual(self.service._search_netease("稻香"),
                             ("123", "稻香 - 周杰伦"))

    def test_song_without_artist(self):
        payload = {"result": {"songs": [{"id": 1, "name": "无伴奏", "artists": []}]}}
        with mock.patch.object(self.service, "_http_bytes",
                               return_value=json.dumps(payload).encode("utf-8")):
            self.assertEqual(self.service._search_netease("无伴奏"), ("1", "无伴奏"))

    def test_no_songs_is_not_found(self):
        with mock.patch.object(self.service, "_http_bytes",
                               return_value=b'{"result": {"songs": []}}'):
            with self.assertRaises(SongError) as ctx:
                self.service._search_netease("没有")
        self.assertEqual(ctx.exception.code, "song_not_found")

    def test_non_json_body_is_not_found_not_crash(self):
        with mock.patch.object(self.service, "_http_bytes",
                               return_value=b"<html>rate limit</html>"):
            with self.assertRaises(SongError) as ctx:
                self.service._search_netease("x")
        self.assertEqual(ctx.exception.code, "song_not_found")


class ResolveSourceTest(unittest.TestCase):
    def setUp(self):
        self.service = make_service(self)
        self.work = pathlib.Path(make_tmp(self, "resolve"))

    def test_http_url_downloads_and_names_by_stem(self):
        with mock.patch.object(self.service, "_download",
                               return_value=self.work / "song.source") as dl:
            src, title = self.service._resolve_source(
                "https://ok.example/mp3/12345", self.work)
        self.assertEqual(title, "12345")
        dl.assert_called_once()

    def test_numeric_id_needs_no_search(self):
        with mock.patch.object(self.service, "_download",
                               return_value=self.work / "song.mp3") as dl,              mock.patch.object(self.service, "_search_netease") as search:
            src, title = self.service._resolve_source("114514", self.work)
        self.assertEqual(title, "网易云 114514")
        search.assert_not_called()
        dl.assert_called_once()

    def test_local_file_under_music_root_is_accepted(self):
        music = self.service.music_root
        music.mkdir(parents=True, exist_ok=True)
        song = music / "我的歌.wav"
        song.write_bytes(b"RIFF")
        src, title = self.service._resolve_source(str(song), self.work)
        self.assertEqual(src, song.resolve())
        self.assertEqual(title, "我的歌")


class SeparatorFallbackTest(unittest.TestCase):
    def test_which_fallback_is_used_when_not_a_file(self):
        service = make_service(self, separator="not-a-real-sep.exe")
        work = pathlib.Path(make_tmp(self, "sepfb"))
        out = work / "separated"
        out.mkdir(parents=True, exist_ok=True)
        (out / "clip_(vocals).wav").write_bytes(b"RIFF")
        (out / "clip_(instrumental).wav").write_bytes(b"RIFF")
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = list(command)

        with mock.patch.object(ss.shutil, "which", return_value="/found/sep.exe"), \
             mock.patch.object(service, "_run", side_effect=fake_run):
            service._separate(work / "clip.wav", out)
        self.assertEqual(captured["command"][0], "/found/sep.exe")


if __name__ == "__main__":
    unittest.main()
