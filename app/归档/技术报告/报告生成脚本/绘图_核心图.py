"""技术报告核心插图：架构图 / 数据流图 / 状态机图 / 三级分类图。

输出到 归档/技术报告/插图/（PNG，300 DPI，白底适合 Word 印刷）。
用法：./.venv/Scripts/python.exe "归档/技术报告/报告生成脚本/绘图_核心图.py"

设计规范（v2 精修）：
  1. 统一视觉语言：柔和投影 + 圆角卡片 + 步骤徽标 + 统一配色；
  2. 文字先按框内可用宽度真实渲染测宽换行，再落图；
  3. 保存前用真实渲染包围盒做越界自检，任何文字超出所属框即告警；
  4. 连线避开文字区，扇入/扇出对称，同层不交叉；
  5. 状态机拓扑与 orchestrator.py 一致：skip_enrich/enrich 均汇入 fanout。
"""

from __future__ import annotations

import re
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path("归档/技术报告/插图")
OUT.mkdir(parents=True, exist_ok=True)

for f in ("Microsoft YaHei", "SimHei"):
    try:
        font_manager.findfont(f, fallback_to_default=False)
        plt.rcParams["font.family"] = f
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False

# 配色（与数据图脚本保持一致）
G_DARK = "#05825c"
G_MAIN = "#0a9d6e"
G_FILL = "#e8f6f0"
G_FILL2 = "#f4fbf7"
NAVY = "#12333a"
INK = "#1c2b26"
GRAY = "#6b7a74"
LINE = "#9dc3b4"
GOLD = "#b7791f"
GOLD_FILL = "#fdf3e3"
RED = "#b54848"
RED_FILL = "#fbeaea"
BLUE = "#2563a8"
BLUE_FILL = "#eaf2fb"
SHADOW = "#12333a"

_FIG_W, _FIG_H = 8.0, 6.0
_FIG_REF = None
_AX_REF = None
_REG: list = []
_WCACHE: dict = {}

_ASCII_TOKEN = re.compile(r"[A-Za-z0-9_./:+~%&#?=<>-]+")


# ============================================================
# 文字排版工具：真实渲染测宽 / 智能换行 / 越界自检
# ============================================================
def _real_w(s: str, fs: float, bold: bool = False, style: str = "normal") -> float:
    """用真实字体渲染测宽，返回以坐标系宽度为 1 的宽度值。"""
    key = (s, fs, bold, style)
    if key in _WCACHE:
        return _WCACHE[key]
    if _AX_REF is None:  # 兜底：CJK 按全宽、ASCII 按 0.55 估算
        w = sum(fs / 72.0 * (1.0 if ord(c) > 0x2E80 else (0.60 if bold else 0.55))
                for c in s)
        _WCACHE[key] = w
        return w
    t = _AX_REF.text(0, 0, s, fontsize=fs,
                     fontweight="bold" if bold else "normal", style=style)
    bb = t.get_window_extent(renderer=_FIG_REF.canvas.get_renderer())
    t.remove()
    w = bb.width / _AX_REF.bbox.width
    _WCACHE[key] = w
    return w


def _tokens(s: str) -> list[str]:
    toks, i = [], 0
    while i < len(s):
        m = _ASCII_TOKEN.match(s, i)
        if m:
            toks.append(m.group())
            i = m.end()
        else:
            toks.append(s[i])
            i += 1
    return toks


