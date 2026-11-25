"""图 10 · 问答评测图（响应时间分布与分题型准确率）

来源：由 报告生成脚本/绘图_数据图.py 提取，绘图逻辑与原脚本一致。
数据源：归档/技术报告/数据/问答评测报告.json（缺失时回退 _全LLM 版本）。
运行：.venv/Scripts/python.exe "归档/技术报告/插图/图10_问答评测.py"
输出：覆盖本目录 图10_问答评测.png
"""

import matplotlib.pyplot as plt
import numpy as np

from 绘图工具 import G_MAIN, GOLD, RED, NAVY, INK, MASK, load_json, style_ax, thresh, save_chart

# ============================================================
# 可调参数
# ============================================================
FIG_W, FIG_H = 10.0, 4.0
OUT_NAME = "图10_问答评测.png"
JSON_NAME = "问答评测报告.json"
JSON_FALLBACK = "问答评测报告_全LLM.json"

BIN_STEP = 400            # 响应时间直方图分箱宽度（ms）
P95_LINE = 5000           # 赛题上限参考线（ms）
P95_LINE_LABEL = "赛题 ≤5s"
ACC_LIMIT = 85            # 准确率要求线（%）
ACC_LIMIT_LABEL = "≥85%"

# 题型英文名 → 中文显示名
CAT_NAMES = {"fact": "事实查询", "relation": "关联推理", "trend": "趋势分析",
             "advice": "处置建议", "detail": "处置建议", "multihop": "多跳推理"}


def draw():
    try:
        d = load_json(JSON_NAME)
    except FileNotFoundError:
        try:
            d = load_json(JSON_FALLBACK)
        except FileNotFoundError:
            print(f"✗ {JSON_NAME} / {JSON_FALLBACK} 不存在，跳过")
            return
    results = d["results"]
    lat = sorted(r["elapsed_ms"] for r in results)

    fig, axes = plt.subplots(1, 2, figsize=(FIG_W, FIG_H), constrained_layout=True)
    s = d["summary"]
    fig.suptitle(
        f"问答评测：{s['total']} 题 · 准确率 {s['accuracy']}% · 多跳 {s['multihop_accuracy']}%",
        fontsize=12.5, fontweight="bold", color=NAVY)

    ax = axes[0]
    style_ax(ax)
    bins = np.arange(0, max(lat) + BIN_STEP, BIN_STEP)
    ax.hist(lat, bins=bins, color=G_MAIN, edgecolor="white", lw=0.6)
    ax.set_xlim(0, max(max(lat) + 900, 5400))
    ax.set_ylim(0, ax.get_ylim()[1] * 1.18)
    p95 = s["latency"]["p95_ms"]
    ax.axvline(p95, color=RED, ls="--", lw=1.4)
    ax.text(p95 + 90, ax.get_ylim()[1] * 0.94, f"P95 = {p95}ms", fontsize=8.4,
            color=RED, va="top", ha="left", bbox=MASK)
    ax.axvline(P95_LINE, color=NAVY, ls=":", lw=1.4)
    ax.text(P95_LINE - 70, ax.get_ylim()[1] * 0.74, P95_LINE_LABEL, fontsize=7.8,
            color=NAVY, va="top", ha="right", bbox=MASK)
    ax.set_xlabel("单题响应时间（ms）", fontsize=9)
    ax.set_ylabel("题目数", fontsize=9)
    ax.set_title("响应时间分布", fontsize=10.5, color=NAVY, pad=10)

    ax = axes[1]
    style_ax(ax)
    cats = s["by_category"]
    labels = [CAT_NAMES.get(k, k) for k in cats]
    vals = [cats[k]["accuracy"] for k in cats]
    ns = [cats[k]["total"] for k in cats]
    bars = ax.bar(labels, vals, color=[G_MAIN if v >= ACC_LIMIT else GOLD for v in vals],
                  width=0.6, edgecolor="white", lw=0.6)
    for b, v, n in zip(bars, vals, ns):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.4,
                f"{v:.1f}%", ha="center", fontsize=8.6, color=INK)
        ax.text(b.get_x() + b.get_width() / 2, 3, f"n={n}", ha="center",
                fontsize=7, color="#ffffff", fontweight="bold")
    ax.set_xlim(-0.55, len(labels) + 0.10)
    ax.set_ylim(0, 116)
    thresh(ax, ACC_LIMIT, RED, ACC_LIMIT_LABEL, side="right")
    ax.set_ylabel("准确率（%）", fontsize=9)
    ax.set_title("分题型准确率（要求 ≥85%）", fontsize=10.5, color=NAVY, pad=10)

    save_chart(fig, OUT_NAME)


if __name__ == "__main__":
    draw()