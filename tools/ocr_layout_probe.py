# -*- coding: utf-8 -*-
"""定位表格短单元格漏检根因：边框 vs 字号 对照。"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
WORK = Path(r"D:\AIJiYi\.tmp")
font_path = r"C:\Windows\Fonts\msyh.ttc"

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from rapidocr_onnxruntime import RapidOCR  # noqa: E402

engine = RapidOCR()


def probe(tag, bordered, font_size):
    img = Image.new("RGB", (600, 200), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, font_size)
    cells = ["公司", "南山区科技园"]
    x, y = 40, 60
    for c in cells:
        if bordered:
            draw.rectangle([x, y, x + 240, y + 60], outline="black", width=2)
        draw.text((x + 10, y + 8), c, fill="black", font=font)
        x += 260
    p = WORK / f"probe_{tag}.png"
    img.save(p)
    result, _ = engine(str(p))
    got = [it[1] for it in result] if result else []
    print(f"[{tag}] border={bordered} font={font_size} → {got}")


probe("b28", True, 28)
probe("nb28", False, 28)
probe("b44", True, 44)
probe("nb44", False, 44)