def wrap_text(text: str, max_w: float, fs: float, bold: bool = False,
              style: str = "normal") -> str:
    """按最大宽度换行；ASCII 连续串整体不拆；末行过短时从上行回填（平衡断行）。"""
    out: list[str] = []
    for para in text.split("\n"):
        if not para:
            out.append("")
            continue
        lines: list[list[str]] = [[]]
        cur_w = 0.0
        for tok in _tokens(para):
            tw = _real_w(tok, fs, bold, style)
            if cur_w and cur_w + tw > max_w:
                if lines[-1] and lines[-1][-1] == " ":
                    lines[-1].pop()
                    cur_w -= _real_w(" ", fs, bold, style)
                lines.append([])
                cur_w = 0.0
                if tok == " ":
                    continue
            lines[-1].append(tok)
            cur_w += tw
        # 平衡断行：末行过短则从上行挪 token（空格原样挪动，防止字距丢失）
        while len(lines) > 1 and len(lines[-2]) > 2:
            last_w = sum(_real_w(t, fs, bold, style) for t in lines[-1])
            if last_w >= max_w * 0.35:
                break
            lines[-1].insert(0, lines[-2].pop())
        out.append("\n".join("".join(ln).strip() for ln in lines))
    return "\n".join(out)


def _reg(t, ax, x, y, w, h):
    _REG.append((t, ax, x, y, w, h))


def save(fig, name: str):
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    bad = 0
    for t, ax, x, y, w, h in _REG:
        if not t.get_text().strip():
            continue
        bb = t.get_window_extent(renderer=r)
        (x0, y0), (x1, y1) = ax.transData.transform([(x, y), (x + w, y + h)])
        if bb.x0 < x0 - 3 or bb.x1 > x1 + 3 or bb.y0 < y0 - 3 or bb.y1 > y1 + 3:
            bad += 1
            print(f"  ! 文字越界: {t.get_text()[:24]!r}")
    mark = "✓ 无越界" if bad == 0 else f"✗ {bad} 处越界"
    fig.savefig(OUT / name, bbox_inches="tight", facecolor="white", pad_inches=0.12)
    plt.close(fig)
    print(f"{mark}  {name}")


# ============================================================
# 视觉基础图元：柔和投影 / 徽标 / 卡片 / 面板 / 连线
# ============================================================
def soft_shadow(ax, x, y, w, h, radius=0.045, zorder=1, alpha=0.10, k=1.0):
    """双层偏移圆角矩形模拟柔和投影（近实远虚）。"""
    for i, a in ((1.0, alpha), (1.9, alpha * 0.5)):
        ax.add_patch(FancyBboxPatch(
            (x + 0.0045 * i * k, y - 0.0055 * i * k), w, h,
            boxstyle=f"round,pad=0.011,rounding_size={radius}",
            fc=SHADOW, ec="none", alpha=a, zorder=zorder))


def badge(ax, x, y, label, fc, tc="#ffffff", fs=7.4, zorder=6, pad=0.34):
    """圆形步骤徽标（用 circle boxstyle，避免坐标轴各向异性拉成椭圆）。"""
    ax.text(x, y, label, ha="center", va="center", fontsize=fs, color=tc,
            fontweight="bold", zorder=zorder,
            bbox=dict(boxstyle=f"circle,pad={pad}", fc=fc, ec="white", lw=1.1))


def pill(ax, x, y, text, fc="#ffffff", ec=LINE, tc=INK, fs=7.6, zorder=6,
         ha="center"):
    """圆角胶囊标签，用于边注释/角标，白底避免压线。"""
    ax.text(x, y, text, ha=ha, va="center", fontsize=fs, color=tc, zorder=zorder,
            bbox=dict(boxstyle="round,pad=0.32", fc=fc, ec=ec, lw=0.7))


def new_fig(w, h):
    global _FIG_W, _FIG_H, _FIG_REF, _AX_REF
    _FIG_W, _FIG_H = w, h
    _REG.clear()
    _WCACHE.clear()
    fig, ax = plt.subplots(figsize=(w, h), dpi=300)
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    _FIG_REF, _AX_REF = fig, ax
    fig.canvas.draw()
    return fig, ax


