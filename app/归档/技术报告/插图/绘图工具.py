"""技术报告插图 · 公共绘图工具

本模块汇总 10 张插图的共享能力，各图脚本（图1_*.py ~ 图10_*.py）从这里导入：

  · 全局配色与中文字体      —— 改配色 / 字体只需改本文件
  · 示意图图元              —— 圆角卡片 box / 两段卡片 card2 / 信息面板 panel
                               徽标 badge / 胶囊标签 pill / 柔和投影 soft_shadow / 连线 arrow
  · 文字排版                —— 真实渲染测宽 + 自动换行（杜绝文字溢出框）
  · 数据图样式              —— 坐标轴美化 style_ax / 阈值线 thresh / 数据库查询 sql
  · 统一路径与保存          —— 输出到本目录（归档/技术报告/插图）

依赖：matplotlib、numpy；图 5~8 另需本机 docker 中的 aiintel-postgres 容器。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Patch

# ============================================================
# 路径（脚本与本模块同目录，无需修改）
# ============================================================
OUT = Path(__file__).resolve().parent          # 归档/技术报告/插图
ROOT = OUT.parents[2]                          # ICT-WB 仓库根
DATA = ROOT / "归档/技术报告/数据"              # 数据快照 / JSON 数据源
OUT.mkdir(parents=True, exist_ok=True)

# ============================================================
# 中文字体（找不到则回退 SimHei）
# ============================================================
for _f in ("Microsoft YaHei", "SimHei"):
    try:
        font_manager.findfont(_f, fallback_to_default=False)
        plt.rcParams["font.family"] = _f
        break
    except Exception:
        continue

# ============================================================
# 全局配色（10 张图统一使用，改这里即可整体换色）
# ============================================================
G_DARK = "#05825c"      # 主绿 · 深
G_MAIN = "#0a9d6e"      # 主绿 · 标准
G_LIGHT = "#7cc7a8"     # 主绿 · 浅
G_PALE = "#cfe9dd"      # 主绿 · 极浅
G_FILL = "#e8f6f0"      # 主绿填充
G_FILL2 = "#f4fbf7"     # 主绿填充 · 更浅
NAVY = "#12333a"        # 墨绿（标题 / 连线）
INK = "#1c2b26"         # 正文黑
GRAY = "#6b7a74"        # 次要灰
LINE = "#9dc3b4"        # 线条 / 网格
GOLD = "#b7791f"        # 强调金
GOLD_FILL = "#fdf3e3"
RED = "#b54848"         # 警示红
RED_FILL = "#fbeaea"
BLUE = "#2563a8"        # 信息蓝
BLUE_FILL = "#eaf2fb"
PURPLE = "#7c5cbf"
SHADOW = "#12333a"      # 投影色
PALETTE = [G_DARK, BLUE, GOLD, PURPLE, RED, G_MAIN, GRAY, "#3aa6a0", "#c98a3d"]

# 数据图全局 rcParams
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

# 阈值 / 注释统一白底遮罩，杜绝文字压线
MASK = dict(facecolor="white", edgecolor="none", alpha=0.9, pad=1.6)
# 数据图图例统一样式
LEG = dict(frameon=True, facecolor="white", edgecolor=LINE, framealpha=0.96,
           borderpad=0.45, handlelength=1.6, handletextpad=0.6)


# ============================================================
# 文字排版：真实渲染测宽 / 智能换行 / 越界自检
# ============================================================
_FIG_W, _FIG_H = 8.0, 6.0
_FIG_REF = None
_AX_REF = None
_REG: list = []
_WCACHE: dict = {}

_ASCII_TOKEN = re.compile(r"[A-Za-z0-9_./:+~%&#?=<>-]+")


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


def reg(t, ax, x, y, w, h):
    """登记一个文字对象参与保存前的越界自检（自定义图元用）。"""
    _REG.append((t, ax, x, y, w, h))


# ============================================================
# 画布与保存
# ============================================================
def new_fig(w, h, dpi=300):
    """新建归一化坐标画布（0~1），并重置测宽缓存与越界登记。"""
    global _FIG_W, _FIG_H, _FIG_REF, _AX_REF
    _FIG_W, _FIG_H = w, h
    _REG.clear()
    _WCACHE.clear()
    fig, ax = plt.subplots(figsize=(w, h), dpi=dpi)
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    _FIG_REF, _AX_REF = fig, ax
    fig.canvas.draw()
    return fig, ax


def save_diagram(fig, name):
    """保存示意图：保存前用真实渲染包围盒做越界自检，输出到 插图/。"""
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


def save_chart(fig, name):
    """保存数据图：bbox_inches=tight，300 DPI，白底。"""
    fig.savefig(OUT / name, bbox_inches="tight", facecolor="white",
                pad_inches=0.12, dpi=300)
    plt.close(fig)
    print(f"✓ {name}")


# ============================================================
# 示意图图元
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
    """圆角胶囊标签，用于边注释 / 角标，白底避免压线。"""
    ax.text(x, y, text, ha=ha, va="center", fontsize=fs, color=tc, zorder=zorder,
            bbox=dict(boxstyle="round,pad=0.32", fc=fc, ec=ec, lw=0.7))


def box(ax, x, y, w, h, text="", fc="#ffffff", ec=LINE, tc=INK, fs=9, lw=1.2,
        bold=False, radius=0.045, zorder=3, align="center", wrap=True,
        linespacing=1.5, style="normal", shadow=False, shadow_alpha=0.10,
        shadow_k=1.0):
    """圆角卡片（可带投影），文字自动换行并登记越界检查。"""
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
    reg(t, ax, x + 0.005, y + 0.005, w - 0.010, h - 0.010)


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
    reg(t1, ax, x + 0.006, y + 0.006, w - 0.012, h - 0.012)
    reg(t2, ax, x + 0.006, y + 0.006, w - 0.012, h - 0.012)


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
    reg(t1, ax, x + 0.006, y + 0.006, w - 0.012, h - 0.012)
    reg(t2, ax, x + 0.006, y + 0.006, w - 0.012, h - 0.012)


def arrow(ax, x1, y1, x2, y2, color=G_DARK, lw=1.6, style="-|>",
          shrink=2, rad=0.0, zorder=2, ls="-"):
    """连线 / 箭头（rad 控制弧度，ls 控制虚实）。"""
    ax.add_patch(FancyArrowPatch(
        (x1, y1), (x2, y2),
        arrowstyle=style, mutation_scale=13, color=color, lw=lw,
        shrinkA=shrink, shrinkB=shrink, zorder=zorder, linestyle=ls,
        connectionstyle=f"arc3,rad={rad}",
    ))


# ============================================================
# 数据图样式与数据源
# ============================================================
def style_ax(ax, ygrid=True):
    """数据图坐标轴统一美化：去顶右脊、浅网格、零刻度线。"""
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


def sql(query: str) -> list[list[str]]:
    """经 docker exec 查询 aiintel-postgres（图 5~8 数据源）。"""
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


def load_json(name: str):
    """读取 归档/技术报告/数据/<name>。"""
    return json.loads((DATA / name).read_text(encoding="utf-8"))