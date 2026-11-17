"""画一张演示用的印刷题目图，再用项目的 OCR 读一遍，看能不能原样认出来。"""
import io
import sys
from pathlib import Path

sys.path.insert(0, r"D:\competition")
sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image, ImageDraw, ImageFont

from xiangke.recognize import recognize

LINES = [
    "She ___ apples. (like / likes)",
    "They ___ my friends. (am / is / are)",
]
out = Path(r"D:\competition\演示\题目.png")
out.parent.mkdir(exist_ok=True)
font = ImageFont.truetype(r"C:\Windows\Fonts\times.ttf", 54)
image = Image.new("RGB", (1200, 340), "white")
draw = ImageDraw.Draw(image)
y = 70
for line in LINES:
    draw.text((70, y), line, fill="black", font=font)
    y += 120
image.save(out)
buffer = io.BytesIO()
image.save(buffer, format="PNG")
print("OCR 认出:", repr(recognize(buffer.getvalue())))