def box(ax, x, y, w, h, text="", fc="#ffffff", ec=LINE, tc=INK, fs=9, lw=1.2,
        bold=False, radius=0.045, zorder=3, align="center", wrap=True,
        linespacing=1.5, style="normal", shadow=False, shadow_alpha=0.10,
        shadow_k=1.0):
    if shadow:
        soft_shadow(ax, x, y, w, h, radius=radius, zorder=zorder - 1,
                    alpha=shadow_alpha, k=shadow_k)
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0.011,rounding_size={radius}",
        fc=fc, ec=ec, lw=lw, zorder=zorder,
    ))
    if not text:
        return
    if wrap:
        text = wrap_text(text, w - 0.032, fs, bold, style)
    if align == "center":
        t = ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
                    fontsize=fs, color=tc, zorder=zorder + 1, style=style,
                    fontweight="bold" if bold else "normal", linespacing=linespacing)
    else:
        t = ax.text(x + 0.017, y + h / 2, text, ha="left", va="center",
                    fontsize=fs, color=tc, zorder=zorder + 1, style=style,
                    fontweight="bold" if bold else "normal", linespacing=linespacing)
    _reg(t, ax, x + 0.005, y + 0.005, w - 0.010, h - 0.010)


def card2(ax, x, y, w, h, title, sub, fc="#ffffff", ec=LINE,
          ts=8.6, ss=7.1, tc=NAVY, sc=INK, radius=0.045, lw=1.2, zorder=3,
          shadow=True):
    """标题（粗）+ 正文（自动换行）两段式卡片。"""
    if shadow:
        soft_shadow(ax, x, y, w, h, radius=radius, zorder=zorder - 1)
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0.011,rounding_size={radius}",
        fc=fc, ec=ec, lw=lw, zorder=zorder,
    ))
    t1 = ax.text(x + w / 2, y + h - 0.014, title, ha="center", va="top",
                 fontsize=ts, color=tc, fontweight="bold", zorder=zorder + 1)
    th = ts * 1.35 / 72 / _FIG_H
    t2 = ax.text(x + w / 2, y + h - 0.014 - th - 0.008,
                 wrap_text(sub, w - 0.026, ss),
                 ha="center", va="top", fontsize=ss, color=sc,
                 zorder=zorder + 1, linespacing=1.6)
    _reg(t1, ax, x + 0.006, y + 0.006, w - 0.012, h - 0.012)
    _reg(t2, ax, x + 0.006, y + 0.006, w - 0.012, h - 0.012)


def panel(ax, x, y, w, h, header, body, ec=NAVY, fc="#ffffff",
          hs=9.2, bs=7.8, pad=0.020, shadow=True):
    """左对齐信息面板：粗体标题 + 分隔细线 + 正文。"""
    if shadow:
        soft_shadow(ax, x, y, w, h, radius=0.045, zorder=2)
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.011,rounding_size=0.045",
        fc=fc, ec=ec, lw=1.3, zorder=3,
    ))
    t1 = ax.text(x + pad, y + h - 0.018, header, ha="left", va="top",
                 fontsize=hs, color=ec, fontweight="bold", zorder=4)
    th = hs * 1.35 / 72 / _FIG_H
    y_line = y + h - 0.018 - th - 0.008
    ax.plot([x + pad, x + w - pad], [y_line, y_line], color=ec, lw=0.9,
            alpha=0.4, zorder=4)
    t2 = ax.text(x + pad, y_line - 0.016,
                 wrap_text(body, w - 2 * pad, bs),
                 ha="left", va="top", fontsize=bs, color=INK, zorder=4,
                 linespacing=1.75)
    _reg(t1, ax, x + 0.006, y + 0.006, w - 0.012, h - 0.012)
    _reg(t2, ax, x + 0.006, y + 0.006, w - 0.012, h - 0.012)


def arrow(ax, x1, y1, x2, y2, color=G_DARK, lw=1.6, style="-|>",
          shrink=2, rad=0.0, zorder=2, ls="-"):
    ax.add_patch(FancyArrowPatch(
        (x1, y1), (x2, y2),
        arrowstyle=style, mutation_scale=13, color=color, lw=lw,
        shrinkA=shrink, shrinkB=shrink, zorder=zorder, linestyle=ls,
        connectionstyle=f"arc3,rad={rad}",
    ))


