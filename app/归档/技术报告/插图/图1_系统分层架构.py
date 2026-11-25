"""图 1 · 系统分层架构图

来源：由 报告生成脚本/绘图_核心图.py 提取，绘图逻辑与原脚本一致。
运行：.venv/Scripts/python.exe "归档/技术报告/插图/图1_系统分层架构.py"
输出：覆盖本目录 图1_系统分层架构.png
"""

from 绘图工具 import (
    NAVY, GRAY, INK, BLUE, G_DARK, G_MAIN, GOLD,
    new_fig, box, card2, badge, soft_shadow, arrow, save_diagram,
)

# ============================================================
# 可调参数
# ============================================================
FIG_W, FIG_H = 7.8, 9.3            # 画布尺寸（英寸）
DPI = 300                           # 输出分辨率
OUT_NAME = "图1_系统分层架构.png"

TITLE = "SENTINEL · AI 安全知识情报系统总体架构"
SUBTITLE = "四层解耦架构 · 展示 / 编排 / 能力 / 存储"

CONT_X0, CONT_X1 = 0.010, 0.990     # 层容器左右边界
CHIP_W = 0.098                      # 左侧层名色块宽度
CH = 0.164                          # 每层容器高度

FOOTER = (
    "监控层：Prometheus 指标采集（/metrics） · Grafana 12 面板 · Alertmanager 告警规则\n"
    "CI：GitHub Actions（pytest 107 · tsc · vite build）"
)

# 四层定义：(层名, y 坐标, 主题色, 底色, 底部注释, [(卡片标题, 卡片副文), ...])
LAYERS = [
    ("展示层", 0.760, BLUE, "#f6f9fd",
     "React 18 · TypeScript · Ant Design 5 · ECharts ｜ 黑夜 / 白天双主题（WCAG AA 对比度）",
     [("监控看板", "调度状态·采集流水\n流水线拓扑"),
      ("态势总览", "时序分桶·类别分布\n严重度分布"),
      ("情报中心", "检索筛选·富化详情\n攻击链·CSV 导出"),
      ("知识图谱", "力导向·路径查询\n节点详情"),
      ("智能问答", "多轮对话·引用溯源\n会话上下文管理"),
      ("运维监控", "组件健康·热切换\nLLM 模型历史")]),
    ("编排层", 0.549, G_DARK, "#f3faf7",
     "多智能体协作：条件边 · 并行节点 · 反思循环 · 熔断降级",
     [("LangGraph 状态机", "monitor → triage → (enrich ‖ qa) → fanout → supervisor → reflect"),
      ("APScheduler 调度", "9 源差异化周期 30min~7d · 断点续采 catch_up_missed()")]),
    ("能力层", 0.338, G_MAIN, "#f2faf6",
     "BGE-small-zh 本地嵌入（96MB）· DeepSeek LLM · RRF 混合检索重排",
     [("情报监测", "9 类数据源\n增量游标 · 跨源去重"),
      ("AI 三级分类", "规则 → 嵌入 → LLM\n灰区逐级下沉"),
      ("精准富化", "7 维并行\n置信度分层"),
      ("情报问答", "混合检索 · 多跳推理\n回答附引用溯源"),
      ("知识图谱", "组件—漏洞—技术\n关联网络构建")]),
    ("存储层", 0.127, GRAY, "#f4f5f4",
     "全 Docker Compose 编排 · 数据卷持久化 · 定期备份",
     [("PostgreSQL 16 + pgvector", "业务库 + 向量索引（HNSW）\n7500+ 条漏洞情报"),
      ("Neo4j 5", "知识图谱\n2363 节点 / 1373 关系"),
      ("Redis 7", "游标缓存 · 限流器 · 会话状态")]),
]


# ============================================================
# 绘图
# ============================================================
def draw():
    fig, ax = new_fig(FIG_W, FIG_H, dpi=DPI)

    ax.text(0.5, 0.979, TITLE, ha="center", va="center",
            fontsize=13.5, fontweight="bold", color=NAVY)
    ax.text(0.5, 0.957, SUBTITLE, ha="center", va="center",
            fontsize=8.2, color=GRAY)

    for idx, (name, y0, ec, bgc, note, items) in enumerate(LAYERS, 1):
        soft_shadow(ax, CONT_X0, y0, CONT_X1 - CONT_X0, CH, radius=0.035,
                    zorder=1, alpha=0.07)
        box(ax, CONT_X0, y0, CONT_X1 - CONT_X0, CH, fc=bgc, ec=ec, lw=1.1,
            radius=0.035, zorder=2)
        chip_x = CONT_X0 + 0.014
        box(ax, chip_x, y0 + 0.014, CHIP_W, CH - 0.028, "", fc=ec, ec=ec, lw=0,
            radius=0.030, zorder=3)
        badge(ax, chip_x + CHIP_W / 2, y0 + CH - 0.040, f"{idx:02d}",
              "#ffffff", tc=ec, fs=7.2)
        ax.text(chip_x + CHIP_W / 2, y0 + 0.052, name, ha="center", va="center",
                fontsize=11, color="#ffffff", fontweight="bold", zorder=4)
        cx0 = chip_x + CHIP_W + 0.014
        cx1 = 0.982
        gap = 0.011
        n = len(items)
        w = (cx1 - cx0 - gap * (n - 1)) / n
        for i, (title, sub) in enumerate(items):
            x = cx0 + i * (w + gap)
            card2(ax, x, y0 + 0.048, w, 0.104, title, sub, fc="#ffffff", ec=ec,
                  ts=8.6, ss=6.8, tc=ec, sc=INK, zorder=3)
        ax.text(cx1, y0 + 0.026, note, ha="right", va="center", fontsize=7.0,
                color="#5f6f68", style="italic", zorder=3)

    for i in range(len(LAYERS) - 1):
        y_from = LAYERS[i][1] - 0.004
        y_to = LAYERS[i + 1][1] + CH + 0.004
        arrow(ax, 0.5, y_from, 0.5, y_to, color=NAVY, lw=2.0,
              style="<|-|>", shrink=0)

    box(ax, 0.010, 0.014, 0.980, 0.080, FOOTER,
        fc="#fbf9f4", ec=GOLD, fs=8.2, radius=0.030, shadow=True)

    save_diagram(fig, OUT_NAME)


if __name__ == "__main__":
    draw()