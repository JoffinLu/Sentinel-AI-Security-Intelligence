// SENTINEL 答辩讲解 PPT 生成脚本
// 用法：node 生成PPT.mjs   （需先 npm install pptxgenjs，素材由 准备素材.py 生成）
// 输出：归档/技术报告/SENTINEL_答辩讲解PPT.pptx
//
// 设计规范：
//   · 三明治结构：深墨绿封面/演示/结尾 + 浅绿白内容页
//   · 字体：思源宋体（标题/大数字）+ 微软雅黑（正文）
//   · 母题：圆形序号徽标 + 白色圆角投影卡片 + 金色强调，与报告插图视觉同源
//   · 16 页：封面/目录/赛题/总览/架构/数据流/数据资产/状态机/三级分类/七维富化/评测/压测/演示×2/创新/致谢

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import pptxgen from "pptxgenjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const A = (name) => path.join(__dirname, "素材", name);
const OUT = path.join(__dirname, "..", "SENTINEL_答辩讲解PPT.pptx");

// ============================================================
// 配色（与报告插图同源）
// ============================================================
const DARK = "0B2B23";      // 深墨绿（封面/演示/结尾底色）
const INK = "1C2B26";       // 正文墨色
const PRIMARY = "05825C";   // 深绿
const MAIN = "0A9D6E";      // 主绿
const LIGHT = "F6FBF8";     // 内容页浅底
const GFILL = "E8F6F0";     // 浅绿填充
const MUTED = "6B7A74";     // 次要文字
const LINE = "9DC3B4";      // 描边绿
const GOLD = "B7791F";      // 金色强调
const GOLDF = "FDF3E3";     // 金底
const NAVY = "12333A";      // 藏青
const RED = "B54848";       // 红
const PALE = "CDE8DC";      // 深底上的浅绿文字
const PALE2 = "9DC3B4";     // 深底上的次要文字
const FOOT = "9DB5AA";      // 页脚

const SERIF = "Noto Serif SC";
const SANS = "Microsoft YaHei";

// ============================================================
// 幻灯片尺寸 + 容器系统
// ============================================================
const SLIDE_W = 10, SLIDE_H = 5.625;
const SH = { type: "outer", angle: 90, blur: 4, offset: 2, color: "12333A", opacity: 0.16 };

let pres = new pptxgen();
pres.layout = "LAYOUT_16x9";
pres.author = "SENTINEL Team";
pres.title = "SENTINEL · AI 安全知识情报系统 答辩讲解";

function parseImageDimensions(p) {
  const m = p.match(/_(\d+)x(\d+)\.(png|jpg|jpeg|gif|webp)$/i);
  return m ? { width: +m[1], height: +m[2] } : null;
}
function calculateScaledImageOpts(opts) {
  const { path: p, w: tw, h: th, x = 0, y = 0, ...rest } = opts;
  if (!p || !tw || !th) return opts;
  const d = parseImageDimensions(p);
  if (!d) return opts;
  const ia = d.width / d.height, ta = tw / th;
  let w, h, ox = 0, oy = 0;
  if (ia > ta) { w = tw; h = tw / ia; oy = (th - h) / 2; }
  else { h = th; w = th * ia; ox = (tw - w) / 2; }
  return { path: p, x: x + ox, y: y + oy, w, h, ...rest };
}
function createVirtualNode(type, data, px = 0, py = 0) {
  const opts = data.opts || {};
  const node = {
    type, data,
    absX: px + (opts.x || 0), absY: py + (opts.y || 0),
    w: opts.w || 0, h: opts.h || 0, children: [],
  };
  node.addShape = function (shapeType, o = {}) {
    const c = createVirtualNode("shape", { shapeType, opts: o }, node.absX, node.absY);
    node.children.push(c); return c;
  };
  node.addText = function (text, o = {}) {
    const c = createVirtualNode("text", { text, opts: { fit: "shrink", ...o } }, node.absX, node.absY);
    node.children.push(c); return c;
  };
  node.addImage = function (o = {}) {
    const c = createVirtualNode("image", { opts: calculateScaledImageOpts(o) }, node.absX, node.absY);
    node.children.push(c); return c;
  };
  node.addTable = function (t, o = {}) {
    const c = createVirtualNode("table", { tableData: t, opts: o }, node.absX, node.absY);
    node.children.push(c); return c;
  };
  return node;
}
function flattenNode(node, realSlide) {
  const absOpts = { ...node.data.opts, x: node.absX, y: node.absY };
  if (node.type === "shape") realSlide.addShape(node.data.shapeType, absOpts);
  else if (node.type === "text") realSlide.addText(node.data.text, absOpts);
  else if (node.type === "image") realSlide.addImage(absOpts);
  else if (node.type === "table") realSlide.addTable(node.data.tableData, absOpts);
  node.children.forEach((c) => flattenNode(c, realSlide));
}
const originalAddSlide = pres.addSlide.bind(pres);
pres.addSlide = function (options) {
  const realSlide = originalAddSlide(options);
  const virtualSlide = {
    children: [], _realSlide: realSlide,
    set background(v) { realSlide.background = v; },
    get background() { return realSlide.background; },
    addShape: function (shapeType, o = {}) {
      const n = createVirtualNode("shape", { shapeType, opts: o }, 0, 0);
      this.children.push(n); return n;
    },
    addText: function (text, o = {}) {
      const n = createVirtualNode("text", { text, opts: { fit: "shrink", ...o } }, 0, 0);
      this.children.push(n); return n;
    },
    addImage: function (o = {}) {
      const n = createVirtualNode("image", { opts: calculateScaledImageOpts(o) }, 0, 0);
      this.children.push(n); return n;
    },
    addTable: function (t, o = {}) {
      const n = createVirtualNode("table", { tableData: t, opts: o }, 0, 0);
      this.children.push(n); return n;
    },
    addChart: function (t, d, o) { realSlide.addChart(t, d, o); },
    addNotes: function (n) { realSlide.addNotes(n); },
    render: function () { this.children.forEach((c) => flattenNode(c, realSlide)); },
  };
  return virtualSlide;
};

