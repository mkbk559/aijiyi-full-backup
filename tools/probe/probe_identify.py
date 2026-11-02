"""识别层深探：7 种输入 × 状态机（ok/unsupported/low_confidence/timeout/error） + 真实引擎端到端。"""

from __future__ import annotations

import io
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.probe._harness import (  # noqa: E402
    Probe, cleanup, make_memory, quiet_env, workdir,
)

from aiji.recognize import Item, Recognizers  # noqa: E402

quiet_env()
probe = Probe("identify")
base_dir = workdir("identify")


class Boom:
    def __call__(self, item):
        raise RuntimeError("engine boom")


def slow(item):
    time.sleep(2.0)
    return ("慢引擎", 0.9)


try:
    # ---------- 1. 无引擎时的诚实降级 ----------
    bare = Recognizers(timeout_s=1.0, min_confidence=0.5)
    cases = [
        (Item("image", "i1", url="https://x/y.png"), "unsupported", "no_ocr_engine"),
        (Item("voice", "v1", url="https://x/y.wav"), "unsupported", "no_asr_engine"),
    ]
    for item, want_status, reason in cases:
        env = bare.recognize(item)
        probe.case(f"无引擎 {item.kind} → {want_status}",
                    env.status == want_status and env.meta.get("reason") == reason,
                    f"status={env.status} reason={env.meta.get('reason')}")

    # ---------- 2. 文本 / 表情 ----------
    env = bare.recognize(Item("text", "t1", text="我叫李雷"))
    probe.case("text 直用", env.status == "ok" and env.text == "我叫李雷" and env.confidence == 1.0,
               f"{env.status} {env.text!r}")
    env = bare.recognize(Item("text", "t2", text="   "))
    probe.case("空 text → unsupported", env.status == "unsupported", env.status)
    env = bare.recognize(Item("emoji", "e1", text="今天真不错 😂👍"))
    probe.case("emoji 映射成语义标签",
               env.status == "ok" and "好笑" in env.text and "赞同" in env.text,
               f"{env.text!r}")
    env = bare.recognize(Item("emoji", "e2", text="没有表情"))
    probe.case("无表情的 emoji 消息原样通过", env.status == "ok" and env.text == "没有表情",
               f"{env.text!r}")

    # ---------- 3. 识别器状态机（mock 引擎） ----------
    ok_ocr = Recognizers(ocr=lambda i: ("发票金额 128 元", 0.86), timeout_s=1.0)
    env = ok_ocr.recognize(Item("image", "i2", url="https://x/a.png"))
    probe.case("有引擎 image → ok + 文本 + 置信",
                env.status == "ok" and "128" in env.text and abs(env.confidence - 0.86) < 1e-6,
                f"{env.status} {env.text!r} conf={env.confidence}")

    low = Recognizers(ocr=lambda i: ("模糊", 0.3), timeout_s=1.0)
    env = low.recognize(Item("image", "i3", url="https://x/b.png"))
    probe.case("低置信 → low_confidence", env.status == "low_confidence", env.status)

    to = Recognizers(ocr=slow, timeout_s=0.4)
    env = to.recognize(Item("image", "i4", url="https://x/c.png"))
    probe.case("引擎超时 → timeout", env.status == "timeout",
               f"{env.status} {env.meta}")

    bad = Recognizers(ocr=Boom(), timeout_s=1.0)
    env = bad.recognize(Item("image", "i5", url="https://x/d.png"))
    probe.case("引擎抛错 → error（不炸 worker）", env.status == "error",
               f"{env.status} {env.meta.get('reason')}")

    # ---------- 4. 链接 / 文件 / 视频 ----------
    from unittest import mock

    link = Recognizers(fetcher=lambda url: "<html><head><title>记忆系统 - 官方文档</title></head></html>")
    with mock.patch("aiji.recognize.validate_public_http_url", lambda url: None):
        env = link.recognize(Item("link", "l1", url="https://example.test/a"))
    probe.case("link 抓 <title>", env.status == "ok" and "官方文档" in env.text, f"{env.text!r}")

    # 私网/环回地址必须在识别层就被拦下（SSRF 防线）
    env = link.recognize(Item("link", "l1b", url="http://127.0.0.1:8787/health"))
    probe.case("link 私网地址 → error（SSRF 拦截）",
                env.status == "error", f"{env.status} reason={env.meta.get('reason')}")

    def boom_fetch(url):
        raise OSError("network down")

    link2 = Recognizers(fetcher=boom_fetch)
    env = link2.recognize(Item("link", "l2", url="https://example.test/b"))
    probe.case("link 抓取失败 → error 且保留 url", env.status == "error",
               f"{env.status} reason={env.meta.get('reason')}")

    env = bare.recognize(Item("file", "f1", text="文件里的文字", mime="text/plain"))
    probe.case("file 调用方给文本 → ok", env.status == "ok" and env.text == "文件里的文字",
               f"{env.status} {env.text!r}")
    env = bare.recognize(Item("file", "f2", mime="application/pdf"))
    probe.case("file 无提取器 → unsupported（不编造）",
               env.status == "unsupported" and env.meta.get("reason") == "no_extractor_for_mime",
               f"{env.status} {env.meta.get('reason')}")
    env = bare.recognize(Item("video", "v2", url="https://x/v.mp4"))
    probe.case("video 只存元数据", env.status == "metadata_only" and env.usable is False,
               f"{env.status} usable={env.usable}")
    env = bare.recognize(Item("hologram", "h1"))
    probe.case("未知 kind → unsupported",
               env.status == "unsupported" and "unknown_kind" in env.meta.get("reason", ""),
               f"{env.status} {env.meta.get('reason')}")

    # ---------- 5. 真实引擎（本机装了才跑） ----------
    from aiji import real_engines as re

    probe.case("真实 OCR 引擎已安装（rapidocr）", re.HAS_OCR, f"HAS_OCR={re.HAS_OCR}")
    if re.HAS_OCR:
        from PIL import Image, ImageDraw, ImageFont

        font_path = Path(r"C:\Windows\Fonts\msyh.ttc")
        image = Image.new("RGB", (760, 190), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype(str(font_path), 40) if font_path.exists() else ImageFont.load_default()
        draw.text((30, 40), "我叫陈明，住在深圳", fill="black", font=font)
        draw.text((30, 110), "周末喜欢钓鱼", fill="black", font=font)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        png = buffer.getvalue()
        started = time.time()
        text, conf = re.ocr_image(png)
        took = time.time() - started
        probe.case("真实 OCR：中文两行可读",
                    "陈明" in text and "深圳" in text and "钓鱼" in text,
                    f"text={text!r} conf={conf:.2f} {took:.1f}s")
        text2, _ = re.ocr_images([png, png])
        probe.case("真实 OCR 批量接口", text2.count("陈明") >= 2, f"len={len(text2)}")

    probe.case("真实 ASR 引擎已安装（funasr/whisper）", re.HAS_ASR or re.HAS_FUNASR,
               f"HAS_ASR={re.HAS_ASR} HAS_FUNASR={re.HAS_FUNASR}")
    if re.HAS_ASR or re.HAS_FUNASR:
        wav = base_dir / "tts.wav"
        tts = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(Path(__file__).resolve().parents[1] / "sapi_tts.ps1"),
             "-Text", "我叫陈明，住在深圳，周末喜欢钓鱼。", "-Output", str(wav)],
            capture_output=True, timeout=60, check=False,
        )
        probe.case("SAPI 合成参考音频成功", tts.returncode == 0 and wav.exists(),
                   f"path={wav} bytes={wav.stat().st_size if wav.exists() else 0}")
        if wav.exists():
            started = time.time()
            text, conf = re.transcribe_wav_bytes(wav.read_bytes())
            took = time.time() - started
            hit = sum(1 for kw in ("陈明", "深圳", "钓鱼") if kw in text)
            probe.case("真实 ASR：核心信息 ≥2/3",
                        hit >= 2, f"text={text!r} hit={hit}/3 conf={conf:.2f} {took:.1f}s")

    # ---------- 6. 识别 → 落库闭环（真实引擎自动接线） ----------
    memory, _ = make_memory(base_dir / "mem", engines="auto")
    try:
        memory.ingest(("wechat", "alice"), kind="image", ref="img-e2e",
                      url="https://example.test/a.png",
                      extra={"bytes": png if re.HAS_OCR else b""})
        memory.ingest(("wechat", "alice"), kind="image", ref="img-url-only",
                      url="https://example.test/b.png")
        memory.flush(60)
        rows = [dict(r) for r in memory.db.read(
            "SELECT ref,kind,status,text,recognizer,confidence FROM media ORDER BY id")]
        probe.case("ingest 落 media 行（引用 + 识别结果 + 状态）",
                    len(rows) == 2 and all(r["recognizer"] in ("ocr", "image") for r in rows),
                    f"rows={[{k: r[k] for k in ('ref','status','recognizer')} for r in rows]}")
        by_ref = {r["ref"]: r for r in rows}
        if re.HAS_OCR:
            probe.case("有字节的图片：真实 OCR 文本落库",
                        "陈明" in (by_ref["img-e2e"]["text"] or ""),
                        f"text={str(by_ref['img-e2e']['text'])[:60]!r}")
            only_url = by_ref["img-url-only"]
            probe.case("只有 url 的图片：识别不生效（已知缺口 G1）",
                        not (only_url["text"] or "").strip()
                        and only_url["status"] in ("unsupported", "low_confidence"),
                        f"status={only_url['status']} text={only_url['text']!r}")
        else:
            probe.case("无 OCR 时如实 low_confidence/unsupported",
                        all(r["status"] in ("unsupported", "low_confidence") for r in rows),
                        f"status={[r['status'] for r in rows]}")
    finally:
        memory.close()
finally:
    cleanup(base_dir)

raise SystemExit(probe.finish())
