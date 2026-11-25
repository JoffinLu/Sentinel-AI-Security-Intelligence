// 报告内容 · 第一部分：封面 / 目录 / 第 0 章预览 / 第 1 章架构 / 第 2 章智能体
import {
  AlignmentType, HeadingLevel, Paragraph, TextRun, TableOfContents, PageBreak,
} from "docx";
import {
  h1, h2, h3, p, bullet, quote, table, figure, figurePair, code, SHOT,
} from "./lib.mjs";

const CN = "微软雅黑";

// ================= 封面 =================
export function cover() {
  const center = (text, { size, bold, color, before = 0, after = 0, en } = {}) =>
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before, after },
      children: [new TextRun({
        text, bold, size, color: color || "000000",
        font: { eastAsia: CN, ascii: en || "Times New Roman" },
      })],
    });
  return [
    center("", { size: 20 }),
    center("", { size: 20 }),
    center("SENTINEL", { size: 56, bold: true, color: "0B3D2E", en: "Georgia" }),
    center("AI 安全知识情报系统设计与实现", { size: 40, bold: true, color: "12333A", before: 120 }),
    center("—— 从多源监测、智能富化到可溯源问答的全链路实现", { size: 22, color: "5A6B64", before: 100 }),
    center("", { size: 20 }),
    center("第三届「中国电子杯」高校 ICT 产教融合创新大赛 · 赛题九", { size: 21, color: "0A5C40" }),
    center("参赛队：AI 情报先锋队", { size: 21, before: 60 }),
    center("2026 年 10 月 10 日", { size: 21, color: "5A6B64", before: 60 }),
    center("", { size: 20 }),
    center("", { size: 20 }),
    ...table(
      ["核心指标", "赛题要求", "实测结果", "结论"],
      [
        ["情报监测数据源", "≥ 7 类", "9 类（漏洞库/社区/学术/标准/政策法规）", { text: "达标", color: "0A5C40", bold: true }],
        ["富化维度", "7 维", "7 维全覆盖 · 3,304 条富化记录", { text: "达标", color: "0A5C40", bold: true }],
        ["累计情报入库", "—", "8,313 条（7,789 漏洞 + 273 论文 + 227 技术 + 24 政策）", "持续增长"],
        ["问答准确率", "≥ 85%", "87.88%（198 题结构化断言评测）", { text: "达标", color: "0A5C40", bold: true }],
        ["问答响应 P95", "≤ 5s", "3,617 ms", { text: "达标", color: "0A5C40", bold: true }],
        ["多跳推理准确率", "≥ 80%", "100%（20 题全部通过）", { text: "达标", color: "0A5C40", bold: true }],
      ],
      "核心指标达成情况（数据由评测脚本自动生成，全部可复现）",
      { widths: [1900, 1500, 4200, 1500] }
    ),
    new Paragraph({ children: [new PageBreak()] }),
  ];
}

// ================= 目录 =================
export function toc() {
  return [
    new Paragraph({
      heading: HeadingLevel.HEADING_1,
      spacing: { before: 360, after: 240 },
      children: [new TextRun({ text: "目　录", bold: true, size: 32, color: "0B3D2E", font: { eastAsia: CN } })],
    }),
    new TableOfContents("目录", {
      hyperlink: true,
      headingStyleRange: "1-3",
    }),
    new Paragraph({
      spacing: { before: 200 },
      children: [new TextRun({
        text: "（提示：在 Word 中打开后按 Ctrl+A → F9 可刷新目录页码）",
        size: 18, color: "6B7A74", font: { eastAsia: CN },
      })],
    }),
    new Paragraph({ children: [new PageBreak()] }),
  ];
}

