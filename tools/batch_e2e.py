# -*- coding: utf-8 -*-
"""批量识别端到端：一次 ingest 带多张图/多段音频 → bytes_list → 单引擎循环 → 落库。

跑法: python tools/batch_e2e.py
验证：2 图（邮箱/公司 + 年龄/爱好）+ 2 音频（姓名/居住地 + 职业）全部经
真实引擎批量识别 → 模型抽取 → fact 落库。
"""
import io
import json
import os
import sys
import wave
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
WORK = ROOT / ".tmp"
WORK.mkdir(mode=0o777, exist_ok=True)
db = WORK / "batch_e2e.db"
if db.exists():
    db.unlink()

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

font_path = r"C:\Windows\Fonts\msyh.ttc"


def make_img(name, lines):
    img = Image.new("RGB", (720, 200), "white")
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(font_path, 42)
    for i, ln in enumerate(lines):
        d.text((40, 30 + i * 90), ln, fill="black", font=f)
    p = WORK / name
    img.save(p)
    return p


img1 = make_img("batch_img1.png", ["我的邮箱是 chenming@example.com", "公司在南山区科技园"])
img2 = make_img("batch_img2.png", ["我今年 30 岁", "我喜欢钓鱼"])


def make_wav(name, text):
    import win32com.client
    p = WORK / name
    tts = win32com.client.Dispatch("SAPI.SpVoice")
    for v in tts.GetVoices():
        if "Huihui" in v.GetDescription():
            tts.Voice = v
            break
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 34
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format = fmt
    stream.Open(str(p), 3, False)
    tts.AudioOutputStream = stream
    tts.Speak(text)
    stream.Close()
    return p


wav1 = make_wav("batch_wav1.wav", "我叫陈明，住在深圳")
wav2 = make_wav("batch_wav2.wav", "我的职业是工程师")

# ---- 注入 + key 桥 + 路由 ----
sys.path.insert(0, str(ROOT / "tools"))
from tools.load_keys import bridge  # noqa: E402
bridge()
os.environ["AIJI_ROUTES"] = json.dumps([
    {"name": "workbuddy", "base_url": "http://127.0.0.1:8788/v1",
     "model": "glm-5.3-flash", "api_key": os.environ.get("WORKBUDDY_HUB_KEY", "")},
])

from aiji import Memory  # noqa: E402
m = Memory.open(path=str(db))

# 一次 ingest 带多张图；一次 ingest 带多段音频（bytes_list 批量）
m.ingest(("qq", "u_batch"), kind="image", ref="batch-img",
         mime="image/png", session="s1",
         extra={"bytes_list": [img1.read_bytes(), img2.read_bytes()]})
m.ingest(("qq", "u_batch"), kind="voice", ref="batch-wav",
         mime="audio/wav", session="s1",
         extra={"bytes_list": [wav1.read_bytes(), wav2.read_bytes()]})

ok = m.flush(180)
rows = m.db.read("SELECT predicate, object, source_kind, kind FROM fact"
                 " WHERE platform_user_id='u_batch' AND status='active'")
print(f"flush={'OK' if ok else 'TIMEOUT'}  facts={len(rows)}")
for r in rows:
    print(f"  {r['predicate']}={r['object']} src={r['source_kind']}")

# 断言：批量识别后 ≥6 个 fact 落库（模型抽取的维度名可能用近义词，
# 如"公司位置/爱好/姓名"，不写死维度名，直接看 fact 数量 + 关键 object 值）
objs = {r["object"] for r in rows}
want_objs = ["chenming@example", "南山区科技园", "30", "钓鱼", "陈明", "深圳", "工程师"]
hit = sum(1 for w in want_objs if any(w in o for o in objs))
print(f"\n关键 object 命中 {hit}/{len(want_objs)}")
sys.exit(0 if hit >= 6 else 1)