// ============================================================
// 通用图元
// ============================================================
function card(slide, x, y, w, h, { fill = "FFFFFF", radius = 0.09, shadow = true, line = null } = {}) {
  const o = { x, y, w, h, fill: { color: fill }, rectRadius: radius };
  if (shadow) o.shadow = SH;
  if (line) o.line = line;
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, o);
}
function accentBar(slide, x, y, h, color, w = 0.07) {
  slide.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color } });
}
function badge(slide, x, y, d, label, fill, tc = "FFFFFF", fs = 12) {
  slide.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: fill } });
  slide.addText(label, {
    x, y, w: d, h: d, align: "center", valign: "middle",
    fontFace: SERIF, fontSize: fs, bold: true, color: tc, margin: 0,
  });
}
let PAGE = 0;
const TOTAL = 16;
function chrome(slide, { num, title, en, dark = false }) {
  PAGE += 1;
  slide.background = { path: A(dark ? "bg_demo_2000x1125.png" : "bg_light_2000x1125.png") };
  badge(slide, 0.5, 0.35, 0.47, num, dark ? MAIN : PRIMARY, "FFFFFF", 12.5);
  slide.addText(title, {
    x: 1.14, y: 0.3, w: 5.2, h: 0.56, fontFace: SERIF, fontSize: 22.5, bold: true,
    color: dark ? "FFFFFF" : INK, valign: "middle", margin: 0, charSpacing: 1,
  });
  slide.addText(en, {
    x: 6.45, y: 0.46, w: 3.05, h: 0.26, align: "right", fontSize: 8,
    color: dark ? PALE2 : MUTED, charSpacing: 2, fontFace: SANS, margin: 0, valign: "middle",
  });
  slide.addText("SENTINEL · AI 安全知识情报系统", {
    x: 0.5, y: 5.36, w: 3.6, h: 0.2, fontSize: 7.5,
    color: dark ? "7FA898" : FOOT, fontFace: SANS, margin: 0,
  });
  slide.addText(`${String(PAGE).padStart(2, "0")} / ${TOTAL}`, {
    x: 8.9, y: 5.36, w: 0.6, h: 0.2, align: "right", fontSize: 8,
    color: dark ? "7FA898" : FOOT, fontFace: SANS, margin: 0,
  });
}

// ============================================================
// S1 · 封面（深色）
// ============================================================
(function cover() {
  const s = pres.addSlide();
  PAGE += 1;
  s.background = { path: A("bg_cover_2000x1125.png") };

  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 2.6, y: 0.58, w: 4.8, h: 0.44, rectRadius: 0.22,
    fill: { color: "10362C" },
    line: { color: LINE, width: 0.75, transparency: 40 },
  });
  s.addText("第三届「中国电子杯」高校 ICT 产教融合创新大赛 · 决赛答辩", {
    x: 2.6, y: 0.58, w: 4.8, h: 0.44, align: "center", valign: "middle",
    fontSize: 10, color: PALE, fontFace: SANS, margin: 0, charSpacing: 0.5,
  });

  s.addText("SENTINEL", {
    x: 0.5, y: 1.3, w: 9, h: 0.85, align: "center", valign: "middle",
    fontFace: SERIF, fontSize: 46, bold: true, color: "FFFFFF", charSpacing: 8, margin: 0,
  });
  s.addText("AI 安全知识情报系统", {
    x: 0.5, y: 2.2, w: 9, h: 0.62, align: "center", valign: "middle",
    fontFace: SERIF, fontSize: 28, bold: true, color: "FFFFFF", charSpacing: 3, margin: 0,
  });
  s.addText("采集 · 富化 · 问答 —— 面向 AI 安全的全链路知识情报系统", {
    x: 0.5, y: 2.94, w: 9, h: 0.34, align: "center", valign: "middle",
    fontSize: 12.5, color: PALE2, fontFace: SANS, margin: 0, charSpacing: 1,
  });

  s.addShape(pres.shapes.OVAL, { x: 4.72, y: 3.47, w: 0.07, h: 0.07, fill: { color: LINE } });
  s.addShape(pres.shapes.OVAL, { x: 4.955, y: 3.44, w: 0.1, h: 0.1, fill: { color: GOLD } });
  s.addShape(pres.shapes.OVAL, { x: 5.22, y: 3.47, w: 0.07, h: 0.07, fill: { color: LINE } });

  const stats = [
    ["8,313", "全库情报总量"],
    ["87.88%", "问答准确率 · 赛题 ≥85%"],
    ["3.6 s", "P95 响应 · 赛题 ≤5s"],
    ["100%", "多跳推理 · 赛题 ≥80%"],
  ];
  stats.forEach(([v, l], i) => {
    const x = 0.6 + i * 2.26;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y: 3.84, w: 2.02, h: 1.04, rectRadius: 0.08,
      fill: { color: "10362C" },
      line: { color: LINE, width: 0.75, transparency: 50 },
    });
    s.addText(v, {
      x, y: 3.97, w: 2.02, h: 0.42, align: "center", valign: "middle",
      fontFace: SERIF, fontSize: 19, bold: true, color: "FFFFFF", margin: 0,
    });
    s.addText(l, {
      x: x + 0.06, y: 4.44, w: 1.9, h: 0.32, align: "center", valign: "middle",
      fontSize: 8.5, color: PALE2, fontFace: SANS, margin: 0,
    });
  });

  s.addText("SENTINEL 团队 · 2026 年 10 月", {
    x: 0.5, y: 5.12, w: 9, h: 0.28, align: "center", valign: "middle",
    fontSize: 9.5, color: "7FA898", fontFace: SANS, margin: 0, charSpacing: 1,
  });
  s.addNotes("各位评委好，我们是 SENTINEL 团队，参赛作品是「AI 安全知识情报系统」——一套覆盖采集、富化、问答全链路的 AI 安全情报平台。先报三个数：全库 8313 条情报、问答准确率 87.88%、P95 响应 3.6 秒，对照赛题三项硬指标全部达标。");
  s.render();
})();

