"""图 2 · 全链路数据流图

来源：由 报告生成脚本/绘图_核心图.py 提取，绘图逻辑与原脚本一致。
运行：.venv/Scripts/python.exe "归档/技术报告/插图/图2_全链路数据流.py"
输出：覆盖本目录 图2_全链路数据流.png
"""

from 绘图工具 import (
    NAVY, GRAY, INK, LINE, BLUE, BLUE_FILL, G_DARK, G_FILL, G_FILL2,
    G_MAIN, GOLD, GOLD_FILL, RED, RED_FILL,
    new_fig, card2, badge, pill, arrow, save_diagram,
)

# ============================================================
# 可调参数
# ============================================================
FIG_W, FIG_H = 10.4, 5.0            # 画布尺寸（英寸）
DPI = 300
OUT_NAME = "图2_全链路数据流.png"

TITLE = "全链路数据流：一条情报从采集到可问答回答"
SUBTITLE = "六阶段流水线 · 9 类数据源统一编排 · 全程可溯源"

GAP = 0.024                         # 阶段卡片间距
YS, HS = 0.575, 0.265               # 阶段卡片底边 y / 高度
YN, HN = 0.185, 0.250               # 旁路卡片底边 y / 高度
NOTE_W = 0.300                      # 旁路卡片宽度

# 六个阶段：(标题, 正文, 主题色, 底色)
STAGES = [
    ("① 采集", "9 类数据源自动轮询\nNVD · GHSA · OSV\nKEV · POC · RSS\narXiv · ATLAS · 政策", BLUE, BLUE_FILL),
    ("② 过滤", "三级 AI 分类\n规则 → 嵌入 → LLM\nAI 真实占比 <1%", G_DARK, G_FILL),
    ("③ 入库", "结构化存储\nvulnerabilities\npapers / policies\ntechniques", GRAY, "#f2f4f3"),
    ("④ 富化", "7 维并行富化\n资产 · POC · 技术\n政策 · 风险 · 修复\n论文关联", G_MAIN, G_FILL2),
    ("⑤ 向量化", "pgvector HNSW\nNeo4j 图谱构建\n跨源实体对齐", G_MAIN, G_FILL2),
    ("⑥ 问答", "可溯源 RAG\n混合检索 + 重排\n多跳推理 · 附引用", G_DARK, G_FILL),
]

# 旁路卡片：(标题, 正文, 主题色, 底色, 卡片中心 x, 指向的阶段序号)
NOTES = [
    ("旁路 · 断点续采", "collector_cursors 增量游标\ncatch_up_missed() 重启补跑\n30min~7d 差异化轮询周期",
     GOLD, GOLD_FILL, 0.175, 0),
    ("旁路 · 置信度分层", "≥ 0.8 直采入库\n0.5 ~ 0.8 标注保留\n< 0.5 进人工核验队列",
     RED, RED_FILL, 0.500, 3),
    ("旁路 · 降级与熔断", "LLM 429 冷却退避\n意图规则兜底应答\n保障 P95 ≤ 5s 不超时",
     GOLD, GOLD_FILL, 0.825, 5),
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
    for i, (title, sub, ec, fc) in enumerate(STAGES):
        card2(ax, xs[i], YS, w, HS, title, sub, fc=fc, ec=ec,
              ts=9.0, ss=7.2, tc=ec, sc=INK, zorder=3)
        badge(ax, xs[i] + 0.012, YS + HS, str(i + 1), ec, "#ffffff", fs=8.0)
        if i < n - 1:
            arrow(ax, xs[i] + w + 0.002, YS + HS / 2,
                  xs[i + 1] - 0.002, YS + HS / 2, color=NAVY, lw=2.0, shrink=0)

    for title, body, ec, fc, fx, k in NOTES:
        tx = xs[k] + w / 2
        card2(ax, fx - NOTE_W / 2, YN, NOTE_W, HN, title, body, fc=fc, ec=ec,
              ts=8.8, ss=7.4, tc=ec, sc=INK, zorder=3)
        pill(ax, fx - NOTE_W / 2 + 0.050, YN + HN, "旁路", fc=ec, ec=ec,
             tc="#ffffff", fs=6.8, zorder=6)
        arrow(ax, fx, YN + HN + 0.003, tx, YS - 0.005, color=LINE, lw=1.3,
              ls="--", rad=-0.12, shrink=1)

    save_diagram(fig, OUT_NAME)


if __name__ == "__main__":
    draw()