// ================= 第 0 章 · 重要内容预览 =================
export function chapter0() {
  return [
    h1("0. 重要内容预览"),
    p("本系统是面向 AI 安全领域的**全链路知识情报系统**：从 9 类数据源自动采集原始情报，经三级 AI 分类流水线筛出 AI 安全范畴条目，由 LangGraph 智能体编排完成七维富化，最终通过可溯源 RAG 问答向用户输出带引用的回答。系统 7×24 自动运行，截至报告撰写时已累计入库 **8,313 条**情报，其中 **574 条**经三级分类判定属于 AI 安全范畴并完成富化。"),

    h2("0.1 四个核心亮点"),

    h3("亮点一 · 9 源实时监测，差异化周期调度"),
    bullet("**结论**：NVD / CISA KEV / OSV / GHSA / POC 仓库 / RSS 安全资讯 / arXiv / MITRE ATLAS / 政策法规 9 类数据源全部自动轮询，RSS 30 分钟、CISA KEV 1 小时、NVD/GHSA 2 小时、OSV/POC/arXiv/政策 12 小时、MITRE ATLAS 7 天，按源更新速率与情报价值差异化周期（30 分钟~7 天）。"),
    bullet("**证据**：collection_logs 表 157 次真实运行记录；服务重启后 `catch_up_missed()` 自动补采错过的周期（解决 APScheduler 重启即丢任务的根因问题）。"),

    h3("亮点二 · LangGraph 状态机编排，可恢复可反思"),
    bullet("**结论**：monitor → triage 条件分叉 →（enrich ‖ skip）→ fanout 并行汇聚 → supervisor → reflect 回环（上限 3 次），六个节点全部携带状态与推理依据，任意节点失败可定位、可重试。"),
    bullet("**证据**：图执行历史实时落库，前端「智能体编排拓扑」页面可视化每次运行的节点状态与耗时。"),

    h3("亮点三 · 三级 AI 分类 + 七维富化的证据链"),
    bullet("**结论**：规则 → 嵌入（BGE-small-zh，实测 5.8ms/条）→ LLM 批量复核的漏斗式分类，将 LLM 调用成本压缩约两个数量级；富化产出资产 / POC / ATT&CK·ATLAS 映射 / 政策 / 风险定级 / 修复建议 / 关联论文七维证据，置信度分层展示。"),
    bullet("**证据**：7,789 条原始漏洞中 574 条被精准筛出（若不过滤，NVD 七天窗口内真正属于 AI 安全的不足 1%）；富化记录 3,304 条，平均置信度 0.69–0.89。"),

    h3("亮点四 · 可溯源问答，指标全面达标"),
    bullet("**结论**：每条论断绑定 CVE 编号 + 来源 URL + 采集时间，前端可点击溯源；时间预算内 LLM 生成、预算不足自动降级为结构化模板，P95 稳定可控。"),
    bullet("**证据**：198 题评测准确率 **87.88%**、P95 **3,617ms**、多跳推理 **100%**，三项赛题指标全部达标；全 LLM 消融实验（79.8%）反向验证了降级设计的必要性。"),

    h2("0.2 系统总览"),
    ...figure("截图_态势总览_白天.png", "系统态势总览页（白天主题）：情报总量、AI 范畴筛出量、严重度分布与入库趋势", { dir: SHOT, widthEmu: 560 }),

    h2("0.3 关键数字"),
    ...table(
      ["维度", "数字", "说明"],
      [
        ["情报总量", "8,313 条", "9 源累计入库（快照时点，调度器持续增长）"],
        ["AI 安全范畴", "574 条", "三级分类流水线筛出，含 4 类风险框架标注"],
        ["富化记录", "3,304 条", "七维证据，平均置信度 0.69–0.89"],
        ["知识节点与关联边", "7 类节点", "漏洞/资产/技术/论文/政策等，支撑多跳推理"],
        ["REST 接口", "47 个", "FastAPI 自动生成 OpenAPI 文档"],
        ["前端页面", "7 页", "黑夜/白天双主题，WCAG 对比度实测驱动"],
        ["自动化测试", "107 项", "pytest 全量通过，覆盖采集/分类/富化/问答"],
      ],
      "系统关键数字一览",
      { widths: [2400, 1800, 4900] }
    ),
    new Paragraph({ children: [new PageBreak()] }),
  ];
}

