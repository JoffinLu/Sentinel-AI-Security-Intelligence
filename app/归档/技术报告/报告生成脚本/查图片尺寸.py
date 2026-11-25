# -*- coding: utf-8 -*-
import zipfile, io, re
from PIL import Image

ROOT = Path = __import__('pathlib').Path
docx = Path(__file__).resolve().parents[3] / "归档" / "技术报告" / "AI安全知识情报系统技术报告.docx"
z = zipfile.ZipFile(docx)
xml = z.read("word/document.xml").decode("utf-8")

exts = re.findall(r'<wp:extent cx="(\d+)" cy="(\d+)"', xml)
print(f"图片显示尺寸（英寸）: {len(exts)} 处")
inches = [(int(cx)/914400, int(cy)/914400) for cx, cy in exts]
print("  宽度范围: %.2f ~ %.2f, 高度范围: %.2f ~ %.2f" % (
    min(w for w, h in inches), max(w for w, h in inches),
    min(h for w, h in inches), max(h for w, h in inches)))

media = [n for n in z.namelist() if n.startswith("word/media/") and n.endswith(".png")]
media.sort(key=lambda n: -z.getinfo(n).file_size)
print("像素尺寸 Top10:")
for n in media[:10]:
    img = Image.open(io.BytesIO(z.read(n)))
    w_in = 6.5
    print(f"  {img.size[0]}x{img.size[1]}px ({z.getinfo(n).file_size//1024} KB) -> 6.5in 显示宽度下约 {img.size[0]/w_in:.0f} DPI")
