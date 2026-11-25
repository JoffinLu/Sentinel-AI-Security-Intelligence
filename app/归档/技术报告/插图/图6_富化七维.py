"""图 6 · 七维富化实测图（各维度耗时与置信度分层）

来源：由 报告生成脚本/绘图_数据图.py 提取，绘图逻辑与原脚本一致。
数据源：本机 docker 容器 aiintel-postgres 的 enrichments 表。
运行：.venv/Scripts/python.exe "归档/技术报告/插图/图6_富化七维.py"
输出：覆盖本目录 图6_富化七维.png
"""

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

from 绘图工具 import G_MAIN, G_DARK, GOLD, RED, INK, NAVY, LEG, sql, style_ax, save_chart

# ============================================================
# 可调参数
# ============================================================
FIG_W, FIG_H = 10.0, 4.0
OUT_NAME = "图6_富化七维.png"
SUPTITLE = "七维富化实测：耗时分布与置信度分层（enrichments 表）"

# 维度英文名 → 中文显示名
DIM_NAMES = {
    "asset": "受影响资产", "cvss": "CVSS 评分", "fix": "修复建议",
    "paper": "关联论文", "poc": "POC 验证", "risk": "风险定级",
    "technique": "技术映射",
}

QUERY = (
    "SELECT dimension, COUNT(*), ROUND(AVG(confidence)::numeric,2), "
    "ROUND(AVG(elapsed_ms)::numeric,0) FROM enrichments "
    "WHERE status='success' GROUP BY 1 ORDER BY dimension"
)


def draw():
    rows = sql(QUERY)
    dims = [DIM_NAMES.get(r[0], r[0]) for r in rows]
    conf = [float(r[2]) for r in rows]
    ms = [float(r[3]) for r in rows]
    counts = [int(r[1]) for r in rows]

    fig, axes = plt.subplots(1, 2, figsize=(FIG_W, FIG_H), constrained_layout=True)
    fig.suptitle(SUPTITLE, fontsize=12.5, fontweight="bold", color=NAVY)

    ax = axes[0]
    style_ax(ax)
    x = np.arange(len(dims))
    bars = ax.bar(x, [m / 1000 for m in ms], color=G_MAIN, width=0.6,
                  edgecolor="white", lw=0.6)
    for b, v in zip(bars, ms):
        label = f"{v/1000:.1f}s" if v >= 1000 else f"{v:.0f}ms"
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + max(ms) / 1000 * 0.025,
                label, ha="center", fontsize=8.2, color=INK)
    ax.set_xticks(x)
    ax.set_xticklabels(dims, fontsize=8.0)
    ax.set_ylabel("平均耗时（秒）", fontsize=9)
    ax.set_ylim(0, max(ms) / 1000 * 1.18)
    ax.set_title("各维度平均耗时", fontsize=10.5, color=NAVY, pad=10)

    ax = axes[1]
    style_ax(ax)
    colors = [G_DARK if c >= 0.8 else (GOLD if c >= 0.5 else RED) for c in conf]
    bars = ax.bar(x, conf, color=colors, width=0.6, edgecolor="white", lw=0.6)
    for b, v, n in zip(bars, conf, counts):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.018,
                f"{v:.2f}", ha="center", fontsize=8.2, color=INK)
        ax.text(b.get_x() + b.get_width() / 2, 0.035, f"n={n}", ha="center",
                fontsize=6.8, color="#ffffff", fontweight="bold")
    ax.axhline(0.8, color=G_DARK, ls="--", lw=1.1, alpha=0.8)
    ax.axhline(0.5, color=GOLD, ls="--", lw=1.1, alpha=0.8)
    handles = [Line2D([], [], color=G_DARK, ls="--", lw=1.5, label="高置信 ≥0.8"),
               Line2D([], [], color=GOLD, ls="--", lw=1.5, label="中置信 0.5~0.8")]
    ax.legend(handles=handles, loc="upper right", fontsize=7.4, **LEG)
    ax.set_xticks(x)
    ax.set_xticklabels(dims, fontsize=8.0)
    ax.set_ylabel("平均置信度", fontsize=9)
    ax.set_ylim(0, 1.20)
    ax.set_title("各维度平均置信度", fontsize=10.5, color=NAVY, pad=10)

    save_chart(fig, OUT_NAME)


if __name__ == "__main__":
    draw()