// ================= 第 1 章 · 总体架构 =================
export function chapter1() {
  return [
    h1("1. 系统总体架构设计"),

    h2("1.1 设计目标与约束"),
    p("AI 组件（推理框架、智能体框架、模型仓库、向量库等）的漏洞情报**天然分散**：CVE 编号在 NVD，可利用性证据在 POC 仓库，攻击手法在 MITRE ATLAS，学术分析在 arXiv，合规要求在监管文件里。从业者要在 5+ 个渠道间人工交叉比对，信息过载严重；而现有商用情报平台对「AI 安全」这一新兴领域的覆盖深度不足。"),
    p("赛题对本系统提出三项硬性约束，构成本架构设计的出发点："),
    bullet("**时效性**：情报源须自动轮询监测，不能依赖人工触发；"),
    bullet("**准确性**：问答准确率 ≥85%、响应 P95 ≤5s、多跳推理 ≥80%；"),
    bullet("**可溯源性**：每条论断须能回溯到原始情报（来源 URL + 采集时间），不允许模型自由发挥。"),

    h2("1.2 分层架构"),
    p("系统采用四层架构 + 监控横切面，如图 1。自下而上：**存储层**提供关系/向量/图三种数据底座；**能力层**实现采集、分类、富化、检索四大引擎；**编排层**以 LangGraph 状态机串联能力层并管理失败恢复；**展示层**面向四类角色提供界面。"),
    ...figure("图1_系统分层架构.png", "系统分层架构：四层 + 监控横切面"),
    ...table(
      ["层次", "技术选型", "职责"],
      [
        [{ text: "展示层", bold: true }, { text: "React 18 + TypeScript + Ant Design 5 + ECharts", align: "left" }, { text: "落地页 / 态势总览 / 情报中心 / 知识图谱 / 智能问答 / 监控看板 / 运维监控（7 页，黑夜/白天双主题）", align: "left" }],
        [{ text: "编排层", bold: true }, { text: "LangGraph 状态机 + APScheduler", align: "left" }, { text: "monitor→triage→enrich‖skip→fanout→supervisor→reflect 反思回环；9 源差异化周期调度", align: "left" }],
        [{ text: "能力层", bold: true }, { text: "FastAPI（47 接口）+ 规则引擎 + BGE-small-zh + DeepSeek", align: "left" }, { text: "三级 AI 分类 / 七维富化 / 混合检索（pgvector + 关键词）/ 图谱多跳", align: "left" }],
        [{ text: "存储层", bold: true }, { text: "PostgreSQL 16 + pgvector / Neo4j 5 / Redis 7", align: "left" }, { text: "业务数据与向量索引（同库）/ 知识图谱 / 缓存与限流", align: "left" }],
        [{ text: "监控横切面", bold: true }, { text: "Prometheus + Grafana + Alertmanager", align: "left" }, { text: "指标采集 / 可视化看板 / 三类告警规则", align: "left" }],
      ],
      "分层架构与技术选型",
      { widths: [1500, 3300, 4300] }
    ),

    h2("1.3 模块划分与职责边界"),
    p("后端按「一个领域一个包」划分为五模块，边界清晰、可独立测试："),
    ...table(
      ["模块", "核心职责", "关键产出"],
      [
        ["数据采集", "9 源采集器 + 调度器 + 断点续采", "增量原始情报（去重入库）"],
        ["智能体编排", "LangGraph 状态机 + 熔断/降级", "节点级执行历史"],
        ["知识库", "SQLAlchemy 模型 + 仓储查询", "统一数据访问层"],
        ["问答模块", "意图识别 + 混合检索 + 生成 + 引用校验", "带溯源的回答"],
        ["后端服务", "FastAPI 路由 + 配置 + 日志 + 指标", "47 个 REST 接口"],
      ],
      "五大后端模块职责",
      { widths: [1600, 3600, 3900] }
    ),
    p("前后端通过 OpenAPI 契约协作：前端由 openapi.json 生成类型化客户端，接口变更在编译期即暴露，杜绝字段名不一致类低级错误。"),

    h2("1.4 数据流总览"),
    p("一条情报的完整生命周期如图 2：从 RSS/NVD 等源进入 → 去重入库 → 三级分类判定 AI 相关性 →（值得富化的）七维富化 → 向量化 → 图谱关系抽取 → 可被检索与问答。每一步都留下日志与置信度，构成可审计的证据链。"),
    ...figure("图2_全链路数据流.png", "全链路数据流：从原始情报到可溯源回答"),
    quote("设计原则：**数据只进一次，处处留痕**。同一条情报的采集延迟、分类方法、富化置信度、引用次数全部可查，这是「可溯源」要求的工程化落地。"),
    new Paragraph({ children: [new PageBreak()] }),
  ];
}

