# -*- coding: utf-8 -*-
"""用 Pillow 生成中文测试图 → RapidOCR 真跑 → 验证识别质量。"""
import io
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

WORK = Path(r"D:\AIJiYi\.tmp")
WORK.mkdir(mode=0o777, exist_ok=True)

# 找一个中文字体（Windows 自带）
fonts = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]
font_path = next((f for f in fonts if Path(f).exists()), None)
print("font:", font_path)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

TEXT_LINES = ["用户名叫陈明", "住在深圳市南山区", "联系电话 138-1234-5678"]
img = Image.new("RGB", (640, 240), "white")
draw = ImageDraw.Draw(img)
font = ImageFont.truetype(font_path, 40)
y = 30
for line in TEXT_LINES:
    draw.text((40, y), line, fill="black", font=font)
    y += 65
p = WORK / "ocr_test.png"
img.save(p)
print("img:", p)

from rapidocr_onnxruntime import RapidOCR  # noqa: E402

engine = RapidOCR()
t0 = time.time()
result, elapse = engine(str(p))
dt = round((time.time() - t0) * 1000)
print(f"OCR {dt}ms（含首次模型加载）")
got = []
if result:
    for item in result:
        # RapidOCR 返回 (box, text, score)；有的版本 score 是 str
        box, text = item[0], item[1]
        try:
            score = float(item[2])
        except (ValueError, TypeError, IndexError):
            score = -1.0
        got.append((text, round(score, 3)))
        print(f"  [{score:.3f}] {text}")
truth = "".join(TEXT_LINES)
hit = sum(1 for t, _ in got if t.replace(" ", "") in truth or
          any(t.replace(" ", "") in ln for ln in TEXT_LINES))
print(f"\n命中 {hit}/{len(TEXT_LINES)}")
