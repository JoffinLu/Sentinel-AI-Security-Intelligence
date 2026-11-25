"""
技术报告插图生成

为技术报告与答辩 PPT 生成四类图：
  ① 总体架构图     —— 四层架构 + 三大模块
  ② 情报采集流程图 —— 8 源采集 + 增量 + 去重 + 分类
  ③ 智能体编排图   —— LangGraph 状态机（条件边/并行/反思）
  ④ 富化七维图     —— 7 维富化与置信度分层

用 matplotlib 直接绘制，保证中文字体正确、风格统一。
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---------- 样式配置 ----------
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

C_BG = "#f7f9fc"
C_LAYER = ["#e8f0fe", "#e6f7ef", "#fef7e6", "#fce8ec"]
C_BORDER = ["#4285f4", "#0f9d58", "#f9ab00", "#ea4335"]
C_TEXT = "#202124"
C_SUB = "#5f6368"


def _box(ax, x, y, w, h, text, fc, ec, fontsize=10, bold=False, subtext=None):
    """绘制圆角矩形 + 居中文字（有副标题时按行数自适应上下留白）"""
    box = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.012,rounding_size=0.02",
        facecolor=fc, edgecolor=ec, linewidth=1.6,
    )
    ax.add_patch(box)

    if subtext:
        # 标题占 h*0.58（下部），副标题占 h*0.30（下部），按实际行数微调，
        # 避免多行标题与副标题重叠（旧实现固定 ±0.022/0.032 偏移必然撞车）
        main_lines = text.count("\n") + 1
        line_h = 0.030
        main_h = main_lines * line_h
        sub_h = (subtext.count("\n") + 1) * (line_h * 0.78)
        total = main_h + sub_h
        # 整体垂直居中
        top_y = y + h / 2 + total / 2
        main_cy = top_y - main_h / 2
        sub_cy = top_y - main_h - sub_h / 2
        ax.text(x + w / 2, main_cy, text, ha="center", va="center",
                fontsize=fontsize, fontweight="bold" if bold else "normal",
                color=C_TEXT, linespacing=1.15)
        ax.text(x + w / 2, sub_cy, subtext, ha="center", va="center",
                fontsize=fontsize - 2.2, color=C_SUB, linespacing=1.3)
    else:
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
                fontsize=fontsize, fontweight="bold" if bold else "normal",
                color=C_TEXT, linespacing=1.15)


def _arrow(ax, x1, y1, x2, y2, label="", color="#5f6368", style="-|>", ls="-"):
    """绘制箭头"""
    ax.add_patch(
        FancyArrowPatch(
            (x1, y1), (x2, y2),
            arrowstyle=style, mutation_scale=13,
            color=color, linewidth=1.4, linestyle=ls, shrinkA=0, shrinkB=0,
        )
    )
    if label:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.022, label,
                ha="center", va="bottom", fontsize=8, color=color,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.85))


def _canvas(w=13.6, h=8.0):
    fig, ax = plt.subplots(figsize=(w, h), dpi=170)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.patch.set_facecolor(C_BG)
    ax.set_facecolor(C_BG)
    return fig, ax


# ========== 图 1：总体架构 ==========


def gen_architecture(out: Path):
    fig, ax = _canvas(13.6, 8.4)
    ax.text(0.5, 0.955, "AI 安全知识情报系统总体架构",
            ha="center", fontsize=18, fontweight="bold", color=C_TEXT)
    ax.text(0.5, 0.915,
            "智能体驱动 · 四层架构 · 三大模块",
            ha="center", fontsize=10.5, color=C_SUB)

    layers = [
        ("可视化层", "React 18 + TypeScript + Ant Design 5 + ECharts",
         "监控看板　│　情报中心　│　智能问答（SSE 流式）", 0.735),
        ("编排层", "LangGraph 状态机　·　多 Agent 协作",
         "监测Agent → 优先级判定 → (富化 ∥ 问答) → 主管反思", 0.565),
        ("能力层", "三大核心模块 + 支撑组件",
         "情报监测(8源)　│　AI分类(三级)　│　精准富化(7维)　│　混合检索+图谱多跳",
         0.375),
        ("存储层", "数据存储与监控",
         "PostgreSQL16+pgvector　│　Neo4j 5　│　Redis 7　│　Prometheus+Grafana",
         0.185),
    ]

    for i, (name, tech, detail, y) in enumerate(layers):
        # 层标签
        ax.text(0.022, y + 0.055, name, ha="left", va="center",
                fontsize=12.5, fontweight="bold", color=C_BORDER[i])
        _box(ax, 0.13, y, 0.845, 0.135, tech, C_LAYER[i], C_BORDER[i], 10.5, True)
        ax.text(0.13, y - 0.028, detail, ha="left", va="top",
                fontsize=9, color=C_SUB)

    # 层间箭头
    for i in range(len(layers) - 1):
        y_top = layers[i][3]
        y_bot = layers[i + 1][3] + 0.135
        _arrow(ax, 0.5, y_bot, 0.5, y_top - 0.008)

    # 右侧标注：赛题对应
    ax.text(
        0.985, 0.42,
        "对应赛题三大核心能力\n\n"
        "① 情报监测模块\n   多源自动化采集\n\n"
        "② 情报富化模块\n   7 维深度加工\n\n"
        "③ 情报问答模块\n   语义检索+关联推理",
        ha="right", va="center", fontsize=8.6, color=C_SUB,
        bbox=dict(boxstyle="round,pad=0.55", fc="white", ec="#dadce0", linewidth=1),
    )

    fig.savefig(out, bbox_inches="tight", facecolor=C_BG)
    plt.close(fig)
    print(f"  [1/4] 总体架构图 → {out.name}")


# ========== 图 2：情报采集流程 ==========


def gen_collect_flow(out: Path):
    # 横向布局：适配 PPT 第 5 页插图区（11.9in × 2.85in，约 4.18:1）
    # 注意 _box 的 boxstyle 带 pad=0.012，框会向外扩，因此间距要留 ≥0.024
    fig, ax = _canvas(13.6, 3.25)
    ax.text(0.5, 0.940, "情报采集与 AI 相关性过滤流程",
            ha="center", fontsize=16, fontweight="bold", color=C_TEXT)
    ax.text(0.5, 0.828,
            "9 类数据源 → 统一归一化 → 跨源去重 → 三级 AI 相关性过滤",
            ha="center", fontsize=9.6, color=C_SUB)

    # ---- 第一列：数据源分组（3 组横排，组内条目竖排）----
    groups = [
        ("漏洞库 4 源", ["NVD CVE 2.0", "GitHub Advisory", "OSV.dev", "CISA KEV"], "#e8f0fe", "#4285f4"),
        ("学术与标准", ["arXiv 论文", "MITRE ATLAS"], "#e6f7ef", "#0f9d58"),
        ("社区与政策", ["安全博客 RSS", "POC 代码", "政策法规 NIST"], "#fef7e6", "#f9ab00"),
    ]
    gw, ggap, ih, igap = 0.088, 0.014, 0.070, 0.030
    gx = 0.015
    for title, items, fc, ec in groups:
        _box(ax, gx, 0.630, gw, 0.075, title, fc, ec, 8.4, True)
        for i, it in enumerate(items):
            _box(ax, gx, 0.525 - i * (ih + igap), gw, ih, it, "white", ec, 7.0)
        gx += gw + ggap
    col1_right = gx - ggap  # 第一列右边界

    # ---- 第二列：归一化 / 去重 ----
    mid_x, mid_w = 0.335, 0.135
    _arrow(ax, col1_right + 0.010, 0.470, mid_x - 0.010, 0.470)
    # 框高需容下：主标题 1 行 + 副标题 3 行（约 0.30），并留 0.04 余量
    _box(ax, mid_x, 0.330, mid_w, 0.300, "", "#e8f0fe", "#4285f4")
    ax.text(mid_x + mid_w / 2, 0.545, "统一归一化", ha="center", va="center",
            fontsize=9.0, fontweight="bold", color=C_TEXT)
    ax.text(mid_x + mid_w / 2, 0.435, "（RawItem）\nCVE 去重键 / CVSS\n资产 / 时间戳",
            ha="center", va="center", fontsize=6.4, color=C_SUB, linespacing=1.6)
    # 箭头走在两框之间的空隙（0.330 下方）
    _arrow(ax, mid_x + mid_w / 2, 0.318, mid_x + mid_w / 2, 0.300)
    _box(ax, mid_x, 0.060, mid_w, 0.240, "", "#e8f0fe", "#4285f4")
    ax.text(mid_x + mid_w / 2, 0.232, "跨源去重\n+ 增量游标", ha="center", va="center",
            fontsize=8.4, fontweight="bold", color=C_TEXT, linespacing=1.3)
    ax.text(mid_x + mid_w / 2, 0.118, "同一 CVE 多源合并\n保留全部溯源",
            ha="center", va="center", fontsize=6.0, color=C_SUB, linespacing=1.4)

    # ---- 第三列：三级过滤（横排 3 级）----
    fx = 0.505
    _arrow(ax, mid_x + mid_w + 0.010, 0.470, fx - 0.010, 0.470)
    ax.text(fx + 0.165, 0.700, "三级 AI 相关性过滤", ha="center", va="center",
            fontsize=10.5, fontweight="bold", color="#ea4335")
    stages = [
        ("规则层", "13类 90+ 关键词\n38 个排除词\n60 个 AI 组件", "#fce8ec", "#ea4335"),
        ("嵌入层", "bge-small-zh\n12 条基准语句\n跨语言匹配", "#fef7e6", "#f9ab00"),
        ("LLM 层", "批量复核 5条/批\n预算上限 6 批\n溢出走启发式", "#e8f0fe", "#4285f4"),
    ]
    sw, sgap = 0.098, 0.018
    sx = fx
    for i, (title, detail, fc, ec) in enumerate(stages):
        # 文字自行定位，故_box 传空串
        _box(ax, sx, 0.090, sw, 0.530, "", fc, ec)
        ax.text(sx + sw / 2, 0.545, title, ha="center", va="center",
                fontsize=9.2, fontweight="bold", color=C_TEXT)
        ax.text(sx + sw / 2, 0.320, detail, ha="center", va="center",
                fontsize=6.4, color=C_SUB, linespacing=1.7)
        if i < len(stages) - 1:
            _arrow(ax, sx + sw + 0.003, 0.355, sx + sw + sgap - 0.003, 0.355)
        sx += sw + sgap
    col3_right = sx - sgap

    # ---- 第四列：成果 ----
    ox, ow = 0.862, 0.125
    _arrow(ax, col3_right + 0.010, 0.470, ox - 0.010, 0.470)
    _box(ax, ox, 0.090, ow, 0.530, "", "#ffffff", "#4285f4")
    ax.text(ox + ow / 2, 0.545, "入库成果", ha="center", va="center",
            fontsize=9.2, fontweight="bold", color=C_TEXT)
    ax.text(ox + ow / 2, 0.310,
            "累计入库\n5,085 条\n\nAI 相关\n168 条\n\n增量延迟\n1.7 秒",
            ha="center", va="center", fontsize=6.8, color=C_TEXT, linespacing=1.6)

    fig.savefig(out, bbox_inches="tight", facecolor=C_BG)
    plt.close(fig)
    print(f"  [2/4] 采集流程图 → {out.name}")


# ========== 图 3：智能体编排 ==========


def gen_agent_graph(out: Path):
    fig, ax = _canvas(13.0, 7.8)
    ax.text(0.5, 0.955, "LangGraph 智能体编排状态机",
            ha="center", fontsize=18, fontweight="bold", color=C_TEXT)
    ax.text(0.5, 0.915,
            "条件边 · 并行节点 · 反思循环 · 人工介入",
            ha="center", fontsize=10.5, color=C_SUB)

    node_w, node_h = 0.145, 0.082

    def node(x, y, text, fc, ec, fs=10, sub=None):
        _box(ax, x, y, node_w, node_h, text, fc, ec, fs, True, sub)

    # 主干
    node(0.035, 0.700, "START", "#e8e8e8", "#9aa0a6", 9.5)
    node(0.215, 0.700, "监测 Agent", "#e8f0fe", "#4285f4", 10, "8 源增量采集")
    node(0.410, 0.700, "优先级判定", "#fce8ec", "#ea4335", 10, "AI 相关? CVSS≥7?")

    # 条件分支
    node(0.625, 0.795, "富化 Agent", "#e6f7ef", "#0f9d58", 9.6, "7 维并行")
    node(0.625, 0.605, "跳过富化", "#f0f0f0", "#9aa0a6", 9.6, "价值不足")

    # 并行汇合
    node(0.830, 0.700, "问答 Agent", "#e8f0fe", "#4285f4", 10, "语义+图谱")

    node(0.400, 0.400, "主管 Agent", "#fef7e6", "#f9ab00", 10, "反思 + 质量门禁")
    node(0.185, 0.400, "反思重试", "#fce8ec", "#ea4335", 9.6, "最多 2 次")
    node(0.625, 0.400, "人工核验", "#fce8ec", "#ea4335", 9.6, "置信度<0.5")
    node(0.625, 0.185, "END", "#e8e8e8", "#9aa0a6", 9.5)

    # 连线
    _arrow(ax, 0.18, 0.741, 0.215, 0.741)
    _arrow(ax, 0.36, 0.741, 0.410, 0.741)
    _arrow(ax, 0.555, 0.760, 0.625, 0.836, "值得")
    _arrow(ax, 0.555, 0.722, 0.625, 0.646, "不值得")
    _arrow(ax, 0.770, 0.836, 0.900, 0.760)
    _arrow(ax, 0.770, 0.646, 0.900, 0.722)
    _arrow(ax, 0.900, 0.700, 0.760, 0.480, "并行汇合")
    _arrow(ax, 0.697, 0.795, 0.545, 0.482, "跳过高置信度富化")
    _arrow(ax, 0.472, 0.400, 0.330, 0.400, "可恢复错误", ls="--")
    _arrow(ax, 0.257, 0.400, 0.260, 0.205, "")
    _arrow(ax, 0.330, 0.205, 0.555, 0.400, "重试后复判", ls="--")
    _arrow(ax, 0.545, 0.400, 0.660, 0.445, "需人工")
    _arrow(ax, 0.697, 0.400, 0.660, 0.267)

    # 图例
    legend = [
        ("普通流转", "#5f6368", "-"),
        ("条件判断", "#ea4335", "-"),
        ("反思循环", "#ea4335", "--"),
    ]
    lx = 0.045
    for text, color, ls in legend:
        ax.plot([lx, lx + 0.038], [0.085, 0.085], color=color, lw=1.8, ls=ls)
        ax.text(lx + 0.048, 0.085, text, ha="left", va="center",
                fontsize=9, color=C_SUB)
        lx += 0.155

    # 特性说明
    ax.text(0.985, 0.60,
            "编排能力对应优化指标\n\n"
            "▸ 条件边\n  triage 决策是否富化\n  supervisor 判断是否重试\n\n"
            "▸ 并行节点\n  富化与问答同时执行\n\n"
            "▸ 反思循环\n  超时/限流自动重试\n\n"
            "▸ 人工介入\n  低置信度转核验队列\n\n"
            "▸ Agent 熔断\n  连续失败3次跳过该Agent",
            ha="right", va="center", fontsize=8.4, color=C_TEXT, linespacing=1.5,
            bbox=dict(boxstyle="round,pad=0.55", fc="white", ec="#f9ab00", linewidth=1.3),
        )

    fig.savefig(out, bbox_inches="tight", facecolor=C_BG)
    plt.close(fig)
    print(f"  [3/4] 智能体编排图 → {out.name}")


# ========== 图 4：七维富化 ==========


def gen_enrichment(out: Path):
    fig, ax = _canvas(13.6, 7.4)
    ax.text(0.5, 0.955, "七维富化体系与置信度分层",
            ha="center", fontsize=18, fontweight="bold", color=C_TEXT)
    ax.text(0.5, 0.915,
            "维度丰富度（功能指标）+ 置信度可核验（准确率指标）",
            ha="center", fontsize=10.5, color=C_SUB)

    dims = [
        ("① CVSS 评分", "规范公式计算\n与NVD数据交叉验证\n2119/2274 一致", 0.95, "#4285f4"),
        ("② 受影响资产", "CPE解析\n版本范围\n生态归一化", 0.88, "#0f9d58"),
        ("③ POC 关联", "仓库匹配\n星标加权\n可用性评估", 0.79, "#f9ab00"),
        ("④ 关联论文", "语义相似度\n同义扩展\n主题加权", 0.71, "#ea4335"),
        ("⑤ 修复建议", "补丁版本\n修复步骤\n缓解措施", 0.86, "#8430ce"),
        ("⑥ 攻击技映射", "规则匹配ATLAS\nLLM受限集判定\n传统攻击覆盖", 0.61, "#1a73e8"),
        ("⑦ AI风险定级", "四分类归属\n业务影响\nP1-P4优先级", 0.83, "#e8710a"),
    ]

    n = len(dims)
    left, right = 0.055, 0.945
    col_w = (right - left) / n
    for i, (name, detail, conf, color) in enumerate(dims):
        x = left + i * col_w
        w = col_w - 0.022
        h = 0.30
        y = 0.44
        _box(ax, x, y, w, h, name, "white", color, 10.2, True)
        ax.text(x + w / 2, y - 0.075, detail, ha="center", va="top",
                fontsize=8.2, color=C_SUB, linespacing=1.6)

        # 置信度条
        bar_y = y - 0.215
        ax.add_patch(
            FancyBboxPatch((x, bar_y), w, 0.022,
                            boxstyle="round,pad=0.001,rounding_size=0.008",
                            facecolor="#e8eaed", edgecolor="none")
        )
        tier = "high" if conf >= 0.8 else ("medium" if conf >= 0.5 else "low")
        bar_color = {"high": "#0f9d58", "medium": "#f9ab00", "low": "#ea4335"}[tier]
        ax.add_patch(
            FancyBboxPatch((x, bar_y), w * conf, 0.022,
                            boxstyle="round,pad=0.001,rounding_size=0.008",
                            facecolor=bar_color, edgecolor="none")
        )
        ax.text(x + w / 2, bar_y - 0.022, f"{conf:.2f}",
                ha="center", va="top", fontsize=9.4,
                fontweight="bold", color=bar_color)

    # 置信度分层图例
    ax.text(0.5, 0.325, "置信度分层机制", ha="center", fontsize=12.5,
            fontweight="bold", color=C_TEXT)
    tiers = [
        ("高置信 ≥0.8", "确定性计算或权威数据源", "可直接采信", "#0f9d58"),
        ("中置信 0.5-0.8", "规则或模型推断", "展示并标注", "#f9ab00"),
        ("低置信 <0.5", "可靠性不足", "转人工核验队列", "#ea4335"),
    ]
    tx = 0.16
    for title, desc, action, color in tiers:
        w = 0.225
        _box(ax, tx, 0.155, w, 0.105, title, "white", color, 10, True)
        ax.text(tx + w / 2, 0.205, desc, ha="center", va="center",
                fontsize=8.4, color=C_SUB)
        ax.text(tx + w / 2, 0.175, action, ha="center", va="center",
                fontsize=8.8, color=color, fontweight="bold")
        tx += w + 0.028

    # 底部成果
    ax.text(0.5, 0.065,
            "富化完整度 100%（7/7 维）　·　基准评测准确率 100%（20/20）　·　"
            "已富化 168 条　·　平均置信度 0.83",
            ha="center", va="center", fontsize=10.5, color=C_TEXT,
            bbox=dict(boxstyle="round,pad=0.5", fc="#e8f0fe", ec="#4285f4", linewidth=1.3),
        )

    fig.savefig(out, bbox_inches="tight", facecolor=C_BG)
    plt.close(fig)
    print(f"  [4/4] 七维富化图 → {out.name}")


def main():
    out_dir = Path(__file__).resolve().parents[1] / "图表"
    out_dir.mkdir(parents=True, exist_ok=True)
    print("生成技术报告插图：")
    gen_architecture(out_dir / "系统架构图.png")
    gen_collect_flow(out_dir / "情报采集流程图.png")
    gen_agent_graph(out_dir / "智能体编排图.png")
    gen_enrichment(out_dir / "七维富化图.png")
    print("全部完成")


if __name__ == "__main__":
    main()
