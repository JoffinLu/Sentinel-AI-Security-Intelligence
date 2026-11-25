"""图 3 · LangGraph 智能体状态机图

来源：由 报告生成脚本/绘图_核心图.py 提取，绘图逻辑与原脚本一致。
拓扑与 orchestrator.py 保持一致：skip_enrich / enrich 均汇入 fanout。
运行：.venv/Scripts/python.exe "归档/技术报告/插图/图3_智能体状态机.py"
输出：覆盖本目录 图3_智能体状态机.png
"""

from matplotlib.patches import FancyBboxPatch

from 绘图工具 import (
    NAVY, INK, GRAY, G_DARK, G_FILL, GOLD, GOLD_FILL, RED, RED_FILL,
    new_fig, soft_shadow, reg, arrow, pill, save_diagram,
)

# ============================================================
# 可调参数
# ============================================================
FIG_W, FIG_H = 8.8, 6.6            # 画布尺寸（英寸）
DPI = 300
OUT_NAME = "图3_智能体状态机.png"

TITLE = "LangGraph 智能体状态机（7 节点 · 条件边 · 反思循环）"

NW, NH = 0.195, 0.104              # 节点宽 / 高
R1, R2, R3, R4 = 0.815, 0.610, 0.405, 0.205   # 四行节点的底边 y 坐标

FOOTNOTE = (
    "注 ① LangGraph 竞争入边：enrich 与 qa 若各自连入 fanout 只会执行其一，故并行改在 fanout 内以 asyncio.gather 实现\n"
    "注 ② 图外组件：APScheduler 按 9 源差异化周期驱动入口，断点续采 catch_up_missed() 保证重启后自动补跑"
)

# 普通节点：(x, y, 英文名, 中文说明, 填充色, 边框色, 标题色)
NODES = [
    (0.180, R1, "monitor", "监测 Agent · 触发采集", G_FILL, G_DARK, NAVY),
    (0.432, R1, "triage", "优先级判定", GOLD_FILL, GOLD, GOLD),
    (0.222, R2, "skip_enrich", "低价值直通（占位）", "#f2f4f3", GRAY, GRAY),
    (0.522, R2, "enrich", "七维富化 · 并行执行", G_FILL, G_DARK, NAVY),
    (0.372, R3, "fanout", "asyncio.gather 汇聚", G_FILL, G_DARK, NAVY),
    (0.050, R4, "reflect", "自愈重试 · 上限 3 次", RED_FILL, RED, RED),
    (0.372, R4, "supervisor", "主管反思 · 结果校验", GOLD_FILL, GOLD, GOLD),
]

# 起止节点：(x, y, 宽度, 文字)
TERMINALS = [
    (0.018, R1, 0.112, "START\n入口"),
    (0.760, R4, 0.122, "END\n出口"),
]


