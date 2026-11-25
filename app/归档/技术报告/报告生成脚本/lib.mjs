// 技术报告生成 · 工具函数库
// 字体：中文微软雅黑 / 英文 Times New Roman；A4；页边距 0.9"
import fs from "node:fs";
import path from "node:path";
import {
  AlignmentType,
  Footer,
  HeadingLevel,
  ImageRun,
  PageBreak,
  PageNumber,
  Paragraph,
  Table,
  TableCell,
  TableRow,
  TextRun,
  WidthType,
  BorderStyle,
  ShadingType,
  VerticalAlign,
} from "docx";

export const ROOT = path.resolve(import.meta.dirname, "..", "..", "..");
export const IMG = path.join(ROOT, "归档", "技术报告", "插图");
export const SHOT = path.join(ROOT, "归档", "技术报告", "截图");
export const DATA = path.join(ROOT, "归档", "技术报告", "数据");
export const OUT = path.join(ROOT, "归档", "技术报告", "AI安全知识情报系统技术报告.docx");

const CN = "微软雅黑";
const EN = "Times New Roman";

// PNG IHDR 尺寸解析（避免额外依赖）
function pngSize(buf) {
  return { w: buf.readUInt32BE(16), h: buf.readUInt32BE(20) };
}

let figNo = 0;
let tabNo = 0;
export function resetCounters() { figNo = 0; tabNo = 0; }

// ---------- 文本 ----------

export function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 360, after: 200 },
    children: [new TextRun({ text, bold: true, size: 32, color: "0B3D2E", font: { eastAsia: CN, ascii: EN } })],
  });
}

export function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 280, after: 160 },
    children: [new TextRun({ text, bold: true, size: 26, color: "0A5C40", font: { eastAsia: CN, ascii: EN } })],
  });
}

export function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    spacing: { before: 220, after: 120 },
    children: [new TextRun({ text, bold: true, size: 23, color: "1C2B26", font: { eastAsia: CN, ascii: EN } })],
  });
}

// 正文：支持 **加粗** 内联标记
export function p(text, opts = {}) {
  const runs = [];
  for (const seg of String(text).split(/(\*\*[^*]+\*\*)/g)) {
    if (!seg) continue;
    if (seg.startsWith("**") && seg.endsWith("**")) {
      runs.push(new TextRun({ text: seg.slice(2, -2), bold: true, size: 21, color: "12333A", font: { eastAsia: CN, ascii: EN } }));
    } else {
      runs.push(new TextRun({ text: seg, size: 21, color: "1C2B26", font: { eastAsia: CN, ascii: EN } }));
    }
  }
  return new Paragraph({
    spacing: { after: 120, line: 312, ...(opts.spacing || {}) },
    alignment: opts.align || AlignmentType.JUSTIFIED,
    indent: opts.noIndent ? undefined : { firstLine: 420 },
    children: runs,
  });
}

export function bullet(text, level = 0) {
  const runs = [];
  for (const seg of String(text).split(/(\*\*[^*]+\*\*)/g)) {
    if (!seg) continue;
    if (seg.startsWith("**") && seg.endsWith("**")) {
      runs.push(new TextRun({ text: seg.slice(2, -2), bold: true, size: 21, color: "12333A", font: { eastAsia: CN, ascii: EN } }));
    } else {
      runs.push(new TextRun({ text: seg, size: 21, color: "1C2B26", font: { eastAsia: CN, ascii: EN } }));
    }
  }
  return new Paragraph({
    spacing: { after: 80, line: 300 },
    indent: { left: 420 + level * 360, hanging: 260 },
    children: runs,
  });
}

// 引用框（浅绿底）
export function quote(text) {
  return new Paragraph({
    spacing: { before: 100, after: 140 },
    indent: { left: 360, right: 360 },
    shading: { type: ShadingType.CLEAR, fill: "EAF4EE" },
    border: {
      left: { style: BorderStyle.SINGLE, size: 18, color: "0A9D6E", space: 8 },
    },
    children: [new TextRun({ text, size: 20, color: "35544A", font: { eastAsia: CN, ascii: EN } })],
  });
}

// ---------- 表格 ----------

const TH_FILL = "0B3D2E";
const TD_ALT = "F2F8F4";

