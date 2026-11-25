// 技术报告生成 · 主入口
// 用法：node 生成报告.mjs
// 输出：归档/技术报告/AI安全知识情报系统技术报告.docx
import fs from "node:fs";
import path from "node:path";
import { Document, Packer, HeadingLevel } from "docx";
import { pageFooter, OUT, SHOT } from "./lib.mjs";
import { cover, toc, chapter0, chapter1, chapter2 } from "./content1.mjs";
import { chapter3, chapter4, chapter5 } from "./content2.mjs";
import { chapter6, chapter7, chapter8 } from "./content3.mjs";
import { chapter9, chapter10, appendix } from "./content4.mjs";

// ---------- 浏览器截图归档（临时目录 → 项目目录，保证可复现） ----------
const TMP_SHOTS = "C:\\Users\\29582\\AppData\\Local\\Temp\\trae\\screenshots";
const SHOT_MAP = {
  "landing_9sources.png": "截图_落地首页_黑夜.png",
  "qa_dark.png": "截图_智能问答_黑夜.png",
  "qa_light.png": "截图_智能问答_白天.png",
};
for (const [src, dst] of Object.entries(SHOT_MAP)) {
  const from = path.join(TMP_SHOTS, src);
  const to = path.join(SHOT, dst);
  if (fs.existsSync(from)) {
    fs.copyFileSync(from, to);
    console.log(`截图归档：${src} → ${dst}`);
  } else if (fs.existsSync(to)) {
    console.log(`截图已就位（跳过复制）：${dst}`);
  } else {
    console.warn(`⚠ 缺失截图：${src}`);
  }
}

// ---------- 组装 ----------
const children = [
  ...cover(),
  ...toc(),
  ...chapter0(),
  ...chapter1(),
  ...chapter2(),
  ...chapter3(),
  ...chapter4(),
  ...chapter5(),
  ...chapter6(),
  ...chapter7(),
  ...chapter8(),
  ...chapter9(),
  ...chapter10(),
  ...appendix(),
];

const doc = new Document({
  creator: "SENTINEL Team",
  title: "AI 安全知识情报系统设计与实现",
  description: "第三届「中国电子杯」高校 ICT 产教融合创新大赛 · 技术报告",
  features: { updateFields: true },
  styles: {
    default: {
      document: {
        run: { font: { eastAsia: "微软雅黑", ascii: "Times New Roman" }, size: 21 },
      },
    },
    paragraphStyles: [
      {
        id: "Heading1",
        name: "Heading 1",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
        run: { size: 32, bold: true, color: "0B3D2E", font: { eastAsia: "微软雅黑", ascii: "Times New Roman" } },
        paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0 },
      },
      {
        id: "Heading2",
        name: "Heading 2",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
        run: { size: 26, bold: true, color: "0A5C40", font: { eastAsia: "微软雅黑", ascii: "Times New Roman" } },
        paragraph: { spacing: { before: 280, after: 160 }, outlineLevel: 1 },
      },
      {
        id: "Heading3",
        name: "Heading 3",
        basedOn: "Normal",
        next: "Normal",
        quickFormat: true,
        run: { size: 23, bold: true, color: "1C2B26", font: { eastAsia: "微软雅黑", ascii: "Times New Roman" } },
        paragraph: { spacing: { before: 220, after: 120 }, outlineLevel: 2 },
      },
    ],
  },
  sections: [
    {
      properties: {
        page: {
          margin: { top: 1300, bottom: 1300, left: 1300, right: 1300 },
        },
      },
      footers: { default: pageFooter() },
      children,
    },
  ],
});

const buf = await Packer.toBuffer(doc);
fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, buf);
console.log(`\n✓ 报告已生成：${OUT}`);
console.log(`  大小：${(buf.length / 1024).toFixed(0)} KB`);
