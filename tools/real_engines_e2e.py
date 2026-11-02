# -*- coding: utf-8 -*-
"""真引擎端到端：真实图片/音频 → real_engines → Recognizers → writer 全管线。"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WORK = ROOT / ".tmp"
WORK.mkdir(mode=0o777, exist_ok=True)
db = WORK / "real_engines.db"
if db.exists():
    db.unlink()

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

# ---- 造真实输入：图（含待记忆事实）+ 音频（复用 asr_smoke 的 TTS 产物）----
font_path = r"C:\Windows\Fonts\msyh.ttc"
img = Image.new("RGB", (720, 200), "white")
d = ImageDraw.Draw(img)
f = ImageFont.truetype(font_path, 42)
d.text((40, 30), "我的邮箱是 chenming@example.com", fill="black", font=f)
d.text((40, 110), "公司在南山区科技园", fill="black", font=f)
img_path = WORK / "e2e_ocr.png"
img.save(img_path)

wav_path = WORK / "asr_test.wav"  # asr_smoke 生成：我叫陈明，住在深圳，周末喜欢钓鱼。
wav_bytes = wav_path.read_bytes()

# ---- 注入 Recognizers ----
from aiji.hippocampus import real_engines  # noqa: E402
from aiji.hippocampus.recognize import Item, Recognizers  # noqa: E402

rec = Recognizers(ocr=lambda it: real_engines.ocr_image(it.extra["bytes"]),
                  asr=lambda it: real_engines.transcribe_wav_bytes(it.extra["bytes"]),
                  timeout_s=60.0, min_confidence=0.5)

out = {}
# OCR 通道
env_img = rec.recognize(Item(kind="image", ref="e2e-img", extra={"bytes": img_path.read_bytes()}))
out["ocr"] = env_img.to_dict()
print("[OCR]", json.dumps(env_img.to_dict(), ensure_ascii=False))

# ASR 通道
env_wav = rec.recognize(Item(kind="voice", ref="e2e-wav", extra={"bytes": wav_bytes}))
out["asr"] = env_wav.to_dict()
print("[ASR]", json.dumps(env_wav.to_dict(), ensure_ascii=False))

# ---- 全管线：识别产物喂 writer（真实模型抽取走网关）----
import os  # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
from tools.load_keys import bridge  # noqa: E402
bridge()
os.environ["AIJI_ROUTES"] = json.dumps([
    {"name": "workbuddy", "base_url": "http://127.0.0.1:8788/v1",
     "model": "glm-5.3-flash", "api_key": os.environ.get("WORKBUDDY_HUB_KEY", "")},
])

from aiji import Memory  # noqa: E402
m = Memory.open(path=str(db))
if env_img.usable:
    m.ingest(("qq", "u_e2e"), kind="image", ref="e2e-img",
             mime="image/png", session="s1", extra={"bytes": img_path.read_bytes()})
if env_wav.usable:
    m.ingest(("qq", "u_e2e"), kind="voice", ref="e2e-wav",
             mime="audio/wav", session="s1", extra={"bytes": wav_bytes})
ok = m.flush(120)
rows = m.db.read("SELECT predicate, object, source_kind, kind, text FROM fact"
                 " WHERE platform_user_id='u_e2e' AND status='active'")
out["facts"] = [dict(r) for r in rows]
print(f"\n[facts] {len(rows)} 条（flush={'OK' if ok else 'TIMEOUT'}）:")
for r in rows:
    print(f"  {r['predicate']}={r['object']} kind={r['kind']} src={r['source_kind']} | {r['text'][:40]}")

(WORK / "real_engines_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
n_ok = sum(1 for e in (env_img, env_wav) if e.status == "ok") + (1 if rows else 0)
print(f"\n== 结果：{n_ok}/3（OCR ok / ASR ok / facts 落库）==")
sys.exit(0 if n_ok == 3 else 1)