export function table(headers, rows, caption, opts = {}) {
  tabNo += 1;
  const widths = opts.widths || headers.map(() => Math.floor(9100 / headers.length));
  const mk = (text, { head = false, fill = null, bold = false } = {}) =>
    new TableCell({
      verticalAlign: VerticalAlign.CENTER,
      shading: fill ? { type: ShadingType.CLEAR, fill } : undefined,
      margins: { top: 60, bottom: 60, left: 110, right: 110 },
      children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 0 },
        children: [new TextRun({
          text: String(text), bold: head || bold, size: head ? 20 : 19.5,
          color: head ? "FFFFFF" : "1C2B26",
          font: { eastAsia: CN, ascii: EN },
        })],
      })],
    });

  const trs = [new TableRow({
    tableHeader: true,
    children: headers.map((t, i) => mk(t, { head: true, fill: TH_FILL })),
  })];
  rows.forEach((r, ri) => {
    trs.push(new TableRow({
      children: r.map((c, i) => {
        const cell = typeof c === "object" && c !== null ? c : { text: c };
        return new TableCell({
          verticalAlign: VerticalAlign.CENTER,
          shading: ri % 2 ? { type: ShadingType.CLEAR, fill: TD_ALT } : undefined,
          margins: { top: 50, bottom: 50, left: 110, right: 110 },
          children: [new Paragraph({
            alignment: cell.align === "left" ? AlignmentType.LEFT : AlignmentType.CENTER,
            spacing: { after: 0 },
            children: [new TextRun({
              text: String(cell.text), bold: !!cell.bold, size: 19.5,
              color: cell.color || "1C2B26",
              font: { eastAsia: CN, ascii: EN },
            })],
          })],
        });
      }),
    }));
  });

  const out = [new Table({
    width: { size: 9100, type: WidthType.DXA },
    columnWidths: widths,
    borders: {
      top: { style: BorderStyle.SINGLE, size: 6, color: "9DC3B4" },
      bottom: { style: BorderStyle.SINGLE, size: 6, color: "9DC3B4" },
      left: { style: BorderStyle.SINGLE, size: 6, color: "C8DED4" },
      right: { style: BorderStyle.SINGLE, size: 6, color: "C8DED4" },
      insideHorizontal: { style: BorderStyle.SINGLE, size: 4, color: "D8E8E0" },
      insideVertical: { style: BorderStyle.SINGLE, size: 4, color: "D8E8E0" },
    },
    rows: trs,
  })];
  out.push(captionParagraph(`表 ${tabNo}　${caption}`));
  return out;
}

function captionParagraph(text) {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 80, after: 200 },
    children: [new TextRun({ text, size: 18, color: "6B7A74", font: { eastAsia: CN, ascii: EN } })],
  });
}

// ---------- 图片 ----------

export function figure(filename, caption, opts = {}) {
  figNo += 1;
  const dir = opts.dir || IMG;
  const file = path.join(dir, filename);
  const buf = fs.readFileSync(file);
  const { w, h } = pngSize(buf);
  const width = opts.widthEmu || 590;  // pt
  const height = Math.round(width * (h / w));
  return [
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 140, after: 60 },
      children: [new ImageRun({ data: buf, type: "png", transformation: { width, height } })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 200 },
      children: [new TextRun({ text: `图 ${figNo}　${caption}`, size: 18, color: "6B7A74", font: { eastAsia: CN, ascii: EN } })],
    }),
  ];
}

// 双图并排（截图对照）
export function figurePair(fileL, capL, fileR, capR, opts = {}) {
  figNo += 1;
  const mk = (filename, cap, n) => {
    const buf = fs.readFileSync(path.join(opts.dir || SHOT, filename));
    const { w, h } = pngSize(buf);
    const width = 285;
    const height = Math.round(width * (h / w));
    return [
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 140, after: 40 },
        children: [new ImageRun({ data: buf, type: "png", transformation: { width, height } })],
      }),
      new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { after: 60 },
        children: [new TextRun({ text: `（${n}）${cap}`, size: 17, color: "6B7A74", font: { eastAsia: CN, ascii: EN } })],
      }),
    ];
  };
  return [
    new Table({
      width: { size: 9100, type: WidthType.DXA },
      columnWidths: [4550, 4550],
      borders: {
        top: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
        bottom: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
        left: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
        right: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
        insideHorizontal: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
        insideVertical: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" },
      },
      rows: [new TableRow({
        children: [
          new TableCell({ children: mk(fileL, capL, "左"), margins: { top: 0, bottom: 0, left: 40, right: 40 } }),
          new TableCell({ children: mk(fileR, capR, "右"), margins: { top: 0, bottom: 0, left: 40, right: 40 } }),
        ],
      })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 200 },
      children: [new TextRun({ text: `图 ${figNo}　${opts.title || ""}`, size: 18, color: "6B7A74", font: { eastAsia: CN, ascii: EN } })],
    }),
  ];
}

// ---------- 代码块 ----------

export function code(lines, caption) {
  const paras = lines.map((line, i) =>
    new Paragraph({
      spacing: { after: i === lines.length - 1 ? 80 : 0, line: 240 },
      shading: { type: ShadingType.CLEAR, fill: "F5F7F6" },
      indent: { left: 200, right: 200 },
      children: [new TextRun({ text: line.replace(/\t/g, "    "), size: 17, color: "253B33", font: { eastAsia: "Consolas", ascii: "Consolas" } })],
    })
  );
  const out = [...paras];
  if (caption) out.push(captionParagraph(caption));
  return out;
}

// ---------- 页脚 / 分页 ----------

export function pageFooter() {
  return new Footer({
    children: [new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [
        new TextRun({ text: "— ", size: 18, color: "6B7A74" }),
        new TextRun({ children: [PageNumber.CURRENT], size: 18, color: "6B7A74" }),
        new TextRun({ text: " —", size: 18, color: "6B7A74" }),
      ],
    })],
  });
}

export function pageBreak() {
  return new Paragraph({ children: [new PageBreak()] });
}

// JSON 读取助手
export function readJson(name) {
  return JSON.parse(fs.readFileSync(path.join(DATA, name), "utf-8"));
}
