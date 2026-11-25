"""生成加密演示用图：SENTINEL 品牌卡片（无任何外部机构标识）。

替换原 MD1.0 附带的校徽图片（避免信息泄露）。

用法（在项目根目录下运行，依赖根目录相对路径）：
    python 项目源码/拓展模块/make_demo_image.py
生成物：数据/拓展资源/demo_image.jpg
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W = 512
ROOT_ICO = "发布/logo.ico"
OUT = "数据/拓展资源/demo_image.jpg"

GREEN = (61, 220, 151)
DARK_LINE = (31, 82, 62)


def vertical_gradient(top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGB", (W, W), top)
    d = ImageDraw.Draw(img)
    for y in range(W):
        t = y / (W - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)
    return img


def add_center_glow(img: Image.Image) -> Image.Image:
    mask = Image.new("L", (W, W), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([70, 20, W - 70, W - 120], fill=110)
    mask = mask.filter(ImageFilter.GaussianBlur(70))
    accent = Image.new("RGB", (W, W), (23, 74, 56))
    return Image.composite(Image.blend(img, accent, 0.55), img, mask)


def draw_circuit_traces(d: ImageDraw.ImageDraw) -> None:
    """四角 PCB 走线装饰（低对比，不喧宾夺主）。"""

    def trace(pts, w=3):
        d.line(pts, fill=DARK_LINE, width=w, joint="curve")
        for x, y in (pts[0], pts[-1]):
            d.ellipse([x - 6, y - 6, x + 6, y + 6], outline=DARK_LINE, width=2)
        # 中点小节点
        mx, my = pts[len(pts) // 2]
        d.ellipse([mx - 4, my - 4, mx + 4, my + 4], fill=DARK_LINE)

    trace([(36, 92), (36, 56), (86, 56)])
    trace([(W - 36, 120), (W - 36, 96), (W - 92, 96)])
    trace([(36, W - 120), (36, W - 96), (W - 92 - 60, W - 96)])
    trace([(W - 36, W - 70), (W - 36, W - 44), (W - 86, W - 44), (W - 86, W - 24)])


def draw_spaced_text(
    d: ImageDraw.ImageDraw, text: str, font, y: float, fill, spacing: float = 6
) -> None:
    width = sum(d.textlength(c, font=font) for c in text) + spacing * (len(text) - 1)
    x = (W - width) / 2
    for c in text:
        d.text((x, y), c, font=font, fill=fill)
        x += d.textlength(c, font=font) + spacing


def main() -> None:
    img = vertical_gradient((10, 20, 17), (17, 49, 37))
    img = add_center_glow(img)

    d = ImageDraw.Draw(img)
    draw_circuit_traces(d)

    # 徽标 + 光晕
    logo = Image.open(ROOT_ICO)
    logo.size = (256, 256)
    logo = logo.convert("RGBA")
    lx, ly = (W - 256) // 2, 108

    alpha = logo.split()[3].filter(ImageFilter.GaussianBlur(16))
    halo = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    halo_layer = Image.new("RGBA", (256, 256), (*GREEN, 0))
    halo_layer.putalpha(alpha.point(lambda a: int(a * 0.4)))
    halo.paste(halo_layer, (lx, ly), halo_layer)
    img.paste(halo, (0, 0), halo)

    img.paste(logo, (lx, ly), logo)

    # 文案
    f_title = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 36)
    f_sub = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 19)
    d = ImageDraw.Draw(img)
    draw_spaced_text(d, "SENTINEL", f_title, 386, (233, 240, 234), spacing=7)
    sub = "AI 安全知识情报系统"
    w2 = d.textlength(sub, font=f_sub)
    d.text(((W - w2) / 2, 440), sub, font=f_sub, fill=(126, 156, 142))

    # 分隔短线
    d.line([(W / 2 - 34, 430), (W / 2 - 10, 430)], fill=DARK_LINE, width=2)
    d.line([(W / 2 + 10, 430), (W / 2 + 34, 430)], fill=DARK_LINE, width=2)

    # 边框
    d.rounded_rectangle([12, 12, W - 12, W - 12], radius=24, outline=DARK_LINE, width=2)

    img.save(OUT, quality=90)
    print("saved", OUT, img.size)


if __name__ == "__main__":
    main()
