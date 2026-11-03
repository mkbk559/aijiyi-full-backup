"""声线适配器与后处理适配器（`heart/voice_service.py` 的"失败要说清楚"契约）。

与 `tests/test_voice_service.py` 分工：那个文件测"声线选择/回退/各 provider 端到端"，
这个文件专门盯**适配器自身**——命令拼装、编码、上限、异常转译。
全部离线：edge-tts 用假模块，SAPI/ffmpeg/RVC 用假 `subprocess.run` 或假 `_fetch`。
"""
from __future__ import annotations

import array
import base64
import io
import json
import pathlib
import sys
import types
import unittest
import urllib.error
import wave
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from tests._tempdir import make_tmp  # noqa: E402
from aiji.heart import voice_service as vs  # noqa: E402
from aiji.heart.voice_service import (  # noqa: E402
    SynthesisResult,
    VoiceError,
    VoiceProfile,
    VoiceRegistry,
)
from aiji.right_hand import params  # noqa: E402


def registry(case, **kwargs) -> VoiceRegistry:
    return VoiceRegistry(make_tmp(case, "voice-adapters"), **kwargs)


def write_wav(path: pathlib.Path, *, seconds=1.0, rate=16000, peak=1000) -> pathlib.Path:
    frames = max(2, int(rate * seconds) // 2 * 2)
    samples = array.array("h", [peak, -peak] * (frames // 2))
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(samples.tobytes())
    return path


class HelperTest(unittest.TestCase):
    def test_safe_id(self):
        self.assertEqual(vs._safe_id(""), "default")
        self.assertEqual(vs._safe_id("我的 声线!!"), vs._safe_id("我的 声线!!"))
        self.assertEqual(vs._safe_id("a/b\\c"), "a-b-c")
        self.assertEqual(len(vs._safe_id("x" * 200)), 80)
        self.assertEqual(vs._safe_id("-.-abc-.-"), "abc")

    def test_clamp(self):
        self.assertEqual(vs._clamp("abc", 0, 10, 3), 3)
        self.assertEqual(vs._clamp("99", 0, 10), 10)
        self.assertEqual(vs._clamp(None, 0, 10, 5), 5)

    def test_bool(self):
        self.assertTrue(vs._bool("YES"))
        self.assertTrue(vs._bool("on"))
        self.assertFalse(vs._bool("0"))
        self.assertTrue(vs._bool(None, True))
        self.assertFalse(vs._bool(None))

    def test_atomic_json_leaves_no_temp(self):
        tmp = pathlib.Path(make_tmp(self, "atomic"))
        target = tmp / "sub" / "a.json"
        vs._atomic_json(target, {"k": "值"})
        self.assertEqual(json.loads(target.read_text("utf-8")), {"k": "值"})
        vs._atomic_json(target, {"k": "新"})
        self.assertEqual(json.loads(target.read_text("utf-8")), {"k": "新"})
        self.assertEqual(list((tmp / "sub").glob("*.tmp")), [])

    def test_reference_bytes_is_cached(self):
        tmp = pathlib.Path(make_tmp(self, "refcache"))
        vs._REFERENCE_CACHE.clear()
        first = tmp / "a.wav"
        first.write_bytes(b"AAAA")
        self.assertEqual(vs._reference_bytes(first), b"AAAA")
        self.assertEqual(vs._reference_bytes(first), b"AAAA")
        self.assertEqual(len(vs._REFERENCE_CACHE), 1)
        with self.assertRaises(OSError):
            vs._reference_bytes(tmp / "missing.wav")
        vs._REFERENCE_CACHE.clear()


class ProfileTest(unittest.TestCase):
    def test_from_dict_clamps_everything(self):
        profile = VoiceProfile.from_dict({
            "id": "我的声线", "name": "", "provider": "EDGE",
            "rate": 999, "pitch": -999, "volume": "abc",
            "timeout_s": 99999, "prompt_lang": "xx", "text_lang": "yy",
            "extra": "不是 dict", "postprocess": [1, 2],
        })
        self.assertEqual(profile.name, "未命名声线")
        self.assertEqual(profile.provider, "edge")
        self.assertEqual((profile.rate, profile.pitch, profile.volume), (100, -24, 0))
        self.assertEqual(profile.timeout_s, 900)
        self.assertEqual(profile.prompt_lang, "zh", "非法语言回 zh")
        self.assertEqual(profile.text_lang, "zh")
        self.assertEqual(profile.extra, {})
        self.assertEqual(profile.postprocess, {})

    def test_timeout_floor_is_five(self):
        self.assertEqual(VoiceProfile.from_dict({"timeout_s": 0}).timeout_s, 5)

    def test_unsupported_provider_is_rejected(self):
        with self.assertRaises(VoiceError) as ctx:
            VoiceProfile.from_dict({"provider": "elevenlabs"})
        self.assertEqual(ctx.exception.code, "unsupported_provider")

    def test_public_never_leaks_api_key(self):
        profile = VoiceProfile(id="a", name="A", endpoint="http://x",
                               api_key="secret-key", reference_audio="ref.wav")
        public = profile.public(active=True)
        self.assertNotIn("secret-key", json.dumps(public, ensure_ascii=False))
        self.assertTrue(public["has_reference"])
        self.assertIs(public.get("active"), True)

    def test_synthesis_result_carries_path(self):
        result = SynthesisResult(path=pathlib.Path("a.wav"), profile_id="p",
                                 provider="edge", took_ms=1.0)
        self.assertEqual(result.profile_id, "p")


class ProviderReadyTest(unittest.TestCase):
    def setUp(self):
        self.reg = registry(self)

    def test_edge_needs_the_package(self):
        with mock.patch.dict(sys.modules, {"edge_tts": None}):
            self.assertFalse(self.reg._provider_ready(self.reg._builtin))
        fake = types.SimpleNamespace(Communicate=lambda *a, **k: None)
        with mock.patch.dict(sys.modules, {"edge_tts": fake}):
            self.assertTrue(self.reg._provider_ready(self.reg._builtin))

    def test_http_needs_placeholder(self):
        profile = VoiceProfile(id="h", name="h", provider="http")
        self.assertFalse(self.reg._provider_ready(profile))
        profile.extra = {"url_template": "http://x/tts?t={text}"}
        self.assertTrue(self.reg._provider_ready(profile))

    def test_openai_needs_endpoint(self):
        profile = VoiceProfile(id="o", name="o", provider="openai")
        self.assertFalse(self.reg._provider_ready(profile))
        profile.endpoint = "http://x"
        self.assertTrue(self.reg._provider_ready(profile))

    def test_gptsovits_needs_reference_file(self):
        profile = VoiceProfile(id="g", name="g", provider="gptsovits",
                               endpoint="http://x", reference_audio="ref.wav")
        self.assertFalse(self.reg._provider_ready(profile))
        (self.reg.root / "ref.wav").write_bytes(b"RIFF")
        self.assertTrue(self.reg._provider_ready(profile))

    def test_cosyvoice_sft_vs_zero_shot(self):
        profile = VoiceProfile(id="c", name="c", provider="cosyvoice",
                               endpoint="", mode="sft")
        self.assertFalse(self.reg._provider_ready(profile), "没 endpoint 不可用")
        profile.endpoint = "http://x"
        self.assertTrue(self.reg._provider_ready(profile))
        profile.mode = "zero_shot"
        profile.reference_audio = ""
        self.assertFalse(self.reg._provider_ready(profile))

    def test_unknown_provider_is_never_ready(self):
        self.assertFalse(self.reg._provider_ready(
            VoiceProfile(id="u", name="u", provider="nope")))

    def test_ready_probes_postprocess_port(self):
        profile = VoiceProfile(id="p", name="p", provider="openai", endpoint="http://x",
                               postprocess={"url": "https://127.0.0.1:9/convert"})
        with mock.patch.object(vs.socket, "create_connection", side_effect=OSError("x")):
            self.assertFalse(self.reg._ready(profile), "换声服务连不上也当不可用")
        with mock.patch.object(vs.socket, "create_connection",
                              return_value=mock.MagicMock()) as probe:
            self.assertTrue(self.reg._ready(profile))
        self.assertEqual(probe.call_args.args[0][1], 9, "端口来自 url")

    def test_has_local_reference(self):
        self.assertFalse(self.reg._has_local_reference(""))
        self.assertFalse(self.reg._has_local_reference("nope.wav"))
        (self.reg.root / "ref.wav").write_bytes(b"RIFF")
        self.assertTrue(self.reg._has_local_reference("ref.wav"))


class RegistryFilesTest(unittest.TestCase):
    def test_non_dict_manifest_is_skipped(self):
        reg = registry(self)
        (reg.root / "listy").mkdir()
        (reg.root / "listy" / "profile.json").write_text("[]", encoding="utf-8")
        self.assertEqual([p.id for p in reg.profiles()], ["edge-default"])

    def test_snapshot_reports_ffmpeg_flag(self):
        reg = registry(self, ffmpeg=str(pathlib.Path(make_tmp(self, "ff")) / "none"))
        self.assertFalse(reg.snapshot()["ffmpeg"])
        self.assertIn("last_fallback", reg.snapshot())
        self.assertIn("last_skipped", reg.snapshot())


class EdgeAdapterTest(unittest.TestCase):
    def setUp(self):
        self.reg = registry(self)
        self.out = pathlib.Path(make_tmp(self, "edge-out")) / "o.wav"

    def test_missing_package_is_reported(self):
        with mock.patch.dict(sys.modules, {"edge_tts": None}):
            with self.assertRaises(VoiceError) as ctx:
                self.reg._edge("你好", self.out, self.reg._builtin)
        self.assertEqual(ctx.exception.code, "provider_unavailable")

    def test_mp3_is_moved_onto_output(self):
        class Communicate:
            def __init__(self, text, voice, **kwargs):
                self.kwargs = kwargs

            async def save(self, path):
                pathlib.Path(path).write_bytes(b"ID3" + b"m" * 300)

        fake = types.SimpleNamespace(Communicate=Communicate)
        with mock.patch.dict(sys.modules, {"edge_tts": fake}):
            self.reg._edge("你好", self.out, self.reg._builtin)
        self.assertEqual(self.out.read_bytes()[:3], b"ID3")

    def test_no_audio_is_empty_audio(self):
        class Communicate:
            def __init__(self, *a, **k):
                pass

            async def save(self, path):
                return None

        with mock.patch.dict(sys.modules, {"edge_tts": types.SimpleNamespace(
                Communicate=Communicate)}):
            with self.assertRaises(VoiceError) as ctx:
                self.reg._edge("你好", self.out, self.reg._builtin)
        self.assertEqual(ctx.exception.code, "empty_audio")


class SapiAdapterTest(unittest.TestCase):
    def setUp(self):
        self.reg = registry(self)
        self.out = pathlib.Path(make_tmp(self, "sapi-out")) / "o.wav"

    def test_command_shape(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = list(command)
            pathlib.Path(command[command.index("-Output") + 1]).write_bytes(b"RIFF" + b"s" * 200)
            return mock.Mock(returncode=0, stderr=b"", stdout=b"")

        with mock.patch.object(vs.subprocess, "run", side_effect=fake_run):
            self.reg._sapi("你好", self.out, self.reg._builtin)
        command = captured["command"]
        self.assertEqual(command[0], "powershell.exe")
        self.assertIn("-File", command)
        self.assertIn("-Text", command)
        self.assertTrue(self.out.is_file())

    def test_nonzero_exit_carries_stderr(self):
        result = mock.Mock(returncode=1, stderr="没有这个语音包".encode("utf-8"), stdout=b"")
        with mock.patch.object(vs.subprocess, "run", return_value=result):
            with self.assertRaises(VoiceError) as ctx:
                self.reg._sapi("你好", self.out, self.reg._builtin)
        self.assertEqual(ctx.exception.code, "provider_failed")
        self.assertIn("没有这个语音包", ctx.exception.message)

    def test_spawn_exception_is_wrapped(self):
        with mock.patch.object(vs.subprocess, "run", side_effect=OSError("spawn 失败")):
            with self.assertRaises(VoiceError) as ctx:
                self.reg._sapi("你好", self.out, self.reg._builtin)
        self.assertEqual(ctx.exception.code, "provider_failed")

    def test_zero_exit_without_file_still_fails(self):
        result = mock.Mock(returncode=0, stderr=b"", stdout=b"")
        with mock.patch.object(vs.subprocess, "run", return_value=result):
            with self.assertRaises(VoiceError):
                self.reg._sapi("你好", self.out, self.reg._builtin)


class HttpAdapterTest(unittest.TestCase):
    def setUp(self):
        self.reg = registry(self)
        self.out = pathlib.Path(make_tmp(self, "http-out")) / "o.wav"

    def test_gptsovits_requires_endpoint(self):
        with self.assertRaises(VoiceError) as ctx:
            self.reg._gptsovits("你好", self.out,
                                VoiceProfile(id="g", name="g", provider="gptsovits"))
        self.assertEqual(ctx.exception.code, "not_configured")

    def test_gptsovits_sends_bearer_when_key_present(self):
        profile = VoiceProfile(id="g", name="g", provider="gptsovits",
                               endpoint="http://x", api_key="k")
        with mock.patch.object(self.reg, "_fetch", return_value=b"RIFF" + b"g" * 200) as fetch:
            self.reg._gptsovits("你好", self.out, profile)
        self.assertEqual(fetch.call_args.kwargs["headers"]["Authorization"], "Bearer k")

    def test_cosyvoice_requires_endpoint(self):
        with self.assertRaises(VoiceError) as ctx:
            self.reg._cosyvoice("你好", self.out,
                                VoiceProfile(id="c", name="c", provider="cosyvoice"))
        self.assertEqual(ctx.exception.code, "not_configured")

    def test_cosyvoice_instruct_picks_instruct_endpoint(self):
        profile = VoiceProfile(id="c", name="c", provider="cosyvoice",
                               endpoint="http://x", mode="sft", instruct="温柔一点")
        with mock.patch.object(self.reg, "_fetch", return_value=b"RIFF" + b"j" * 200) as fetch:
            self.reg._cosyvoice("你好", self.out, profile)
        self.assertTrue(fetch.call_args.args[0].endswith("/inference_instruct"))
        self.assertIn(b"instruct_text", fetch.call_args.kwargs["data"])

    def test_cosyvoice_sft_without_instruct(self):
        profile = VoiceProfile(id="c", name="c", provider="cosyvoice",
                               endpoint="http://x", mode="sft")
        with mock.patch.object(self.reg, "_fetch", return_value=b"RIFF" + b"k" * 200) as fetch:
            self.reg._cosyvoice("你好", self.out, profile)
        self.assertTrue(fetch.call_args.args[0].endswith("/inference_sft"))
        self.assertEqual(fetch.call_args.kwargs["headers"]["Content-Type"],
                         "application/x-www-form-urlencoded")

    def test_openai_requires_endpoint(self):
        with self.assertRaises(VoiceError) as ctx:
            self.reg._openai("你好", self.out,
                             VoiceProfile(id="o", name="o", provider="openai"))
        self.assertEqual(ctx.exception.code, "not_configured")

    def test_openai_endpoint_normalisation(self):
        cases = {
            "http://x/audio/speech": "http://x/audio/speech",
            "http://x/v1": "http://x/v1/audio/speech",
            "http://x": "http://x/v1/audio/speech",
        }
        for endpoint, expected in cases.items():
            profile = VoiceProfile(id="o", name="o", provider="openai",
                                   endpoint=endpoint, api_key="k")
            with mock.patch.object(self.reg, "_fetch",
                                   return_value=b"ID3" + b"l" * 200) as fetch:
                self.reg._openai("你好", self.out, profile)
            self.assertEqual(fetch.call_args.args[0], expected, endpoint)
            self.assertEqual(fetch.call_args.kwargs["headers"]["Authorization"], "Bearer k")

    def test_http_template_requires_placeholder(self):
        with self.assertRaises(VoiceError) as ctx:
            self.reg._http("你好", self.out, VoiceProfile(id="h", name="h", provider="http"))
        self.assertEqual(ctx.exception.code, "not_configured")

    def test_http_template_url_encodes_text(self):
        profile = VoiceProfile(id="h", name="h", provider="http",
                               extra={"url_template": "http://x/tts?t={text}"})
        with mock.patch.object(self.reg, "_fetch", return_value=b"ID3" + b"m" * 200) as fetch:
            self.reg._http("你好 世界", self.out, profile)
        url = fetch.call_args.args[0]
        self.assertNotIn(" ", url, "文本必须 URL 编码")
        self.assertIn("%E4%BD%A0%E5%A5%BD", url)


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.reg = registry(self)

    def test_http_error_keeps_body_head(self):
        error = urllib.error.HTTPError(
            "http://x", 500, "boom", {},
            io.BytesIO("模型没加载".encode("utf-8")))
        with mock.patch.object(vs.net, "urlopen", side_effect=error):
            with self.assertRaises(VoiceError) as ctx:
                self.reg._fetch("http://x", method="GET", timeout_s=5)
        self.assertEqual(ctx.exception.code, "provider_http_error")
        self.assertIn("500", ctx.exception.message)
        self.assertIn("模型没加载", ctx.exception.message)

    def test_network_error_is_unreachable(self):
        with mock.patch.object(vs.net, "urlopen", side_effect=OSError("refused")):
            with self.assertRaises(VoiceError) as ctx:
                self.reg._fetch("http://x", method="GET", timeout_s=5)
        self.assertEqual(ctx.exception.code, "provider_unreachable")

    def test_oversize_response_is_rejected(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"x" * (24 * 1024 * 1024 + 1)
        with mock.patch.object(vs.net, "urlopen", return_value=response):
            with self.assertRaises(VoiceError) as ctx:
                self.reg._fetch("http://x", method="GET", timeout_s=5)
        self.assertEqual(ctx.exception.code, "audio_too_large")


class AudioHelperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(make_tmp(self, "audio-helpers"))

    def test_audio_seconds(self):
        path = write_wav(self.tmp / "a.wav", seconds=2.0, rate=8000)
        self.assertAlmostEqual(VoiceRegistry._audio_seconds(path), 2.0, places=2)

    def test_audio_seconds_none_when_rate_is_zero(self):
        reader = mock.MagicMock()
        reader.__enter__.return_value = reader
        reader.getframerate.return_value = 0
        with mock.patch.object(wave, "open", return_value=reader):
            self.assertIsNone(VoiceRegistry._audio_seconds(pathlib.Path("x.wav")))

    def test_audio_seconds_none_for_garbage(self):
        path = self.tmp / "bad.wav"
        path.write_bytes(b"not a wav")
        with self.assertLogs("aiji.heart.voice_service", level="WARNING"):
            self.assertIsNone(VoiceRegistry._audio_seconds(path))

    def test_limit_peak_scales_clipped_audio(self):
        path = write_wav(self.tmp / "clip.wav", seconds=0.2, rate=8000, peak=32767)
        VoiceRegistry._limit_peak(path)
        with wave.open(str(path), "rb") as reader:
            samples = array.array("h")
            samples.frombytes(reader.readframes(reader.getnframes()))
        self.assertLessEqual(max(max(samples), -min(samples)), int(0.95 * 32767) + 1)

    def test_limit_peak_leaves_quiet_and_garbage_alone(self):
        quiet = write_wav(self.tmp / "quiet.wav", seconds=0.2, rate=8000, peak=1000)
        before = quiet.read_bytes()
        VoiceRegistry._limit_peak(quiet)
        self.assertEqual(quiet.read_bytes(), before)
        junk = self.tmp / "x.wav"
        junk.write_bytes(b"garbage")
        VoiceRegistry._limit_peak(junk)
        self.assertEqual(junk.read_bytes(), b"garbage")

    def test_multipart_body(self):
        ref = self.tmp / "ref.wav"
        ref.write_bytes(b"RIFFDATA")
        body, content_type = VoiceRegistry._multipart(
            {"tts_text": "你好"}, file_field="prompt_wav", file_path=ref)
        self.assertIn(b'name="tts_text"', body)
        self.assertIn("你好".encode(), body)
        self.assertIn(b"RIFFDATA", body)
        self.assertIn("boundary=", content_type)
        self.assertTrue(body.endswith(b"--\r\n"))


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.reg = registry(self)
        self.tmp = pathlib.Path(make_tmp(self, "convert"))
        self.src = write_wav(self.tmp / "src.wav", seconds=0.5)

    def test_no_converter_is_rejected(self):
        with mock.patch.object(self.reg, "get", return_value=VoiceProfile(id="p", name="p")):
            with self.assertRaises(VoiceError) as ctx:
                self.reg.convert_audio(self.src, self.tmp / "o.wav", profile_id="p")
        self.assertEqual(ctx.exception.code, "converter_not_configured")

    def test_missing_input_is_rejected(self):
        profile = VoiceProfile(id="p", name="p", postprocess={"command": ["x"]})
        with mock.patch.object(self.reg, "get", return_value=profile):
            with self.assertRaises(VoiceError) as ctx:
                self.reg.convert_audio(self.tmp / "nope.wav", self.tmp / "o.wav",
                                       profile_id="p")
        self.assertEqual(ctx.exception.code, "input_missing")

    def test_pitch_override_and_copy(self):
        profile = VoiceProfile(id="p", name="p", postprocess={"command": ["x"]})
        converted = self.tmp / "converted.wav"
        converted.write_bytes(b"RIFF" + b"z" * 100)
        with mock.patch.object(self.reg, "get", return_value=profile), \
             mock.patch.object(self.reg, "_postprocess", return_value=converted) as post:
            out = self.reg.convert_audio(self.src, self.tmp / "final.wav",
                                         profile_id="p", pitch=99, mode="song")
        self.assertEqual(profile.pitch, 24, "pitch 也要夹在 ±24")
        self.assertEqual(post.call_args.kwargs["mode"], "song")
        self.assertTrue(out.is_file())


class OverBudgetTest(unittest.TestCase):
    def setUp(self):
        self.reg = registry(self)
        self.tmp = pathlib.Path(make_tmp(self, "budget"))

    def test_song_mode_never_skips(self):
        long = write_wav(self.tmp / "long.wav", seconds=20.0, rate=8000)
        profile = VoiceProfile(id="p", name="p", postprocess={"max_seconds": 3})
        self.assertIsNone(self.reg._over_budget(long, profile, "song"))

    def test_speech_over_budget_returns_seconds(self):
        long = write_wav(self.tmp / "long2.wav", seconds=5.0, rate=8000)
        profile = VoiceProfile(id="p", name="p", postprocess={"max_seconds": 2})
        self.assertAlmostEqual(self.reg._over_budget(long, profile, "speech"), 5.0, places=2)

    def test_bad_limit_falls_back_and_zero_cap_disables(self):
        path = write_wav(self.tmp / "x.wav", seconds=0.1, rate=8000)
        profile = VoiceProfile(id="p", name="p", postprocess={"max_seconds": "abc"})
        with mock.patch.object(params, "VOICE_MAX_CONVERT_SECONDS", 0):
            self.assertIsNone(self.reg._over_budget(path, profile, "speech"),
                              "cap<=0 表示不设限")


class PostprocessTest(unittest.TestCase):
    def setUp(self):
        self.reg = registry(self)
        self.tmp = pathlib.Path(make_tmp(self, "post"))
        self.src = write_wav(self.tmp / "src.wav", seconds=0.3)

    def test_command_must_be_a_list(self):
        profile = VoiceProfile(id="p", name="p", postprocess={"command": "conv.exe"})
        self.assertEqual(self.reg._postprocess(self.src, self.tmp, profile), self.src)

    def test_placeholders_are_substituted(self):
        profile = VoiceProfile(id="p", name="p", pitch=4, postprocess={
            "command": ["conv.exe", "--in", "{input}", "--out", "{output}",
                        "--pitch", "{pitch}", "--profile", "{profile}"]})
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = list(command)
            target = command[command.index("--out") + 1]
            pathlib.Path(target).write_bytes(b"RIFF" + b"w" * 100)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(vs.subprocess, "run", side_effect=fake_run):
            out = self.reg._postprocess(self.src, self.tmp, profile)
        command = captured["command"]
        self.assertEqual(command[0], "conv.exe")
        self.assertEqual(command[2], str(self.src), "{input} 要替换成真实路径")
        self.assertEqual(command[3], "--out")
        self.assertEqual(command[6], "4", "{pitch}")
        self.assertEqual(command[8], "p", "{profile}")
        self.assertTrue(out.is_file())

    def test_failure_carries_stderr(self):
        profile = VoiceProfile(id="p", name="p",
                               postprocess={"command": ["conv.exe", "{input}"]})
        result = mock.Mock(returncode=2, stdout="", stderr="CUDA 爆显存")
        with mock.patch.object(vs.subprocess, "run", return_value=result):
            with self.assertRaises(VoiceError) as ctx:
                self.reg._postprocess(self.src, self.tmp, profile)
        self.assertEqual(ctx.exception.code, "postprocess_failed")
        self.assertIn("CUDA", ctx.exception.message)

    def test_spawn_exception_is_wrapped(self):
        profile = VoiceProfile(id="p", name="p", postprocess={"command": ["conv.exe"]})
        with mock.patch.object(vs.subprocess, "run", side_effect=OSError("spawn")):
            with self.assertRaises(VoiceError) as ctx:
                self.reg._postprocess(self.src, self.tmp, profile)
        self.assertEqual(ctx.exception.code, "postprocess_failed")

    def test_over_budget_skips_without_running(self):
        long = write_wav(self.tmp / "long.wav", seconds=8.0, rate=8000)
        profile = VoiceProfile(id="p", name="p",
                               postprocess={"command": ["x"], "max_seconds": 2})
        with mock.patch.object(vs.subprocess, "run") as run:
            out = self.reg._postprocess(long, self.tmp, profile, mode="speech")
        self.assertEqual(out, long, "超预算返回原音")
        run.assert_not_called()
        self.assertEqual(self.reg.last_skipped["profile"], "p")
        self.assertAlmostEqual(self.reg.last_skipped["seconds"], 8.0, places=1)

    def test_http_postprocess_requires_reference(self):
        profile = VoiceProfile(id="p", name="p", postprocess={"url": "http://x/convert"})
        with self.assertRaises(VoiceError) as ctx:
            self.reg._postprocess_http(self.src, self.tmp, profile, "http://x/convert")
        self.assertEqual(ctx.exception.code, "reference_missing")

    def test_http_postprocess_sends_base64_pair_with_token(self):
        ref = self.reg.root / "ref.wav"
        ref.write_bytes(b"RIFFREF")
        profile = VoiceProfile(id="p", name="p", postprocess={
            "url": "http://x/convert", "reference": "ref.wav",
            "diffusion_steps": 7, "token": "tok"})
        with mock.patch.object(self.reg, "_fetch",
                               return_value=b"RIFF" + b"v" * 400) as fetch:
            out = self.reg._postprocess_http(self.src, self.tmp, profile, "http://x/convert")
        body = json.loads(fetch.call_args.kwargs["data"].decode("utf-8"))
        self.assertEqual(base64.b64decode(body["source_b64"]), self.src.read_bytes())
        self.assertEqual(base64.b64decode(body["target_b64"]), ref.read_bytes())
        self.assertEqual(body["diffusion_steps"], 7)
        self.assertEqual(fetch.call_args.kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertTrue(out.is_file())

    def test_http_postprocess_rejects_empty_audio(self):
        (self.reg.root / "ref.wav").write_bytes(b"RIFFREF")
        profile = VoiceProfile(id="p", name="p", postprocess={
            "url": "http://x/convert", "reference": "ref.wav"})
        with mock.patch.object(self.reg, "_fetch", return_value=b"xx"):
            with self.assertRaises(VoiceError) as ctx:
                self.reg._postprocess_http(self.src, self.tmp, profile, "http://x/convert")
        self.assertEqual(ctx.exception.code, "postprocess_failed")


class ToWavTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(make_tmp(self, "towav"))
        self.src = self.tmp / "a.mp3"
        self.src.write_bytes(b"ID3")

    def test_missing_ffmpeg(self):
        reg = registry(self, ffmpeg=str(self.tmp / "no-such-ffmpeg"))
        with mock.patch.object(vs.shutil, "which", return_value=None):
            with self.assertRaises(VoiceError) as ctx:
                reg._to_wav(self.src, self.tmp / "o.wav")
        self.assertEqual(ctx.exception.code, "ffmpeg_missing")

    def test_ffmpeg_failure_message(self):
        binary = self.tmp / "ffmpeg.exe"
        binary.write_bytes(b"")
        reg = registry(self, ffmpeg=str(binary))
        result = mock.Mock(returncode=1, stderr="could not open codec", stdout="")
        with mock.patch.object(vs.subprocess, "run", return_value=result):
            with self.assertRaises(VoiceError) as ctx:
                reg._to_wav(self.src, self.tmp / "o.wav")
        self.assertEqual(ctx.exception.code, "audio_convert_failed")
        self.assertIn("codec", ctx.exception.message)

    def test_ffmpeg_success_writes_output(self):
        binary = self.tmp / "ffmpeg2.exe"
        binary.write_bytes(b"")
        reg = registry(self, ffmpeg=str(binary))
        out = self.tmp / "o.wav"

        def fake_run(command, **kwargs):
            pathlib.Path(command[-1]).write_bytes(b"RIFF" + b"a" * 100)
            return mock.Mock(returncode=0, stderr="", stdout="")

        with mock.patch.object(vs.subprocess, "run", side_effect=fake_run):
            reg._to_wav(self.src, out)
        self.assertTrue(out.is_file())



class FetchSuccessTest(unittest.TestCase):
    def test_small_body_is_returned(self):
        reg = registry(self)
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"RIFF" + b"q" * 50
        with mock.patch.object(vs.net, "urlopen", return_value=response):
            self.assertEqual(reg._fetch("http://x", method="POST", timeout_s=5,
                                        data=b"{}", headers={"A": "b"}),
                             b"RIFF" + b"q" * 50)


class CleanTextTest(unittest.TestCase):
    def setUp(self):
        self.reg = registry(self)
        self.out = pathlib.Path(make_tmp(self, "clean")) / "o.wav"

    def test_control_chars_are_stripped_before_length_check(self):
        # 4000 个正常字 + 200 个控制字符：控制字符先被删掉，不该报太长
        text = "字" * 3000 + "\x00" * 200
        self.assertEqual(len(self.reg._clean_text(text)), 3000)

    def test_too_long_is_rejected(self):
        with self.assertRaises(VoiceError) as ctx:
            self.reg.synthesize("字" * 4001, self.out)
        self.assertEqual(ctx.exception.code, "text_too_long")


class EdgeFailureTest(unittest.TestCase):
    def setUp(self):
        self.reg = registry(self)
        self.out = pathlib.Path(make_tmp(self, "edge-fail")) / "o.wav"

    def test_provider_exception_becomes_provider_failed(self):
        class Communicate:
            def __init__(self, *a, **k):
                pass

            async def save(self, path):
                raise RuntimeError("403 forbidden")

        with mock.patch.dict(sys.modules, {"edge_tts": types.SimpleNamespace(
                Communicate=Communicate)}):
            with self.assertRaises(VoiceError) as ctx:
                self.reg._edge("你好", self.out, self.reg._builtin)
        self.assertEqual(ctx.exception.code, "provider_failed")
        self.assertIn("403", ctx.exception.message)


class CosyZeroShotTest(unittest.TestCase):
    def test_zero_shot_posts_multipart_with_prompt(self):
        reg = registry(self)
        ref = reg.root / "ref.wav"
        ref.write_bytes(b"RIFF" + b"r" * 100)
        profile = VoiceProfile(id="c", name="c", provider="cosyvoice",
                               endpoint="http://x", mode="zero_shot",
                               reference_audio="ref.wav", prompt_text="原话")
        out = pathlib.Path(make_tmp(self, "cosy")) / "o.wav"
        with mock.patch.object(reg, "_fetch", return_value=b"RIFF" + b"i" * 200) as fetch:
            reg._cosyvoice("你好", out, profile)
        self.assertTrue(fetch.call_args.args[0].endswith("/inference_zero_shot"))
        self.assertIn("multipart/form-data; boundary=",
                      fetch.call_args.kwargs["headers"]["Content-Type"])
        self.assertIn(b'name="prompt_wav"', fetch.call_args.kwargs["data"])
        self.assertIn(b"prompt_text", fetch.call_args.kwargs["data"])


class DispatchTest(unittest.TestCase):
    """synthesize 的分发与"edge 二次失败"路径。"""

    def setUp(self):
        self.reg = registry(self)
        self.out = pathlib.Path(make_tmp(self, "dispatch")) / "o.wav"

    def _profile(self, provider: str) -> VoiceProfile:
        return VoiceProfile(id="p", name="p", provider=provider,
                            endpoint="http://x", postprocess={},
                            extra={"url_template": "http://x/t?t={text}"})

    def _run(self, provider: str, attr: str):
        profile = self._profile(provider)

        def fake(text, target, prof):  # noqa: ARG001
            pathlib.Path(target).write_bytes(b"RIFF" + b"d" * 400)

        with mock.patch.object(self.reg, "get", return_value=profile), \
             mock.patch.object(self.reg, "_to_wav",
                               side_effect=lambda src, dst: dst.write_bytes(b"RIFF" + b"e" * 400)), \
             mock.patch.object(self.reg, attr, side_effect=fake) as called:
            result = self.reg.synthesize("你好", self.out)
        self.assertEqual(result.provider, provider)
        called.assert_called_once()

    def test_dispatches_http(self):
        self._run("http", "_http")

    def test_dispatches_openai(self):
        self._run("openai", "_openai")

    def test_edge_retry_then_sapi_failure_reraises_primary(self):
        profile = self._profile("edge")
        profile.voice = "zh-CN-XiaoyiNeural"
        primary = VoiceError("provider_failed", "第一次就挂了")

        def edge(text, target, prof):  # noqa: ARG001
            raise primary

        def sapi(text, target, prof):  # noqa: ARG001
            raise VoiceError("provider_failed", "SAPI 也没有")

        with mock.patch.object(self.reg, "get", return_value=profile), \
             mock.patch.object(self.reg, "_edge", side_effect=edge), \
             mock.patch.object(self.reg, "_sapi", side_effect=sapi):
            with self.assertRaises(VoiceError) as ctx:
                self.reg.synthesize("你好", self.out)
        self.assertIs(ctx.exception, primary, "要抛最初那个错误，别被 SAPI 的盖掉")

    def test_non_edge_provider_failure_is_not_retried(self):
        profile = self._profile("http")
        with mock.patch.object(self.reg, "get", return_value=profile), \
             mock.patch.object(self.reg, "_http",
                               side_effect=VoiceError("provider_unreachable", "down")), \
             mock.patch.object(self.reg, "_sapi") as sapi:
            with self.assertRaises(VoiceError) as ctx:
                self.reg.synthesize("你好", self.out)
        self.assertEqual(ctx.exception.code, "provider_unreachable")
        sapi.assert_not_called()



class OverridesAndEdgesTest(unittest.TestCase):
    def setUp(self):
        self.reg = registry(self)
        self.out = pathlib.Path(make_tmp(self, "edges")) / "o.wav"

    def test_synthesize_overrides_reach_the_provider(self):
        seen = {}

        def provider(text, target, profile):  # noqa: ARG001
            seen.update(rate=profile.rate, pitch=profile.pitch,
                        volume=profile.volume, instruct=profile.instruct)
            pathlib.Path(target).write_bytes(b"RIFF" + b"o" * 400)

        with mock.patch.object(self.reg, "_edge", side_effect=provider), \
             mock.patch.object(self.reg, "_to_wav",
                               side_effect=lambda src, dst: dst.write_bytes(b"RIFF" + b"p" * 400)):
            self.reg.synthesize("你好", self.out, rate=999, pitch=-999,
                                volume=50, instruct="  小声点  ")
        self.assertEqual(seen["rate"], 100)
        self.assertEqual(seen["pitch"], -24)
        self.assertEqual(seen["volume"], 50)
        self.assertEqual(seen["instruct"], "小声点", "instruct 要去空白")

    def test_provider_returning_tiny_file_is_empty_audio(self):
        def provider(text, target, profile):  # noqa: ARG001
            pathlib.Path(target).write_bytes(b"x" * 10)   # < 100 字节

        with mock.patch.object(self.reg, "_edge", side_effect=provider):
            with self.assertRaises(VoiceError) as ctx:
                self.reg.synthesize("你好", self.out)
        self.assertEqual(ctx.exception.code, "empty_audio")

    def test_limit_peak_skips_non_pcm16(self):
        path = pathlib.Path(make_tmp(self, "u8")) / "eight.wav"
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(1)
            writer.setframerate(8000)
            writer.writeframes(b"\xff" * 100)
        before = path.read_bytes()
        VoiceRegistry._limit_peak(path)
        self.assertEqual(path.read_bytes(), before, "8-bit 不处理")

    def test_limit_peak_skips_empty_frame_wav(self):
        path = pathlib.Path(make_tmp(self, "empty")) / "empty.wav"
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(8000)
            writer.writeframes(b"")
        VoiceRegistry._limit_peak(path)   # 不抛
        self.assertTrue(path.is_file())

    def test_postprocess_http_reports_unreadable_source(self):
        (self.reg.root / "ref.wav").write_bytes(b"RIFFREF")
        profile = VoiceProfile(id="p", name="p", postprocess={
            "url": "http://x/convert", "reference": "ref.wav"})
        folder = pathlib.Path(make_tmp(self, "dirsrc"))
        with self.assertRaises(VoiceError) as ctx:
            self.reg._postprocess_http(folder, folder, profile, "http://x/convert")
        self.assertEqual(ctx.exception.code, "postprocess_failed")
        self.assertIn("cannot read source", ctx.exception.message)

    def test_postprocess_http_reports_unreadable_reference(self):
        (self.reg.root / "ref.wav").write_bytes(b"RIFFREF")
        profile = VoiceProfile(id="p", name="p", postprocess={
            "url": "http://x/convert", "reference": "ref.wav"})
        work = pathlib.Path(make_tmp(self, "refbad"))
        src = write_wav(work / "src.wav", seconds=0.2)
        with mock.patch.object(vs, "_reference_bytes", side_effect=OSError("锁住了")):
            with self.assertRaises(VoiceError) as ctx:
                self.reg._postprocess_http(src, work, profile, "http://x/convert")
        self.assertEqual(ctx.exception.code, "reference_missing")
        self.assertIn("锁住了", ctx.exception.message)

    def test_reference_cache_is_cleared_when_it_grows(self):
        vs._REFERENCE_CACHE.clear()
        tmp = pathlib.Path(make_tmp(self, "cachecap"))
        for i in range(10):
            path = tmp / f"{i}.wav"
            path.write_bytes(bytes([i]))
            vs._reference_bytes(path)
        self.assertLessEqual(len(vs._REFERENCE_CACHE), 10)
        vs._REFERENCE_CACHE.clear()


if __name__ == "__main__":
    unittest.main()
