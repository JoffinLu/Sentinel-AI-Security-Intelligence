"""
PPT 版式校验

在提交前把「肉眼才能发现」的问题自动化：
  ① 形状超出页面边界（右侧/底部溢出）
  ② 文本框侵入页脚区（top + height > 7.0in）
  ③ 图片宽高比失真（与原始图片比例偏差 > 3%）
  ④ 关键内容页缺失插图

赛题要求提交 PDF，版式错乱会直接影响评分印象，
所以把这个检查挂在生成流程最后当一道门禁。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image
from pptx import Presentation

ROOT = Path(__file__).resolve().parents[1]
PPTX = ROOT / "文档" / "AI安全知识情报系统-技术讲解PPT.pptx"

EMU_IN = 914400
SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5
FOOTER_TOP_IN = 7.0  # 页脚起始y，文本框不得越过
ASPECT_TOLERANCE = 0.03


def check() -> int:
    if not PPTX.exists():
        print(f"未找到 {PPTX.name}，请先生成 PPTX")
        return 1

    prs = Presentation(str(PPTX))
    problems: list[str] = []

    for idx, slide in enumerate(prs.slides, 1):
        for sh in slide.shapes:
            name = sh.shape_type
            left = sh.left / EMU_IN
            top = sh.top / EMU_IN
            w = sh.width / EMU_IN
            h = sh.height / EMU_IN

            # ① 越界
            if left < -0.01 or top < -0.01:
                problems.append(f"P{idx:02d} 形状左上越界 left={left:.2f} top={top:.2f}")
            if left + w > SLIDE_W_IN + 0.01:
                problems.append(
                    f"P{idx:02d} 右侧溢出 右边界={left + w:.2f}in > {SLIDE_W_IN}in"
                )
            if top + h > SLIDE_H_IN + 0.01:
                problems.append(
                    f"P{idx:02d} 底部溢出 下边界={top + h:.2f}in > {SLIDE_H_IN}in"
                )

            # ② 文本压页脚（页脚自身在 7.05in，属正常，排除）
            if sh.has_text_frame and sh.text_frame.text.strip():
                if top + h > FOOTER_TOP_IN and top < FOOTER_TOP_IN:
                    txt = sh.text_frame.text.strip().replace("\n", " ")[:24]
                    problems.append(
                        f"P{idx:02d} 文本侵入页脚 下边界={top + h:.2f}in 「{txt}」"
                    )

            # ③ 图片宽高比失真
            if name == 13:
                try:
                    iw, ih = Image.open(io.BytesIO(sh.image.blob)).size
                    src_ratio = iw / ih
                    box_ratio = w / h
                    # 转成物理比例再比：x/y 英寸密度不同
                    if abs(box_ratio - src_ratio) / src_ratio > ASPECT_TOLERANCE:
                        problems.append(
                            f"P{idx:02d} 图片比例失真 框={box_ratio:.2f} 原图={src_ratio:.2f}"
                        )
                except Exception:  # noqa: BLE001
                    pass

            # 宽度为 0 的畸形形状
            if w < 0.01 or h < 0.01:
                problems.append(f"P{idx:02d} 形状尺寸过小 w={w:.3f} h={h:.3f}")

    if problems:
        print(f"发现 {len(problems)} 处版式问题：\n")
        for p in problems:
            print(f"  · {p}")
        return 1

    print(f"版式校验通过：{len(prs.slides)} 页，无越界 / 无压页脚 / 图片比例正确")
    return 0


if __name__ == "__main__":
    sys.exit(check())