// ============================================================
// S2 · 目录（浅色）
// ============================================================
(function toc() {
  const s = pres.addSlide();
  chrome(s, { num: "目", title: "目录", en: "CONTENTS" });
  const items = [
    ["01", "赛题理解", "痛点拆解 · 设计目标 · 硬指标对照"],
    ["02", "总体设计", "方案总览 · 分层架构 · 全链路数据流 · 数据资产"],
    ["03", "智能体引擎", "LangGraph 状态机 · 三级 AI 分类 · 七维富化"],
    ["04", "质量与性能", "问答评测 · 消融实验 · 四档并发压测"],
    ["05", "系统演示", "智能问答 · 双主题 · 态势洞察 · 知识图谱"],
    ["06", "创新总结", "六大创新点 · 关键数字回顾"],
  ];
  items.forEach(([n, t, d], i) => {
    const x = i % 2 === 0 ? 0.5 : 5.18;
    const y = 1.22 + Math.floor(i / 2) * 1.36;
    card(s, x, y, 4.32, 1.18);
    s.addText(n, {
      x: x + 0.26, y: y + 0.22, w: 0.78, h: 0.74, fontFace: SERIF, fontSize: 24,
      bold: true, color: PRIMARY, valign: "middle", margin: 0,
    });
    s.addText(t, {
      x: x + 1.12, y: y + 0.2, w: 3.05, h: 0.4, fontFace: SANS, fontSize: 14,
      bold: true, color: INK, valign: "middle", margin: 0,
    });
    s.addText(d, {
      x: x + 1.12, y: y + 0.62, w: 3.08, h: 0.4, fontSize: 8.8, color: MUTED,
      fontFace: SANS, valign: "top", margin: 0,
    });
  });
  s.addNotes("汇报分六个部分：先讲赛题理解和痛点，再看总体设计与数据资产，重点展开智能体引擎的设计，然后用评测、消融和压测三组实测数据说明质量，接着进入系统演示，最后总结创新点。");
  s.render();
})();

// ============================================================
// S3 · 赛题理解（01）
// ============================================================
(function problem() {
  const s = pres.addSlide();
  chrome(s, { num: "01", title: "赛题理解 · 痛点与目标", en: "PROBLEM & GOALS" });

  const pains = [
    ["情报高度分散", RED, "AI 组件漏洞散落在 NVD、GHSA、OSV、CISA KEV 等 9 类异构数据源，格式与协议各异，人工跟踪无法覆盖"],
    ["有效信号被噪声淹没", GOLD, "NVD 7 天窗口 2,563 条 CVE 中，真 AI 范畴不足 1% —— 需要在海量噪声中精准筛出目标"],
    ["生成内容难以溯源", NAVY, "通用 LLM 直接作答数值统计不可靠（实测趋势类仅 36.4%），回答必须可验证、可回查证据链"],
  ];
  pains.forEach(([t, c, d], i) => {
    const y = 1.16 + i * 1.4;
    card(s, 0.5, y, 5.42, 1.24);
    accentBar(s, 0.5, y, 1.24, c);
    s.addText(t, {
      x: 0.78, y: y + 0.16, w: 4.9, h: 0.32, fontFace: SANS, fontSize: 13,
      bold: true, color: INK, margin: 0,
    });
    s.addText(d, {
      x: 0.78, y: y + 0.52, w: 4.98, h: 0.62, fontSize: 9.5, color: MUTED,
      fontFace: SANS, margin: 0, valign: "top",
    });
  });

  card(s, 6.14, 1.16, 3.36, 4.04, { fill: DARK, radius: 0.1 });
  s.addText("赛题硬指标", {
    x: 6.44, y: 1.4, w: 2.8, h: 0.36, fontFace: SERIF, fontSize: 15, bold: true,
    color: "FFFFFF", margin: 0,
  });
  s.addText("CHALLENGE METRICS", {
    x: 6.44, y: 1.78, w: 2.8, h: 0.22, fontSize: 7.5, color: PALE2,
    charSpacing: 2, fontFace: SANS, margin: 0,
  });
  const mets = [["问答准确率", "≥ 85%"], ["响应时间", "≤ 5 s"], ["多跳推理", "≥ 80%"]];
  mets.forEach(([k, v], i) => {
    const y = 2.18 + i * 0.52;
    s.addText(k, {
      x: 6.44, y, w: 1.6, h: 0.4, fontSize: 10.5, color: PALE, fontFace: SANS,
      valign: "middle", margin: 0,
    });
    s.addText(v, {
      x: 8.0, y, w: 1.3, h: 0.4, align: "right", fontFace: SERIF, fontSize: 13,
      bold: true, color: "FFFFFF", valign: "middle", margin: 0,
    });
  });
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 6.38, y: 3.88, w: 2.88, h: 1.08, rectRadius: 0.08,
    fill: { color: "0E4D3B" },
  });
  s.addText("实测成绩", {
    x: 6.62, y: 3.98, w: 2.4, h: 0.22, fontSize: 8.5, color: PALE2, fontFace: SANS, margin: 0,
  });
  s.addText("87.88% ｜ 3.6 s ｜ 100%", {
    x: 6.62, y: 4.22, w: 2.5, h: 0.36, fontFace: SERIF, fontSize: 15.5, bold: true,
    color: "FFFFFF", margin: 0,
  });
  s.addText("三项硬指标全部达标 ✓", {
    x: 6.62, y: 4.62, w: 2.4, h: 0.26, fontSize: 9.5, color: "6FD9A8", fontFace: SANS, margin: 0,
  });
  s.addNotes("我们对赛题的拆解是三个痛点：第一，情报分散——AI 组件漏洞散落在 9 类异构源里；第二，噪声淹没——NVD 一周 2500 多条 CVE 里真 AI 的不足 1%；第三，溯源困难——通用大模型直接数数不可靠。对应赛题三个硬指标，右下角是我们的实测成绩：87.88%、3.6 秒、100%，三项全部超标达成。");
  s.render();
})();

