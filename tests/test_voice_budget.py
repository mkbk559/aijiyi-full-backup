# -*- coding: utf-8 -*-
"""换声时长预算（2026-11-03）：语音条别让 RVC 把工作线程占十几秒。

背景：常驻服务把冷启动消掉了，但 CPU 上 RTF 仍是 1.6~2.1
（1.5 s 素材 2.9 s、6 s 素材 9.5 s，见 docs/agent/evidence/gap-closure-2026-11-03/EVIDENCE-rvc-resident.md）。
所以不是"快不快"的问题，而是"要不要在这条路上换声"：语音条超过预算就直接用 TTS 原声，
并把原因留痕（`last_skipped`）——静默跳过会让人以为"定制语音坏了"。
点歌/翻唱（mode="song"）不受限。
"""
from __future__ import annotations

import json
import math
import struct
import sys
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aiji.heart.voice_service import VoiceProfile, VoiceRegistry
from aiji.right_hand import params

WORK = ROOT / ".tmp" / "test_voice_budget"


def write_wav(path: Path, seconds: float, rate: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    for i in range(int(rate * seconds)):
        frames += struct.pack("<h", int(3000 * math.sin(2 * math.pi * 220 * i / rate)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


class TestConvertBudget(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.reg = VoiceRegistry(root=WORK / "voices")
        self.marker = WORK / "converted.marker"
        if self.marker.exists():
            self.marker.unlink()
        # 假换声器：跑起来就写一个 marker 文件（这样能证明"到底跑没跑"）
        script = WORK / "fake_convert.py"
        script.write_text(
            "import pathlib, sys, shutil\n"
            f"pathlib.Path({str(self.marker)!r}).write_text('ran')\n"
            "shutil.copyfile(sys.argv[1], sys.argv[2])\n",
            encoding="utf-8",
        )
        self.profile = VoiceProfile(
            id="rvc-test", name="测试声线", provider="edge", voice="zh-CN-XiaoxiaoNeural",
            postprocess={"command": [sys.executable, str(script), "{input}", "{output}"]},
            timeout_s=120,
        )

    def test_short_audio_is_converted(self):
        src = write_wav(WORK / "short.wav", 1.0)
        out = self.reg._postprocess(src, WORK, self.profile, mode="speech")
        self.assertTrue(self.marker.exists(), "1 秒素材应当照常换声")
        self.assertIsNone(self.reg.last_skipped)
        self.assertTrue(Path(out).exists())

    def test_long_speech_is_skipped(self):
        src = write_wav(WORK / "long.wav", params.VOICE_MAX_CONVERT_SECONDS + 5)
        out = self.reg._postprocess(src, WORK, self.profile, mode="speech")
        self.assertFalse(self.marker.exists(), "超预算的语音条不该跑换声器")
        self.assertEqual(Path(out), src, "跳过后应把 TTS 原声原样返回")
        self.assertIsNotNone(self.reg.last_skipped)
        self.assertEqual(self.reg.last_skipped["mode"], "speech")
        self.assertGreater(self.reg.last_skipped["seconds"], params.VOICE_MAX_CONVERT_SECONDS)

    def test_song_mode_has_no_budget(self):
        src = write_wav(WORK / "song.wav", params.VOICE_MAX_CONVERT_SECONDS + 5)
        out = self.reg._postprocess(src, WORK, self.profile, mode="song")
        self.assertTrue(self.marker.exists(), "点歌/翻唱是离线任务，不该被预算拦")
        self.assertIsNone(self.reg.last_skipped)
        self.assertNotEqual(Path(out), src)

    def test_profile_can_override_limit(self):
        profile = VoiceProfile(
            id="rvc-tight", name="紧预算", provider="edge",
            voice="zh-CN-XiaoxiaoNeural",
            postprocess={
                "command": [sys.executable, str(WORK / "fake_convert.py"), "{input}", "{output}"],
                "max_seconds": 0.5,
            },
            timeout_s=120,
        )
        src = write_wav(WORK / "mid.wav", 1.0)
        out = self.reg._postprocess(src, WORK, profile, mode="speech")
        self.assertFalse(self.marker.exists(), "profile 里 max_seconds=0.5 应当生效")
        self.assertEqual(Path(out), src)
        self.assertAlmostEqual(self.reg.last_skipped["limit"], 0.5)

    def test_non_wav_is_not_skipped(self):
        """读不出时长就不能跳（宁可慢，也不要莫名其妙不换声）。"""
        src = WORK / "notawav.mp3"
        src.write_bytes(b"ID3not-really-audio")
        self.assertIsNone(self.reg._postprocess.__self__._audio_seconds(src))

    def test_snapshot_exposes_last_skipped(self):
        src = write_wav(WORK / "long2.wav", params.VOICE_MAX_CONVERT_SECONDS + 5)
        self.reg._postprocess(src, WORK, self.profile, mode="speech")
        snap = self.reg.snapshot()
        self.assertIn("last_skipped", snap)
        json.dumps(snap)  # 必须可序列化（它进 /v1/voices 的 HTTP 响应）


if __name__ == "__main__":
    unittest.main()