# ============================================================
# 绘图
# ============================================================
def draw():
    fig, ax = new_fig(FIG_W, FIG_H, dpi=DPI)

    ax.text(0.5, 0.955, TITLE, ha="center", va="center",
            fontsize=13, fontweight="bold", color=NAVY)

    def node(x, y, name, cn, fc=G_FILL, ec=G_DARK, tc=NAVY, ctc=INK):
        soft_shadow(ax, x, y, NW, NH, radius=0.045, zorder=2)
        ax.add_patch(FancyBboxPatch(
            (x, y), NW, NH,
            boxstyle="round,pad=0.011,rounding_size=0.045",
            fc=fc, ec=ec, lw=1.4, zorder=3))
        ax.add_patch(FancyBboxPatch(
            (x + 0.012, y + 0.017), 0.006, NH - 0.034,
            boxstyle="round,pad=0,rounding_size=0.003",
            fc=ec, ec="none", zorder=4))
        t1 = ax.text(x + NW / 2 + 0.009, y + NH - 0.034, name, ha="center",
                     va="center", fontsize=9.6, color=tc, fontweight="bold",
                     zorder=4)
        t2 = ax.text(x + NW / 2 + 0.009, y + 0.032, cn, ha="center",
                     va="center", fontsize=7.6, color=ctc, zorder=4)
        reg(t1, ax, x + 0.006, y + 0.006, NW - 0.012, NH - 0.012)
        reg(t2, ax, x + 0.006, y + 0.006, NW - 0.012, NH - 0.012)

    def terminal(x, y, w, label):
        soft_shadow(ax, x, y, w, NH, radius=0.052, zorder=2)
        ax.add_patch(FancyBboxPatch(
            (x, y), w, NH,
            boxstyle="round,pad=0.011,rounding_size=0.052",
            fc=NAVY, ec=NAVY, lw=1.4, zorder=3))
        ax.text(x + w / 2, y + NH / 2, label, ha="center", va="center",
                fontsize=9.2, color="#ffffff", fontweight="bold", zorder=4,
                linespacing=1.4)

    y1, y4 = R1 + NH / 2, R4 + NH / 2

    for x, y, w, label in TERMINALS:
        terminal(x, y, w, label)
    for x, y, name, cn, fc, ec, tc in NODES:
        node(x, y, name, cn, fc=fc, ec=ec, tc=tc)

    # 主干（第一行水平）
    arrow(ax, 0.130, y1, 0.180, y1, color=NAVY, lw=1.8, shrink=0)
    arrow(ax, 0.375, y1, 0.432, y1, color=NAVY, lw=1.8, shrink=0)
    # triage 条件边：对称扇出
    arrow(ax, 0.600, R1, 0.6195, R2 + NH, color=NAVY, lw=1.8, shrink=0)
    pill(ax, 0.706, 0.766, "是 · AI 相关且高优先", fc="#ffffff", ec=NAVY, tc=NAVY, fs=7.6)
    arrow(ax, 0.460, R1, 0.3195, R2 + NH, color=GOLD, lw=1.6, shrink=0)
    pill(ax, 0.300, 0.766, "否 · 低价值直通", fc="#ffffff", ec=GOLD, tc=GOLD, fs=7.6)
    # skip_enrich / enrich 对称扇入 fanout（正确拓扑：skip_enrich → fanout）
    arrow(ax, 0.3195, R2, 0.4695, R3 + NH, color=NAVY, lw=1.6, shrink=0)
    arrow(ax, 0.6195, R2, 0.4695, R3 + NH, color=NAVY, lw=1.6, shrink=0)
    pill(ax, 0.4695, 0.562, "并行产出汇聚", fc="#ffffff", ec=GRAY, tc=GRAY, fs=7.2)
    # fanout → supervisor
    arrow(ax, 0.4695, R3, 0.4695, R4 + NH, color=NAVY, lw=1.8, shrink=0)
    pill(ax, 0.560, 0.357, "节点产出", fc="#ffffff", ec=GRAY, tc=GRAY, fs=7.2)
    # supervisor 条件边
    arrow(ax, 0.567, y4, 0.760, y4, color=NAVY, lw=1.8, shrink=0)
    pill(ax, 0.663, 0.285, "全部成功", fc="#ffffff", ec=NAVY, tc=NAVY, fs=7.6)
    arrow(ax, 0.372, y4, 0.245, y4, color=RED, lw=1.6, shrink=0)
    pill(ax, 0.308, 0.285, "失败且可重试", fc="#ffffff", ec=RED, tc=RED, fs=7.6)
    # reflect 自愈回路（弧线走空区，不与任何文字相交）
    arrow(ax, 0.200, R4 + NH, 0.400, R4 + NH, color=RED, lw=1.5, rad=-0.35, shrink=0)
    pill(ax, 0.300, 0.375, "retry < max · 自愈重试", fc="#ffffff", ec=RED, tc=RED, fs=7.2)
    # reflect 超限放弃 → END（底部大弧线）
    arrow(ax, 0.100, R4, 0.821, R4, color=RED, lw=1.4, ls="--", rad=0.16, shrink=0)
    pill(ax, 0.470, 0.105, "超限放弃（retry ≥ 3）", fc="#ffffff", ec=RED, tc=RED, fs=7.2)

    ax.text(0.5, 0.038, FOOTNOTE, ha="center", va="center",
            fontsize=7.4, color=GRAY, linespacing=1.8)

    save_diagram(fig, OUT_NAME)


if __name__ == "__main__":
    draw()