// ============================================================
// S4 · 方案总览（02）
// ============================================================
(function overview() {
  const s = pres.addSlide();
  chrome(s, { num: "02", title: "方案总览 · 一套全链路情报系统", en: "SOLUTION OVERVIEW" });

  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 1.1, w: 9, h: 0.58, rectRadius: 0.09, fill: { color: GFILL },
  });
  s.addText("「 面向 AI 安全的 采集 → 富化 → 问答 全链路知识情报系统 」", {
    x: 0.5, y: 1.1, w: 9, h: 0.58, align: "center", valign: "middle",
    fontFace: SERIF, fontSize: 14.5, bold: true, color: PRIMARY, margin: 0,
  });

  const feats = [
    ["源", PRIMARY, "9 源实时监测", "NVD · GHSA · OSV · CISA KEV · arXiv · POC · ATLAS · 政策 · RSS，差异化周期调度（30min~7d）+ 断点续采"],
    ["编", NAVY, "智能体编排", "LangGraph 状态机：优先级分诊 → 并行富化 → supervisor 校验，自愈重试 + 熔断降级"],
    ["维", MAIN, "七维富化", "资产 / POC / ATT&CK 映射 / 风险定级 / 修复建议 / 关联论文 / CVSS，置信度分级"],
    ["问", GOLD, "可溯源问答", "RAG 多轮对话 · 多跳关联推理 · 回答附可点击引用，证据可回查"],
  ];
  feats.forEach(([g, c, t, d], i) => {
    const x = i % 2 === 0 ? 0.5 : 5.09;
    const y = 1.88 + Math.floor(i / 2) * 1.58;
    card(s, x, y, 4.41, 1.4);
    badge(s, x + 0.22, y + 0.22, 0.42, g, c, "FFFFFF", 13);
    s.addText(t, {
      x: x + 0.8, y: y + 0.2, w: 3.4, h: 0.36, fontFace: SANS, fontSize: 13.5,
      bold: true, color: INK, valign: "middle", margin: 0,
    });
    s.addText(d, {
      x: x + 0.26, y: y + 0.66, w: 3.95, h: 0.64, fontSize: 9, color: MUTED,
      fontFace: SANS, margin: 0, valign: "top",
    });
  });

  s.addText([
    { text: "全库情报 ", options: { color: MUTED } },
    { text: "8,313", options: { color: PRIMARY, bold: true, fontFace: SERIF, fontSize: 12 } },
    { text: " 条 · AI 范畴 ", options: { color: MUTED } },
    { text: "574", options: { color: PRIMARY, bold: true, fontFace: SERIF, fontSize: 12 } },
    { text: " 条 · 富化记录 ", options: { color: MUTED } },
    { text: "3,304", options: { color: PRIMARY, bold: true, fontFace: SERIF, fontSize: 12 } },
    { text: " 条 · API 接口 ", options: { color: MUTED } },
    { text: "47", options: { color: PRIMARY, bold: true, fontFace: SERIF, fontSize: 12 } },
    { text: " 个", options: { color: MUTED } },
  ], {
    x: 0.5, y: 4.98, w: 9, h: 0.32, align: "center", valign: "middle",
    fontSize: 10, fontFace: SANS, margin: 0,
  });
  s.addNotes("一句话定位：面向 AI 安全的采集-富化-问答全链路情报系统。四个亮点：九类数据源实时监测、LangGraph 智能体编排、七维情报富化、可溯源的 RAG 问答。底下这排数字是当前的实测规模。");
  s.render();
})();

// ============================================================
// S5 · 总体架构（02）
// ============================================================
(function architecture() {
  const s = pres.addSlide();
  chrome(s, { num: "02", title: "总体架构 · 四层设计", en: "SYSTEM ARCHITECTURE" });

  card(s, 0.5, 1.1, 3.6, 4.1, { radius: 0.06 });
  s.addImage({ path: A("fig_arch_2412x2862.png"), x: 0.61, y: 1.2, w: 3.38, h: 3.9 });

  const layers = [
    ["04", "展示层", MAIN, "React 18 + antd5 + ECharts —— 7 大页面 · 黑夜/白天双主题"],
    ["03", "智能层", PRIMARY, "LangGraph 编排 · BGE-small-zh 嵌入 · DeepSeek LLM（fallback 双模型）"],
    ["02", "服务层", NAVY, "FastAPI 47 接口 · APScheduler 差异化调度 · 每源 2rps 限速"],
    ["01", "数据层", INK, "PostgreSQL 16 + pgvector 向量检索 · Redis 缓存 · Neo4j 图谱"],
  ];
  layers.forEach(([n, t, c, d], i) => {
    const y = 1.1 + i * 1.06;
    card(s, 4.3, y, 5.2, 0.93);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x: 4.48, y: y + 0.215, w: 0.82, h: 0.5, rectRadius: 0.07, fill: { color: c },
    });
    s.addText(n + " " + t, {
      x: 4.48, y: y + 0.215, w: 0.82, h: 0.5, align: "center", valign: "middle",
      fontFace: SANS, fontSize: 10.5, bold: true, color: "FFFFFF", margin: 0,
    });
    s.addText(d, {
      x: 5.48, y: y + 0.13, w: 3.9, h: 0.68, fontSize: 9, color: INK,
      fontFace: SANS, margin: 0, valign: "middle",
    });
  });
  s.addNotes("系统分四层。数据层三库协同：PostgreSQL 加 pgvector 做向量检索、Redis 缓存、Neo4j 存图谱；服务层 FastAPI 提供 47 个接口，调度器按源差异化限速；智能层是系统的大脑，后面重点讲；展示层七个页面、双主题。");
  s.render();
})();

