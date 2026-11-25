// 报告内容 · 第三部分：第 6 章实现细节 / 第 7 章核心代码 / 第 8 章效果展示
import { Paragraph, PageBreak } from "docx";
import {
  h1, h2, h3, p, bullet, quote, table, figure, figurePair, code, SHOT,
} from "./lib.mjs";

// ================= 第 6 章 · 系统实现细节 =================
export function chapter6() {
  return [
    h1("6. 系统实现细节"),
    p("本章记录七个「实现过才知道难」的细节。每条按 **问题 → 方案 → 效果** 组织，全部来自真实开发过程中的踩坑与修复："),

    h3("细节一 · 时区分桶守恒"),
    bullet("**问题**：入库时间戳统一 UTC，但看板要按本地日聚合。简单 date_trunc 会出现「UTC 桶边界」与「本地展示边界」错位，且各桶求和 ≠ 原始总数，评委对不上数就是致命伤。"),
    bullet("**方案**：分桶一律 `date_trunc('day', ingest_at AT TIME ZONE 'Asia/Shanghai')`，并在测试中加**守恒断言** sum(桶) == sum(原始记录)。"),
    bullet("**效果**：看板曲线与列表总数严格一致，图 8 入库时序可直接对账。"),
    ...figure("图8_入库时序.png", "情报入库时序：每日入库总量与 AI 范畴筛出量（按本地日聚合，逐桶可对账）"),

    h3("细节二 · 增量防重（去重键设计）"),
    bullet("**问题**：同一情报会以不同面目重复到达——NVD 修订、RSS 重复推送、GHSA 与 CVE 双编号。"),
    bullet("**方案**：三层去重：结构化源用业务键（cve_id / arxiv_id）；非结构化源用 content_hash（正文 SHA-256）；跨源同主体用引用对齐（poc_refs 挂到已有 CVE 而非新建记录）。"),
    bullet("**效果**：8,313 条入库记录无一重复主体；重复公告只更新 modified_at，不产生新行。"),

    h3("细节三 · 图谱大邻居的渲染策略"),
    bullet("**问题**：高热度资产（如 transformers）在图谱中有一百多个邻居，一次性渲染节点重叠成一团，交互完全卡死。"),
    bullet("**方案**：邻居分批滚动加载 + 标签防重叠（冲突检测后自动偏移/隐藏）+ 节点聚合入口（「+N 更多」收起次级邻居）。"),
    bullet("**效果**：百级邻居下首屏渲染 <1s，拖拽/缩放保持流畅。"),

    h3("细节四 · 双主题的 WCAG 对比度工程"),
    bullet("**问题**：白天主题不是「把黑夜变量反色」——实测暗色主题的轴标签 #6b7870 放到白底只有 2.6:1 对比度，图表糊成一片；而 ECharts **不认 CSS 变量**（option 里写 var(--accent) 直接失效回退黑色）。"),
    bullet("**方案**：全站 CSS 变量自动翻转，唯独图表色板走 JS（chartPalette 模块，切主题 setOption 重着色，不重建实例避免动画丢失）；白天主题文字类一律用实测 ≥4.5:1（AAA 级 #46584f 为 7.0:1）的深墨绿。"),
    bullet("**效果**：黑夜/白天双主题 7 个页面全部通过对比度自检，无障碍与美学兼得（详见 7.4 代码节选）。"),

    h3("细节五 · 情报导出 CSV 的端到端守恒"),
    bullet("**问题**：导出「当前筛选结果」看似简单，实际有三个坑：分页循环拉全时筛选条件必须快照冻结；CSV 转义（字段内换行/引号/逗号）要严格 RFC 4180；超大数据量浏览器会拦截多文件下载。"),
    bullet("**方案**：后端 list_vulns 加 include_extra 参数（一次 IN 查询 JOIN 富化表，无 N+1），前端循环分页 500/页 → UTF-8 BOM + RFC 4180 转义 → 超 1 万行自动分片（间隔 400ms 防拦截）。"),
    bullet("**效果**：实测 584/584 条守恒；转义压力测试（235 个换行 / 90 个引号 / 448 个逗号）全部正确；富化列 489/489 非空。"),

    h3("细节六 · 前端防闪烁与动画纪律"),
    bullet("**问题**：主题切换与图表刷新时的白屏闪烁、深色页面的「亮闪」，以及后台标签页时极光背景持续烧 GPU。"),
    bullet("**方案**：主题类先行内联注入（首帧即正确）；极光背景动画在 document.hidden 时真停 rAF（不是降帧）；图表更新走 setOption 增量而非重建实例。"),
    bullet("**效果**：切换零闪烁；后台标签页 GPU 占用归零。"),

    h3("细节七 · asyncio 任务的静默消失"),
    bullet("**问题**：启动补采任务偶发「快的跑完了、慢的静默消失」——0.2 秒的 arxiv 成功，115 秒的 osv 无任何记录。"),
    bullet("**方案**：asyncio.create_task 的返回值必须持强引用（事件循环只持弱引用，任务可能在执行途中被 GC）。补采任务统一放入 self._background 集合，完成后回调移除。"),
    bullet("**效果**：长耗时采集任务不再静默丢失，该问题自修复后未复现。"),
    new Paragraph({ children: [new PageBreak()] }),
  ];
}

