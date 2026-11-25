"""图 9 · API 压测性能图（P95 延迟与吞吐）

来源：由 报告生成脚本/绘图_数据图.py 提取，绘图逻辑与原脚本一致。
数据源：归档/技术报告/数据/性能压测.json。
运行：.venv/Scripts/python.exe "归档/技术报告/插图/图9_压测性能.py"
输出：覆盖本目录 图9_压测性能.png
"""

import matplotlib.pyplot as plt
import numpy as np

from 绘图工具 import (
    G_LIGHT, G_MAIN, G_DARK, NAVY, RED, PALETTE, LEG,
    load_json, style_ax, thresh, save_chart,
)

# ============================================================
# 可调参数
# ============================================================
FIG_W, FIG_H = 10.6, 4.2
OUT_NAME = "图9_压测性能.png"
JSON_NAME = "性能压测.json"
SUPTITLE = "API 压测：核心只读接口 × 并发梯度（10/50/100/200）"

CONC = [10, 50, 100, 200]                      # 并发梯度
BAR_COLORS = [G_LIGHT, G_MAIN, G_DARK, NAVY]   # P95 柱状配色
P95_LIMIT = 5                                   # 赛题 P95 上限（秒）
P95_LIMIT_LABEL = "赛题要求 P95 ≤ 5s"


def draw():
    try:
        d = load_json(JSON_NAME)
    except FileNotFoundError:
        print(f"✗ {JSON_NAME} 不存在，跳过")
        return
    api = d["api"]
    endpoints = []
    for row in api:
        if row["endpoint"] not in endpoints:
            endpoints.append(row["endpoint"])

    fig, axes = plt.subplots(1, 2, figsize=(FIG_W, FIG_H), constrained_layout=True)
    fig.suptitle(SUPTITLE, fontsize=12.5, fontweight="bold", color=NAVY)

    ax = axes[0]
    style_ax(ax)
    x = np.arange(len(endpoints))
    w = 0.19
    p95 = {e: [next(r["p95_ms"] for r in api if r["endpoint"] == e and r["concurrency"] == c)
               for c in CONC] for e in endpoints}
    pmax = max(v for e in endpoints for v in p95[e])
    for i, c in enumerate(CONC):
        ax.bar(x + (i - 1.5) * w, [p95[e][i] / 1000 for e in endpoints],
               width=w, color=BAR_COLORS[i], label=f"c={c}", edgecolor="white", lw=0.5)
    ax.set_ylim(0, pmax / 1000 * 1.28)
    thresh(ax, P95_LIMIT, RED, P95_LIMIT_LABEL, side="right")
    ax.set_xticks(x)
    ax.set_xticklabels(endpoints, fontsize=8.2)
    ax.set_ylabel("P95 响应时间（秒）", fontsize=9)
    ax.legend(fontsize=8, ncol=4, loc="upper right", **LEG)
    ax.set_title("P95 延迟", fontsize=10.5, color=NAVY, pad=10)

    ax = axes[1]
    style_ax(ax)
    xc = np.arange(len(CONC))
    qps = {e: [next(r["qps"] for r in api if r["endpoint"] == e and r["concurrency"] == c)
               for c in CONC] for e in endpoints}
    for i, e in enumerate(endpoints):
        ax.plot(xc, qps[e], "o-", color=PALETTE[i % len(PALETTE)], lw=1.8,
                ms=4.8, mfc="white", mew=1.5, label=e, zorder=4)
    ax.set_xticks(xc)
    ax.set_xticklabels([f"c={c}" for c in CONC], fontsize=8.6)
    ax.set_ylabel("吞吐 QPS", fontsize=9)
    ax.set_ylim(0, max(v for e in endpoints for v in qps[e]) * 1.18)
    ax.legend(fontsize=7.6, ncol=2, loc="center right", **LEG)
    ax.set_title("吞吐量", fontsize=10.5, color=NAVY, pad=10)

    save_chart(fig, OUT_NAME)


if __name__ == "__main__":
    draw()