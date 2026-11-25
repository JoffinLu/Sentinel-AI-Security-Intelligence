# -*- coding: utf-8 -*-
"""把报告插图/截图复制到 PPT 素材目录，文件名带尺寸（供 pptxgenjs 等比缩放）。"""
from PIL import Image
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]          # ICT-WB
SRC_FIG = ROOT / "归档" / "技术报告" / "插图"
SRC_SHOT = ROOT / "归档" / "技术报告" / "截图"
DST = Path(__file__).resolve().parent / "素材"
DST.mkdir(exist_ok=True)

# (源文件, 目标短名) —— PPT 要用的图
USE = {
    "图1_系统分层架构.png": "fig_arch",
    "图2_全链路数据流.png": "fig_flow",
    "图3_智能体状态机.png": "fig_state",
    "图4_三级AI分类.png": "fig_classify",
    "图6_富化七维.png": "fig_enrich",
    "图7_情报分布.png": "fig_dist",
    "图9_压测性能.png": "fig_bench",
    "图10_问答评测.png": "fig_qa",
    "截图_落地首页_黑夜.png": "shot_landing_dark",
    "截图_智能问答_黑夜.png": "shot_qa_dark",
    "截图_智能问答_白天.png": "shot_qa_light",
    "截图_态势总览_黑夜.png": "shot_situation_dark",
    "截图_知识图谱_黑夜.png": "shot_graph_dark",
}

for src_name, short in USE.items():
    src = (SRC_FIG if src_name.startswith("图") else SRC_SHOT) / src_name
    im = Image.open(src)
    w, h = im.size
    dst = DST / f"{short}_{w}x{h}.png"
    shutil.copyfile(src, dst)
    print(f"{dst.name}  ({w}x{h}, ratio {w/h:.2f})")
print("\n素材目录:", DST)