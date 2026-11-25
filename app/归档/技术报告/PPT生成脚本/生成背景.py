# -*- coding: utf-8 -*-
"""生成 PPT 背景图（装饰圆形烘进背景，避免形状越界）。
输出 2000x1125（10x5.625in @200DPI）到 素材/ 目录。"""
from PIL import Image, ImageDraw
from pathlib import Path

OUT = Path(__file__).resolve().parent / "素材"
OUT.mkdir(exist_ok=True)
W, H = 2000, 1125

DARK = (11, 43, 35)        # 0B2B23
LIGHT = (246, 251, 248)    # F6FBF8
MAIN = (10, 157, 110)      # 0A9D6E
PRIMARY = (5, 130, 92)     # 05825C
GOLD = (183, 121, 31)      # B7791F
LINE = (157, 195, 180)     # 9DC3B4


def make(name, base, circles, dots=()):
    img = Image.new("RGB", (W, H), base)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for (cx, cy, r, color, alpha) in circles:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (alpha,))
    for (cx, cy, r, color, alpha) in dots:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (alpha,))
    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    img.save(OUT / name, "PNG")
    print(name, img.size)


# 封面/致谢：右上主绿光晕 + 左下深绿光晕 + 金点
make("bg_cover_2000x1125.png", DARK, [
    (1930, 30, 560, MAIN, 30),
    (40, 1080, 470, PRIMARY, 42),
], dots=[(1655, 128, 15, GOLD, 210), (300, 205, 9, LINE, 90)])

# 演示页：更克制的角部光晕
make("bg_demo_2000x1125.png", DARK, [
    (1980, -40, 380, MAIN, 22),
    (30, 1140, 300, PRIMARY, 30),
])

# 内容页：极浅角部色晕（不干扰白卡片）
make("bg_light_2000x1125.png", LIGHT, [
    (2010, -80, 430, MAIN, 12),
    (-30, 1180, 330, PRIMARY, 14),
])
