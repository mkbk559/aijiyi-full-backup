"""Voice registry and provider contract regressions."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from aiji.voice_service import VoiceError, VoiceRegistry
from tools.voice_reference_select import select_closest


def _wav_bytes(seconds: float = 0.2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(b"\x00\x00" * int(24000 * seconds))
    return buffer.getvalue()


class VoiceRegistryTest(unittest.TestCase):
    def test_edge_source_voice_falls_back_before_sapi(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_dir = root / "custom"
            profile_dir.mkdir()
            (profile_dir / "profile.json").write_text(json.dumps({
                "name": "Custom",
                "provider": "edge",
                "voice": "zh-CN-XiaoyiNeural",
            }), encoding="utf-8")
            registry = VoiceRegistry(root)
            seen = []

            def fake_edge(text, output, profile):
                seen.append(profile.voice)
                if profile.voice == "zh-CN-XiaoyiNeural":
                    raise VoiceError("provider_failed", "no audio")
                Path(output).write_bytes(_wav_bytes())

            def fake_to_wav(source, output):
                Path(output).write_bytes(Path(source).read_bytes())

            with (
                mock.patch.object(registry, "_edge", fake_edge),
                mock.patch.object(registry, "_to_wav", fake_to_wav),
                mock.patch.object(
                    registry, "_sapi",
                    side_effect=AssertionError("SAPI should not run"),
                ),
            ):
                result = registry.synthesize(
                    "测试语音", root / "out.wav", profile_id="custom")

            self.assertEqual(seen, [
                "zh-CN-XiaoyiNeural",
                "zh-CN-XiaoxiaoNeural",
            ])
            self.assertEqual(result.provider, "edge")

    def test_edge_failure_falls_back_to_windows_sapi(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = VoiceRegistry(root)

            def fake_edge(text, output, profile):
                raise VoiceError("provider_failed", "edge unavailable")

            def fake_sapi(text, output, profile):
                self.assertEqual(Path(output).suffix, ".wav")
                Path(output).write_bytes(_wav_bytes())

            def fake_to_wav(source, output):
                Path(output).write_bytes(Path(source).read_bytes())

            with (
                mock.patch.object(registry, "_edge", fake_edge),
                mock.patch.object(
                    registry, "_sapi", fake_sapi, create=True),
                mock.patch.object(registry, "_to_wav", fake_to_wav),
            ):
                result = registry.synthesize(
                    "测试语音", root / "out.wav",
                    profile_id="edge-default")

            self.assertEqual(result.provider, "sapi")
            self.assertEqual(result.profile_id, "edge-default")
            self.assertEqual((root / "out.wav").read_bytes()[:4], b"RIFF")

    def test_unready_profile_falls_back_to_ready_edge_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_dir = root / "fragile"
            profile_dir.mkdir()
            (profile_dir / "profile.json").write_text(json.dumps({
                "name": "Fragile",
                "provider": "edge",
                "postprocess": {
                    "url": "http://127.0.0.1:1/convert",
                },
            }), encoding="utf-8")
            registry = VoiceRegistry(root)
            seen = []

            def fake_edge(text, output, profile):
                seen.append(profile.id)
                Path(output).write_bytes(_wav_bytes())

            def fake_to_wav(source, output):
                Path(output).write_bytes(Path(source).read_bytes())

            with (
                mock.patch.object(registry, "_edge", fake_edge),
                mock.patch.object(registry, "_to_wav", fake_to_wav),
                mock.patch.object(registry, "_provider_ready", return_value=True),
            ):
                result = registry.synthesize(
                    "测试语音", root / "out.wav", profile_id="fragile")

            self.assertEqual(seen, ["edge-default"])
            self.assertEqual(result.profile_id, "edge-default")
            self.assertEqual(result.provider, "edge")

    def test_builtin_profile_and_active_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = VoiceRegistry(root)
            self.assertEqual(registry.active_id(), "edge-default")
            self.assertEqual(registry.get().id, "edge-default")

            profile_dir = root / "friendly"
            profile_dir.mkdir()
            (profile_dir / "profile.json").write_text(json.dumps({
                "name": "Friendly",
                "provider": "edge",
                "voice": "zh-CN-XiaoyiNeural",
                "rate": 8,
                "pitch": 2,
            }), encoding="utf-8")

            profile = registry.set_active("friendly")
            self.assertEqual(profile.name, "Friendly")
            self.assertEqual(registry.active_id(), "friendly")
            self.assertEqual(registry.get().voice, "zh-CN-XiaoyiNeural")
            self.assertTrue(registry.snapshot()["profiles"][0]["active"])

    def test_unknown_provider_is_not_exposed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_dir = root / "bad"
            profile_dir.mkdir()
            (profile_dir / "profile.json").write_text(json.dumps({
                "name": "Bad",
                "provider": "unknown-engine",
            }), encoding="utf-8")
            registry = VoiceRegistry(root)
            self.assertEqual(
                [item.id for item in registry.profiles()], ["edge-default"])

    def test_gptsovits_profile_passes_reference_and_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref = root / "ref.wav"
            ref.write_bytes(_wav_bytes())
            profile_dir = root / "dafeiyu"
            profile_dir.mkdir()
            (profile_dir / "profile.json").write_text(json.dumps({
                "name": "Dafeiyu",
                "provider": "gptsovits",
                "endpoint": "http://127.0.0.1:9880",
                "reference_audio": str(ref),
                "prompt_text": "你好呀",
                "text_lang": "zh",
                "prompt_lang": "zh",
            }), encoding="utf-8")
            registry = VoiceRegistry(root)
            seen: list[str] = []

            def fake_fetch(url: str, **_kwargs):
                seen.append(url)
                return _wav_bytes()

            with mock.patch.object(registry, "_fetch", fake_fetch):
                output = root / "out.wav"
                result = registry.synthesize(
                    "测试一句", output, profile_id="dafeiyu")

            self.assertEqual(result.provider, "gptsovits")
            self.assertEqual(output.read_bytes()[:4], b"RIFF")
            self.assertEqual(len(seen), 1)
            self.assertIn("/tts?", seen[0])
            self.assertIn(
                "prompt_text=%E4%BD%A0%E5%A5%BD%E5%91%80", seen[0])

    def test_gptsovits_v2_posts_json_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_dir = root / "dafeiyu-v2"
            profile_dir.mkdir()
            ref = profile_dir / "ref.wav"
            ref.write_bytes(_wav_bytes())
            (profile_dir / "profile.json").write_text(json.dumps({
                "name": "Dafeiyu V2",
                "provider": "gptsovits",
                "endpoint": "http://127.0.0.1:9880",
                "reference_audio": "dafeiyu-v2/ref.wav",
                "prompt_text": "你好呀",
                "text_lang": "zh",
                "prompt_lang": "zh",
                "extra": {"api_style": "v2"},
            }), encoding="utf-8")
            registry = VoiceRegistry(root)
            seen = {}

            def fake_fetch(url: str, **kwargs):
                seen.update({"url": url, **kwargs})
                return _wav_bytes()

            with mock.patch.object(registry, "_fetch", fake_fetch):
                output = root / "out.wav"
                registry.synthesize("测试一句", output, profile_id="dafeiyu-v2")

            self.assertEqual(seen["url"], "http://127.0.0.1:9880/tts")
            self.assertEqual(seen["method"], "POST")
            payload = json.loads(seen["data"].decode("utf-8"))
            self.assertEqual(payload["text"], "测试一句")
            self.assertEqual(payload["ref_audio_path"], str(ref))
            self.assertEqual(payload["prompt_text"], "你好呀")
            self.assertEqual(payload["text_lang"], "zh")

    def test_cosyvoice_cross_lingual_uploads_reference_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_dir = root / "cosy"
            profile_dir.mkdir()
            reference = profile_dir / "reference.wav"
            reference.write_bytes(_wav_bytes())
            (profile_dir / "profile.json").write_text(json.dumps({
                "name": "CosyVoice",
                "provider": "cosyvoice",
                "endpoint": "http://127.0.0.1:50000",
                "mode": "cross_lingual",
                "reference_audio": "cosy/reference.wav",
            }), encoding="utf-8")
            registry = VoiceRegistry(root)
            seen = {}

            def fake_fetch(url: str, **kwargs):
                seen.update({"url": url, **kwargs})
                return _wav_bytes()

            with mock.patch.object(registry, "_fetch", fake_fetch):
                registry.synthesize(
                    "hello from cosyvoice",
                    root / "out.wav",
                    profile_id="cosy",
                )

            self.assertEqual(
                seen["url"],
                "http://127.0.0.1:50000/inference_cross_lingual",
            )
            self.assertEqual(seen["method"], "POST")
            self.assertIn("multipart/form-data", seen["headers"]["Content-Type"])
            body = seen["data"]
            self.assertIn(b'name="tts_text"', body)
            self.assertIn(b'name="prompt_wav"; filename="reference.wav"', body)

    def test_cosyvoice_zero_shot_is_not_ready_without_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_dir = root / "cosy-missing"
            profile_dir.mkdir()
            (profile_dir / "profile.json").write_text(json.dumps({
                "name": "Missing ref",
                "provider": "cosyvoice",
                "endpoint": "http://127.0.0.1:50000",
                "mode": "zero_shot",
                "reference_audio": "cosy-missing/missing.wav",
            }), encoding="utf-8")
            snapshot = VoiceRegistry(root).snapshot()
            profile = next(
                item for item in snapshot["profiles"]
                if item["id"] == "cosy-missing"
            )
            self.assertFalse(profile["ready"])

    def test_missing_profile_is_structured(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = VoiceRegistry(tmp)
            with self.assertRaises(VoiceError) as caught:
                registry.get("does-not-exist")
            self.assertEqual(caught.exception.code, "profile_not_found")

    def test_empty_synthesis_text_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = VoiceRegistry(tmp)
            with self.assertRaises(VoiceError) as caught:
                registry.synthesize("  \n ", Path(tmp) / "out.wav")
            self.assertEqual(caught.exception.code, "text_required")

    def test_http_converter_can_process_tts_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference = root / "dafeiyu" / "reference.wav"
            reference.parent.mkdir()
            reference.write_bytes(_wav_bytes())
            profile_dir = root / "dafeiyu"
            profile_dir.mkdir(exist_ok=True)
            (profile_dir / "profile.json").write_text(json.dumps({
                "name": "Dafeiyu",
                "provider": "edge",
                "postprocess": {
                    "url": "http://127.0.0.1:9880/convert",
                    "reference": "dafeiyu/reference.wav",
                },
            }), encoding="utf-8")
            registry = VoiceRegistry(root)
            source = root / "source.wav"
            source.write_bytes(_wav_bytes())
            output = root / "converted.wav"
            with mock.patch.object(registry, "_fetch", return_value=_wav_bytes()):
                registry.convert_audio(
                    source, output, profile_id="dafeiyu", pitch=2)
            self.assertEqual(output.read_bytes()[:4], b"RIFF")

    def test_postprocess_failure_returns_source_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_dir = root / "dafeiyu"
            profile_dir.mkdir()
            reference = profile_dir / "reference.wav"
            reference.write_bytes(_wav_bytes())
            (profile_dir / "profile.json").write_text(json.dumps({
                "name": "Dafeiyu",
                "provider": "edge",
                "voice": "zh-CN-XiaoyiNeural",
                "postprocess": {
                    "url": "http://127.0.0.1:9880/convert",
                    "reference": "dafeiyu/reference.wav",
                },
            }), encoding="utf-8")
            registry = VoiceRegistry(root)
            output = root / "out.wav"

            def fake_edge(_text, target, _profile):
                Path(target).write_bytes(_wav_bytes())

            def fake_to_wav(source, target):
                Path(target).write_bytes(Path(source).read_bytes())

            with (
                mock.patch.object(registry, "_ready", return_value=True),
                mock.patch.object(registry, "_edge", fake_edge),
                mock.patch.object(registry, "_to_wav", fake_to_wav),
                mock.patch.object(
                    registry,
                    "_postprocess",
                    side_effect=VoiceError("postprocess_failed", "offline"),
                ),
            ):
                result = registry.synthesize(
                    "定制声线测试", output, profile_id="dafeiyu")

            self.assertEqual(result.provider, "edge")
            self.assertEqual(output.read_bytes()[:4], b"RIFF")

    def test_reference_selection_prefers_cluster_centroid(self):
        rows = [
            {"file": "a", "seconds": 6.0, "embedding": [1.0, 0.0]},
            {"file": "b", "seconds": 6.0, "embedding": [0.9, 0.1]},
            {"file": "c", "seconds": 6.0, "embedding": [0.0, 1.0]},
        ]
        selected = select_closest(
            rows, centroid=[1.0, 0.0], count=2)
        self.assertEqual([row["file"] for row in selected], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