// ============================================================
// S6 · 全链路数据流（02）
// ============================================================
(function dataflow() {
  const s = pres.addSlide();
  chrome(s, { num: "02", title: "全链路数据流 · 一条情报的生命周期", en: "END-TO-END DATA FLOW" });

  card(s, 0.5, 1.08, 9, 3.22, { radius: 0.06 });
  s.addImage({ path: A("fig_flow_3192x1572.png"), x: 0.6, y: 1.18, w: 8.8, h: 3.02 });

  const pts = [
    ["六阶段全链路", "采集 → 分诊 → 分类 → 富化 → 入库 → 问答，环环留痕"],
    ["三条旁路设计", "低价值直通 · 熔断降级 · LLM 预算超限兜底"],
    ["断点续采", "catch_up_missed() 重启自动补跑，任务不丢失"],
  ];
  pts.forEach(([t, d], i) => {
    const x = 0.5 + i * 3.065;
    card(s, x, 4.46, 2.87, 0.76);
    accentBar(s, x, 4.46, 0.76, i === 2 ? GOLD : PRIMARY, 0.055);
    s.addText(t, {
      x: x + 0.18, y: 4.55, w: 2.6, h: 0.26, fontFace: SANS, fontSize: 10.5,
      bold: true, color: INK, margin: 0,
    });
    s.addText(d, {
      x: x + 0.18, y: 4.84, w: 2.62, h: 0.3, fontSize: 8.5, color: MUTED,
      fontFace: SANS, margin: 0,
    });
  });
  s.addNotes("这是一条情报的完整生命周期：从 9 类源采集进来，先分诊判断价值，再三级分类判定是否 AI 范畴，命中的做七维富化，入库后支撑顶层的问答和看板。工程上重点做了三件事：低价值旁路、熔断降级、断点续采——保证系统长时间无人值守运行不出事。");
  s.render();
})();

// ============================================================
// S7 · 数据资产（02）
// ============================================================
(function assets() {
  const s = pres.addSlide();
  chrome(s, { num: "02", title: "数据资产 · 持续自动采集的实证", en: "DATA ASSETS" });

  card(s, 0.5, 1.08, 9, 2.86, { radius: 0.06 });
  s.addImage({ path: A("fig_dist_3330x1216.png"), x: 0.6, y: 1.18, w: 8.8, h: 2.66 });

  const stats = [
    ["8,313", "全库情报总量"],
    ["574", "AI 范畴漏洞"],
    ["9 类", "异构数据源 · 5 大类"],
    ["3,304", "七维富化记录"],
  ];
  stats.forEach(([v, l], i) => {
    const x = 0.5 + i * 2.3;
    card(s, x, 4.12, 2.1, 1.08);
    s.addText(v, {
      x, y: 4.26, w: 2.1, h: 0.44, align: "center", valign: "middle",
      fontFace: SERIF, fontSize: 20, bold: true, color: PRIMARY, margin: 0,
    });
    s.addText(l, {
      x: x + 0.08, y: 4.74, w: 1.94, h: 0.34, align: "center", fontSize: 8.5,
      color: MUTED, fontFace: SANS, margin: 0,
    });
  });
  s.addNotes("目前全库 8313 条情报，其中 AI 范畴 574 条，来自 9 类异构数据源，另有 3304 条富化记录。这些全部是系统 7×24 小时自动采集的真实数据，不是静态导入的演示数据集。");
  s.render();
})();

// ============================================================
// S8 · 智能体状态机（03）
// ============================================================
(function statemachine() {
  const s = pres.addSlide();
  chrome(s, { num: "03", title: "智能体引擎 · LangGraph 状态机", en: "AGENT ORCHESTRATION" });

  card(s, 0.5, 1.1, 5.35, 4.1, { radius: 0.06 });
  s.addImage({ path: A("fig_state_2712x2052.png"), x: 0.6, y: 1.2, w: 5.15, h: 3.9 });

  const pts = [
    ["可恢复编排", PRIMARY, "supervisor 结果校验 + reflect 自愈回环（上限 3 次），节点失败不丢数据"],
    ["并行 · 规避竞争", NAVY, "fanout 节点内 asyncio.gather 并行执行，规避 LangGraph 入边竞争陷阱"],
    ["三重容错", GOLD, "熔断器开路 · 单批富化上限 20 条 · 部分失败容忍，七维独立记录"],
  ];
  pts.forEach(([t, c, d], i) => {
    const y = 1.1 + i * 1.41;
    card(s, 6.05, y, 3.45, 1.28);
    accentBar(s, 6.05, y, 1.28, c, 0.06);
    s.addText(t, {
      x: 6.29, y: y + 0.16, w: 3.05, h: 0.3, fontFace: SANS, fontSize: 12,
      bold: true, color: INK, margin: 0,
    });
    s.addText(d, {
      x: 6.29, y: y + 0.5, w: 3.08, h: 0.68, fontSize: 9, color: MUTED,
      fontFace: SANS, margin: 0, valign: "top",
    });
  });
  s.addNotes("智能体引擎的核心是这张状态机：监测节点触发后先做优先级分诊，高价值条目进入并行富化，汇聚后由 supervisor 校验，失败走 reflect 自愈回环，最多重试三次。三个设计点：可恢复、真并行、三重容错。特别说明右侧第二条——LangGraph 两条入边会竞争只执行其一，我们把并行放进 fanout 节点内部用 gather 实现，这是踩过坑之后的正确解法。");
  s.render();
})();

// ============================================================
// S9 · 三级 AI 分类（03）
// ============================================================
(function classify() {
  const s = pres.addSlide();
  chrome(s, { num: "03", title: "三级 AI 分类 · 噪声中精准筛出", en: "3-STAGE AI CLASSIFIER" });

  card(s, 0.5, 1.06, 9, 3.42, { radius: 0.06 });
  s.addImage({ path: A("fig_classify_2952x1662.png"), x: 0.6, y: 1.16, w: 8.8, h: 3.22 });

  const stats = [
    ["<1%", "真 AI 范畴占比（NVD 7 天 2,563 条实测）"],
    ["5.8 ms", "嵌入层单条判定耗时 · 10× 快于 LLM 复核"],
    ["38 个", "排除词控制误杀 · 灰区条目留痕可追溯"],
  ];
  stats.forEach(([v, l], i) => {
    const x = 0.5 + i * 3.065;
    card(s, x, 4.6, 2.87, 0.66);
    s.addText([
      { text: v + "  ", options: { fontFace: SERIF, fontSize: 15, bold: true, color: PRIMARY } },
      { text: l, options: { fontSize: 8.5, color: MUTED } },
    ], {
      x: x + 0.16, y: 4.6, w: 2.6, h: 0.66, valign: "middle",
      fontFace: SANS, margin: 0,
    });
  });
  s.addNotes("怎么从 99% 的噪声里捞出不足 1% 的真 AI 漏洞？我们的答案是三级流水线：第一级规则层，13 类 90 多个关键词加 38 个排除词，零成本；第二级嵌入层，BGE 语义相似度判定，单条 5.8 毫秒，比 LLM 快十倍；第三级 LLM 只复核灰区条目，且每源预算 30 条封顶。确定性条目就地判定，只有拿不准的才往下沉——成本和精度就是这么平衡出来的。");
  s.render();
})();

