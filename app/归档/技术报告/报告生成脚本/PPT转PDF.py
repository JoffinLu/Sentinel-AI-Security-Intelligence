"""
PPT → PDF 转换（高保真）

赛题要求「技术讲解 PPT：以 PDF 格式提交」。

为什么不用 LibreOffice / Word COM
------------------------------
本机未安装 LibreOffice，且沙箱限制 PowerShell 的 COM 调用。
而用 python-pptx 读出形状后用 reportlab 重排会丢失布局
（实测 KPI 卡片的标签与数值被拆到不同段落，产生 21 页而非 14 页）。

方案
----
用 matplotlib 按 PPTX 中的真实坐标（EMU）逐个重绘形状，
再把每页图片合成 PDF。好处是：
  ① 坐标与 PPTX 一致，版式不会走样
  ② 不依赖外部工具，任何环境都能跑
  ③ 中文字体通过 matplotlib 的 font.sans-serif 处理
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pymupdf
from PIL import Image
from pptx import Presentation
from pptx.util import Emu

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
PPTX = ROOT / "文档" / "AI安全知识情报系统-技术讲解PPT.pptx"
PDF = ROOT / "文档" / "AI安全知识情报系统-技术讲解PPT.pdf"
TMP = ROOT / "数据" / "ppt_render"

# 颜色（与 PPT 主题一致）
C = {
    "dark": "#1A1A1A", "gray": "#5F6368", "blue": "#4285F4",
    "green": "#0F9D58", "orange": "#F9AB00", "red": "#EA4335",
    "light": "#E8F0FE", "white": "#FFFFFF", "bg": "#F7F9FC",
}

# 中文字体：微软雅黑无 ▸ 符号，故正文用 SimHei（覆盖更全）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# PPT 页面尺寸（EMU）
SLIDE_W_EMU = 12191695
SLIDE_H_EMU = 6858000
RENDER_W_IN = 13.333
RENDER_H_IN = 7.5
DPI = 110


def emu_to_frac(v: int, total: int) -> float:
    """EMU 坐标转 0-1 归一化坐标"""
    return v / total


def shape_fill_rgb(shape):
    """提取形状填充色"""
    try:
        f = shape.fill
        if f.type is not None and f.type == 1:  # SOLID
            return "#" + str(f.fore_color.rgb)
    except Exception:  # noqa: BLE001
        pass
    return None


def render_slide(slide, idx: int, out_png: Path) -> None:
    """把一页 PPT 按原始坐标渲染为图片"""
    fig = plt.figure(figsize=(RENDER_W_IN, RENDER_H_IN), dpi=DPI)
    fig.patch.set_facecolor(C["bg"])
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # 先画形状（矩形/圆角框），再画文字与图片，保证层级正确
    for shape in slide.shapes:
        x = emu_to_frac(shape.left, SLIDE_W_EMU)
        y = 1 - emu_to_frac(shape.top + shape.height, SLIDE_H_EMU)
        w = emu_to_frac(shape.width, SLIDE_W_EMU)
        h = emu_to_frac(shape.height, SLIDE_H_EMU)

        # ---------- 图片（必须先判，否则会被下面的矩形分支吃掉：
        #            AUTO_SHAPE 与 PICTURE 都是 has_text_frame=False）----------
        if shape.shape_type == 13:
            try:
                import io

                blob = shape.image.blob
                pil = Image.open(io.BytesIO(blob))
                iw, ih = pil.size
                # 等比内接必须在「物理英寸」里算：归一化坐标的 x/y 比例不同
                # （x 1 单位 = 13.333in，y 1 单位 = 7.5in），直接比 w/h 会把
                # 宽高比放大 1.778 倍，导致竖图被压成横图并溢出画布。
                box_w_in = w * RENDER_W_IN
                box_h_in = h * RENDER_H_IN
                if ih > 0 and iw > 0 and box_h_in > 0:
                    scale = min(box_w_in / iw, box_h_in / ih)
                    draw_w_in, draw_h_in = iw * scale, ih * scale
                else:
                    draw_w_in, draw_h_in = box_w_in, box_h_in
                # 在框内居中
                cx_in = x * RENDER_W_IN + box_w_in / 2
                cy_in = y * RENDER_H_IN + box_h_in / 2
                dx = (cx_in - draw_w_in / 2) / RENDER_W_IN
                dy = (cy_in - draw_h_in / 2) / RENDER_H_IN
                draw_w = draw_w_in / RENDER_W_IN
                draw_h = draw_h_in / RENDER_H_IN
                ax.imshow(
                    plt.imread(io.BytesIO(blob)),
                    extent=[dx, dx + draw_w, dy, dy + draw_h],
                    aspect="auto", zorder=1,
                )
            except Exception:  # noqa: BLE001
                pass
            continue

        # ---------- 矩形（KPI 卡片、分隔线）----------
        if shape.shape_type is not None and not shape.has_text_frame:
            fill = shape_fill_rgb(shape)
            if fill and w > 0.05 and h > 0.01:
                ax.add_patch(
                    mpatches.FancyBboxPatch(
                        (x, y), w, h,
                        boxstyle="round,pad=0.002,rounding_size=0.006",
                        facecolor=fill, edgecolor=C["light"], linewidth=1.0,
                    )
                )
            continue

        # 文字
        if shape.has_text_frame and shape.text_frame.text.strip():
            txt = shape.text_frame.text.strip()
            # 从第一个 run 取字号与颜色
            size_pt, color, bold = 12, C["dark"], False
            try:
                run = shape.text_frame.paragraphs[0].runs[0]
                if run.font.size:
                    size_pt = run.font.size.pt
                if run.font.color and run.font.color.rgb:
                    color = "#" + str(run.font.color.rgb)
                bold = bool(run.font.bold)
            except Exception:  # noqa: BLE001
                pass

            # 英寸 → 点（matplotlib 按 72dpi 换算）
            fontsize = size_pt * 0.75
            if fontsize < 5:
                fontsize = 5
            # 长文本自动缩小
            lines_estimate = max(1, int(len(txt) / max(int(w * 190), 20)))
            if lines_estimate > 6:
                fontsize *= 0.78

            ax.text(
                x, y + h, txt,
                fontsize=fontsize, color=color,
                fontweight="bold" if bold else "normal",
                ha="left", va="top", linespacing=1.35, zorder=2,
            )

    # imshow 会改动坐标轴范围，这里复位一次保证 0-1 归一化坐标有效
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    fig.savefig(out_png, dpi=DPI, facecolor=C["bg"])
    plt.close(fig)


def main() -> None:
    if not PPTX.exists():
        print(f"未找到 {PPTX.name}，请先生成 PPTX")
        return

    TMP.mkdir(parents=True, exist_ok=True)
    prs = Presentation(str(PPTX))
    print(f"正在渲染 {len(prs.slides)} 页…")

    page_pngs: list[Path] = []
    for i, slide in enumerate(prs.slides, 1):
        p = TMP / f"page{i:02d}.png"
        render_slide(slide, i, p)
        page_pngs.append(p)
        print(f"  第 {i} 页完成")

    # 合成 PDF
    doc = pymupdf.open()
    for p in page_pngs:
        page = doc.new_page(width=842, height=474)  # 16:9
        page.insert_image(pymupdf.Rect(0, 0, 842, 474), stream=p.read_bytes())
    doc.save(str(PDF), deflate=True)

    size_mb = PDF.stat().st_size / 1024 / 1024
    print(f"\nPDF 已生成：{PDF.name}")
    print(f"  {len(doc)} 页，{size_mb:.2f} MB")

    # 清理临时文件
    for p in page_pngs:
        p.unlink(missing_ok=True)
    try:
        TMP.rmdir()
    except OSError:
        pass


if __name__ == "__main__":
    main()