// ================= 第 7 章 · 核心代码说明 =================
export function chapter7() {
  return [
    h1("7. 核心代码说明"),
    p("四段核心代码，每段先说功能，再看节选（完整实现见源码仓库），最后逐段注释设计意图。"),

    h2("7.1 LangGraph 状态机构建（orchestrator.py）"),
    ...code([
      "def build(self):",
      "    \"\"\"条件边实现 triage 跳过逻辑与 reflect 重试循环\"\"\"",
      "    from langgraph.graph import END, START, StateGraph",
      "",
      "    def _after_triage(state):            # 条件边①：是否富化",
      "        triage = state.get(\"triage\") or {}",
      "        if triage.get(\"worth_enriching\") is False:",
      "            return \"skip_enrich\"",
      "        return \"enrich\"",
      "",
      "    def _after_supervisor(state):         # 条件边②：是否反思重试",
      "        for result in state.node_results:",
      "            if result.status == NodeStatus.FAILED \\",
      "               and self.supervisor.should_retry(state, result):",
      "                return \"reflect\"",
      "        return \"done\"",
      "",
      "    builder = StateGraph(WorkflowState)",
      "    for name in (\"monitor\", \"triage\", \"enrich\", \"qa\",",
      "                 \"fanout\", \"supervisor\", \"reflect\", \"skip_enrich\"):",
      "        builder.add_node(name, getattr(self, f\"_node_{name}\"))",
      "",
      "    builder.add_edge(START, \"monitor\")",
      "    builder.add_edge(\"monitor\", \"triage\")",
      "    builder.add_conditional_edges(\"triage\", _after_triage,",
      "        {\"enrich\": \"enrich\", \"skip_enrich\": \"skip_enrich\"})",
      "    # 富化与问答在 fanout 节点内 asyncio.gather 真正并行——",
      "    # 两条入边做汇合是「竞争」语义：enrich 先到 qa 即被跳过",
      "    builder.add_edge(\"enrich\", \"fanout\")",
      "    builder.add_edge(\"skip_enrich\", \"fanout\")",
      "    builder.add_edge(\"fanout\", \"supervisor\")",
      "    builder.add_conditional_edges(\"supervisor\", _after_supervisor,",
      "        {\"reflect\": \"reflect\", \"done\": END})",
      "    self._graph = builder.compile()",
    ], "代码 2　状态机构建：六节点两条件边一并行汇聚"),
    bullet("**注释**：条件边集中声明所有控制流（对应赛题「条件边」要求）；reflect 上限 3 次的回环在 _after_reflect 中闭合（对应「反思循环」）；注释里的「入边竞争」是实测踩坑后留下的论证。"),

    h2("7.2 三级分类的嵌入判定（ai_classifier.py）"),
    ...code([
      "EMBEDDING_REJECT_THRESHOLD = 0.25   # 排除阈值：刻意低于正判定阈值",
      "",
      "def _classify_by_embedding(self, text):",
      "    # 基准句按「文档」编码，encode_query 加指令前缀——",
      "    # BGE 标准的非对称检索配对",
      "    vecs = embedder.encode_documents(BENCHMARK_SENTENCES)",
      "    vec = embedder.encode_query(text)",
      "    sims = np.dot(np.array(vecs), vec)      # 两侧已归一化，点积即余弦",
      "    best_sim = float(np.max(sims))",
      "",
      "    if best_sim >= threshold:              # 正判定：足够相似 → AI 安全",
      "        return ClassificationResult(is_ai_security=True,",
      "            score=min(0.95, 0.6 + best_sim * 0.35), method=\"embedding\")",
      "",
      "    if best_sim < EMBEDDING_REJECT_THRESHOLD:   # 强排除",
      "        return ClassificationResult(is_ai_security=False,",
      "            score=0.1, method=\"embedding-negative\")",
      "    return None    # 中间地带 → 交给第三级 LLM 复核",
    ], "代码 3　嵌入层判定：三出口设计（正判定 / 强排除 / 交 LLM）"),
    bullet("**注释**：排除阈值取 0.25 而非 0.5 是**刻意保守**——宁可漏判、让 LLM 复核中间地带，也不误杀真正相关的样本。5.8ms/条的单条成本让这一层可以全量跑灰区。"),

    h2("7.3 断点续采（scheduler.py）"),
    ...code([
      "async def catch_up_missed(self) -> list[str]:",
      "    \"\"\"启动时补跑「已错过周期」的采集",
      "    APScheduler 默认 MemoryJobStore，进程重启后 job 状态丢失，",
      "    所有任务按「启动时刻+周期」重排——错过的周期永远不补。",
      "    misfire_grace_time 只管「调度器活着但任务被拖延」，",
      "    管不了「整个进程停过」。\"\"\"",
      "    for code, collector in get_collectors().items():",
      "        last = await session.scalar(",
      "            select(func.max(CollectionLog.started_at))",
      "            .where(CollectionLog.source_code == code)",
      "            .where(CollectionLog.status == \"success\"))",
      "        if last and (now - last).total_seconds() > collector.default_interval:",
      "            overdue.append(code)",
      "",
      "    for code in overdue:",
      "        task = asyncio.create_task(self._run_job(code))",
      "        self._background.add(task)              # 强引用防 GC",
      "        task.add_done_callback(self._background.discard)",
    ], "代码 4　断点续采：按各源水位补齐停机期间错过的周期"),
    bullet("**注释**：以 collection_logs 的**最后成功时间**为水位（而非内存状态），天然跨重启；补跑走 _run_job（自带「正在采集中则跳过」防撞车）；create_task 异步发起不阻塞启动（osv 单次要 115 秒）。"),

    h2("7.4 图表双主题色板（chartPalette.ts）"),
    ...code([
      "// ECharts 不认 CSS 变量——option 里写 var(--accent) 会回退成黑色。",
      "// 所以图表色只能走 JS 取值，切主题时 setOption 重着色。",
      "// （这是项目里唯一必须「用 JS 读主题」的地方，其余都走 CSS 变量）",
      "const DARK = {",
      "  axisLabel: '#6b7870',            // 暗底可读",
      "  splitLine: 'rgba(180,220,195,0.08)',",
      "  series: ['#4edea3', '#4cd7f6', '#ff5c7a', '#ffc857', ...],",
      "}",
      "const LIGHT = {",
      "  // 暗色的轴标签 #6b7870 在白底只有 2.6:1 对比度（WCAG 不达标）",
      "  axisLabel: '#46584f',            // 实测 7.0:1（AAA）",
      "  splitLine: 'rgba(23,58,43,0.09)', // 白底可见但不抢眼",
      "  // 系列色全部加深一档，白底上仍是可辨识的深色",
      "}",
      "// 切主题时不要重建图表实例（key 重建会取消动画），",
      "// 只 setOption({ ...option }) 让颜色生效。",
    ], "代码 5　chartPalette：ECharts 与 CSS 变量体系的边界处理"),
    bullet("**注释**：这段代码体现「**知道边界在哪**」的工程判断——CSS 变量管 99% 的主题翻转，图表这一处例外用 JS 显式处理，且注释写清了为什么例外、怎么验证（对比度实测值）。"),
    new Paragraph({ children: [new PageBreak()] }),
  ];
}