// ============================================================
// S10 · 七维富化（03）
// ============================================================
(function enrich() {
  const s = pres.addSlide();
  chrome(s, { num: "03", title: "七维富化 · 给情报装上证据链", en: "7-DIMENSION ENRICHMENT" });

  card(s, 0.5, 1.08, 9, 3.02, { radius: 0.06 });
  s.addImage({ path: A("fig_enrich_3046x1246.png"), x: 0.6, y: 1.18, w: 8.8, h: 2.82 });

  const pts = [
    ["置信度分级", "≥0.8 直采入库 · 0.5~0.8 带置信标注 · <0.5 转人工核验"],
    ["3,304 条实测", "七维富化记录 · 平均置信度 0.69 ~ 0.89"],
    ["容错设计", "七维独立记录 · 单维失败不阻塞 · 富化熔断保护"],
  ];
  pts.forEach(([t, d], i) => {
    const x = 0.5 + i * 3.065;
    card(s, x, 4.24, 2.87, 0.98);
    accentBar(s, x, 4.24, 0.98, i === 1 ? GOLD : PRIMARY, 0.055);
    s.addText(t, {
      x: x + 0.18, y: 4.35, w: 2.6, h: 0.28, fontFace: SANS, fontSize: 11,
      bold: true, color: INK, margin: 0,
    });
    s.addText(d, {
      x: x + 0.18, y: 4.66, w: 2.62, h: 0.5, fontSize: 8.5, color: MUTED,
      fontFace: SANS, margin: 0, valign: "top",
    });
  });
  s.addNotes("富化是给每条情报装上证据链：受影响资产、POC 验证、ATT&CK 技术映射、风险定级、修复建议、关联论文、CVSS 评分，七个维度。设计原则三条：置信度分级——高置信直接采、低置信转人工；七维独立记录——单维失败不阻塞；实测 3304 条，平均置信度 0.69 到 0.89。");
  s.render();
})();

// ============================================================
// S11 · 问答评测与消融（04）
// ============================================================
(function evaluation() {
  const s = pres.addSlide();
  chrome(s, { num: "04", title: "问答评测 · 三项硬指标达标", en: "EVALUATION & ABLATION" });

  card(s, 0.5, 1.06, 9, 2.6, { radius: 0.06 });
  s.addImage({ path: A("fig_qa_3046x1246.png"), x: 0.6, y: 1.16, w: 8.8, h: 2.4 });

  const stats = [
    ["87.88%", "问答准确率", "✓ 赛题 ≥85%（198 题）"],
    ["3,617 ms", "P95 响应时间", "✓ 赛题 ≤5s"],
    ["100%", "多跳推理通过率", "✓ 赛题 ≥80%"],
  ];
  stats.forEach(([v, l, c], i) => {
    const x = 0.5 + i * 2.2;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y: 3.82, w: 1.98, h: 1.4, rectRadius: 0.09, fill: { color: GFILL },
    });
    s.addText(v, {
      x, y: 3.94, w: 1.98, h: 0.4, align: "center", valign: "middle",
      fontFace: SERIF, fontSize: 17, bold: true, color: PRIMARY, margin: 0,
    });
    s.addText(l, {
      x: x + 0.06, y: 4.38, w: 1.86, h: 0.26, align: "center", fontFace: SANS,
      fontSize: 9.5, bold: true, color: INK, margin: 0,
    });
    s.addText(c, {
      x: x + 0.06, y: 4.68, w: 1.86, h: 0.42, align: "center", fontSize: 8,
      color: MUTED, fontFace: SANS, margin: 0, valign: "top",
    });
  });

  card(s, 7.1, 3.82, 2.4, 1.4);
  accentBar(s, 7.1, 3.82, 1.4, GOLD, 0.07);
  s.addText("消融实验验证", {
    x: 7.32, y: 3.94, w: 2.05, h: 0.26, fontFace: SANS, fontSize: 10.5, bold: true,
    color: GOLD, margin: 0,
  });
  s.addText("关闭降级后全 LLM 直答仅 79.8%，趋势分析骤降至 36.4% —— 「LLM 优先 + 时间预算降级模板」数值直接查库，精确可靠", {
    x: 7.32, y: 4.22, w: 2.08, h: 0.92, fontSize: 8, color: INK,
    fontFace: SANS, margin: 0, valign: "top",
  });
  s.addNotes("主评测 198 题，从库内实时生成、结构化断言判分。三项硬指标：准确率 87.88%、P95 响应 3.6 秒、多跳推理 100%，全部达标。重点讲右边这个消融实验：把降级机制关掉、全走 LLM 直答，准确率掉到 79.8%，其中趋势分析类骤降到 36.4%——因为大模型数数不可靠。我们的设计是 LLM 优先、时间预算内没出结果就降级到模板直接查库，数值精确。这个消融证明了该设计的必要性。");
  s.render();
})();

