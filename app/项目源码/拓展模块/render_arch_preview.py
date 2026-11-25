"""渲染 RoenArchDiagram 的 SVG 几何自检图（暗色主题逐色复刻）。

仅用于视觉校验坐标/连线/文字排版——最终以 React 组件为准。
（本机无头浏览器截图不可用，用 resvg 渲染静态复刻图做几何核对。）

用法（在项目根目录下运行）：python 项目源码/拓展模块/render_arch_preview.py
生成物：.tmp/arch_preview.png
"""

from __future__ import annotations

import resvg_py

MAIN, OK, WARM = "#4cd7f6", "#4edea3", "#ff8a5c"
TXT = {
    "main": ("#e9f8ff", "rgba(150,190,208,.78)"),
    "ok": ("#e8fff6", "rgba(150,205,180,.78)"),
    "warm": ("#fff1ea", "rgba(215,170,150,.80)"),
}
FONT = "'Microsoft YaHei', sans-serif"

NODES = [
    # key, x, y, w, h, label, sub, tone, outside, strong
    ("y-in", 44, 60, 108, 40, "Y 序列", "Yt-n … Yt", "main", True, False),
    ("y-mlp", 184, 60, 88, 40, "MLP", "多层感知", "main", False, False),
    ("y-safc", 308, 60, 88, 40, "SAFC", "自适应提取", "main", False, False),
    ("y-lot", 432, 60, 88, 40, "LOT", "循环优化", "main", False, False),
    ("y-out", 552, 60, 88, 40, "Yt+T", "显式表征", "main", False, False),
    ("x-in", 44, 300, 108, 40, "X 序列", "Xt-n … Xt", "ok", True, False),
    ("x-mlp", 184, 300, 88, 40, "MLP", "多层感知", "ok", False, False),
    ("x-tet", 308, 300, 88, 40, "TET", "拓扑提取", "ok", False, False),
    ("x-mcs", 432, 300, 88, 40, "MCS", "运动捕获", "ok", False, False),
    ("x-out", 552, 300, 88, 40, "Xt+T", "隐式表征", "ok", False, False),
    ("input", 44, 182, 108, 40, "当前窗口", "Xt · Yt", "warm", True, False),
    ("samlp", 521, 180, 150, 44, "SAMLP", "全局融合引擎", "warm", False, True),
    ("cls", 740, 182, 152, 40, "流量分类", "BENIGN / DDoS / PortScan", "main", False, False),
]

EDGES = [
    ("M152 80 H184", "main"), ("M272 80 H308", "main"),
    ("M396 80 H432", "main"), ("M520 80 H552", "main"),
    ("M152 320 H184", "ok"), ("M272 320 H308", "ok"),
    ("M396 320 H432", "ok"), ("M520 320 H552", "ok"),
    ("M596 100 V180", "warm"), ("M596 300 V224", "warm"),
    ("M152 202 H521", "warm"), ("M671 202 H740", "warm"),
    ("M892 202 H910", "main"),
]

LOOPS = [
    ("M596 60 V22 H98 V60", "main", "回环优化 · 反馈调整", 347, 13),
    ("M596 340 V378 H98 V340", "ok", "运动捕获 · 拓扑演化反馈", 347, 393),
]

parts: list[str] = []
parts.append(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 400">'
    '<rect width="960" height="400" fill="#0d1512"/>'
)

# 线程分组框
parts.append(
    f'<rect x="28" y="44" width="628" height="72" rx="12" fill="{MAIN}" fill-opacity="0.03" '
    f'stroke="{MAIN}" stroke-opacity="0.32" stroke-dasharray="5 4"/>'
    f'<rect x="28" y="284" width="628" height="72" rx="12" fill="{OK}" fill-opacity="0.03" '
    f'stroke="{OK}" stroke-opacity="0.32" stroke-dasharray="5 4"/>'
)

# 光晕层 + 主体层 + 光点（静态：光点画在路径起点）
for d, tone in EDGES:
    color = {"main": MAIN, "ok": OK, "warm": WARM}[tone]
    parts.append(
        f'<path d="{d}" fill="none" stroke="{color}" stroke-opacity="0.07" stroke-width="4"/>'
        f'<path d="{d}" fill="none" stroke="{color}" stroke-opacity="0.55" stroke-width="1.1"/>'
    )

for d, tone, label, lx, ly in LOOPS:
    color = {"main": MAIN, "ok": OK, "warm": WARM}[tone]
    parts.append(
        f'<path d="{d}" fill="none" stroke="{color}" stroke-opacity="0.08" stroke-width="4"/>'
        f'<path d="{d}" fill="none" stroke="{color}" stroke-opacity="0.42" stroke-width="1" stroke-dasharray="2.5 2.5"/>'
    )

# 注释
parts.append(
    f'<text x="36" y="38" fill="{MAIN}" font-family="{FONT}" font-size="9.5">显式特征提取 · 统计特征（Y）</text>'
    f'<text x="36" y="278" fill="{OK}" font-family="{FONT}" font-size="9.5">隐式特征提取 · 动态拓扑（X）</text>'
)
for d, tone, label, lx, ly in LOOPS:
    parts.append(
        f'<text x="{lx}" y="{ly}" fill="rgba(163,192,180,.92)" font-family="{FONT}" '
        f'font-size="9.5" text-anchor="middle">{label}</text>'
    )

# 节点
for key, x, y, w, h, label, sub, tone, outside, strong in NODES:
    color = {"main": MAIN, "ok": OK, "warm": WARM}[tone]
    t1, t2 = TXT[tone]
    fill = "rgba(9,17,15,.7)" if outside else "rgba(9,17,15,.84)"
    dash = ' stroke-dasharray="3 2.5"' if outside else ""
    glow = (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="none" '
        f'stroke="{color}" stroke-opacity="0.16" stroke-width="4"/>'
        if strong else ""
    )
    parts.append(
        f'{glow}<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{fill}" '
        f'stroke="{color}" stroke-opacity="{0.85 if strong else 0.5}" '
        f'stroke-width="{1.2 if strong else 0.8}"{dash}/>'
        f'<text x="{x + w / 2}" y="{y + h * 0.445}" fill="{t1}" font-family="{FONT}" '
        f'font-size="10.5" font-weight="600" text-anchor="middle">{label}</text>'
        f'<text x="{x + w / 2}" y="{y + h * 0.78}" fill="{t2}" font-family="{FONT}" '
        f'font-size="8.5" text-anchor="middle">{sub}</text>'
    )

parts.append(
    f'<circle cx="920" cy="202" r="4" fill="none" stroke="{MAIN}" stroke-opacity="0.45"/>'
    f'<circle cx="920" cy="202" r="1.5" fill="{MAIN}" fill-opacity="0.75"/>'
)
parts.append("</svg>")

svg = "".join(parts)
png = resvg_py.svg_to_bytes(svg_string=svg, width=1440)
with open(".tmp/arch_preview.png", "wb") as f:
    f.write(bytes(png))
print("rendered .tmp/arch_preview.png")
