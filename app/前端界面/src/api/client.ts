/**
 * 后端 API 客户端
 *
 * 统一封装错误处理与类型，与 FastAPI 后端契约一一对应。
 */

import axios from 'axios'

/**
 * API 基地址解析
 *
 * 开发环境走 Vite 的同源代理（见 vite.config.ts 的 server.proxy），
 * 避免跨域——后端虽有 CORS 配置，但同源代理更稳妥且能规避
 * 127.0.0.1 与 localhost 混用导致的 Origin 不匹配。
 *
 * 生产环境由 Nginx 反向代理 /api，用相对路径即可；
 * 若前后端分离部署，则通过 VITE_API_BASE 指定绝对地址。
 */
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export const http = axios.create({
  baseURL: API_BASE,
  /**
   * 默认 30 秒。
   *
   * ⚠️ 采集类接口必须显式覆盖为更长的超时（见 COLLECT_TIMEOUT）。
   * 实测各源真实耗时：
   *   poc_repo 32.4s / arxiv 34.5s / atlas 30.2s / cisa_kev 2.5s / nvd 10.9s
   * 拿 30s 当全局超时会让 poc_repo / arxiv 必然报
   * 「timeout of 30000ms exceeded」——
   * 但**后端采集其实成功了**（实测超时后仍入库 97 条），
   * 只是前端等不到就抛错，用户误以为采集失败。
   */
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

/** 采集类接口的等待上限（毫秒）。取 180s 覆盖最慢源并留足余量。 */
export const COLLECT_TIMEOUT = 180000

// ---------- 类型定义 ----------

/** 系统健康状态 */
export interface HealthStatus {
  status: 'healthy' | 'degraded'
  timestamp: string
  components: {
    database: { available: boolean; version?: string; error?: string; pool_size?: number }
    llm: {
      primary: { available: boolean; model: string; error?: string }
      fallback: { available: boolean; model: string }
      degraded_mode: boolean
    }
    collectors: { registered: number; codes: string[] }
    scheduler: { running: boolean; paused: boolean }
  }
  llm_stats: Record<string, number>
}

/** 数据源元信息 */
export interface SourceInfo {
  code: string
  name: string
  category: string
  homepage: string
  interval_hours: number
  ai_native: boolean
  records: number
}

/** 漏洞情报条目 */
/** 单个知识底座的条数与新鲜度 */
export interface KbFreshnessItem {
  basin: 'papers' | 'techniques' | 'policies'
  /** 当前条数（实时统计，非缓存） */
  total: number
  /** 最近一次成功采集完成时间 */
  last_collected_at: string | null
  /** 产出该底座的数据源代码 */
  sources: string[]
  /** 距上次采集的秒数 */
  seconds_since_collect: number | null
  /** 是否超过 2 个调度周期未更新 */
  stale: boolean
  /** 该底座的调度间隔（秒） */
  interval_seconds: number
  /** 最新一条内容的发布时间（业务时间） */
  latest_content_at: string | null
}

export interface VulnItem {
  cve_id: string
  title: string
  severity: string | null
  cvss_score: number | null
  ai_category: string | null
  ai_relevance: number
  ai_method: string | null
  source: string
  published_at: string | null
  latency_seconds: number | null
  enrichment_status: string
  risk_priority: string | null
  risk_level: string | null
  url: string | null
}

/** 7 维富化中的单个维度 */
export interface EnrichDimension {
  dimension: string
  label: string
  status: 'success' | 'failed'
  confidence: number
  quality_tier: 'high' | 'medium' | 'low'
  content: Record<string, unknown> | null
  error: string | null
  elapsed_ms: number | null
}

/** 富化结果 */
export interface EnrichResult {
  cve_id: string
  title: string
  completeness: number
  success_count: number
  total_count: number
  avg_confidence: number
  enrichment_status: string
  dimensions: EnrichDimension[]
}

/** 富化质量统计 */
export interface EnrichStats {
  enriched_vulnerabilities: number
  dimension_count: number
  overall_coverage: number
  high_confidence_ratio: number
  dimensions: Array<{
    dimension: string
    label: string
    success: number
    failed: number
    coverage: number
    avg_confidence: number
    high_confidence: number
    medium_confidence: number
    low_confidence: number
  }>
}

/** 学术论文 */
export interface PaperItem {
  arxiv_id: string
  title: string
  authors: string[]
  ai_topics: string[] | null
  published_at: string | null
  pdf_url: string | null
}

/** 攻击技术 */
export interface TechniqueItem {
  atlas_id: string
  name: string
  tactic: string | null
  description: string
  platform: string | null
  /** 成熟度：realized=已实证 / demonstrated=已演示 / feasible=可行。
   *  反映该技术在真实攻击中被观测到的程度。 */
  maturity: string | null
}

/** 政策法规（赛题赛题三类知识底座之一） */
export interface PolicyItem {
  doc_key: string
  title: string
  summary: string | null
  policy_kind: string | null
  org: string | null
  region: string | null
  doc_numbers: string[]
  source_name: string | null
  source_url: string | null
  published_at: string | null
}

/**
 * 知识底座总览
 *
 * 赛题要求同步采集学术论文、技术标准与政策法规，构建完整知识底座。
 * 这三个数字从后端实时取，不在前端硬编码——数据增长时页面自动跟随。
 */
export interface KnowledgeBaseStats {
  vulnerabilities: number
  ai_vulnerabilities: number
  enriched_ai_vulnerabilities: number
  sources: number
  papers: number
  techniques: number
  policies: number
  knowledge_base_total: number
}

/**
 * 延迟统计（赛题性能指标：监测时效性）
 *
 * 口径 2026-10-14 定稿：7 天发布窗口全表统一 + 回合有效性
 * （距同源上次成功 ≤ max(2×周期, 2h)，首回合/停摆回合剔除）
 * + ghsa 双钥匙按同源 URL 首次入库去重。
 */
export interface LatencySummary {
  /** 有效样本数（7 天发布窗口 × 有效回合）*/
  samples: number
  valid_rounds: number
  total_rounds: number
  /** 当期采集周期（秒）；总体为 0 */
  interval_seconds: number
  window_days: number
  p50_seconds: number | null
  p90_seconds: number | null
  p95_seconds: number | null
  max_seconds: number | null
  avg_seconds: number | null
  within_6h_ratio: number | null
  within_24h_ratio: number | null
  /** 剔除原因计数：无效回合样本 / 窗口外（冷启动回填）/ 无归属回合 */
  excluded_round_samples: number
  out_of_window: number
  unattributed: number
  /** 样本不足 10，分位数仅供参考 */
  insufficient: boolean
}

export interface LatencySourceSummary extends LatencySummary {
  source: string
  /** ATLAS 技术表无时间戳字段，延迟指标不适用 */
  not_applicable?: boolean
}

export interface LatencyOverallSummary extends LatencySummary {
  /** 并入总体的源（6 个漏洞源）*/
  sources: string[]
  _note: string
}

/** 采集概览（含分源时效性分位数，赛题性能指标举证数据）*/
export interface MonitorOverview {
  totals: {
    vulnerabilities: number
    ai_security: number
    papers: number
    techniques: number
    sources: number
  }
  by_source: Record<string, number>
  // 注：sources 字段仅为数据源元信息，不含记录数
  sources: Array<{ code: string; name: string; category: string; active: boolean }>
  last_runs: Record<string, string | null>
  /** 分源延迟统计（9 源，含 atlas 不适用行）*/
  latency_by_source: LatencySourceSummary[]
  /** 总体延迟（6 漏洞源样本级合并）*/
  latency_overall: LatencyOverallSummary
  /** @deprecated 与 latency_overall 同值（新口径），兼容旧代码，勿在新组件使用 */
  latency_stats: LatencyOverallSummary
  recent_logs: Array<{
    source: string
    status: string
    new_count: number
    elapsed_ms: number | null
    finished_at: string | null
  }>
  generated_at: string
}

/** 分类统计 */
export interface Stats {
  by_ai_category: Record<string, number>
  by_severity: Record<string, number>
  by_classify_method: Record<string, number>
}

/** 调度状态 */
export interface SchedulerStatus {
  running: boolean
  /** 自动采集是否被手动暂停（调度器仍在运行，只是不再定时触发）*/
  paused: boolean
  jobs: Array<{
    job_id: string
    name: string
    source_code: string
    paused: boolean
    next_run: string | null
    /** 上次执行时间（由 collection_logs 历史回填，进程重启后仍可见）*/
    last_run: string | null
    last_ok: boolean | null
    last_new: number | null
    interval_seconds: number
    /** 采集器内置的默认周期（秒）*/
    default_interval: number
    /** 当前周期是否为运维自定义（决定是否显示「恢复默认」）*/
    is_custom: boolean
    /** 允许的最小/最大周期（秒），由上游 API 限流决定 */
    min_seconds: number
    max_seconds: number
    /** 区间依据，用于解释「为什么这个源不能调到 10 分钟」 */
    interval_basis: string
    /** 可选周期档位 */
    options: Array<{ seconds: number; label: string; current: boolean }>
  }>
}

/** 设置采集周期的返回 */
export interface IntervalSetResult {
  ok: boolean
  error?: string
  /** 越界时附带区间依据，供界面解释原因 */
  reason?: string
  min_seconds?: number
  max_seconds?: number
  source_code?: string
  interval_seconds?: number
  /** 是否已立即重排生效（调度器未启动时仅落库，为 false）*/
  applied_immediately?: boolean
}

// ---------- 接口方法 ----------

/**
 * 调度状态变更事件名
 *
 * **为什么需要这个事件**：侧栏的「数据源 · 已暂停」与顶栏悬浮坞的
 * 调度状态读的都是 `api.health`，而App.tsx 里的 health
 * 只有「挂载时拉一次 + 每 30s 轮询」。
 * 用户在看板点「暂停/恢复」时，DashboardPage 刷新了自己的
 * scheduler，但 App 的 health 完全不知情——
 * 结果这两个位置要等最多 30 秒才变，看起来就是「点了没反应」。
 *
 * 用事件把「调度状态已变」这件事广播出去，App 收到后立即重拉 health。
 * 放在 client.ts 而非 App.tsx，是为了避免 App ←→ Dashboard 的循环依赖。
 */
export const SCHED_CHANGED_EVENT = 'aiintel:scheduler-changed'

/** 调度状态变更时广播，通知 App.tsx 立即刷新 health */
export function notifySchedulerChanged() {
  window.dispatchEvent(new CustomEvent(SCHED_CHANGED_EVENT))
}

/** 单条采集到的数据 */
export interface CollectFeedItem {
  id: string
  title: string
  severity: string | null
  cvss: number | null
  url: string | null
}

/** 扁平化的一行：一条采集到的数据（看板表格用） */
/** LLM 配置（Key 始终为掩码，后端不回传完整值） */
export interface LLMConfig {
  api_base: string
  model: string
  key_masked: string
  key_configured: boolean
  is_custom: boolean
  defaults: { api_base: string; model: string }
  last_saved_at: number | null
  presets: { name: string; api_base: string; model: string }[]
}

export interface CollectFeedRow {
  source: string
  finished_at: string | null
  id: string
  title: string
  severity: string | null
  cvss: number | null
  url: string | null
}

/** 一次采集批次（无论有无新增数据都会出现，用于未读检测） */
export interface CollectBatch {
  log_id: number
  source: string
  finished_at: string | null
  new_count: number
  fetched_count?: number
}

/** 一次采集（对应若干条具体数据） */
export interface CollectFeedGroup {
  log_id: number
  source: string
  /** 声称新增的条数（可能大于 items.length，因为明细是采样） */
  new_count: number
  /** 本次抓到的总量（去重前）；与 new_count 对比可知是否全是已有数据 */
  fetched_count?: number
  finished_at: string | null
  items: CollectFeedItem[]
  /** 去重后本次真正展示的条目 id 列表（跨组去重的结果） */
  item_ids?: string[]
}

/** 数据存储位置信息（系统设置 → 数据存储位置） */
export interface DataLocationInfo {
  /** 当前实际使用的位置 */
  current_dir: string
  /** 用户设定的位置（输入框回填值） */
  configured_dir: string
  /** 是否有待重启生效的变更 */
  restart_required: boolean
}

export const api = {
  /** 系统健康状态 */
  health: () => http.get<HealthStatus>('/api/health').then((r) => r.data),
  /** 读 LLM 配置（Key 为掩码） */
  llmConfig: () => http.get<LLMConfig>('/api/llm/config').then((r) => r.data),
  /**
   * 保存 LLM 配置并立即生效
   * api_key 传空字符串表示沿用当前 Key（仅换模型名时用）
   */
  saveLLMConfig: (body: { api_base: string; model: string; api_key?: string }) =>
    http
      .post<{ ok: boolean; message: string; available?: boolean; model?: string }>(
        '/api/llm/config',
        body,
      )
      .then((r) => r.data),

  /** 试连测试（不保存、不影响正在服务的 LLM） */
  testLLMConfig: (body: { api_base: string; model: string; api_key: string }) =>
    http
      .post<{ ok: boolean; message: string; models: string[] }>('/api/llm/config/test', body)
      .then((r) => r.data),

  /** 恢复出厂默认 */
  restoreLLMConfig: () =>
    http
      .post<{ ok: boolean; message: string }>('/api/llm/config/restore')
      .then((r) => r.data),

  /** 读数据存储位置（系统设置） */
  dataLocation: () =>
    http.get<DataLocationInfo>('/api/settings/data-location').then((r) => r.data),

  /** 保存数据存储位置（重启程序后自动迁移） */
  setDataLocation: (data_dir: string) =>
    http
      .post<{ ok: boolean; restart_required: boolean; message: string }>(
        '/api/settings/data-location',
        { data_dir },
      )
      .then((r) => r.data),

  /** 数据源列表 */
  sources: () =>
    http.get<{ total: number; sources: SourceInfo[] }>('/api/sources').then((r) => r.data),

  /** 采集概览 */
  monitorOverview: () =>
    http.get<MonitorOverview>('/api/monitor/overview').then((r) => r.data),

  /** 调度状态 */
  scheduler: () =>
    http.get<SchedulerStatus>('/api/monitor/scheduler').then((r) => r.data),

  /**
   * 暂停 / 恢复自动采集
   *
   * 暂停时调度器仍在运行，系统可正常查询与问答，
   * 只是不再自动发起采集；手动「采集」按钮不受影响。
   */
  setSchedulerPaused: (paused: boolean) =>
    http
      .post(paused ? '/api/monitor/scheduler/pause' : '/api/monitor/scheduler/resume')
      .then((r) => r.data),

  /**
   * 设置 / 恢复某数据源的采集周期
   *
   * intervalSeconds 传 null = 恢复该源的默认周期。
   * 自定义值会持久化到 scheduler_settings 表，重启后依然生效。
   */
  setSchedulerInterval: (sourceCode: string, intervalSeconds: number | null) =>
    http
      .post<IntervalSetResult>('/api/monitor/scheduler/interval', {
        source_code: sourceCode,
        interval_seconds: intervalSeconds,
      })
      .then((r) => r.data),

  /** 分类统计 */
  stats: () => http.get<Stats>('/api/stats').then((r) => r.data),
  /** 知识底座总览（三类底座 + 富化覆盖） */
  knowledgeBase: () =>
    http.get<KnowledgeBaseStats>('/api/stats/knowledge-base').then((r) => r.data),

  /** 漏洞列表 */
  vulns: (params?: {
    ai_only?: boolean
    category?: string
    severity?: string
    source?: string
    keyword?: string
    limit?: number
    offset?: number
    /** 导出用：附完整描述与富化 extra（资产/POC/修复建议等） */
    include_extra?: boolean
  }) =>
    http
      .get<{ total: number; items: VulnItem[] }>('/api/vulns', { params })
      .then((r) => r.data),

  /** 论文列表。基数已 200+，故支持检索与分页：
   *  keyword 按标题/作者模糊搜索，topic 按 AI 主题过滤，
   *  offset 用于翻页（缺了它翻页会返回同一批数据）。
   *  只给「最新 N 条」既找不到目标，也翻不完。 */
  papers: (limit = 50, keyword?: string, topic?: string, offset = 0) =>
    http
      .get<{ total: number; items: PaperItem[] }>('/api/papers', {
        params: {
          limit,
          offset,
          keyword: keyword || undefined,
          topic: topic || undefined,
        },
      })
      .then((r) => r.data),

  /** 赛题三类知识底座的条数与新鲜度（供卡片显示「共 N 篇 · 更新于 X」） */
  kbFreshness: () =>
    http
      .get<{ items: KbFreshnessItem[]; generated_at: string }>('/api/kb-freshness')
      .then((r) => r.data),

  /**
   * 最近采集到的**具体条目**（不只是「新增 N 条」）
   *
   * 数据来自 collection_logs.metrics.sample_ids —— 入库时记录的条目 ID 采样。
   * 不用时间字段反查的原因：rss/poc 这类源重复采集走「合并已有条目」，
   * 不产生新行，ingest_at / modified_at 都不变，反查恒为 0 条。
   */
  collectFeed: (minutes = 1440) =>
    http
      .get<{
        groups: CollectFeedGroup[]
        rows: CollectFeedRow[]
        batches: CollectBatch[]
        generated_at: string
      }>('/api/collect/feed', {
        params: { minutes },
      })
      .then((r) => r.data),

  /** 攻击技术列表 */
  techniques: (limit = 50) =>
    http
      .get<{ total: number; items: TechniqueItem[] }>('/api/techniques', { params: { limit } })
      .then((r) => r.data),

  /** 政策法规列表（赛题赛题三类知识底座之一） */
  policies: (limit = 50, kind?: string) =>
    http
      .get<{ total: number; items: PolicyItem[] }>('/api/policies', {
        params: { limit, kind },
      })
      .then((r) => r.data),

  /** 手动触发采集 */
  /**
   * 手动触发采集。
   *
   * 必须传 COLLECT_TIMEOUT：实测慢源要 30~35 秒，
   * 用默认的 30s 会让「后端成功、前端报错」。
   * 「全部采集」更久（串行跑 9 个源），给 10 分钟。
   */
  triggerCollect: (code: string) =>
    http.post(`/api/collect/${code}`, undefined, { timeout: COLLECT_TIMEOUT })
      .then((r) => r.data),

  /** 触发全部采集 */
  /** 触发全部采集（9 个源串行，可能几分钟） */
  triggerCollectAll: () =>
    http.post('/api/collect/all', undefined, { timeout: 600000 })
      .then((r) => r.data),

  /** 查询富化结果 */
  enrichment: (cveId: string) =>
    http.get<EnrichResult>(`/api/enrich/${cveId}`).then((r) => r.data),

  /** 触发单个富化 */
  triggerEnrich: (cveId: string) =>
    http.post<EnrichResult>(`/api/enrich/${cveId}`).then((r) => r.data),

  /** 批量富化 */
  enrichBatch: (params?: { limit?: number; ai_only?: boolean; concurrency?: number }) =>
    http
      .post<{
        total: number
        succeeded: number
        complete_enriched: number
        completeness_rate: number
        avg_completeness: number
        avg_confidence: number
        dimension_coverage: number
        elapsed_ms: number
      }>('/api/enrich-batch', null, { params })
      .then((r) => r.data),

  /** 富化质量统计 */
  enrichStats: () => http.get<EnrichStats>('/api/enrich/stats').then((r) => r.data),

  /* ============ 系统运维与自愈 ============ */

  /** 链路组件健康：编排节点 + 调度器 */
  opsComponents: () =>
    http.get<OpsComponents>('/api/ops/components').then((r) => r.data),

  /** 资源水位：CPU / 内存 / 磁盘 / 数据库连接（psutil 真实采样） */
  opsResources: () =>
    http.get<OpsResources>('/api/ops/resources').then((r) => r.data),

  /** 队列与延迟趋势（按小时分桶） */
  opsTimeline: (buckets = 24) =>
    http
      .get<OpsTimeline>('/api/ops/timeline', { params: { buckets } })
      .then((r) => r.data),

  /** 自愈事件与告警 */
  opsEvents: (limit = 60) =>
    http
      .get<OpsEvents>('/api/ops/events', { params: { limit } })
      .then((r) => r.data),

  /* ============ 知识图谱可视化 ============ */

  /** 图谱总览：一片子图（按度数取 topN 节点）+ 统计 + 关系分布 */
  graphOverview: (limit = 120, minDegree = 1) =>
    http
      .get<SituationGraphOverview>('/api/graph/overview', {
        params: { limit, min_degree: minDegree },
      })
      .then((r) => r.data),

  /** 节点详情：基本信息 + 按关系类型分组的邻居 */
  graphNode: (nodeId: string, limitEach = 12) =>
    http
      .get<SituationGraphNode>('/api/graph/node', {
        params: { node_id: nodeId, limit_each: limitEach },
      })
      .then((r) => r.data),

  /** 两点间多跳链路（最多 3 跳，赛题要求） */
  graphPath: (source: string, target: string) =>
    http
      .get<SituationGraphPath>('/api/graph/path', {
        params: { source, target, max_hops: 3 },
      })
      .then((r) => r.data),

  /**
   * 态势总览（聚合接口）
   *
   * 一次返回某时间范围内的全部分析数据——
   * 指标卡、趋势、分类与严重度分布、最新高危、流水线、情报源健康。
   * 服务端一次算好，口径天然一致（不会出现"分类算7天、趋势算30天"）。
   *
   * @param hours 回看小时数；省略或传undefined 表示全周期
   */
  situationOverview: (hours?: number) =>
    http
      .get<SituationOverview>('/api/situation/overview', {
        params: hours ? { hours } : undefined,
      })
      .then((r) => r.data),

  /* ============ 拓展模块：ROEN 网络入侵检测 ============ */

  /** 拓展模块状态（监控/锁定/统计/图像） */
  roenStatus: () => http.get<RoenStatus>('/api/roen/status').then((r) => r.data),

  /** 最近检测记录（最新在前） */
  roenHistory: (limit = 50) =>
    http.get<RoenHistoryItem[]>('/api/roen/history', { params: { limit } }).then((r) => r.data),

  /** 模型信息与论文引用 */
  roenModelInfo: () => http.get<RoenModelInfo>('/api/roen/model-info').then((r) => r.data),

  /** 开始监控（可同时带参数） */
  roenMonitorStart: (cfg?: RoenMonitorConfig) =>
    http.post<RoenActionResult>('/api/roen/monitor/start', cfg ?? {}).then((r) => r.data),

  /** 停止监控 */
  roenMonitorStop: () =>
    http.post<RoenActionResult>('/api/roen/monitor/stop').then((r) => r.data),

  /** 调整监控参数 */
  roenMonitorConfigure: (cfg: RoenMonitorConfig) =>
    http.post<RoenActionResult>('/api/roen/monitor/configure', cfg).then((r) => r.data),

  /** 立即检测一次 */
  roenDetectOnce: () =>
    http.post<RoenDetectResult>('/api/roen/detect/once').then((r) => r.data),

  /** 模拟一次攻击事件（演练，界面会标注） */
  roenSimulateAttack: () =>
    http.post<RoenSimulateResult>('/api/roen/response/simulate-attack').then((r) => r.data),

  /** 管理员口令解锁 */
  roenUnlock: (admin_key: string) =>
    http.post<RoenActionResult>('/api/roen/unlock', { admin_key }).then((r) => r.data),

  /** 手动加密全部演示图像 */
  roenEncryptImages: () =>
    http.post<RoenActionResult>('/api/roen/images/encrypt').then((r) => r.data),

  /** 解密还原全部演示图像 */
  roenDecryptImages: () =>
    http.post<RoenActionResult>('/api/roen/images/decrypt').then((r) => r.data),
}

/** 拓展模块（ROEN）响应结构，与后端 拓展模块/routes.py 对应 */
export interface RoenStatus {
  model_ready: boolean
  model_error: string | null
  monitoring: boolean
  locked: boolean
  total_detections: number
  total_attacks: number
  simulated_events: number
  total_encrypted: number
  config: { interval: number; batch_size: number; attack_probability: number }
  simulator: { total_records: number; normal_records: number; attack_records: number } | null
  images: Array<{ id: string; filename: string; is_encrypted: boolean; size: number }>
  last_detection: RoenHistoryItem | null
}

export interface RoenHistoryItem {
  ts: string
  total_flows: number
  attack_type: string
  threat_level: string
  confidence_score: number
  is_attack: boolean
  class_distribution: Record<string, number>
  simulation: boolean
}

export interface RoenModelInfo {
  ready: boolean
  model_path?: string
  device?: string
  num_classes?: number
  classes?: string[]
  params?: number
  threat_levels?: string[]
  paper: { title: string; venue: string; authors: string }
  figures: { scenario: string; architecture: string }
}

export interface RoenMonitorConfig {
  interval?: number
  batch_size?: number
  attack_probability?: number
}

export interface RoenActionResult {
  ok: boolean
  message: string
  monitoring?: boolean
  locked?: boolean
  config?: { interval: number; batch_size: number; attack_probability: number }
}

export interface RoenDetectResult {
  ok: boolean
  result: {
    timestamp: string
    total_flows: number
    attack_type: string
    threat_level: string
    confidence_score: number
    class_distribution: Record<string, number>
    is_attack: boolean
    source_ips: string[]
    target_ips: string[]
  }
}

export interface RoenSimulateResult {
  ok: boolean
  result: { attack_type: string; threat_level: string; confidence_score: number } & Record<
    string,
    unknown
  >
  locked: boolean
}

/** 态势总览响应结构（与后端 situation.py 对应） */
export interface SituationOverview {
  range: {
    hours: number | null
    bucket: 'hour' | 'day'
    label: string
    start: string | null
  }
  metrics: {
    new_intel: number
    new_vuln: number
    other: Record<string, number>
    enriched: number
    high_count: number
    high_ratio: number
    avg_cvss: number | null
    classified: number
    indexed: number
    graph: Record<string, unknown>
    source_count: number
  }
  trend: {
    buckets: string[]
    new: number[]
    high: number[]
    enriched: number[]
    /** 当前小时已过去的比例 0~1（仅小时粒度有值）。可用于判断"数据是否该更新了" */
    progress?: number | null
  }
  by_category: Array<{ name: string; value: number }>
  by_severity: Array<{ name: string; value: number }>
  top_critical: Array<{
    cve_id: string
    title: string
    cvss: number | null
    severity: string
    ai_category: string | null
    source: string | null
    published_at: string | null
    ingest_at: string | null
  }>
  latency: {
    median: number
    avg: number
    max: number
    p95: number
    samples: number
  }
  pipeline: Array<{ stage: string; count: number }>
  sources: Array<{
    code: string
    name: string
    category: string | null
    count: number
    runs: number
    success_rate: number | null
    avg_latency: number | null
  }>
}

/* ============ 知识图谱类型（与后端 graph_view.py 对应） ============ */
export interface GraphNodeItem {
  id: string
  name: string
  full_name: string
  label: string
  label_cn: string
  degree: number
  cvss?: number | null
  severity?: string | null
}

export interface GraphLinkItem {
  source: string
  target: string
  type: string
  type_cn: string
}

export interface SituationGraphOverview {
  available: boolean
  stats: {
    nodes: number
    relationships: number
    avg_degree: number
    max_degree: number
    shown_nodes: number
    shown_relationships: number
  }
  nodes: GraphNodeItem[]
  links: GraphLinkItem[]
  relation_types: Array<{ type: string; type_cn: string; count: number }>
  degree_histogram: Array<{ range: string; count: number }>
}

export interface GraphNeighborItem {
  id: string
  name: string
  label: string
  label_cn: string
}

export interface SituationGraphNode {
  node: {
    id: string
    name: string
    label: string
    label_cn: string
    degree: number
    cvss?: number | null
    severity?: string | null
  }
  neighbors: Array<{
    type: string
    type_cn: string
    items: GraphNeighborItem[]
  }>
}

export interface SituationGraphPath {
  found: boolean
  hops: number | null
  nodes: Array<{
    id: string
    name: string
    label: string
    label_cn: string
    via: string | null
    hop: number
  }>
  relations: Array<{
    from: string
    to: string
    type: string
    type_cn: string
  }>
}

/* ============ 系统运维与自愈（对应后端 ops.py） ============ */

export interface OpsNode {
  key: string
  name: string
  desc: string
  kind: 'agent' | 'scheduler'
  runs?: number
  success_rate?: number | null
  avg_ms?: number | null
  last_ms?: number | null
  retries?: number
  circuit_open?: boolean
  healthy?: boolean | null
  last_heartbeat?: string | null
  /** 最后一次被调度/执行的 ISO 时间——「N 分钟前触发」的数据源 */
  last_run_at?: string | null
  last_error?: string | null
  /** 该Agent 的真实触发条件（后端 TRIGGER_HINT），runs=0 时用于说明原因 */
  trigger?: string | null
  /** 执行记录的存储范围（进程内缓冲，重启清零） */
  record_scope?: string
  running?: boolean
  paused?: boolean
}

export interface OpsCollector {
  key: string
  name: string
  desc: string
  kind: 'collector'
  runs: number
  success_rate: number | null
  avg_ms: number | null
  new_items: number
}

export interface OpsComponents {
  nodes: OpsNode[]
  collectors: OpsCollector[]
  graph: { available?: boolean; nodes?: number }
  scheduler: { running: boolean; paused: boolean; count: number }
  totals: {
    agent_runs: number
    collector_runs: number
    executed_nodes: number
    total_nodes: number
  }
}

export interface OpsResources {
  cpu: { percent: number; cores: number | null }
  memory: { percent: number; used_gb: number; total_gb: number }
  disk: { percent: number; used_gb: number; total_gb: number }
  database: {
    connections: number | null
    active: number | null
    max_connections: number | null
  }
  process: { uptime_seconds: number | null }
}

export interface OpsTimeline {
  labels: string[]
  agent_runs: number[]
  agent_fails: number[]
  collector_runs: number[]
  collector_fails: number[]
  avg_latency_ms: (number | null)[]
  totals: {
    agent_runs: number
    agent_fails: number
    collector_runs: number
    collector_fails: number
  }
}

export interface OpsEvent {
  kind: string
  title: string
  detail: string
  level: 'info' | 'warn' | 'error'
  ts: number
}

export interface OpsEvents {
  events: OpsEvent[]
  alerts: Array<{
    source: string
    name: string
    fails: number
    level: 'warn' | 'error'
    last_at: string | null
  }>
  counts: { error: number; warn: number; info: number }
}