// ============================================================
// S12 · 性能压测（04）
// ============================================================
function benchmark() {
  const s = pres.addSlide();
  chrome(s, { num: "04", title: "并发压测 · 四档梯度零错误", en: "LOAD TESTING" });

  card(s, 0.5, 1.06, 9, 3.02, { radius: 0.06 });
  s.addImage({ path: A("fig_bench_3226x1306.png"), x: 0.6, y: 1.16, w: 8.8, h: 2.82 });

  const stats = [
    ["10", "并发", "核心接口 P95 全部 ≤2.8s"],
    ["50", "并发", "知识库统计 QPS 150"],
    ["100", "并发", "情报列表 P95 1.4s"],
    ["200", "并发极限", "0 错误 · 列表 P95 2.6s"],
  ];
  stats.forEach(([v, u, d], i) => {
    const x = 0.5 + i * 2.3;
    card(s, x, 4.22, 2.1, 1.04);
    s.addText([
      { text: v, options: { fontFace: SERIF, fontSize: 17, bold: true, color: PRIMARY } },
      { text: " " + u, options: { fontSize: 9, color: MUTED } },
    ], {
      x, y: 4.32, w: 2.1, h: 0.36, align: "center", valign: "middle",
      fontFace: SANS, margin: 0,
    });
    s.addText(d, {
      x: x + 0.08, y: 4.74, w: 1.94, h: 0.42, align: "center", fontSize: 8,
      color: MUTED, fontFace: SANS, margin: 0, valign: "top",
    });
  });
  s.addNotes("压测按 10、50、100、200 四档并发梯度打六个核心接口。结果：全档位零错误；50 并发下知识库统计 QPS 到 150；100 并发情报列表 P95 仍只有 1.4 秒；200 并发极限档也不报错。健康检查接口 2.7 秒是设计内行为——它每次会主动探测全部 9 个数据源的连通性。");
  s.render();
}

// ============================================================
// S13 · 系统演示 A（05 · 深色）
// ============================================================
function demoA() {
  const s = pres.addSlide();
  chrome(s, { num: "05", title: "系统演示 · 智能问答与双主题", en: "LIVE DEMO · INTERACTION", dark: true });

  const shots = [
    ["shot_landing_dark_1308x1239.png", "落地首页 · 黑夜主题"],
    ["shot_qa_dark_1308x1239.png", "智能问答 · 黑夜主题"],
    ["shot_qa_light_1308x1239.png", "智能问答 · 白天主题"],
  ];
  shots.forEach(([img, label], i) => {
    const x = 0.5 + i * 3.065;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y: 1.14, w: 2.87, h: 2.72, rectRadius: 0.08,
      fill: { color: "10362C" }, line: { color: "2E5A4C", width: 1 },
    });
    s.addImage({ path: A(img), x: x + 0.1, y: 1.24, w: 2.67, h: 2.52 });
    s.addText(label, {
      x, y: 3.94, w: 2.87, h: 0.3, align: "center", fontFace: SANS,
      fontSize: 10.5, bold: true, color: "FFFFFF", margin: 0,
    });
  });

  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 4.48, w: 9, h: 0.66, rectRadius: 0.08,
    fill: { color: "123A30" },
  });
  accentBar(s, 0.5, 4.48, 0.66, GOLD, 0.06);
  s.addText("双主题体系：黑夜 / 白天一键切换，色板按 WCAG AA 对比度实测校准 —— 无障碍与美学兼得；问答支持多轮对话，回答附可点击引用溯源", {
    x: 0.74, y: 4.48, w: 8.6, h: 0.66, fontSize: 9.5, color: PALE,
    fontFace: SANS, margin: 0, valign: "middle",
  });
  s.addNotes("进入系统演示。左边是落地首页，中间和右边是智能问答页的黑夜、白天两个主题——切换是一键的，所有色板按 WCAG AA 对比度实测校准，视障友好。问答支持多轮对话，每个回答后面都带引用，可以点回原文核查。");
  s.render();
}

// ============================================================
// S14 · 系统演示 B（05 · 深色）
// ============================================================
function demoB() {
  const s = pres.addSlide();
  chrome(s, { num: "05", title: "系统演示 · 态势洞察与知识图谱", en: "LIVE DEMO · INSIGHT", dark: true });

  const shots = [
    ["shot_situation_dark_1308x1239.png", "态势总览 · 黑夜主题"],
    ["shot_graph_dark_1308x1239.png", "知识图谱 · 黑夜主题"],
  ];
  shots.forEach(([img, label], i) => {
    const x = 0.5 + i * 3.12;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
      x, y: 1.14, w: 2.92, h: 2.77, rectRadius: 0.08,
      fill: { color: "10362C" }, line: { color: "2E5A4C", width: 1 },
    });
    s.addImage({ path: A(img), x: x + 0.1, y: 1.24, w: 2.72, h: 2.57 });
    s.addText(label, {
      x, y: 3.99, w: 2.92, h: 0.3, align: "center", fontFace: SANS,
      fontSize: 10.5, bold: true, color: "FFFFFF", margin: 0,
    });
  });

  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 6.74, y: 1.14, w: 2.76, h: 3.15, rectRadius: 0.08,
    fill: { color: "123A30" },
  });
  s.addText("态势总览", {
    x: 6.98, y: 1.32, w: 2.3, h: 0.3, fontFace: SANS, fontSize: 12, bold: true,
    color: "FFFFFF", margin: 0,
  });
  s.addText("时间分桶曲线（逐桶可对账）· AI 分类环图 · 严重度分布 · 情报新鲜度水位", {
    x: 6.98, y: 1.66, w: 2.42, h: 0.78, fontSize: 8.5, color: PALE,
    fontFace: SANS, margin: 0, valign: "top",
  });
  s.addText("知识图谱", {
    x: 6.98, y: 2.6, w: 2.3, h: 0.3, fontFace: SANS, fontSize: 12, bold: true,
    color: "FFFFFF", margin: 0,
  });
  s.addText("7 类节点力导向图 · 多跳路径查询 · 邻居聚合与标签防遮挡策略", {
    x: 6.98, y: 2.94, w: 2.42, h: 0.78, fontSize: 8.5, color: PALE,
    fontFace: SANS, margin: 0, valign: "top",
  });

  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 4.52, w: 9, h: 0.6, rectRadius: 0.08,
    fill: { color: "123A30" },
  });
  accentBar(s, 0.5, 4.52, 0.6, GOLD, 0.06);
  s.addText("更多功能欢迎现场体验：情报中心检索与富化面板 · ATLAS 攻击链 · 运维监控（队列趋势 / LLM 热切换 / 调度日志）", {
    x: 0.74, y: 4.52, w: 8.6, h: 0.6, fontSize: 9.5, color: PALE,
    fontFace: SANS, margin: 0, valign: "middle",
  });
  s.addNotes("态势总览页：时间分桶曲线逐桶可对账——看板曲线加起来等于列表总数，评委可现场验证；分类环图、严重度分布、新鲜度水位。知识图谱页：七类节点力导向图，支持多跳路径查询。情报中心、运维监控等页面欢迎现场体验。");
  s.render();
}

