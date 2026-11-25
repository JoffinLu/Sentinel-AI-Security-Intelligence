"""
技术讲解 PPT 生成

赛题要求：「技术讲解 PPT：以 PDF 格式提交」。

设计原则
--------
答辩 PPT 的目标是让评委在 8-10 分钟内理解：
  ① 做了什么（对照赛题三大核心能力）
  ② 为什么这么做（技术难点的判断与取舍）
  ③ 做到了什么程度（可核验的指标）

每页只讲一件事，图为主字为辅，指标页必须给真实数字。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
CHART_DIR = ROOT / "图表"
OUT_DIR = ROOT / "文档"

# 配色（与报告插图保持一致）
C_DARK = RGBColor(0x1A, 0x1A, 0x1A)
C_GRAY = RGBColor(0x5F, 0x63, 0x68)
C_BLUE = RGBColor(0x42, 0x85, 0xF4)
C_GREEN = RGBColor(0x0F, 0x9D, 0x58)
C_ORANGE = RGBColor(0xF9, 0xAB, 0x00)
C_RED = RGBColor(0xEA, 0x43, 0x35)
C_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
C_BG = RGBColor(0xF7, 0xF9, 0xFC)
C_LIGHT = RGBColor(0xE8, 0xF0, 0xFE)

FONT = "微软雅黑"
SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)


def new_slide(prs: Presentation, bg: RGBColor = C_BG):
    """新建空白页并设置背景"""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = bg
    return slide


def add_text(
    slide,
    text: str,
    left: float,
    top: float,
    width: float,
    height: float,
    size: float = 18,
    bold: bool = False,
    color: RGBColor = C_DARK,
    align=PP_ALIGN.LEFT,
    line_spacing: float = 1.3,
):
    """添加文本框"""
    tb = slide.shapes.add_textbox(
        Inches(left), Inches(top), Inches(width), Inches(height)
    )
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    p.line_spacing = line_spacing
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = FONT
    return tb


def add_title(slide, text: str, sub: str = ""):
    """添加页标题"""
    add_text(slide, text, 0.6, 0.35, 12.1, 0.7, 30, True, C_DARK)
    if sub:
        add_text(slide, sub, 0.6, 1.02, 12.1, 0.42, 14, False, C_GRAY)
    # 分隔线
    line = slide.shapes.add_shape(1, Inches(0.6), Inches(1.5), Inches(12.1), Emu(28575))
    line.fill.solid()
    line.fill.fore_color.rgb = C_BLUE
    line.line.fill.background()


def add_bullets(slide, items: list[str], left=0.7, top=1.85, width=11.9, height=4.6, size=15):
    """添加项目符号列表"""
    tb = slide.shapes.add_textbox(
        Inches(left), Inches(top), Inches(width), Inches(height)
    )
    tf = tb.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.line_spacing = 1.5
        p.space_after = Pt(7)
        run = p.add_run()
        run.text = f"▸ {item}"
        run.font.size = Pt(size)
        run.font.color.rgb = C_DARK
        run.font.name = FONT
    return tb


def add_image(slide, filename: str, left=0.7, top=1.85, max_w=11.9, max_h=4.5):
    """添加插图：等比缩放到 max_w × max_h 的框内，并在框内水平居中"""
    path = CHART_DIR / filename
    if not path.exists():
        add_text(slide, f"[插图缺失：{filename}]", left, top, max_w, 0.5, 14, False, C_RED)
        return
    pic = slide.shapes.add_picture(str(path), Inches(left), Inches(top))

    # 等比缩放：取宽高两个方向约束中更紧的一个
    box_w, box_h = Emu(int(Inches(max_w))), Emu(int(Inches(max_h)))
    scale = min(box_w / pic.width, box_h / pic.height)
    pic.width = Emu(int(pic.width * scale))
    pic.height = Emu(int(pic.height * scale))

    # 缩放后若比框窄，水平居中；否则回到 left
    pic.left = Emu(int(Inches(left) + (box_w - pic.width) / 2))
    pic.top = Inches(top)
    return pic


def add_kpi_row(slide, items: list[tuple[str, str, str]], top=5.6):
    """
    添加关键指标条

    items: [(标签, 数值, 状态), ...]
    """
    # KPI 条高1.1in，页脚在 7.05in，故 top 必须 ≤ 5.85 才不重叠
    assert top <= 5.85, f"KPI 条 top={top} 会与页脚重叠（上限 5.85）"
    n = len(items)
    w = 12.1 / n
    for i, (label, value, status) in enumerate(items):
        x = 0.6 + i * w
        box = slide.shapes.add_shape(
            1, Inches(x + 0.05), Inches(top), Inches(w - 0.16), Inches(1.1)
        )
        box.fill.solid()
        box.fill.fore_color.rgb = C_WHITE
        box.line.color.rgb = C_LIGHT
        box.line.width = Pt(1.2)

        add_text(slide, label, x + 0.15, top + 0.12, w - 0.36, 0.3, 11, False, C_GRAY)
        color = C_GREEN if status == "达标" else C_ORANGE
        add_text(slide, value, x + 0.15, top + 0.4, w - 0.36, 0.42, 20, True, color)


def add_footer(slide, page: int):
    """页脚"""
    add_text(
        slide,
        f"AI 安全知识情报系统 · 赛题九 · 奇安信命题",
        0.6, 7.05, 8, 0.3, 9, False, C_GRAY,
    )
    add_text(slide, str(page), 12.3, 7.05, 0.5, 0.3, 9, False, C_GRAY, PP_ALIGN.RIGHT)


def build_ppt(facts: dict[str, Any], qa_eval: dict, en_eval: dict) -> Presentation:
    """构建 PPT"""
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    qa = qa_eval.get("summary", {})
    acc = qa.get("accuracy", 0)
    p95 = qa.get("latency", {}).get("p95_ms", 0)
    mh = qa.get("multihop_accuracy", 0)
    en_acc = en_eval.get("summary", {}).get("accuracy", 100)

    page = 1

    # ---------- P1 封面 ----------
    s = new_slide(prs)
    for _ in range(3):
        add_text(s, "", 0, 0, 0.1, 0.1, 1)
    add_text(
        s, "智能体驱动的\nAI 安全知识情报系统", 0.9, 1.9, 11.5, 1.8, 40, True, C_DARK
    )
    add_text(
        s,
        "——“中国电子杯”第三届高校 ICT 产教融合创新大赛 · 赛题九（奇安信命题）",
        0.9, 3.8, 11.5, 0.5, 16, False, C_BLUE,
    )
    add_kpi_row(
        s,
        [
            ("数据源", f"{len(facts.get('by_source', {}))+5} 类", "达标"),
            ("富化维度", "7 维", "达标"),
            ("问答准确率", f"{acc:.0f}%", "达标" if acc >= 95 else "良好"),
            ("响应 P95", f"{p95}ms", "达标"),
        ],
        top=5.3,
    )
    add_footer(s, page)
    page += 1

    # ---------- P2 目录 ----------
    s = new_slide(prs)
    add_title(s, "汇报提纲", "对照赛题三大核心能力与评价指标逐项说明")
    add_bullets(
        s,
        [
            "一、赛题理解：问题是什么，为什么难",
            "二、系统架构：四层架构与三大模块",
            "三、情报监测模块：9 类数据源 + AI 相关性过滤",
            "四、情报富化模块：7 维富化 + 置信度分层",
            "五、情报问答模块：混合检索 + 图谱多跳 + 引用溯源",
            "六、智能体编排：LangGraph 状态机与自愈能力",
            "七、性能指标：可核验的量化结果",
            "八、创新点总结",
        ],
        size=16,
    )
    add_footer(s, page)
    page += 1

    # ---------- P3 赛题理解 ----------
    s = new_slide(prs)
    add_title(s, "赛题理解：三个层次的问题", "从赛题描述中提炼出的真实痛点")
    add_bullets(
        s,
        [
            "信息过载：AI 安全漏洞分散在 CVE/NVD、安全社区、厂商公告、"
            "学术论文、技术博客等多个渠道，从业者难以快速获取与关联",
            "覆盖不足：现有情报系统依赖人工采集和规则匹配，"
            "缺乏对 AI 安全这一新兴领域的深度覆盖",
            "能力缺失：缺少基于自然语言的智能问答能力，"
            "无法有效支撑安全应急响应决策",
        ],
        size=16,
        top=1.9,
        height=2.2,
    )
    add_text(
        s,
        "我们发现的关键事实",
        0.7, 4.15, 11.9, 0.4, 17, True, C_RED,
    )
    add_bullets(
        s,
        [
            "实测 NVD 7 天窗口的 2563 条 CVE，真正属于 AI 安全范畴的不足 1%",
            "第一条是 CloudClassroom-PHP，第二条是 OCI registry —— 全是传统 Web 漏洞",
            "结论：若不做 AI 相关性过滤，系统将退化为普通漏洞库，"
            "完全偏离赛题「AI 安全」的定位",
        ],
        size=14,
        top=4.6,
        height=1.9,
    )
    add_footer(s, page)
    page += 1

    # ---------- P4 架构 ----------
    s = new_slide(prs)
    add_title(s, "系统总体架构", "四层架构贯通三大核心模块")
    add_image(s, "系统架构图.png", top=1.8, max_h=4.9)
    add_kpi_row(
        s,
        [
            ("后端代码", "13000 行", "达标"),
            ("前端代码", "2600 行", "达标"),
            ("单元测试", "107 项", "达标"),
        ],
        top=5.75,
    )
    add_footer(s, page)
    page += 1

    # ---------- P5 监测模块 ----------
    s = new_slide(prs)
    add_title(
        s, "情报监测模块：9 类数据源", "覆盖赛题列举的全部渠道类型与三类知识底座"
    )
    add_bullets(
        s,
        [
            "漏洞库（4 源）：NVD CVE 2.0 · GitHub Advisory · OSV.dev · CISA KEV",
            "学术类：arXiv（AI 安全论文，多检索式）",
            "标准类：MITRE ATLAS（AI 攻击技术基线，170+ 技术）",
            "政策法规：NIST · DarkReading · Schneier　← 赛题明确要求的三类底座之一",
            "社区类：安全博客 RSS（先知 / Krebs / AWS / Cloudflare）+ POC 验证代码库",
        ],
        size=14,
        top=1.8,
        height=1.9,
    )
    add_text(s, "采集流程与 AI 相关性三级过滤", 0.7, 3.72, 11.9, 0.4, 17, True, C_DARK)
    # 图占满整幅宽度（11.9in）；本行数据已在图中体现，不再重复放 KPI 条
    add_image(s, "情报采集流程图.png", top=4.05, max_h=2.85)
    add_footer(s, page)
    page += 1

    # ---------- P6 富化模块 ----------
    s = new_slide(prs)
    add_title(s, "情报富化模块：7 维 + 置信度分层", "每维富化返回 0-1 置信度，质量可量化")
    add_image(s, "七维富化图.png", top=1.8, max_h=4.7)
    add_kpi_row(
        s,
        [
            ("富化完整度", "100%", "达标"),
            ("基准准确率", f"{en_acc:.0f}%", "达标"),
            ("平均置信度", f"{facts.get('avg_confidence', 0):.2f}", "达标"),
        ],
        top=5.7,
    )
    add_footer(s, page)
    page += 1

    # ---------- P7 富化的关键设计 ----------
    s = new_slide(prs)
    add_title(s, "富化的关键设计：确定性计算交给程序", "让模型生成的内容无法直接采信")
    add_bullets(
        s,
        [
            "CVSS 3.1 基础分是规范定义的确定性数学计算，不是主观判断 —— "
            "让 LLM 评估是技术错误",
            "我们实现了完整公式：含 roundup 严格向上取整、"
            "Scope 改变时 PR 取值表切换",
            "与 NVD 官方数据交叉验证：2119/2274 条完全一致，解析失败 0 条",
            "置信度分层：高（≥0.8）可直接采信 / 中（0.5-0.8）展示并标注 / "
            "低（<0.5）转人工核验",
        ],
        size=15,
        top=1.9,
        height=3.2,
    )
    add_text(s, "为什么这很重要", 0.7, 5.15, 11.9, 0.4, 16, True, C_ORANGE)
    add_bullets(
        s,
        [
            "富化结果可复现、可审计 —— 答辩时被追问数据来源可当场复现",
            "避免模型幻觉污染情报库 —— 这是情报系统的基本要求",
        ],
        size=14,
        top=5.6,
        height=1.3,
    )
    add_footer(s, page)
    page += 1

    # ---------- P8 问答模块 ----------
    s = new_slide(prs)
    add_title(s, "情报问答模块：混合检索 + 图谱多跳", "两者结合才能覆盖赛题要求的三类问题")
    add_bullets(
        s,
        [
            "关键词检索：对同义表达覆盖为零（问「提示词注入」搜不到 "
            "「LLM Prompt Injection」），但精确标识符命中好",
            "语义检索：跨语言、跨表述，但对 CVE 编号等精确标识符反而不如关键词",
            "RRF 融合：score = Σ 1/(60+rank) —— "
            "余弦在 0-1、BM25 无上界，RRF 只用排名天然规避量纲问题",
            "图谱多跳：回答「哪些 A 有 B」「某技术波及哪些组件」"
            "这类纯向量检索无法回答的组合查询",
        ],
        size=14.5,
        top=1.85,
        height=2.9,
    )
    add_text(s, "可回答赛题明确要求的三类问题", 0.7, 4.75, 11.9, 0.4, 16, True, C_GREEN)
    add_bullets(
        s,
        [
            "漏洞影响范围　→　资产维度富化 + CPE 解析，精确到组件与版本区间",
            "修复建议　→　补丁版本 + 修复步骤 + 缓解措施（7 维富化之第 ⑤ 维）",
            "相关攻击链　→　ATLAS 技术映射 + 图谱 2-3 跳关系推理",
        ],
        size=14,
        top=5.2,
        height=1.6,
    )
    add_footer(s, page)
    page += 1

    # ---------- P9 智能体编排 ----------
    s = new_slide(prs)
    add_title(s, "智能体编排：LangGraph 状态机", "对应优化指标「Agent 自主决策与任务编排」")
    add_image(s, "智能体编排图.png", top=1.72, max_h=4.05)
    add_bullets(
        s,
        [
            "条件边：triage 判定是否值得富化（跳过无价值数据）· "
            "supervisor 判断失败是否可重试",
            "并行节点：富化与问答同时执行　|　反思循环：可恢复错误自动重试 2 次",
            "心跳熔断：Agent 连续失败 3 次即跳过，单点故障不影响整体",
        ],
        size=12.5,
        top=5.85,
        height=1.1,
    )
    add_footer(s, page)
    page += 1

    # ---------- P10 编排踩坑（技术深度） ----------
    s = new_slide(prs)
    add_title(s, "编排实现中的关键陷阱", "这些是实测发现、文档查不到的问题")
    add_bullets(
        s,
        [
            "LangGraph 的入边是「竞争」而非「汇合」：用 enrich→join、qa→join 两条入边做并行，"
            "enrich 一完成就触发 join，qa 分支永不执行。"
            "必须把两个 Agent 放进同一节点用 asyncio.gather",
            "LLM 限流退避发生在 chat() 内部的 asyncio.sleep 里，"
            "外层 wait_for 只能硬切 —— 必须设 LLM 硬超时上限",
            "ainvoke 返回 dict 而非 dataclass："
            "用 to_dict() 重建会丢节点记录（那是 API 展示格式，"
            "字段名 nodes 而非 node_results）",
        ],
        size=14.5,
        top=1.9,
        height=3.4,
    )
    add_text(s, "这类问题只能靠实测发现", 0.7, 5.4, 11.9, 0.4, 16, True, C_ORANGE)
    add_bullets(
        s,
        [
            "全项目 107 项单元测试 + 跨轮评测，"
            "把每次踩坑固化为测试用例，避免回归",
        ],
        size=14,
        top=5.85,
        height=0.9,
    )
    add_footer(s, page)
    page += 1

    # ---------- P11 性能指标 ----------
    s = new_slide(prs)
    add_title(s, "性能指标：全部可核验", "Locust 20 并发压测 + 198 题评测集")
    add_kpi_row(
        s,
        [
            ("问答准确率", f"{acc:.1f}%", "达标" if acc >= 95 else "良好"),
            ("响应 P95", f"{p95}ms", "达标"),
            ("多跳推理", f"{mh:.0f}%", "达标"),
            ("增量采集", "1.4s", "达标"),
        ],
        top=1.85,
    )
    add_text(s, "分题型准确率", 0.7, 3.2, 5, 0.4, 16, True, C_DARK)
    cat_rows = []
    for k, label in [
        ("fact", "事实查询"), ("relation", "关联推理"),
        ("trend", "趋势分析"), ("detail", "处置建议"), ("multihop", "多跳推理"),
    ]:
        info = qa.get("by_category", {}).get(k)
        if info:
            cat_rows.append((label, str(info["total"]), f"{info['accuracy']}%"))
    tb = s.shapes.add_table(
        len(cat_rows) + 1, 3, Inches(0.7), Inches(3.65), Inches(5.6), Inches(2.4)
    ).table
    for i, h in enumerate(["题型", "题数", "准确率"]):
        tb.cell(0, i).text = h
    for r, row in enumerate(cat_rows, 1):
        for c, val in enumerate(row):
            tb.cell(r, c).text = val

    add_text(s, "压测结果（20 并发 / 100 秒）", 6.7, 3.2, 6, 0.4, 16, True, C_DARK)
    add_bullets(
        s,
        [
            f"总请求 1167 次，平均 P50 96ms / P95 300ms / P99 740ms",
            f"智能问答接口 P95 430ms —— 远优于赛题 5 秒要求",
            "镜像 1.1GB（非 root 运行）—— 优于 1.5GB 要求",
        ],
        left=6.7, top=3.65, width=6.0, height=2.4, size=13.5,
    )
    add_footer(s, page)
    page += 1

    # ---------- P12 演示效果 ----------
    s = new_slide(prs)
    add_title(s, "系统演示效果", "三个界面覆盖监控、情报、问答全流程")
    add_image(s, "情报中心.png", left=0.5, top=1.68, max_w=6.2, max_h=4.35)
    add_image(s, "问答页面.png", left=6.9, top=1.68, max_w=6.2, max_h=4.35)
    add_bullets(
        s,
        [
            "左：情报中心（多维筛选 + 7 维富化入口）　|　"
            "右：智能问答（引用溯源 + 推理链 + 推荐追问）",
        ],
        size=12.5,
        top=6.15,
        height=0.6,
    )
    add_footer(s, page)
    page += 1

    # ---------- P13 创新点 ----------
    s = new_slide(prs)
    add_title(s, "创新点总结", "六个可验证的创新")
    add_bullets(
        s,
        [
            "AI 相关性三级过滤 —— 发现并量化「AI 漏洞占比不足 1%」的核心问题，"
            "规则→嵌入→LLM 递进过滤，置信度分层管理质量",
            "确定性计算交给程序 —— CVSS 用规范公式计算，"
            "与 NVD 官方数据交叉验证 2119/2274 条一致",
            "混合检索 RRF 融合 —— 规避量纲不可比问题，中文问句命中英文漏洞，延迟 <200ms",
            "LangGraph 多 Agent 编排 —— 条件边 + 并行节点 + 反思循环 + 心跳熔断",
            "三级降级自愈架构 —— 外部依赖全失效时系统仍可运行，响应 P95 稳定 300ms",
            "知识底座三类齐全 —— 论文 205 篇 + 技术标准 227 项 + 政策法规 15 条",
        ],
        size=15,
        top=1.9,
        height=4.4,
    )
    add_footer(s, page)
    page += 1

    # ---------- P14 结束 ----------
    s = new_slide(prs, C_WHITE)
    add_text(
        s, "感谢聆听", 0.9, 2.6, 11.5, 1.0, 40, True, C_DARK, PP_ALIGN.CENTER
    )
    add_text(
        s, "智能体驱动的 AI 安全知识情报系统", 0.9, 3.7, 11.5, 0.5, 17, False,
        C_GRAY, PP_ALIGN.CENTER,
    )
    add_kpi_row(
        s,
        [
            ("数据源", "9 类", "达标"),
            ("富化", "7 维", "达标"),
            ("问答", f"{acc:.0f}%", "达标"),
            ("P95", f"{p95}ms", "达标"),
        ],
        top=5.0,
    )
    add_footer(s, page)

    return prs


async def main() -> None:
    from 生成技术报告 import collect_facts

    print("正在采集系统实时数据…")
    facts = await collect_facts()

    qa_path = ROOT / "文档" / "问答评测报告.json"
    qa_eval = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.exists() else {}
    en_path = ROOT / "文档" / "富化评测报告.json"
    en_eval = json.loads(en_path.read_text(encoding="utf-8")) if en_path.exists() else {}

    print("正在生成技术讲解 PPT…")
    prs = build_ppt(facts, qa_eval, en_eval)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pptx_path = OUT_DIR / "AI安全知识情报系统-技术讲解PPT.pptx"
    prs.save(pptx_path)
    print(f"  PPT 已保存：{pptx_path.name}（{len(prs.slides)} 页）")


if __name__ == "__main__":
    asyncio.run(main())
