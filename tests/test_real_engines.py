"""真实 OCR/ASR 适配器（`hippocampus/real_engines.py`）——原先 41% 覆盖。

**不加载真模型**：引擎单例 `_OCR` / `_ASR` / `_FUNASR` 直接换成假对象，
测的是"输入 → (text, confidence)"这段纯逻辑：坏条目跳过、空结果、
批量拼接与平均置信、wav 解码（16/32 bit、单/双声道）、重采样、模型档位。
（真引擎的质量只在合成样本上验过，见 excluded #48。）
"""
import io
import sys
import unittest
import wave
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from aiji.hippocampus import real_engines as re  # noqa: E402
from tests._tempdir import make_tmp  # noqa: E402


def png_bytes(size=(64, 32), color=(255, 255, 255)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def wav_bytes(seconds=0.1, sr=16000, channels=1, width=2, freq=440.0) -> bytes:
    n = int(seconds * sr)
    t = np.arange(n) / sr
    wave_data = (np.sin(2 * np.pi * freq * t) * 8000).astype(np.int16)
    if channels > 1:
        wave_data = np.repeat(wave_data[:, None], channels, axis=1).reshape(-1)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(sr)
        if width == 2:
            w.writeframes(wave_data.astype(np.int16).tobytes())
        else:
            w.writeframes((wave_data.astype(np.int32) << 16).tobytes())
    return buf.getvalue()


class FakeOcr:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def __call__(self, arr):
        self.calls += 1
        return self.result, 0.01


class OcrTest(unittest.TestCase):
    def _run(self, result):
        fake = FakeOcr(result)
        with mock.patch.object(re, "_OCR", fake):
            out = re.ocr_image(png_bytes())
        return out, fake

    def test_texts_are_joined_and_confidence_averaged(self):
        (text, conf), fake = self._run([
            [[0, 0], "第一行", 0.9],
            [[0, 0], " 第二行 ", "0.7"],
        ])
        self.assertEqual(text, "第一行 第二行")
        self.assertAlmostEqual(conf, 0.8)
        self.assertEqual(fake.calls, 1, "引擎单例只调一次")

    def test_empty_result(self):
        (text, conf), _ = self._run([])
        self.assertEqual((text, conf), ("", 0.0))

    def test_broken_items_are_skipped_not_raised(self):
        (text, conf), _ = self._run([
            "不是列表",
            [[0, 0], "好", 0.6],
            [[0, 0], "", 0.9],
            [[0, 0], "坏分", "abc"],
        ])
        self.assertEqual(text, "好")
        self.assertAlmostEqual(conf, 0.6)

    def test_batch_ocr_joins_with_newline(self):
        outputs = iter([("甲", 0.8), ("乙", 0.6), ("", 0.0)])
        with mock.patch.object(re, "ocr_image", lambda data: next(outputs)):
            text, conf = re.ocr_images([b"1", b"2", b"3"])
        self.assertEqual(text, "甲\n乙")
        self.assertAlmostEqual(conf, 0.7)

    def test_batch_ocr_all_empty(self):
        with mock.patch.object(re, "ocr_image", lambda data: ("", 0.0)):
            self.assertEqual(re.ocr_images([b"1", b"2"]), ("", 0.0))


class FakeAsr:
    def __init__(self, segments):
        self.segments = segments
        self.seen = None

    def transcribe(self, audio, language="zh"):
        self.seen = (audio, language)
        return iter(self.segments), object()


class Seg:
    def __init__(self, text, lp):
        self.text = text
        self.avg_logprob = lp


class AsrTest(unittest.TestCase):
    def test_wav_16bit_mono_is_decoded(self):
        audio, sr = re._load_wav_float32(wav_bytes(sr=16000, channels=1))
        self.assertEqual(sr, 16000)
        self.assertEqual(audio.dtype, np.float32)
        self.assertAlmostEqual(len(audio), 1600, delta=2)
        self.assertLessEqual(float(np.abs(audio).max()), 1.0)

    def test_wav_32bit_stereo_is_downmixed(self):
        audio, sr = re._load_wav_float32(
            wav_bytes(sr=8000, channels=2, width=4))
        self.assertEqual(sr, 8000)
        self.assertEqual(audio.ndim, 1, "双声道要降到单声道")
        self.assertAlmostEqual(len(audio), 800, delta=2)

    def test_unsupported_sample_width_raises(self):
        with self.assertRaises(ValueError):
            re._load_wav_float32(wav_bytes(width=1))

    def test_resample_keeps_16k_and_scales_8k(self):
        audio = np.zeros(800, dtype=np.float32)
        same, sr = re._resample16k(audio, 16000)
        self.assertIs(same, audio)
        self.assertEqual(sr, 16000)
        down, sr = re._resample16k(audio, 8000)
        self.assertEqual(sr, 16000)
        self.assertAlmostEqual(len(down), 1600, delta=2)
        self.assertEqual(down.dtype, np.float32)

    def test_whisper_path_text_and_confidence(self):
        fake = FakeAsr([Seg("你好", -0.2), Seg("世界", -0.2)])
        with mock.patch.object(re, "HAS_FUNASR", False), \
             mock.patch.object(re, "_ASR", fake):
            text, conf = re.transcribe_wav_bytes(wav_bytes())
        self.assertEqual(text, "你好世界")
        self.assertAlmostEqual(conf, 0.77, places=2)   # 0.85 + (-0.2)*0.4
        self.assertEqual(fake.seen[1], "zh", "强制中文")

    def test_whisper_low_confidence_is_floored(self):
        fake = FakeAsr([Seg("嗯", -3.0)])
        with mock.patch.object(re, "HAS_FUNASR", False), \
             mock.patch.object(re, "_ASR", fake):
            _text, conf = re.transcribe_wav_bytes(wav_bytes())
        self.assertAlmostEqual(conf, 0.3)

    def test_whisper_no_segments(self):
        fake = FakeAsr([])
        with mock.patch.object(re, "HAS_FUNASR", False), \
             mock.patch.object(re, "_ASR", fake):
            self.assertEqual(re.transcribe_wav_bytes(wav_bytes()), ("", 0.3))

    def test_batch_asr_joins_and_averages(self):
        with mock.patch.object(re, "transcribe_wav_bytes",
                               side_effect=[("甲", 0.9), ("乙", 0.5), ("", 0.0)]):
            text, conf = re.transcribe_wavs([b"1", b"2", b"3"])
        self.assertEqual(text, "甲 乙")
        self.assertAlmostEqual(conf, 0.7)

    def test_transcribe_file_reads_bytes(self):
        tmp = make_tmp(self, "re-file")
        path = tmp / "a.wav"
        path.write_bytes(wav_bytes())
        fake = FakeAsr([Seg("文件", -0.2)])
        with mock.patch.object(re, "HAS_FUNASR", False), \
             mock.patch.object(re, "_ASR", fake):
            self.assertEqual(re.transcribe_file(path)[0], "文件")

    def test_asr_model_setting_accepts_only_known_sizes(self):
        original = re._ASR_MODEL
        try:
            re.set_asr_model("base")
            self.assertEqual(re._ASR_MODEL, "base")
            re.set_asr_model("不存在的档位")
            self.assertEqual(re._ASR_MODEL, "base", "非法档位不能改状态")
        finally:
            re.set_asr_model(original)


class LazyEngineTest(unittest.TestCase):
    """引擎单例是**懒加载**的：只在第一次真调用时 import + 构造，之后复用。

    这条链以前没测：`_get_ocr` / `_get_asr` / `_get_funasr` 一直跑在覆盖盲区里，
    而它们决定了"没装依赖时是不是报得清、装了是不是只加载一次（whisper tiny 首载 197s）"。
    """

    def setUp(self):
        self.saved = (re._OCR, re._ASR, re._FUNASR)
        re._OCR = re._ASR = re._FUNASR = None

    def tearDown(self):
        re._OCR, re._ASR, re._FUNASR = self.saved

    def test_ocr_built_once(self):
        built = []

        class RapidOCR:
            def __init__(self):
                built.append(1)

        fake = mock.Mock(RapidOCR=RapidOCR)
        with mock.patch.dict(sys.modules, {"rapidocr_onnxruntime": fake}):
            self.assertIs(re._get_ocr(), re._get_ocr())
        self.assertEqual(built, [1])

    def test_asr_built_once_with_cpu_int8(self):
        built = []

        class WhisperModel:
            def __init__(self, size, device=None, compute_type=None):
                built.append((size, device, compute_type))

        fake = mock.Mock(WhisperModel=WhisperModel)
        with mock.patch.dict(sys.modules, {"faster_whisper": fake}):
            first = re._get_asr("base")
            self.assertIs(first, re._get_asr("small"))
        self.assertEqual(built, [("base", "cpu", "int8")], "单例：第二次不再构造")

    def test_asr_default_size_follows_settings(self):
        built = []

        class WhisperModel:
            def __init__(self, size, **_kwargs):
                built.append(size)

        fake = mock.Mock(WhisperModel=WhisperModel)
        with mock.patch.dict(sys.modules, {"faster_whisper": fake}):
            re._ASR_MODEL = "small"
            try:
                re._get_asr()
            finally:
                re._ASR_MODEL = "tiny"
        self.assertEqual(built, ["small"])

    def test_funasr_singleton_carries_vad_and_punc(self):
        built = []

        class AutoModel:
            def __init__(self, **kwargs):
                built.append(kwargs)

        fake = mock.Mock(AutoModel=AutoModel)
        with mock.patch.dict(sys.modules, {"funasr": fake}):
            self.assertIs(re._get_funasr(), re._get_funasr())
        self.assertEqual(built, [{
            "model": "paraformer-zh",
            "vad_model": "fsmn-vad",
            "punc_model": "ct-punc",
            "disable_update": True,
        }])


class FunAsrPathTest(unittest.TestCase):
    """有 funasr 时走 paraformer：中文准确率高于 whisper tiny，是首选路径。"""

    def setUp(self):
        self.saved = (re._FUNASR, re.HAS_FUNASR)
        re._FUNASR = None

    def tearDown(self):
        re._FUNASR, re.HAS_FUNASR = self.saved

    def _engine(self, payload):
        engine = mock.Mock()
        engine.generate.return_value = payload
        fake_torch = mock.Mock()
        fake_torch.from_numpy.side_effect = lambda arr: ("tensor", len(arr))
        re._FUNASR = engine
        re.HAS_FUNASR = True
        return engine, mock.patch.dict(sys.modules, {"torch": fake_torch})

    def test_text_is_stripped_and_confident(self):
        engine, patcher = self._engine([{"text": " 你好世界 "}])
        with patcher:
            text, conf = re.transcribe_wav_bytes(wav_bytes())
        self.assertEqual((text, conf), ("你好世界", 0.9))
        kwargs = engine.generate.call_args.kwargs
        self.assertEqual(kwargs["batch_size_s"], 60)
        self.assertEqual(kwargs["input"][0], "tensor")

    def test_empty_result_is_low_confidence(self):
        _engine, patcher = self._engine([{}])
        with patcher:
            self.assertEqual(re.transcribe_wav_bytes(wav_bytes()), ("", 0.3))

    def test_empty_list_result(self):
        _engine, patcher = self._engine([])
        with patcher:
            self.assertEqual(re.transcribe_wav_bytes(wav_bytes()), ("", 0.3))

    def test_batch_asr_without_text_returns_empty(self):
        self.assertEqual(re.transcribe_wavs([]), ("", 0.0))
        self.assertEqual(re.ocr_images([]), ("", 0.0))


class EngineProbeTest(unittest.TestCase):
    def test_probe_reports_missing_module_as_false(self):
        self.assertFalse(re._probe("definitely_not_a_module_xyz"))

    def test_probe_reports_stdlib_as_true(self):
        self.assertTrue(re._probe("json"))


if __name__ == "__main__":
    unittest.main()