// ============================================================
// S15 · 创新点总结（06）
// ============================================================
function innovation() {
  const s = pres.addSlide();
  chrome(s, { num: "06", title: "创新点总结", en: "INNOVATIONS" });

  const items = [
    ["三级 AI 分类流水线", "规则 → 嵌入 → LLM 逐级下沉，嵌入层 10× 快于 LLM，成本-精度平衡"],
    ["可恢复智能体编排", "supervisor 校验 · reflect 自愈回环 · 熔断降级，节点失败不丢数据"],
    ["七维富化证据链", "置信度分级：≥0.8 直采 / <0.5 人工核验，过程可解释可追溯"],
    ["全链路可溯源 RAG", "回答即证据，引用锚点可点击回查原文，数值类直查数据库"],
    ["工程级可靠性", "断点续采 + 9 源差异化周期（30min~7d）+ 去重键防重入"],
    ["WCAG 双主题体系", "对比度实测驱动的黑夜/白天双主题，无障碍与美学兼得"],
  ];
  items.forEach(([t, d], i) => {
    const x = 0.5 + (i % 3) * 3.065;
    const y = 1.14 + Math.floor(i / 3) * 1.76;
    card(s, x, y, 2.87, 1.6);
    badge(s, x + 0.22, y + 0.18, 0.34, String(i + 1), i < 3 ? PRIMARY : GOLD, "FFFFFF", 10.5);
    s.addText(t, {
      x: x + 0.68, y: y + 0.18, w: 2.1, h: 0.34, fontFace: SANS, fontSize: 11,
      bold: true, color: INK, valign: "middle", margin: 0,
    });
    s.addText(d, {
      x: x + 0.24, y: y + 0.62, w: 2.42, h: 0.9, fontSize: 8.5, color: MUTED,
      fontFace: SANS, margin: 0, valign: "top",
    });
  });

  s.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x: 0.5, y: 4.72, w: 9, h: 0.54, rectRadius: 0.08, fill: { color: GFILL },
  });
  s.addText("三项硬指标全部达标：问答准确率 87.88%（≥85%）· P95 响应 3.6s（≤5s）· 多跳推理 100%（≥80%）", {
    x: 0.5, y: 4.72, w: 9, h: 0.54, align: "center", valign: "middle",
    fontSize: 11, bold: true, color: PRIMARY, fontFace: SANS, margin: 0,
  });
  s.addNotes("六个创新点：前三条是算法与架构层面——三级分类、可恢复编排、七维证据链；后三条是工程层面——可溯源 RAG、断点续采的可靠性设计、WCAG 双主题。最后再强调一遍：三项硬指标全部超标达成。");
  s.render();
}

// ============================================================
// S16 · 致谢（深色）
// ============================================================
function thanks() {
  const s = pres.addSlide();
  PAGE += 1;
  s.background = { path: A("bg_cover_2000x1125.png") };

  s.addText("谢谢聆听", {
    x: 0.5, y: 1.66, w: 9, h: 0.9, align: "center", valign: "middle",
    fontFace: SERIF, fontSize: 42, bold: true, color: "FFFFFF", charSpacing: 6, margin: 0,
  });
  s.addText("恳请各位评委批评指正", {
    x: 0.5, y: 2.66, w: 9, h: 0.4, align: "center", valign: "middle",
    fontSize: 13, color: PALE2, fontFace: SANS, charSpacing: 2, margin: 0,
  });

  s.addShape(pres.shapes.OVAL, { x: 4.72, y: 3.26, w: 0.07, h: 0.07, fill: { color: LINE } });
  s.addShape(pres.shapes.OVAL, { x: 4.955, y: 3.23, w: 0.1, h: 0.1, fill: { color: GOLD } });
  s.addShape(pres.shapes.OVAL, { x: 5.22, y: 3.26, w: 0.07, h: 0.07, fill: { color: LINE } });

  s.addText("SENTINEL · AI 安全知识情报系统", {
    x: 0.5, y: 3.52, w: 9, h: 0.34, align: "center", valign: "middle",
    fontSize: 11.5, bold: true, color: PALE, fontFace: SANS, charSpacing: 1.5, margin: 0,
  });
  s.addText("8,313 条情报 · 87.88% 问答准确率 · P95 3.6s · 多跳推理 100%", {
    x: 0.5, y: 3.94, w: 9, h: 0.3, align: "center", valign: "middle",
    fontSize: 10, color: PALE2, fontFace: SANS, margin: 0,
  });
  s.addText("第三届「中国电子杯」高校 ICT 产教融合创新大赛 · SENTINEL 团队 · 2026 年 10 月", {
    x: 0.5, y: 5.05, w: 9, h: 0.28, align: "center", valign: "middle",
    fontSize: 9, color: "7FA898", fontFace: SANS, margin: 0,
  });
  s.addNotes("汇报完毕，感谢各位评委聆听，恳请批评指正。");
  s.render();
}

// ============================================================
// 按页序执行 + 输出
// ============================================================
benchmark();
demoA();
demoB();
innovation();
thanks();

pres.writeFile({ fileName: OUT }).then(() => {
  const kb = (fs.statSync(OUT).size / 1024).toFixed(0);
  console.log(`\nPPT 已生成：${OUT}`);
  console.log(`大小：${kb} KB · 共 ${TOTAL} 页`);
});
