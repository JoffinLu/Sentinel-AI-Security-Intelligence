"""图 8 · 情报入库时序图（每日总量与 AI 范畴筛出量）

来源：由 报告生成脚本/绘图_数据图.py 提取，绘图逻辑与原脚本一致。
数据源：本机 docker 容器 aiintel-postgres 的 vulnerabilities 表。
运行：.venv/Scripts/python.exe "归档/技术报告/插图/图8_入库时序.py"
输出：覆盖本目录 图8_入库时序.png
"""

import matplotlib.pyplot as plt
import numpy as np

from 绘图工具 import G_MAIN, RED, INK, NAVY, LEG, sql, style_ax, save_chart

# ============================================================
# 可调参数
# ============================================================
FIG_W, FIG_H = 8.4, 3.9
OUT_NAME = "图8_入库时序.png"
TITLE = "情报入库时序：总量与 AI 范畴筛出量（按本地日聚合）"
LABEL_TOTAL = "每日入库总量"
LABEL_AI = "其中 AI 安全范畴"

QUERY = (
    "SELECT date_trunc('day', ingest_at) AT TIME ZONE 'Asia/Shanghai', "
    "COUNT(*), COUNT(*) FILTER (WHERE ai_category IS NOT NULL) "
    "FROM vulnerabilities GROUP BY 1 ORDER BY 1"
)


def draw():
    rows = sql(QUERY)
    days = [r[0][:10] for r in rows]
    total = [int(r[1]) for r in rows]
    ai = [int(r[2]) for r in rows]

    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H), constrained_layout=True)
    style_ax(ax)
    ax.set_title(TITLE, fontsize=12, fontweight="bold", color=NAVY, pad=12)
    x = np.arange(len(days))
    ax.fill_between(x, total, color=G_MAIN, alpha=0.14)
    ax.plot(x, total, "o-", color=G_MAIN, lw=1.9, ms=5, mfc="white", mew=1.6,
            label=LABEL_TOTAL, zorder=4)
    ax.plot(x, ai, "s-", color=RED, lw=1.6, ms=4.5, mfc="white", mew=1.4,
            label=LABEL_AI, zorder=4)
    for xi, v in zip(x, total):
        ax.annotate(f"{v:,}", (xi, v), textcoords="offset points",
                    xytext=(0, 9), ha="center", fontsize=7.8, color=INK, zorder=6)
    ax.set_xticks(x)
    ax.set_xticklabels(days, fontsize=8.2)
    ax.set_ylabel("条数", fontsize=9)
    ax.set_ylim(-max(total) * 0.04, max(total) * 1.22)
    ax.legend(fontsize=8.5, loc="upper right", **LEG)
    save_chart(fig, OUT_NAME)


if __name__ == "__main__":
    draw()