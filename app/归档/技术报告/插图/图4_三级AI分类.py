"""图 4 · 三级 AI 分类流水线图

来源：由 报告生成脚本/绘图_核心图.py 提取，绘图逻辑与原脚本一致。
运行：.venv/Scripts/python.exe "归档/技术报告/插图/图4_三级AI分类.py"
输出：覆盖本目录 图4_三级AI分类.png
"""

from 绘图工具 import (
    NAVY, GRAY, INK, G_MAIN, G_DARK, G_FILL, G_FILL2, GOLD, GOLD_FILL,
    BLUE, BLUE_FILL,
    new_fig, card2, badge, arrow, panel, save_diagram,
)

# ============================================================
# 可调参数
# ============================================================
FIG_W, FIG_H = 9.6, 5.3            # 画布尺寸（英寸）
DPI = 300
OUT_NAME = "图4_三级AI分类.png"

TITLE = "三级 AI 分类流水线：<1% 真 AI 漏洞浓度下的精准筛选"
SUBTITLE = "规则 → 嵌入 → LLM 逐级下沉 · 仅灰区条目进入下一级"

GAP = 0.026                         # 卡片间距
YS, HS = 0.600, 0.265               # 卡片底边 y / 高度
FLOW_NOTE = "▼  流量逐级递减：仅规则不确定（灰区）条目下沉到下一级，确定性条目就地判定并记录 method"

# 五级流水线：(标题, 正文, 填充色, 边框色)
STAGES = [
    ("输入", "新采集漏洞 ≈ 100%\n进入分类流水线", "#f2f4f3", GRAY),
    ("第一级 · 规则层", "13 类 90+ 关键词\n38 排除词 + 60 AI 组件\n零成本 · 确定性判定", G_FILL2, G_MAIN),
    ("第二级 · 嵌入层", "bge-small-zh 语义相似\n12 条基准语句\n5.8ms/条 · 10× 快于 LLM", BLUE_FILL, BLUE),
    ("第三级 · LLM 层", "批量复核（5 条/批）\n预算 30 条/源\n仅灰区条目", G_FILL, G_DARK),
    ("兜底 · 启发式", "预算超限时触发\nAI 组件倾向保留\n标记待人工核验", GOLD_FILL, GOLD),
]

# 底部两个信息面板：(标题, 正文)
PANELS = [
    ("判定结果（分类方法分布 · 实测 7789 条）",
     "rule 规则命中：432 条\n"
     "heuristic-overflow 兜底：3745 条\n"
     "llm / llm-batch 复核：135 条\n"
     "rule-exclude 排除：1288 条\n\n"
     "每条情报标注命中关键词 / 语义分数 / 复核结论，分类过程可解释"),
    ("过滤效果（NVD · GHSA 实测）",
     "7 天窗口 2563 条 CVE → 真 AI 范畴 <1%\n"
     "GHSA 500 条 → 识别 40 条（8%）\n"
     "典型识别：NLTK 投毒 / Gradio 路径穿越 / MLflow SSRF\n\n"
     "误杀控制：38 个排除词拦截 AI 无关关键词，灰区条目留痕可追溯"),
]


# ============================================================
# 绘图
# ============================================================
def draw():
    fig, ax = new_fig(FIG_W, FIG_H, dpi=DPI)

    ax.text(0.5, 0.955, TITLE, ha="center", va="center",
            fontsize=13, fontweight="bold", color=NAVY)
    ax.text(0.5, 0.912, SUBTITLE, ha="center", va="center",
            fontsize=8.0, color=GRAY)

    n = len(STAGES)
    w = (0.980 - GAP * (n - 1)) / n
    xs = [0.010 + i * (w + GAP) for i in range(n)]
    for i, (t, s, fc, ec) in enumerate(STAGES):
        card2(ax, xs[i], YS, w, HS, t, s, fc=fc, ec=ec,
              ts=8.8, ss=7.2, tc=ec, sc=INK, zorder=3)
        badge(ax, xs[i] + 0.012, YS + HS, str(i + 1), ec, "#ffffff", fs=8.0)
        if i < n - 1:
            arrow(ax, xs[i] + w + 0.002, YS + HS / 2,
                  xs[i + 1] - 0.002, YS + HS / 2, color=NAVY,
                  lw=2.4 - 0.35 * i, shrink=0)

    ax.text(0.5, 0.560, FLOW_NOTE, ha="center", va="center",
            fontsize=8.0, color=GRAY)

    for i, (header, body) in enumerate(PANELS):
        panel(ax, 0.015 + i * 0.500, 0.090, 0.470, 0.385, header, body, ec=NAVY)

    save_diagram(fig, OUT_NAME)


if __name__ == "__main__":
    draw()