# ============================================================
# 图 1 · 系统分层架构图
# ============================================================
def fig_architecture():
    fig, ax = new_fig(7.8, 9.3)

    ax.text(0.5, 0.979, "SENTINEL · AI 安全知识情报系统总体架构",
            ha="center", va="center", fontsize=13.5, fontweight="bold", color=NAVY)
    ax.text(0.5, 0.957, "四层解耦架构 · 展示 / 编排 / 能力 / 存储",
            ha="center", va="center", fontsize=8.2, color=GRAY)

    CONT_X0, CONT_X1 = 0.010, 0.990
    CHIP_W = 0.098
    CH = 0.164

    layers = [
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

    for idx, (name, y0, ec, bgc, note, items) in enumerate(layers, 1):
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

    for i in range(len(layers) - 1):
        y_from = layers[i][1] - 0.004
        y_to = layers[i + 1][1] + CH + 0.004
        arrow(ax, 0.5, y_from, 0.5, y_to, color=NAVY, lw=2.0,
              style="<|-|>", shrink=0)

    box(ax, 0.010, 0.014, 0.980, 0.080,
        "监控层：Prometheus 指标采集（/metrics） · Grafana 12 面板 · Alertmanager 告警规则\n"
        "CI：GitHub Actions（pytest 107 · tsc · vite build）",
        fc="#fbf9f4", ec=GOLD, fs=8.2, radius=0.030, shadow=True)

    save(fig, "图1_系统分层架构.png")


# ============================================================
# 图 2 · 全链路数据流图
# ============================================================
def fig_dataflow():
    fig, ax = new_fig(10.4, 5.0)

    ax.text(0.5, 0.955, "全链路数据流：一条情报从采集到可问答回答",
            ha="center", va="center", fontsize=13, fontweight="bold", color=NAVY)
    ax.text(0.5, 0.912, "六阶段流水线 · 9 类数据源统一编排 · 全程可溯源",
            ha="center", va="center", fontsize=8.0, color=GRAY)

    stages = [
        ("① 采集", "9 类数据源自动轮询\nNVD · GHSA · OSV\nKEV · POC · RSS\narXiv · ATLAS · 政策", BLUE, BLUE_FILL),
        ("② 过滤", "三级 AI 分类\n规则 → 嵌入 → LLM\nAI 真实占比 <1%", G_DARK, G_FILL),
        ("③ 入库", "结构化存储\nvulnerabilities\npapers / policies\ntechniques", GRAY, "#f2f4f3"),
        ("④ 富化", "7 维并行富化\n资产 · POC · 技术\n政策 · 风险 · 修复\n论文关联", G_MAIN, G_FILL2),
        ("⑤ 向量化", "pgvector HNSW\nNeo4j 图谱构建\n跨源实体对齐", G_MAIN, G_FILL2),
        ("⑥ 问答", "可溯源 RAG\n混合检索 + 重排\n多跳推理 · 附引用", G_DARK, G_FILL),
    ]

    n = len(stages)
    gap = 0.024
    w = (0.980 - gap * (n - 1)) / n
    xs = [0.010 + i * (w + gap) for i in range(n)]
    yS, hS = 0.575, 0.265
    for i, (title, sub, ec, fc) in enumerate(stages):
        card2(ax, xs[i], yS, w, hS, title, sub, fc=fc, ec=ec,
              ts=9.0, ss=7.2, tc=ec, sc=INK, zorder=3)
        badge(ax, xs[i] + 0.012, yS + hS, str(i + 1), ec, "#ffffff", fs=8.0)
        if i < n - 1:
            arrow(ax, xs[i] + w + 0.002, yS + hS / 2,
                  xs[i + 1] - 0.002, yS + hS / 2, color=NAVY, lw=2.0, shrink=0)

    # 旁路卡片：锚点 → 目标阶段中心
    notes = [
        ("旁路 · 断点续采", "collector_cursors 增量游标\ncatch_up_missed() 重启补跑\n30min~7d 差异化轮询周期",
         GOLD, GOLD_FILL, 0.175, xs[0] + w / 2),
        ("旁路 · 置信度分层", "≥ 0.8 直采入库\n0.5 ~ 0.8 标注保留\n< 0.5 进人工核验队列",
         RED, RED_FILL, 0.500, xs[3] + w / 2),
        ("旁路 · 降级与熔断", "LLM 429 冷却退避\n意图规则兜底应答\n保障 P95 ≤ 5s 不超时",
         GOLD, GOLD_FILL, 0.825, xs[5] + w / 2),
    ]
    yN, hN = 0.185, 0.250
    for title, body, ec, fc, fx, tx in notes:
        card2(ax, fx - 0.150, yN, 0.300, hN, title, body, fc=fc, ec=ec,
              ts=8.8, ss=7.4, tc=ec, sc=INK, zorder=3)
        pill(ax, fx - 0.150 + 0.050, yN + hN, "旁路", fc=ec, ec=ec, tc="#ffffff",
             fs=6.8, zorder=6)
        arrow(ax, fx, yN + hN + 0.003, tx, yS - 0.005, color=LINE, lw=1.3,
              ls="--", rad=-0.12, shrink=1)

    save(fig, "图2_全链路数据流.png")


# ============================================================
# 图 3 · LangGraph 状态机图（拓扑与 orchestrator.py 一致）
# ============================================================
def fig_state_machine():
    fig, ax = new_fig(8.8, 6.6)

    ax.text(0.5, 0.955, "LangGraph 智能体状态机（7 节点 · 条件边 · 反思循环）",
            ha="center", va="center", fontsize=13, fontweight="bold", color=NAVY)

    NW, NH = 0.195, 0.104

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
        _reg(t1, ax, x + 0.006, y + 0.006, NW - 0.012, NH - 0.012)
        _reg(t2, ax, x + 0.006, y + 0.006, NW - 0.012, NH - 0.012)

    def terminal(x, y, w, label):
        soft_shadow(ax, x, y, w, NH, radius=0.052, zorder=2)
        ax.add_patch(FancyBboxPatch(
            (x, y), w, NH,
            boxstyle="round,pad=0.011,rounding_size=0.052",
            fc=NAVY, ec=NAVY, lw=1.4, zorder=3))
        ax.text(x + w / 2, y + NH / 2, label, ha="center", va="center",
                fontsize=9.2, color="#ffffff", fontweight="bold", zorder=4,
                linespacing=1.4)

    R1, R2, R3, R4 = 0.815, 0.610, 0.405, 0.205
    y1, y4 = R1 + NH / 2, R4 + NH / 2

    terminal(0.018, R1, 0.112, "START\n入口")
    node(0.180, R1, "monitor", "监测 Agent · 触发采集")
    node(0.432, R1, "triage", "优先级判定", fc=GOLD_FILL, ec=GOLD, tc=GOLD)
    node(0.222, R2, "skip_enrich", "低价值直通（占位）", fc="#f2f4f3", ec=GRAY, tc=GRAY)
    node(0.522, R2, "enrich", "七维富化 · 并行执行")
    node(0.372, R3, "fanout", "asyncio.gather 汇聚")
    node(0.050, R4, "reflect", "自愈重试 · 上限 3 次", fc=RED_FILL, ec=RED, tc=RED)
    node(0.372, R4, "supervisor", "主管反思 · 结果校验", fc=GOLD_FILL, ec=GOLD, tc=GOLD)
    terminal(0.760, R4, 0.122, "END\n出口")

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

    ax.text(0.5, 0.038,
            "注 ① LangGraph 竞争入边：enrich 与 qa 若各自连入 fanout 只会执行其一，故并行改在 fanout 内以 asyncio.gather 实现\n"
            "注 ② 图外组件：APScheduler 按 9 源差异化周期驱动入口，断点续采 catch_up_missed() 保证重启后自动补跑",
            ha="center", va="center", fontsize=7.4, color=GRAY, linespacing=1.8)

    save(fig, "图3_智能体状态机.png")


# ============================================================
# 图 4 · 三级 AI 分类流水线
# ============================================================
def fig_classify():
    fig, ax = new_fig(9.6, 5.3)

    ax.text(0.5, 0.955, "三级 AI 分类流水线：<1% 真 AI 漏洞浓度下的精准筛选",
            ha="center", va="center", fontsize=13, fontweight="bold", color=NAVY)
    ax.text(0.5, 0.912, "规则 → 嵌入 → LLM 逐级下沉 · 仅灰区条目进入下一级",
            ha="center", va="center", fontsize=8.0, color=GRAY)

    stages = [
        ("输入", "新采集漏洞 ≈ 100%\n进入分类流水线", "#f2f4f3", GRAY),
        ("第一级 · 规则层", "13 类 90+ 关键词\n38 排除词 + 60 AI 组件\n零成本 · 确定性判定", G_FILL2, G_MAIN),
        ("第二级 · 嵌入层", "bge-small-zh 语义相似\n12 条基准语句\n5.8ms/条 · 10× 快于 LLM", BLUE_FILL, BLUE),
        ("第三级 · LLM 层", "批量复核（5 条/批）\n预算 30 条/源\n仅灰区条目", G_FILL, G_DARK),
        ("兜底 · 启发式", "预算超限时触发\nAI 组件倾向保留\n标记待人工核验", GOLD_FILL, GOLD),
    ]
    n = len(stages)
    gap = 0.026
    w = (0.980 - gap * (n - 1)) / n
    xs = [0.010 + i * (w + gap) for i in range(n)]
    yS, hS = 0.600, 0.265
    for i, (t, s, fc, ec) in enumerate(stages):
        card2(ax, xs[i], yS, w, hS, t, s, fc=fc, ec=ec,
              ts=8.8, ss=7.2, tc=ec, sc=INK, zorder=3)
        badge(ax, xs[i] + 0.012, yS + hS, str(i + 1), ec, "#ffffff", fs=8.0)
        if i < n - 1:
            arrow(ax, xs[i] + w + 0.002, yS + hS / 2,
                  xs[i + 1] - 0.002, yS + hS / 2, color=NAVY,
                  lw=2.4 - 0.35 * i, shrink=0)

    ax.text(0.5, 0.560,
            "▼  流量逐级递减：仅规则不确定（灰区）条目下沉到下一级，确定性条目就地判定并记录 method",
            ha="center", va="center", fontsize=8.0, color=GRAY)

    panel(ax, 0.015, 0.090, 0.470, 0.385,
          "判定结果（分类方法分布 · 实测 7789 条）",
          "rule 规则命中：432 条\n"
          "heuristic-overflow 兜底：3745 条\n"
          "llm / llm-batch 复核：135 条\n"
          "rule-exclude 排除：1288 条\n\n"
          "每条情报标注命中关键词 / 语义分数 / 复核结论，分类过程可解释",
          ec=NAVY)
    panel(ax, 0.515, 0.090, 0.470, 0.385,
          "过滤效果（NVD · GHSA 实测）",
          "7 天窗口 2563 条 CVE → 真 AI 范畴 <1%\n"
          "GHSA 500 条 → 识别 40 条（8%）\n"
          "典型识别：NLTK 投毒 / Gradio 路径穿越 / MLflow SSRF\n\n"
          "误杀控制：38 个排除词拦截 AI 无关关键词，灰区条目留痕可追溯",
          ec=NAVY)

    save(fig, "图4_三级AI分类.png")


if __name__ == "__main__":
    fig_architecture()
    fig_dataflow()
    fig_state_machine()
    fig_classify()
    print(f"\n全部完成，输出目录：{OUT}")