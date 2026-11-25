"""技术报告数据图：采集性能 / 富化七维 / 情报分布 / 入库时序 / 压测 / 问答延迟。

数据来源：PostgreSQL（docker exec psql）+ 归档/技术报告/数据/*.json。
输出到 归档/技术报告/插图/（PNG，300 DPI，白底）。
用法：./.venv/Scripts/python.exe "归档/技术报告/报告生成脚本/绘图_数据图.py"

视觉规范：
  1. 统一风格层 style_ax()：去顶右脊、浅网格、零刻度线、统一字号；
  2. 阈值注释一律带白底遮罩并落在空白区，禁止压线压柱；
  3. 图例只放数据空白区，带白底细边框，避免遮挡曲线；
  4. 数值标签预留 15%~30% 顶部余量，杜绝裁切；
  5. 轴标签精简，避免相邻刻度文字重叠。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "归档/技术报告/插图"
DATA = ROOT / "归档/技术报告/数据"
OUT.mkdir(parents=True, exist_ok=True)

for f in ("Microsoft YaHei", "SimHei"):
    try:
        font_manager.findfont(f, fallback_to_default=False)
        plt.rcParams["font.family"] = f
        break
    except Exception:
        continue

G_DARK = "#05825c"
G_MAIN = "#0a9d6e"
G_LIGHT = "#7cc7a8"
G_PALE = "#cfe9dd"
NAVY = "#12333a"
INK = "#1c2b26"
GRAY = "#6b7a74"
LINE = "#9dc3b4"
GOLD = "#b7791f"
RED = "#b54848"
BLUE = "#2563a8"
PURPLE = "#7c5cbf"
PALETTE = [G_DARK, BLUE, GOLD, PURPLE, RED, G_MAIN, GRAY, "#3aa6a0", "#c98a3d"]

plt.rcParams.update({
    "axes.unicode_minus": False,
    "font.size": 9,
    "axes.edgecolor": LINE,
    "axes.linewidth": 0.9,
    "axes.labelcolor": INK,
    "text.color": INK,
    "xtick.color": INK,
    "ytick.color": INK,
})

# 阈值/注释统一白底遮罩，杜绝文字压线
MASK = dict(facecolor="white", edgecolor="none", alpha=0.9, pad=1.6)
LEG = dict(frameon=True, facecolor="white", edgecolor=LINE, framealpha=0.96,
           borderpad=0.45, handlelength=1.6, handletextpad=0.6)


def sql(query: str) -> list[list[str]]:
    r = subprocess.run(
        ["docker", "exec", "aiintel-postgres", "psql", "-U", "aiintel",
         "-d", "ai_security_intel", "-A", "-F", "\x1f", "-t", "-c", query],
        capture_output=True, text=True, encoding="utf-8",
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    rows = []
    for line in r.stdout.strip().splitlines():
        if line:
            rows.append(line.split("\x1f"))
    return rows


def style_ax(ax, ygrid=True):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(LINE)
    ax.tick_params(length=0, labelsize=8.5)
    if ygrid:
        ax.grid(axis="y", color=LINE, alpha=0.30, lw=0.7)
    ax.set_axisbelow(True)


def thresh(ax, y, color, label, side="right", fs=7.6):
    """阈值虚线 + 白底标签（side=right/left 落在对应空白侧）。"""
    ax.axhline(y, color=color, ls="--", lw=1.1, alpha=0.8, zorder=1.5)
    x = 0.985 if side == "right" else 0.015
    ha = "right" if side == "right" else "left"
    ax.text(x, y, label, transform=ax.get_yaxis_transform(), ha=ha,
            va="bottom", fontsize=fs, color=color, bbox=MASK, zorder=6)


def save(fig, name):
    fig.savefig(OUT / name, bbox_inches="tight", facecolor="white",
                pad_inches=0.12, dpi=300)
    plt.close(fig)
    print(f"✓ {name}")


# ============================================================
# 图 5 · 采集性能：各源平均耗时与产出
# ============================================================
def chart_collect():
    rows = sql(
        "SELECT s.name, l.source_code, ROUND(AVG(l.latency_ms)), "
        "SUM(l.fetched_count), COUNT(*) FILTER (WHERE l.status='success'), COUNT(*) "
        "FROM collection_logs l JOIN sources s ON s.code=l.source_code "
        "GROUP BY 1,2 ORDER BY AVG(l.latency_ms) DESC"
    )
    names = [r[0].replace(" ", "\n", 1) for r in rows]
    codes = [r[1] for r in rows]
    lat = [float(r[2]) / 1000 for r in rows]
    fetched = [int(r[3]) for r in rows]
    runs = [int(r[5]) for r in rows]
    ok = [int(r[4]) for r in rows]

    fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.3), constrained_layout=True)
    fig.suptitle("采集性能：9 类数据源实测（collection_logs 真实运行统计）",
                 fontsize=12.5, fontweight="bold", color=NAVY)

    ax = axes[0]
    style_ax(ax)
    bars = ax.barh(names[::-1], lat[::-1], color=G_MAIN, height=0.62,
                   edgecolor="white", lw=0.6)
    for b, v in zip(bars, lat[::-1]):
        ax.text(b.get_width() + max(lat) * 0.014, b.get_y() + b.get_height() / 2,
                f"{v:.1f}s", va="center", fontsize=8.2, color=INK)
    ax.set_xlabel("单次采集平均耗时（秒，含限速等待）", fontsize=9)
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
    ax.set_ylabel("累计采集条数", fontsize=9)
    ax.set_ylim(0, max(fetched) * 1.30)

    ax2 = ax.twinx()
    ax2.plot(x, [o / r * 100 for o, r in zip(ok, runs)], "o-", color=GOLD,
             lw=1.7, ms=4.5, mfc="white", mew=1.4, zorder=5)
    ax2.set_ylim(60, 106)
    ax2.set_ylabel("采集成功率（%）", fontsize=9, color=GOLD)
    ax2.tick_params(colors=GOLD, labelsize=8, length=0)
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_color(GOLD)
    handles = [Patch(facecolor=BLUE, edgecolor="white", label="累计采集条数"),
               Line2D([], [], color=GOLD, marker="o", ms=4.5, lw=1.7,
                      mfc="white", mew=1.4, label="采集成功率")]
    ax.legend(handles=handles, loc="upper right", fontsize=7.8, **LEG)
    ax.set_title("累计产出与成功率", fontsize=10.5, color=NAVY, pad=10)

    save(fig, "图5_采集性能.png")


# ============================================================
# 图 6 · 富化七维：耗时与置信度
# ============================================================
def chart_enrich():
    rows = sql(
        "SELECT dimension, COUNT(*), ROUND(AVG(confidence)::numeric,2), "
        "ROUND(AVG(elapsed_ms)::numeric,0) FROM enrichments "
        "WHERE status='success' GROUP BY 1 ORDER BY dimension"
    )
    dim_names = {
        "asset": "受影响资产", "cvss": "CVSS 评分", "fix": "修复建议",
        "paper": "关联论文", "poc": "POC 验证", "risk": "风险定级",
        "technique": "技术映射",
    }
    dims = [dim_names.get(r[0], r[0]) for r in rows]
    conf = [float(r[2]) for r in rows]
    ms = [float(r[3]) for r in rows]
    counts = [int(r[1]) for r in rows]

    fig, axes = plt.subplots(1, 2, figsize=(10.0, 4.0), constrained_layout=True)
    fig.suptitle("七维富化实测：耗时分布与置信度分层（enrichments 表）",
                 fontsize=12.5, fontweight="bold", color=NAVY)

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

    save(fig, "图6_富化七维.png")


# ============================================================
# 图 7 · 情报分布三面板
# ============================================================
def chart_dist():
    cat = sql("SELECT ai_category, COUNT(*) FROM vulnerabilities "
              "WHERE ai_category IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
    sev = sql("SELECT severity, COUNT(*) FROM vulnerabilities "
              "WHERE ai_category IS NOT NULL GROUP BY 1 ORDER BY 2 DESC")
    method = sql("SELECT ai_relevance_method, COUNT(*) FROM vulnerabilities "
                 "GROUP BY 1 ORDER BY 2 DESC")

    method_names = {
        "heuristic-overflow": "启发式兜底", "rule": "规则命中",
        "rule-exclude": "规则排除", "llm": "LLM 复核",
        "llm-batch": "LLM 批量", "llm-degraded": "LLM 降级", "": "未分类",
    }

    def short_cat(s: str) -> str:
        return s.replace("AI ", "").replace("风险", "").strip() or s

    fig, axes = plt.subplots(1, 3, figsize=(11.0, 3.9), constrained_layout=True)
    fig.suptitle("情报库分布：AI 类别 / 严重度 / 分类方法（PostgreSQL 实时统计）",
                 fontsize=12.5, fontweight="bold", color=NAVY)

    ax = axes[0]
    labels = [short_cat(r[0]) for r in cat]
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
    labels = ["严重", "高危", "中危", "低危", "未定级"]
    vals = [sev_map.get("CRITICAL", 0), sev_map.get("HIGH", 0),
            sev_map.get("MEDIUM", 0), sev_map.get("LOW", 0),
            sum(v for k, v in sev_map.items()
                if k not in ("CRITICAL", "HIGH", "MEDIUM", "LOW"))]
    bars = ax.bar(labels, vals, color=[RED, "#d97706", GOLD, G_MAIN, GRAY],
                  width=0.62, edgecolor="white", lw=0.6)
    for b, v in zip(bars, vals):
        if v:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + max(vals) * 0.025,
                    f"{v}", ha="center", fontsize=8.4, color=INK)
    ax.set_ylabel("AI 漏洞条数", fontsize=9)
    ax.set_ylim(0, max(vals) * 1.18)
    ax.set_title("AI 漏洞严重度分布", fontsize=10.5, color=NAVY, pad=10)

    ax = axes[2]
    style_ax(ax)
    labels = [method_names.get(r[0], r[0] or "未分类") for r in method]
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

    save(fig, "图7_情报分布.png")


# ============================================================
# 图 8 · 入库时序
# ============================================================
def chart_timeline():
    rows = sql(
        "SELECT date_trunc('day', ingest_at) AT TIME ZONE 'Asia/Shanghai', "
        "COUNT(*), COUNT(*) FILTER (WHERE ai_category IS NOT NULL) "
        "FROM vulnerabilities GROUP BY 1 ORDER BY 1"
    )
    days = [r[0][:10] for r in rows]
    total = [int(r[1]) for r in rows]
    ai = [int(r[2]) for r in rows]

    fig, ax = plt.subplots(figsize=(8.4, 3.9), constrained_layout=True)
    style_ax(ax)
    ax.set_title("情报入库时序：总量与 AI 范畴筛出量（按本地日聚合）",
                 fontsize=12, fontweight="bold", color=NAVY, pad=12)
    x = np.arange(len(days))
    ax.fill_between(x, total, color=G_MAIN, alpha=0.14)
    ax.plot(x, total, "o-", color=G_MAIN, lw=1.9, ms=5, mfc="white", mew=1.6,
            label="每日入库总量", zorder=4)
    ax.plot(x, ai, "s-", color=RED, lw=1.6, ms=4.5, mfc="white", mew=1.4,
            label="其中 AI 安全范畴", zorder=4)
    for xi, v in zip(x, total):
        ax.annotate(f"{v:,}", (xi, v), textcoords="offset points",
                    xytext=(0, 9), ha="center", fontsize=7.8, color=INK, zorder=6)
    ax.set_xticks(x)
    ax.set_xticklabels(days, fontsize=8.2)
    ax.set_ylabel("条数", fontsize=9)
    ax.set_ylim(-max(total) * 0.04, max(total) * 1.22)
    ax.legend(fontsize=8.5, loc="upper right", **LEG)
    save(fig, "图8_入库时序.png")


# ============================================================
# 图 9 · 压测：P95 与 QPS
# ============================================================
def chart_loadtest():
    p = DATA / "性能压测.json"
    if not p.exists():
        print("✗ 性能压测.json 不存在，跳过")
        return
    d = json.loads(p.read_text(encoding="utf-8"))
    api = d["api"]
    endpoints = []
    for row in api:
        if row["endpoint"] not in endpoints:
            endpoints.append(row["endpoint"])

    conc = [10, 50, 100, 200]
    colors = [G_LIGHT, G_MAIN, G_DARK, NAVY]

    fig, axes = plt.subplots(1, 2, figsize=(10.6, 4.2), constrained_layout=True)
    fig.suptitle("API 压测：核心只读接口 × 并发梯度（10/50/100/200）",
                 fontsize=12.5, fontweight="bold", color=NAVY)

    ax = axes[0]
    style_ax(ax)
    x = np.arange(len(endpoints))
    w = 0.19
    p95 = {e: [next(r["p95_ms"] for r in api if r["endpoint"] == e and r["concurrency"] == c)
               for c in conc] for e in endpoints}
    pmax = max(v for e in endpoints for v in p95[e])
    for i, c in enumerate(conc):
        ax.bar(x + (i - 1.5) * w, [p95[e][i] / 1000 for e in endpoints],
               width=w, color=colors[i], label=f"c={c}", edgecolor="white", lw=0.5)
    ax.set_ylim(0, pmax / 1000 * 1.28)
    thresh(ax, 5, RED, "赛题要求 P95 ≤ 5s", side="right")
    ax.set_xticks(x)
    ax.set_xticklabels(endpoints, fontsize=8.2)
    ax.set_ylabel("P95 响应时间（秒）", fontsize=9)
    ax.legend(fontsize=8, ncol=4, loc="upper right", **LEG)
    ax.set_title("P95 延迟", fontsize=10.5, color=NAVY, pad=10)

    ax = axes[1]
    style_ax(ax)
    xc = np.arange(len(conc))
    qps = {e: [next(r["qps"] for r in api if r["endpoint"] == e and r["concurrency"] == c)
               for c in conc] for e in endpoints}
    for i, e in enumerate(endpoints):
        ax.plot(xc, qps[e], "o-", color=PALETTE[i % len(PALETTE)], lw=1.8,
                ms=4.8, mfc="white", mew=1.5, label=e, zorder=4)
    ax.set_xticks(xc)
    ax.set_xticklabels([f"c={c}" for c in conc], fontsize=8.6)
    ax.set_ylabel("吞吐 QPS", fontsize=9)
    ax.set_ylim(0, max(v for e in endpoints for v in qps[e]) * 1.18)
    ax.legend(fontsize=7.6, ncol=2, loc="center right", **LEG)
    ax.set_title("吞吐量", fontsize=10.5, color=NAVY, pad=10)

    save(fig, "图9_压测性能.png")


# ============================================================
# 图 10 · 问答延迟分布（评测明细）
# ============================================================
def chart_qa_latency():
    p = DATA / "问答评测报告.json"
    if not p.exists():
        p = DATA / "问答评测报告_全LLM.json"
    if not p.exists():
        print("✗ 问答评测 JSON 不存在，跳过")
        return
    d = json.loads(p.read_text(encoding="utf-8"))
    results = d["results"]
    lat = sorted(r["elapsed_ms"] for r in results)

    fig, axes = plt.subplots(1, 2, figsize=(10.0, 4.0), constrained_layout=True)
    s = d["summary"]
    fig.suptitle(
        f"问答评测：{s['total']} 题 · 准确率 {s['accuracy']}% · 多跳 {s['multihop_accuracy']}%",
        fontsize=12.5, fontweight="bold", color=NAVY)

    ax = axes[0]
    style_ax(ax)
    bins = np.arange(0, max(lat) + 400, 400)
    ax.hist(lat, bins=bins, color=G_MAIN, edgecolor="white", lw=0.6)
    ax.set_xlim(0, max(max(lat) + 900, 5400))
    ax.set_ylim(0, ax.get_ylim()[1] * 1.18)
    p95 = s["latency"]["p95_ms"]
    ax.axvline(p95, color=RED, ls="--", lw=1.4)
    ax.text(p95 + 90, ax.get_ylim()[1] * 0.94, f"P95 = {p95}ms", fontsize=8.4,
            color=RED, va="top", ha="left", bbox=MASK)
    ax.axvline(5000, color=NAVY, ls=":", lw=1.4)
    ax.text(4930, ax.get_ylim()[1] * 0.74, "赛题 ≤5s", fontsize=7.8,
            color=NAVY, va="top", ha="right", bbox=MASK)
    ax.set_xlabel("单题响应时间（ms）", fontsize=9)
    ax.set_ylabel("题目数", fontsize=9)
    ax.set_title("响应时间分布", fontsize=10.5, color=NAVY, pad=10)

    ax = axes[1]
    style_ax(ax)
    cats = s["by_category"]
    cat_names = {"fact": "事实查询", "relation": "关联推理", "trend": "趋势分析",
                 "advice": "处置建议", "detail": "处置建议", "multihop": "多跳推理"}
    labels = [cat_names.get(k, k) for k in cats]
    vals = [cats[k]["accuracy"] for k in cats]
    ns = [cats[k]["total"] for k in cats]
    bars = ax.bar(labels, vals, color=[G_MAIN if v >= 85 else GOLD for v in vals],
                  width=0.6, edgecolor="white", lw=0.6)
    for b, v, n in zip(bars, vals, ns):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.4,
                f"{v:.1f}%", ha="center", fontsize=8.6, color=INK)
        ax.text(b.get_x() + b.get_width() / 2, 3, f"n={n}", ha="center",
                fontsize=7, color="#ffffff", fontweight="bold")
    ax.set_xlim(-0.55, len(labels) + 0.10)
    ax.set_ylim(0, 116)
    thresh(ax, 85, RED, "≥85%", side="right")
    ax.set_ylabel("准确率（%）", fontsize=9)
    ax.set_title("分题型准确率（要求 ≥85%）", fontsize=10.5, color=NAVY, pad=10)

    save(fig, "图10_问答评测.png")


if __name__ == "__main__":
    chart_collect()
    chart_enrich()
    chart_dist()
    chart_timeline()
    chart_loadtest()
    chart_qa_latency()
    print(f"\n全部完成，输出目录：{OUT}")