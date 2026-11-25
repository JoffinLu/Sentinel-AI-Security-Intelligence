"""图 7 · 情报库分布三面板图（AI 类别 / 严重度 / 分类方法）

来源：由 报告生成脚本/绘图_数据图.py 提取，绘图逻辑与原脚本一致。
数据源：本机 docker 容器 aiintel-postgres 的 vulnerabilities 表。
运行：.venv/Scripts/python.exe "归档/技术报告/插图/图7_情报分布.py"
输出：覆盖本目录 图7_情报分布.png
"""

import matplotlib.pyplot as plt
import numpy as np

from 绘图工具 import (
    PALETTE, RED, GOLD, G_MAIN, G_DARK, GRAY, INK, NAVY,
    sql, style_ax, save_chart,
)

# ============================================================
# 可调参数
# ============================================================
FIG_W, FIG_H = 11.0, 3.9
OUT_NAME = "图7_情报分布.png"
SUPTITLE = "情报库分布：AI 类别 / 严重度 / 分类方法（PostgreSQL 实时统计）"

# 分类方法英文名 → 中文显示名
METHOD_NAMES = {
    "heuristic-overflow": "启发式兜底", "rule": "规则命中",
    "rule-exclude": "规则排除", "llm": "LLM 复核",
    "llm-batch": "LLM 批量", "llm-degraded": "LLM 降级", "": "未分类",
}
# 严重度面板：显示顺序 / 中文名 / 配色
SEV_ORDER = [("CRITICAL", "严重"), ("HIGH", "高危"),
             ("MEDIUM", "中危"), ("LOW", "低危")]
SEV_OTHER_LABEL = "未定级"
SEV_COLORS = [RED, "#d97706", GOLD, G_MAIN, GRAY]

QUERY_CAT = ("SELECT ai_category, COUNT(*) FROM vulnerabilities "
             "WHERE ai_category IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
QUERY_SEV = ("SELECT severity, COUNT(*) FROM vulnerabilities "
             "WHERE ai_category IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
QUERY_METHOD = ("SELECT ai_relevance_method, COUNT(*) FROM vulnerabilities "
                "GROUP BY 1 ORDER BY 2 DESC")


def _short_cat(s: str) -> str:
    return s.replace("AI ", "").replace("风险", "").strip() or s


def draw():
    cat = sql(QUERY_CAT)
    sev = sql(QUERY_SEV)
    method = sql(QUERY_METHOD)

    fig, axes = plt.subplots(1, 3, figsize=(FIG_W, FIG_H), constrained_layout=True)
    fig.suptitle(SUPTITLE, fontsize=12.5, fontweight="bold", color=NAVY)

    ax = axes[0]
    labels = [_short_cat(r[0]) for r in cat]
    vals = [int(r[1]) for r in cat]
    wedges, _, _ = ax.pie(
        vals, labels=None, autopct=lambda p: f"{p:.0f}%", pctdistance=0.80,
        colors=PALETTE, startangle=90, counterclock=False,
        wedgeprops=dict(width=0.40, edgecolor="white", lw=1.8),
        textprops=dict(fontsize=7.6, color="white", fontweight="bold"))
    ax.legend(wedges, [f"{l}  {v}" for l, v in zip(labels, vals)],
              loc="center", fontsize=7.4, frameon=False,
              labelspacing=0.9, handlelength=1.1, handletextpad=0.7)
    ax.set_title("AI 漏洞类别分布", fontsize=10.5, color=NAVY, pad=10)

    ax = axes[1]
    style_ax(ax)
    sev_map = {s: int(n) for s, n in sev}
    labels = [cn for _, cn in SEV_ORDER] + [SEV_OTHER_LABEL]
    known = {en for en, _ in SEV_ORDER}
    vals = [sev_map.get(en, 0) for en, _ in SEV_ORDER]
    vals.append(sum(v for k, v in sev_map.items() if k not in known))
    bars = ax.bar(labels, vals, color=SEV_COLORS, width=0.62,
                  edgecolor="white", lw=0.6)
    for b, v in zip(bars, vals):
        if v:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + max(vals) * 0.025,
                    f"{v}", ha="center", fontsize=8.4, color=INK)
    ax.set_ylabel("AI 漏洞条数", fontsize=9)
    ax.set_ylim(0, max(vals) * 1.18)
    ax.set_title("AI 漏洞严重度分布", fontsize=10.5, color=NAVY, pad=10)

    ax = axes[2]
    style_ax(ax)
    labels = [METHOD_NAMES.get(r[0], r[0] or "未分类") for r in method]
    vals = [int(r[1]) for r in method]
    bars = ax.barh(labels[::-1], vals[::-1],
                   color=[G_DARK if "LLM" in l else (GRAY if l in ("规则排除", "未分类") else G_MAIN)
                          for l in labels[::-1]], height=0.6,
                   edgecolor="white", lw=0.6)
    for b, v in zip(bars, vals[::-1]):
        ax.text(b.get_width() + max(vals) * 0.014, b.get_y() + b.get_height() / 2,
                f"{v:,}", va="center", fontsize=8, color=INK)
    ax.set_xlim(0, max(vals) * 1.22)
    ax.set_title("分类方法分布（全库）", fontsize=10.5, color=NAVY, pad=10)

    save_chart(fig, OUT_NAME)


if __name__ == "__main__":
    draw()