// ================= 第 8 章 · 效果展示 =================
export function chapter8() {
  return [
    h1("8. 各功能模块实现效果展示"),
    p("七个页面全部实测截图。除标注外均为运行中的真实数据，非 mock。"),

    h2("8.1 落地页 · 系统门面"),
    ...figure("截图_落地首页_黑夜.png", "落地页：能力标签明示「9 类数据源自动轮询」，动效与数字滚动均为实拍", { dir: SHOT, widthEmu: 560 }),

    h2("8.2 态势总览 · 一屏掌握全局"),
    ...figure("截图_态势总览_黑夜.png", "态势总览（黑夜主题）：情报总量、AI 范畴筛出、严重度分布、入库趋势与实时情报流", { dir: SHOT, widthEmu: 560 }),

    h2("8.3 情报中心 · 检索、筛选与导出"),
    ...figurePair("截图_情报中心_黑夜.png", "黑夜主题", "截图_情报中心_白天.png", "白天主题", { title: "情报中心双主题：多条件筛选、富化面板展开与 CSV 导出" }),
    p("情报中心支持关键词检索 + 严重度/类别/来源多维筛选，单条情报可展开查看七维富化证据与 ATT&CK 攻击链。右上角「导出 CSV」将当前筛选结果完整导出（含富化聚合列），端到端守恒验证见 6.5 节。"),

    h2("8.4 知识图谱 · 关联可视化"),
    ...figurePair("截图_知识图谱_黑夜.png", "黑夜主题", "截图_知识图谱_白天.png", "白天主题", { title: "知识图谱双主题：力导向布局、路径查询与节点详情" }),

    h2("8.5 智能问答 · 可溯源对话"),
    ...figurePair("截图_智能问答_黑夜.png", "黑夜主题", "截图_智能问答_白天.png", "白天主题", { title: "智能问答双主题：回答正文与 11 条引用溯源卡片" }),
    p("回答下方每条引用卡片携带 CVE 编号、来源、采集时间，可点击跳转原始情报——「每条论断可溯源」的直接呈现。"),

    h2("8.6 监控看板 · 自动化运行证据"),
    ...figure("截图_监控看板_白天.png", "监控看板（白天主题）：各源调度状态、上次执行结果、采集吞吐与智能体编排拓扑", { dir: SHOT, widthEmu: 560 }),

    h2("8.7 运维监控 · 健康与热切换"),
    ...figurePair("截图_运维监控_黑夜.png", "黑夜主题", "截图_运维监控_白天.png", "白天主题", { title: "运维监控双主题：队列趋势、组件健康总判与 LLM 网关热切换" }),
    p("运维页提供 LLM 模型库管理（两级视图：生效卡 + 已添加模型列表，一键切换生效模型，历史配置本地持久化），网关故障时无需重启即可切换备用模型。"),
    new Paragraph({ children: [new PageBreak()] }),
  ];
}
