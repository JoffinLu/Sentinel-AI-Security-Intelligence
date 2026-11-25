"""图 5 · 采集性能图（各源单次耗时与累计产出）

来源：由 报告生成脚本/绘图_数据图.py 提取，绘图逻辑与原脚本一致。
数据源：本机 docker 容器 aiintel-postgres 的 collection_logs 表。
运行：.venv/Scripts/python.exe "归档/技术报告/插图/图5_采集性能.py"
输出：覆盖本目录 图5_采集性能.png
"""

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from 绘图工具 import G_MAIN, BLUE, GOLD, INK, NAVY, LEG, sql, style_ax, save_chart

# ============================================================
# 可调参数
# ============================================================
FIG_W, FIG_H = 10.4, 4.3
OUT_NAME = "图5_采集性能.png"
SUPTITLE = "采集性能：9 类数据源实测（collection_logs 真实运行统计）"
XLABEL_LEFT = "单次采集平均耗时（秒，含限速等待）"
YLABEL_RIGHT = "累计采集条数"
YLABEL_RATE = "采集成功率（%）"

# 数据查询（各源平均耗时 / 累计产出 / 成功率）
QUERY = (
    "SELECT s.name, l.source_code, ROUND(AVG(l.latency_ms)), "
    "SUM(l.fetched_count), COUNT(*) FILTER (WHERE l.status='success'), COUNT(*) "
    "FROM collection_logs l JOIN sources s ON s.code=l.source_code "
    "GROUP BY 1,2 ORDER BY AVG(l.latency_ms) DESC"
)


def draw():
    rows = sql(QUERY)
    names = [r[0].replace(" ", "\n", 1) for r in rows]
    codes = [r[1] for r in rows]
    lat = [float(r[2]) / 1000 for r in rows]
    fetched = [int(r[3]) for r in rows]
    runs = [int(r[5]) for r in rows]
    ok = [int(r[4]) for r in rows]

    fig, axes = plt.subplots(1, 2, figsize=(FIG_W, FIG_H), constrained_layout=True)
    fig.suptitle(SUPTITLE, fontsize=12.5, fontweight="bold", color=NAVY)

    ax = axes[0]
    style_ax(ax)
    bars = ax.barh(names[::-1], lat[::-1], color=G_MAIN, height=0.62,
                   edgecolor="white", lw=0.6)
    for b, v in zip(bars, lat[::-1]):
        ax.text(b.get_width() + max(lat) * 0.014, b.get_y() + b.get_height() / 2,
                f"{v:.1f}s", va="center", fontsize=8.2, color=INK)
    ax.set_xlabel(XLABEL_LEFT, fontsize=9)
    ax.set_xlim(0, max(lat) * 1.20)
    ax.set_title("单次采集耗时", fontsize=10.5, color=NAVY, pad=10)

    ax = axes[1]
    style_ax(ax)
    x = np.arange(len(names))
    ax.bar(x, fetched, color=BLUE, width=0.58, label="累计采集条数",
           edgecolor="white", lw=0.6)
    ax.set_xticks(x)
    ax.set_xticklabels(codes, fontsize=7.6)
    for xi, v in zip(x, fetched):
        ax.text(xi, v + max(fetched) * 0.02, f"{v:,}", ha="center",
                fontsize=7.4, color=INK)
    ax.set_ylabel(YLABEL_RIGHT, fontsize=9)
    ax.set_ylim(0, max(fetched) * 1.30)

    ax2 = ax.twinx()
    ax2.plot(x, [o / r * 100 for o, r in zip(ok, runs)], "o-", color=GOLD,
             lw=1.7, ms=4.5, mfc="white", mew=1.4, zorder=5)
    ax2.set_ylim(60, 106)
    ax2.set_ylabel(YLABEL_RATE, fontsize=9, color=GOLD)
    ax2.tick_params(colors=GOLD, labelsize=8, length=0)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_color(GOLD)
    handles = [Patch(facecolor=BLUE, edgecolor="white", label="累计采集条数"),
               Line2D([], [], color=GOLD, marker="o", ms=4.5, lw=1.7,
                      mfc="white", mew=1.4, label="采集成功率")]
    ax.legend(handles=handles, loc="upper right", fontsize=7.8, **LEG)
    ax.set_title("累计产出与成功率", fontsize=10.5, color=NAVY, pad=10)

    save_chart(fig, OUT_NAME)


if __name__ == "__main__":
    draw()