// ================= 第 2 章 · 智能体方案 =================
export function chapter2() {
  return [
    h1("2. 智能体方案设计"),

    h2("2.1 为什么用智能体编排，而不是裸函数链"),
    p("情报处理是典型的**有状态、可失败、需决策**流程：同一条情报可能「值得深度富化」也可能「低价值直通」；富化可能因外部 API 抖动失败，需要判断是重试还是跳过；全部完成后还要汇总评估是否达到发布质量。裸函数链把控制流硬编码在调用顺序里，失败恢复与状态观测都要手工补丁。"),
    ...table(
      ["对比维度", "裸函数链", "LangGraph 状态机（本系统）"],
      [
        ["可控性", "分支逻辑散落各函数", { text: "条件边集中声明，triage 分叉 / reflect 回环一图可见", align: "left" }],
        ["可恢复性", "失败即中断，靠外层 try", { text: "supervisor 统一评估，可重试节点精确到个", align: "left" }],
        ["可观测性", "打印日志各自为政", { text: "NodeResult 结构化落库，前端实时渲染节点状态", align: "left" }],
        ["可测试性", "只能端到端测", { text: "节点可单测，条件边可穷举", align: "left" }],
      ],
      "编排方案对比",
      { widths: [1700, 3000, 4400] }
    ),

    h2("2.2 状态机设计"),
    ...figure("图3_智能体状态机.png", "LangGraph 状态机：节点、条件边与反思回环"),
    p("状态机包含 6 个业务节点与 3 条条件边，覆盖赛题明确要求的「条件边」与「反思循环」两个智能体特性："),
    ...table(
      ["节点", "职责", "失败策略"],
      [
        ["monitor", "触发/拉取指定源增量情报", "记录失败，可重试"],
        ["triage", "AI 相关性初判 + 富化价值评估", "规则兜底，不可失败"],
        ["enrich", "七维富化（LLM + 规则混合）", "熔断器保护，部分失败容忍"],
        ["qa", "问答链路（检索 + 生成）", "时间预算降级"],
        ["fanout", "并行分支汇聚（asyncio.gather）", "任一完成即汇聚"],
        ["supervisor", "质量评估：重试 or 完成 or 人工", "最高决策点"],
        ["reflect", "失败反思与重试（上限 3 次）", "回环至 supervisor"],
      ],
      "状态机节点清单",
      { widths: [1500, 4000, 3600] }
    ),
    h3("四个实现层面的设计难点"),
    p("以下难点均在开发实测中踩过坑，源码注释保留了完整论证："),
    bullet("**① qa 的孤儿问题**：真正并行不靠图结构，而是把 enrich 与 qa 放进同一个 fanout 节点内用 asyncio.gather 并行。LangGraph 的两条入边是「竞争」而非「汇合」——若用 enrich→join、qa→join 建模，enrich 先完成就立即触发 join，**qa 分支被直接跳过**（实测表现为 qa 节点从不执行）。"),
    bullet("**② supervisor 的可恢复判定**：不是简单重试，而是按节点类型区分——富化失败重试有价值（外部 API 抖动），分类失败重试无意义（确定性逻辑），据此 should_retry 给出差异化决策。"),
    bullet("**③ 图外组件边界**：APScheduler 调度器在图之外触发 workflow，通过 run() 入参（workflow_type/cve_id/question）传递上下文，图内不感知调度细节，保证状态机可脱离调度器独立测试。"),
    bullet("**④ 状态重建陷阱**：LangGraph 会把 dataclass State 展开成 dict 返回，必须用 from_state_fields 重建，否则拿到执行前的旧 state、丢掉全部节点结果（表现为节点列表始终为空）。"),

    h2("2.3 三级 AI 分类流水线"),
    p("分类是本系统的**核心技术难点**。实测发现：NVD 七天窗口的数千条 CVE 中，真正属于 AI 安全范畴的**不足 1%**——若不过滤，系统将退化为普通漏洞库，完全偏离「AI 安全知识情报」的赛题定位。三级漏斗以最小成本解决这一精度问题："),
    ...figure("图4_三级AI分类.png", "三级 AI 分类流水线：规则 → 嵌入 → LLM 复核"),
    ...table(
      ["层级", "手段", "成本", "处理范围"],
      [
        ["第一级 规则层", "13 组高置信关键词 + 组件名 + 强排除词", "零成本", "全部入库条目，命中即出结论"],
        ["第二级 嵌入层", "BGE-small-zh 语义相似度比对基准句库", { text: "5.8ms/条（本地 CPU）", align: "left" }, "规则层无法定论的灰区样本"],
        ["第三级 LLM 层", "批量复核（默认 30 条/源/轮）", "高（仅灰区调用）", "得分 0.3–0.7 的深灰区"],
      ],
      "三级分类漏斗",
      { widths: [1600, 3400, 2200, 2300] }
    ),
    p("漏斗的量化效果：全库 7,789 条漏洞中，**约 97% 在前两级零成本解决**，仅深灰区样本进入 LLM 批量复核。相比「全量 LLM 分类」方案，推理成本压缩约两个数量级，且规则层的强排除词（wordpress / drupal 等传统 Web 组件）保证非 AI 条目不进入下游富化。"),

    h2("2.4 容错设计"),
    bullet("**熔断器**：每个 Agent 持有独立熔断状态（连续失败 N 次后开路），LLM 网关 429 限流时富化自动降频，冷却后半开试探恢复；"),
    bullet("**降级模式**：问答的「快速失败 + 诚实降级」——预算 5s、LLM 硬超时 3.5s，超时立即切换结构化模板，保证 P95 永不击穿（详见 3.3 节）；"),
    bullet("**任务引用防 GC**：asyncio.create_task 的返回值必须持强引用，否则任务可能被垃圾回收——实测症状是「快的任务跑完了、慢的静默消失」；"),
    bullet("**断点续采**：APScheduler 的 misfire_grace_time 解决不了重启场景（MemoryJobStore 重启即丢任务），catch_up_missed() 在服务启动时按各源 cursor 补采错过的周期。"),
    new Paragraph({ children: [new PageBreak()] }),
  ];
}
