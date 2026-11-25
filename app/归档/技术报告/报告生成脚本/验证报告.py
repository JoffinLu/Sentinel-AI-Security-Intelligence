# -*- coding: utf-8 -*-
"""docx 完整性体检：XML 可解析、图片关系齐全、结构统计"""
import sys, zipfile, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCX = ROOT / "归档" / "技术报告" / "AI安全知识情报系统技术报告.docx"

print(f"目标: {DOCX.name}  {DOCX.stat().st_size/1024/1024:.2f} MB")

issues = []

# 1) zip 完整性
with zipfile.ZipFile(DOCX) as z:
    bad = z.testzip()
    if bad:
        issues.append(f"zip 损坏: {bad}")
    names = z.namelist()

    # 2) document.xml 可解析
    import xml.dom.minidom as md
    doc_xml = z.read("word/document.xml").decode("utf-8")
    md.parseString(doc_xml)

    # 3) 图片媒体文件
    media = [n for n in names if n.startswith("word/media/")]
    # 4) document.xml 中引用的 rId -> 关系表解析
    rels_xml = z.read("word/_rels/document.xml.rels").decode("utf-8")
    rel_map = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', rels_xml))
    used_rids = set(re.findall(r'r:embed="(rId\d+)"', doc_xml))
    missing_rel = [r for r in used_rids if r not in rel_map]
    if missing_rel:
        issues.append(f"引用了不存在的关系: {missing_rel}")
    img_targets = {rel_map[r] for r in used_rids if r in rel_map}
    missing_media = [t for t in img_targets if f"word/{t.lstrip('/')}" not in names]
    if missing_media:
        issues.append(f"引用的媒体文件缺失: {missing_media}")

    n_para = doc_xml.count("<w:p ") + doc_xml.count("<w:p>")
    n_tbl = doc_xml.count("<w:tbl>")
    n_draw = doc_xml.count("<w:drawing>")
    headers = re.findall(r'w:val="Heading(\d)"', doc_xml)

print(f"段落数≈{n_para}  表格={n_tbl}  图形={n_draw}  媒体文件={len(media)}  引用图片={len(used_rids)}")
from collections import Counter
print(f"标题层级: {dict(sorted(Counter(headers).items()))}")

# 5) python-docx 深度打开
try:
    from docx import Document
    d = Document(str(DOCX))
    n_text = sum(len(p.text) for p in d.paragraphs)
    print(f"python-docx 打开成功: 段落={len(d.paragraphs)} 表格={len(d.tables)} 正文={n_text} 字符")
except Exception as e:
    issues.append(f"python-docx 打开失败: {e}")

if issues:
    print("\n[FAIL] 发现问题:")
    for i in issues:
        print(" -", i)
    sys.exit(1)
print("\n[PASS] docx 结构完整性检查全部通过")
