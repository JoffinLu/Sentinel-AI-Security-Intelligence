/**
 * 情报列表页
 *
 * 展示采集到的漏洞情报，支持按 AI 相关性、分类、严重程度、来源筛选。
 * 智能体对话入口在 P3 阶段接入，此处预留接口。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Button,
  Card,
  Col,
  Drawer,
  Input,
  message,
  Pagination,
  Popconfirm,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import { DownloadOutlined, SearchOutlined, ThunderboltOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

import { api } from '@/api/client'
import type {
  EnrichResult,
  KbFreshnessItem,
  PaperItem,
  PolicyItem,
  Stats,
  TechniqueItem,
  VulnItem,
} from '@/api/client'
import { EnrichmentPanel } from '@/components/EnrichmentPanel'
import { formatDuration } from '@/components/MetricCard'
import {
  CustomScrollArea,
  CustomScrollbar,
  useInnerScrollRef,
} from '@/components/CustomScroll'
import { MATURITY, TACTIC_PHASE, tacticZh, techZh } from '@/lib/atlasTaxonomy'

dayjs.extend(relativeTime)

const { Text } = Typography

/**
 * 相对时间 → 紧凑标签
 *
 * 知识底座三类卡片要在很小的空间里表达「时间」。
 *
 * 原来用相对时间（今天/昨天/N 天前），超期才显示日期 ——
 * 结果**同一个列表里两种格式混排**（政策卡里能看到
 * 「今天」「3 天前」「2026-09-29」挨在一起），
 * 读者要在两种心智模型间来回切换，且「4 天前」这种表述
 * 无法用于精确比对与引用。
 *
 * 改为**统一绝对时间**：MM-DD HH:mm。
 * 数据源本身精确到秒（published_at 是 ISO8601 带时区），
 * 所以显示到分钟是可信的，不是编造的精度。
 * 年份在跨年时会与内容混淆，故只在「跨年时」显示年份。
 */
function freshLabel(iso: string | null | undefined): string {
  if (!iso) return '时间未知'
  const d = dayjs(iso)
  if (!d.isValid()) return '时间未知'
  // 同年只显示 MM-DD HH:mm；跨年补上年份，避免「去年那条」认不出来
  const sameYear = d.year() === dayjs().year()
  return sameYear ? d.format('MM-DD HH:mm') : d.format('YYYY-MM-DD')
}

const SEVERITY_TAG: Record<string, string> = {
  CRITICAL: 'red',
  HIGH: 'volcano',
  MEDIUM: 'gold',
  MODERATE: 'gold',
  LOW: 'blue',
}

/**
 * 严重度 → SENTINEL 语义 class
 *
 * AntD 内置色（red/volcano/gold）在暗色玻璃背景下饱和度过高、
 * 边框是实心色块，与整体质感不搭。改用设计语言定义的语义 class，
 * 颜色由 sentinel-theme.css 统一接管：
 *   CRITICAL 玫红（最高危，浮起） / HIGH 琥珀 / MEDIUM 蓝 / LOW 灰
 */
const SEVERITY_CLASS: Record<string, string> = {
  CRITICAL: 'tag-critical',
  HIGH: 'tag-high',
  MEDIUM: 'tag-medium',
  MODERATE: 'tag-medium',
  LOW: 'tag-low',
}

// 赛题四分类配色
const CATEGORY_COLOR: Record<string, string> = {
  模型与数据层风险: 'purple',
  AI应用与智能体风险: 'magenta',
  供应链风险: 'orange',
  'AI基础设施/推理框架风险': 'cyan',
}

/**
 * AI 分类 → SENTINEL 模块色 class
 *
 * 沿用设计语言 Part D 的 5 色模块色板（青=采集/翡翠=富化/
 * 琥珀=嵌入/紫=图谱/玫红=告急），与图表配色同源，
 * 保证「分类标签」与「分类图表」视觉一致。
 */
const CATEGORY_CLASS: Record<string, string> = {
  模型与数据层风险: 'tag-info',
  AI应用与智能体风险: 'tag-warn',
  供应链风险: 'tag-high',
  'AI基础设施/推理框架风险': 'tag-ok',
}

// 处置优先级配色
const PRIORITY_COLOR: Record<string, string> = {
  P1: 'red',
  P2: 'volcano',
  P3: 'gold',
  P4: 'default',
}

/** 处置优先级 → SENTINEL 语义 class（与严重度同色序，便于横向对比） */
const PRIORITY_CLASS: Record<string, string> = {
  P1: 'tag-critical',
  P2: 'tag-high',
  P3: 'tag-medium',
  P4: 'tag-low',
}

/**
 * 采集时间 → 人类可读
 *
 * 用「x 小时前 / x 天前」而不是绝对时间戳：
 * 用户关心的是「数据新鲜吗」，不是「几点几分采的」。
 */
function collectedAgo(seconds: number | null): string {
  if (seconds === null) return '尚未采集'
  if (seconds < 90) return '刚刚采集'
  const mins = Math.floor(seconds / 60)
  if (mins < 60) return `${mins} 分钟前采集`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} 小时前采集`
  return `${Math.floor(hours / 24)} 天前采集`
}

/**
 * 知识底座的新鲜度徽标
 *
 * 回答用户的三个疑问：
 *   ① 这个数字会自己变吗？→ 会，每 30 秒轮询
 *   ② 数据是什么时候采的？→ 悬浮显示相对时间 + 绝对时间
 *   ③ 是不是太旧了？→ 超过 2 个调度周期则标「待更新」
 *
 * 刚采集完成时（条数变化）会闪一下绿色脉冲，让「自动更新」看得见。
 */
function FreshnessBadge({
  item,
  justUpdated,
}: {
  item: KbFreshnessItem | undefined
  justUpdated: boolean
}) {
  if (!item) return null
  const f = item
  const cls = f.stale ? 'kb-fresh-stale' : 'kb-fresh-ok'
  const label = f.stale ? '待更新' : '自动更新中'
  const exact = f.last_collected_at
    ? dayjs(f.last_collected_at).format('YYYY-MM-DD HH:mm:ss')
    : '从未成功采集'

  return (
    <Tooltip
      title={
        <div>
          <div>最近采集：{exact}</div>
          <div>调度间隔：{Math.round(f.interval_seconds / 3600)} 小时</div>
          <div>数据源：{f.sources.join('、')}</div>
          <div style={{ marginTop: 4, opacity: 0.75 }}>
            每 30 秒自动检查一次，采集完成后条数会自动更新
          </div>
        </div>
      }
    >
      <span className={`kb-fresh ${cls} ${justUpdated ? 'kb-fresh-pulse' : ''}`}>
        <span className="kb-fresh-dot" />
        {label} · {collectedAgo(f.seconds_since_collect)}
      </span>
    </Tooltip>
  )
}

export default function IntelPage() {
  const [items, setItems] = useState<VulnItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [stats, setStats] = useState<Stats | null>(null)
  const [papers, setPapers] = useState<PaperItem[]>([])
  const [techs, setTechs] = useState<TechniqueItem[]>([])
  // 政策法规——赛题要求的赛题赛题三类知识底座之一，此前无独立展示入口
  const [policies, setPolicies] = useState<PolicyItem[]>([])
  // 赛题三类知识底座的**真实总量**。不能直接用 items.length：
  // 卡片只取前几条做展示，用length 会把「展示条数」误报成「总条数」。
  const [kbTotal, setKbTotal] = useState({ papers: 0, techniques: 0, policies: 0 })

  const [aiOnly, setAiOnly] = useState(true)

  /* 手册 3.5：**URL 是检索页唯一的查询状态源**。
   * 原来筛选与页码只在 useState 里——刷新丢失、无法分享链接、
   * 浏览器前进后退失效（演示时观众想回退一步都做不到）。
   * 改为从 URL 派生 + 统一 commit 写回：
   *   刷新可复原、链接可分享、前进后退可用。 */
  const [params, setParams] = useSearchParams()
  const category = params.get('category') || undefined
  const severity = params.get('severity') || undefined
  const page = Math.max(1, Number(params.get('page') ?? 1) || 1)
  /** 搜索框保留本地草稿：每输入一个字都写 URL 会让历史记录被淹没，
   *  只有点搜索 / 按 Enter 时才 commit。 */
  const [keywordDraft, setKeywordDraft] = useState(params.get('q') ?? '')
  /** 情报导出进行中 */
  const [exporting, setExporting] = useState(false)
  const keyword = params.get('q') ?? ''

  /** 统一写回 URL 的入口（手册 3.5）。
   *  查询条件变化时把页码重置回 1——否则在新结果里会停在已经不存在的旧页。
   *
   *  ⚠️ 踩过的坑（表现为「筛选点了没反应」）：
   *  原实现是 `if (touchedFilter && 'page' in next) merged.delete('page')`，
   *  只有「本次调用带 page」时才重置页码。于是筛选的 onChange 里
   *  `setCategory(v)` + `setPage(1)` 连用就会出错 ——
   *  两次 commitQuery 都基于**同一个闭包里的旧 params** 重建 URL，
   *  第二次（page:1）把第一次刚设好的 category 整个覆盖掉，
   *  最终 URL 里 category 根本不存在。
   *
   *  现在改为：**只要本次不是翻页，就重置页码**。
   *  这样调用方只需 commitQuery({category: v}) 一次，
   *  不必也不该再跟一个 setPage(1)。 */
  const commitQuery = useCallback(
    (next: Record<string, string | number | undefined>) => {
      const merged = new URLSearchParams(params)
      for (const [key, value] of Object.entries(next)) {
        // page=1 等价于「无页码」，直接删掉更干净
        if (value === '' || value == null || (key === 'page' && Number(value) === 1)) {
          merged.delete(key)
        } else {
          merged.set(key, String(value))
        }
      }
      // 非翻页的查询变化 → 回到第 1 页（新建、清空筛选都算）
      if (!('page' in next)) merged.delete('page')
      setParams(merged, { replace: false })
    },
    [params, setParams],
  )

  const setCategory = useCallback(
    (v: string | undefined) => commitQuery({ category: v }),
    [commitQuery],
  )
  const setSeverity = useCallback(
    (v: string | undefined) => commitQuery({ severity: v }),
    [commitQuery],
  )
  const setPage = useCallback((p: number) => commitQuery({ page: p }), [commitQuery])
  /** URL 变化（含浏览器前进/后退）后，让草稿与 URL 对齐 */
  useEffect(() => {
    setKeywordDraft(params.get('q') ?? '')
  }, [params])

  const pageSize = 20

  // ---- 论文卡片：检索状态 ----
  // 论文有 205 条，卡片不能只取最新 6 条（那等于把 199 条藏起来），
  // 改为「搜索 + 主题过滤 + 分页」，让每一条都能被看到。
  const [paperKeyword, setPaperKeyword] = useState('')
  const [paperTopic, setPaperTopic] = useState<string | undefined>()
  const [paperPage, setPaperPage] = useState(1)
  const paperPageSize = 20
  /**
   * 论文请求序号：只接受「最新一次」请求的结果。
   *
   * 为什么要这个：翻页是异步的。用户连点第 2、3 页时，
   * 两个请求并发发出，**后发的第 3 页可能先返回**，
   * 若不加区分，第 3 页的数据会被第 2 页的响应覆盖，
   * 出现「页码显示 3、内容却是第 2 页」的错位。
   * 用自增序号比对，丢弃过期响应即可。
   */
  const paperReqId = useRef(0)

  /**
   * 主表横向滚动条：AntD 内部容器无法包裹，只能拿 ref 定位后
   * 把自绘滑块覆盖上去（`.intel-vuln-card` 提供定位上下文）。
   */
  const tableWrapRef = useRef<HTMLDivElement | null>(null)
  const tableScrollRef = useInnerScrollRef(tableWrapRef, '.intel-vuln-table .ant-table-content')

  // ---- 政策法规卡片：按属性过滤 ----
  // 政策共 22 条，卡片只铺 9 条会让用户以为只有 9 条。
  // 拉全量 + 提供「属性」筛选（政策/法规/标准/观点）。
  const [policyKind, setPolicyKind] = useState<string | undefined>()

  // ---- 富化详情抽屉 ----
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [enrichData, setEnrichData] = useState<EnrichResult | null>(null)
  const [enrichLoading, setEnrichLoading] = useState(false)
  const [enriching, setEnriching] = useState<string | null>(null)
  const [batchRunning, setBatchRunning] = useState(false)

  const openEnrichment = async (cveId: string) => {
    setDrawerOpen(true)
    setEnrichLoading(true)
    setEnrichData(null)
    try {
      setEnrichData(await api.enrichment(cveId))
    } catch {
      // 尚未富化，属正常情况
    } finally {
      setEnrichLoading(false)
    }
  }

  const runEnrich = async (cveId: string) => {
    setEnriching(cveId)
    try {
      const r = await api.triggerEnrich(cveId)
      setEnrichData({
        cve_id: r.cve_id,
        title: items.find((i) => i.cve_id === cveId)?.title ?? r.cve_id,
        completeness: r.completeness,
        success_count: r.success_count,
        total_count: r.total_count,
        avg_confidence: r.avg_confidence,
        enrichment_status: 'completed',
        dimensions: r.dimensions as EnrichResult['dimensions'],
      })
      message.success(`富化完成：${r.success_count}/${r.total_count} 维`)
      await load()
    } catch (e) {
      message.error('富化失败：' + (e as Error).message)
    } finally {
      setEnriching(null)
    }
  }

  /* ---------- 情报导出（CSV）----------
 * 范围跟随当前筛选（aiOnly/category/severity/keyword 快照），循环分页拉全；
 * 超 1 万行自动拆分为多个文件；含富化详情（走 include_extra，纯前端拼 CSV）。 */
const EXPORT_PAGE = 500
const EXPORT_ROWS_PER_FILE = 10000

type ExportItem = VulnItem & {
  description?: string | null
  extra?: Record<string, unknown> | null
  /** 按维度聚合的富化内容（后端 include_extra 时批量 JOIN enrichments 表） */
  enrichment?: Record<string, Record<string, unknown> | null> | null
}

const csvEscape = (v: unknown): string => {
  const str = v == null ? '' : String(v)
  return /[",\n\r]/.test(str) ? '"' + str.replace(/"/g, '""') + '"' : str
}

/** 富化维度内容按真实数据结构格式化为人类可读文本。
 *  （数据来源：enrichments 表 content JSONB，经后端 include_extra 聚合返回。
 *    键名以库里真实数据为准，已逐维度核对——不要凭猜改键名。） */
const asArray = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? (v.filter((x) => x && typeof x === 'object') as Record<string, unknown>[]) : []

/** 关联资产：直接用富化生成的摘要（"1 个 AI 组件；生态 PyPI；类型 智能体组件"） */
const fmtAsset = (dims: Record<string, Record<string, unknown>>): string => {
  const c = dims.asset
  if (!c) return ''
  if (typeof c.summary === 'string' && c.summary) return c.summary
  return asArray(c.assets)
    .map((a) => String(a.product ?? ''))
    .filter(Boolean)
    .join('；')
}

/** POC：可利用性结论 + 仓库列表（name(url)） */
const fmtPoc = (dims: Record<string, Record<string, unknown>>): string => {
  const c = dims.poc
  if (!c) return ''
  const parts: string[] = []
  if (typeof c.exploitability === 'string' && c.exploitability) parts.push(c.exploitability)
  const pocs = asArray(c.pocs)
  if (pocs.length) {
    parts.push(
      pocs
        .map((p) => {
          const name = String(p.name ?? '')
          const url = String(p.url ?? '')
          return url ? `${name}(${url})` : name
        })
        .join('；'),
    )
  }
  return parts.join('；')
}

/** MITRE 技术：ATLAS 编号 + 名称 */
const fmtTechnique = (dims: Record<string, Record<string, unknown>>): string => {
  const c = dims.technique
  if (!c) return ''
  if (c.conclusion === 'unmapped') return '未映射到 ATLAS'
  const list = asArray(c.techniques)
  if (!list.length) return ''
  return list
    .map((t) => [String(t.atlas_id ?? ''), String(t.name ?? '')].filter(Boolean).join(' '))
    .join('；')
}

/** 修复建议：缓解措施 + 补丁版本 */
const fmtFix = (dims: Record<string, Record<string, unknown>>): string => {
  const c = dims.fix
  if (!c) return ''
  const parts: string[] = []
  if (typeof c.mitigation === 'string' && c.mitigation) parts.push(c.mitigation)
  if (c.patch_version) parts.push(`补丁版本：${c.patch_version}`)
  const links = asArray(c.fix_links)
  if (links.length) {
    parts.push(links.map((l) => String(l.url ?? l)).join('；'))
  }
  return parts.join('；')
}

/** 相关论文：arXiv 标题 */
const fmtPaper = (dims: Record<string, Record<string, unknown>>): string => {
  const c = dims.paper
  if (!c) return ''
  const papers = asArray(c.papers)
  if (!papers.length) return ''
  return papers
    .map((p) => {
      const title = String(p.title ?? '')
      const id = String(p.arxiv_id ?? '')
      return id ? `${title}（arXiv:${id}）` : title
    })
    .join('；')
}

const CSV_HEADERS = [
  '编号', '标题', '描述', 'CVSS', '严重度', 'AI 分类', 'AI 相关度', 'AI 判定方式',
  '风险等级', '风险优先级', '富化状态', '来源', '发布时间', '采集延迟(秒)', '原文链接',
  '关联资产', 'POC', 'MITRE 技术', '修复建议', '相关论文',
]

const downloadCsv = (name: string, rows: string[][]) => {
  const csv = '\uFEFF' + rows.map((r) => r.map(csvEscape).join(',')).join('\r\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

const handleExportCsv = async () => {
  // 筛选快照：导出开始后的筛选变化不影响本次
  const filters = {
    ai_only: aiOnly,
    category,
    severity,
    keyword: keyword || undefined,
  }
  try {
    setExporting(true)
    message.loading({ content: '正在准备导出…', key: 'export', duration: 0 })
    // 第一页拿 total
    const first = await api.vulns({
      ...filters,
      limit: EXPORT_PAGE,
      offset: 0,
      include_extra: true,
    })
    const total = first.total
    if (!total) {
      message.warning('当前筛选没有数据，无可导出', 3)
      return
    }
    const all: ExportItem[] = [...(first.items as ExportItem[])]
    // 循环拉余下页（后端单次上限 500）
    for (let offset = EXPORT_PAGE; offset < total; offset += EXPORT_PAGE) {
      message.loading({
        content: `导出中 ${all.length}/${total}…`,
        key: 'export',
        duration: 0,
      })
      const r = await api.vulns({ ...filters, limit: EXPORT_PAGE, offset, include_extra: true })
      all.push(...(r.items as ExportItem[]))
    }
    // 行映射（21 列）
    const rows = all.map((it) => {
      // ⚠️ enrichment 是 item 的顶层字段（后端 include_extra 聚合返回），
      // 不在 extra 里 —— 之前在 extra.enrichment 里找，永远空（导出富化列全空的 bug）
      const dims = (it.enrichment ?? {}) as Record<string, Record<string, unknown>>
      const row: string[] = [
        it.cve_id,
        it.title,
        it.description ?? '',
        String(it.cvss_score ?? ''),
        it.severity ?? '',
        it.ai_category ?? '',
        String(it.ai_relevance ?? ''),
        it.ai_method ?? '',
        it.risk_level ?? '',
        it.risk_priority ?? '',
        it.enrichment_status ?? '',
        it.source ?? '',
        it.published_at ?? '',
        String(it.latency_seconds ?? ''),
        it.url ?? '',
        fmtAsset(dims),
        fmtPoc(dims),
        fmtTechnique(dims),
        fmtFix(dims),
        fmtPaper(dims),
      ]
      return row
    })
    // 分片下载（1 万行/文件）
    const parts = Math.ceil(rows.length / EXPORT_ROWS_PER_FILE)
    const stamp = dayjs().format('YYYYMMDD_HHmm')
    for (let p = 0; p < parts; p++) {
      const slice = rows.slice(p * EXPORT_ROWS_PER_FILE, (p + 1) * EXPORT_ROWS_PER_FILE)
      downloadCsv(`情报导出_${stamp}${parts > 1 ? `_part${p + 1}` : ''}.csv`, [
        CSV_HEADERS,
        ...slice,
      ])
      // 连续多文件下载：留间隔避免被浏览器拦截
      if (p < parts - 1) {
        await new Promise((r) => setTimeout(r, 400))
      }
    }
    message.success(`导出完成：${rows.length} 条，${parts} 个 CSV 文件`)
  } catch (e) {
    message.error('导出失败：' + (e as Error).message)
  } finally {
    setExporting(false)
    message.destroy('export')
  }
}

const runBatchEnrich = async () => {
    setBatchRunning(true)
    message.loading({ content: '批量富化中，请稍候…', duration: 0 })
    try {
      const r = await api.enrichBatch({ limit: 20, ai_only: true, concurrency: 3 })
      message.success(
        `批量富化完成：${r.complete_enriched}/${r.total} 条达到 80% 完整度（${r.completeness_rate}%）`,
      )
      await load()
    } catch (e) {
      message.error('批量富化失败：' + (e as Error).message)
    } finally {
      message.destroy()
      setBatchRunning(false)
    }
  }

  const load = async () => {
    setLoading(true)
    try {
      const r = await api.vulns({
        ai_only: aiOnly,
        category,
        severity,
        keyword: keyword || undefined,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      })
      setItems(r.items)
      setTotal(r.total)
    } catch (e) {
      message.error('加载失败：' + (e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aiOnly, category, severity, page])

  /**
   * 拉取论文列表
   *
   * 与 load 分开：主表是「筛选 + 分页」，论文卡是独立的检索区，
   * 两者的触发条件与页码互不干扰，合成一个函数会让 state 纠缠。
   *
   * @param topicOverride 主题筛选。之所以允许传参而不直接读 state：
   *   在 onChange 里 setState 是异步的，此刻 state 还是旧值，
   *   直接读会拿到上一次的 topic 导致筛选不生效。
   * @param pageOverride 目标页码。同理，setState 后立刻要用新页码
   *   就必须走参数，不能读 state。
   *
   * ⚠ offset 必须传，否则翻页会拿回同一批数据
   *（曾出现过：页码从 1 变 2，内容却完全一样）。
   */
  const loadPapers = async (topicOverride?: string, pageOverride?: number) => {
    const t = topicOverride !== undefined ? topicOverride : paperTopic
    const p = pageOverride !== undefined ? pageOverride : paperPage
    // 标记本次请求序号；过期响应会被下面的比对丢弃
    const reqId = ++paperReqId.current
    try {
      const r = await api.papers(
        paperPageSize,
        paperKeyword || undefined,
        t || undefined,
        (p - 1) * paperPageSize,
      )
      // 已有更新的请求发出 → 本次是过期响应，直接丢弃
      if (reqId !== paperReqId.current) return
      setPapers(r.items)
      setKbTotal((s) => ({ ...s, papers: r.total }))
    } catch {
      // 静默降级：论文区挂掉不应影响整页
    }
  }

  useEffect(() => {
    void api.stats().then(setStats).catch(() => undefined)
    // 赛题三类知识底座：展示当前页数据，同时把接口返回的真实总量记下来，
    // 供卡片头部显示「共 N 条」—— 避免用 items.length 把展示条数当总量。
    void loadPapers()
    // ATLAS 技术：拉全量（170项）用于按战术分组。
    // 原来只取 6 条平铺，用户看到的是无上下文的技术名列表。
    void api
      .techniques(200)
      .then((r) => {
        setTechs(r.items)
        setKbTotal((s) => ({ ...s, techniques: r.total }))
      })
      .catch(() => undefined)
    // 政策：拉全量（22 条）。原来只取 9 条铺三列，
    // 「共 22 条」与能看到的 9 条对不上——与论文卡是同一类问题。
    void api
      .policies(50, policyKind)
      .then((r) => {
        setPolicies(r.items)
        setKbTotal((s) => ({ ...s, policies: r.total }))
      })
      .catch(() => undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const categoryOptions = useMemo(
     () => Object.keys(stats?.by_ai_category ?? {}).map((c) => ({ label: c, value: c })),
    [stats],
  )

  /**
   * 论文主题选项
   *
   * 从**当前已加载的论文**里统计，而非另调接口 ——
   * 数据量小（每页 20 条覆盖了绝大多数热门主题），
   * 多一次请求不划算。按出现频次排序取前 8。
   */
  const paperTopics = useMemo(() => {
    const counter = new Map<string, number>
    for (const p of papers) {
      for (const t of p.ai_topics ?? []) {
        counter.set(t, (counter.get(t) ?? 0) + 1)
      }
    }
    return [...counter.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
      .map(([t]) => t)
  }, [papers])

  /**
   * 政策属性下拉的选项：**固定从全量数据统计，不随筛选变化**。
   *
   * ---------- 为什么不能从 policies 统计 ----------
   * `policies` 是**筛选后**的结果。选中「法规」后，数据里只剩法规，
   * 于是下拉里其它属性全部消失 —— 用户再也换不回去
   *（这正是用户反馈的 bug：筛选一次就锁死了）。
   *
   * 正确做法：单独拉一次全量数据只用于提取选项，
   * 让「选项」与「当前筛选结果」彻底解耦。
   */
  const [policyKindOptions, setPolicyKindOptions] = useState<string[]>([])

  useEffect(() => {
    void api
      .policies(200)
      .then((r) => {
        const set = new Set<string>
        for (const p of r.items) {
          if (p.policy_kind) set.add(p.policy_kind)
        }
        setPolicyKindOptions([...set])
      })
      .catch(() => undefined)
  }, [])

  /**
   * 知识底座新鲜度：条数 + 最近采集时间
   *
   * ---------- 为什么要独立轮询，而不是复用列表接口 ----------
   * 列表接口（/api/papers 等）为分页设计，条数藏在响应里，
   * 且**只在用户搜索/翻页时才请求** —— 采集在后台完成后，
   * 页面数字不会自己变。用户会以为「采集没跑」。
   *
   * 这里用一个轻量接口独立轮询：条数与「何时采的」都能自动刷新，
   * 让「自动化」这件事**肉眼可见** —— 这对答辩是加分项。
   */
  const [freshness, setFreshness] = useState<Record<string, KbFreshnessItem>>({})
  const [pulse, setPulse] = useState<Record<string, boolean>>({})

  const loadFreshness = useCallback(async () => {
    try {
      const r = await api.kbFreshness()
      const map: Record<string, KbFreshnessItem> = {}
      for (const it of r.items) map[it.basin] = it
      // 对比条数变化，刚变过的底座打一个高亮脉冲（3秒后自动消退）
      setFreshness((prev) => {
        const next: Record<string, boolean> = {}
        for (const it of r.items) {
          const before = prev[it.basin]?.total
          next[it.basin] = before !== undefined && before !== it.total
        }
        setPulse(next)
        return map
      })
      // 同步真实总量，避免列表接口与新鲜度接口口径不一致
      setKbTotal({
        papers: r.items.find((i) => i.basin === 'papers')?.total ?? 0,
        techniques: r.items.find((i) => i.basin === 'techniques')?.total ?? 0,
        policies: r.items.find((i) => i.basin === 'policies')?.total ?? 0,
      })
    } catch {
      // 静默降级：新鲜度是辅助信息，取不到不该影响主流程
    }
  }, [])

  // 首屏加载 + 每 30 秒轮询一次。
  // 30s 是权衡结果：调度器最短间隔 1 小时，30s 足以在一次采集
  // 完成后很快反映出来，又不至于给后端造成可忽略的压力。
  useEffect(() => {
    void loadFreshness()
    const timer = setInterval(() => void loadFreshness(), 30_000)
    return () => clearInterval(timer)
  }, [loadFreshness])

  // 脉冲高亮 3 秒后自动消退
  useEffect(() => {
    if (!Object.values(pulse).some(Boolean)) return
    const t = setTimeout(
       () => setPulse({}),
      3000,
    )
    return () => clearTimeout(t)
  }, [pulse])

  /**
   * ATLAS 技术 → 按战术（攻击链阶段）分组
   *
   * ATLAS 的 tactic 编号本身就是攻击推进的时序
   * （TA0002 侦察 → TA0003 资源开发 → … → TA0014 影响），
   * 所以**按编号排序即攻击链顺序**，用户顺着读就能理解
   * 「AI 攻击是怎么一步步发生的」。
   *
   * 分组而非平铺的理由：平铺 170 条会让「侦察」和「外泄」
   * 这些阶段性概念消失在一堆技术名里；分组后每个阶段
   * 成为一个可读的语义块。
   *
   * 子技术（ID 带 .000 后缀）继承父技术的战术 —— 库里
   * 子技术的 tactic 字段为空，需从 ID 前缀反查父技术所属战术。
   */
  const techGroups = useMemo(() => {
    // 父技术 ID → 战术，用于给子技术补战术
    const parentTactic = new Map<string, string>
    for (const t of techs) {
      if (t.tactic && !t.atlas_id.includes('.')) {
        parentTactic.set(t.atlas_id, t.tactic)
      }
    }
    const tacticOf = (t: TechniqueItem): string | null => {
      if (t.tactic) return t.tactic
      // 子技术：从自身 ID 去掉最后一段后缀找父技术
      const parentId = t.atlas_id.split('.').slice(0, 2).join('.')
      return parentTactic.get(parentId) ?? null
    }
    // Map 保持插入顺序
    const groups = new Map<string, TechniqueItem[]>
    for (const t of techs) {
      const tac = tacticOf(t)
      if (!tac) continue // 找不到战术的条目不展示，避免出现无归属的孤行
      if (!groups.has(tac)) groups.set(tac, [])
      groups.get(tac)!.push(t)
    }
    /**
     * 显式按编号数字段排序，不依赖 localeCompare。
     * 「攻击链顺序」是本卡片的核心语义，必须由代码保证 ——
     * 一旦排序结果受运行环境 locale 影响，攻击链就乱了。
     *
     * ⚠ 之前写的是 `Number(t.split('.').pop)`，但末段是**带字母的**
     * `TA0002` 而不是 `0002` → `Number('TA0002')` 是 **NaN**，
     * 而 `NaN` 参与比较恒为false → **sort 的比较函数完全失效**，
     * 数组保持插入顺序（依赖后端返回顺序），攻击链看起来是乱的。
     * 这是个「不报错、结果错」的隐性 bug。
     * 正确做法：先剥掉字母前缀再转数字。
     */
    const tacticOrder = (t: string) =>
      Number(String(t).split('.').pop()?.replace(/[^0-9]/g, '') ?? 0)
    return [...groups.entries()]
      .sort(([a], [b]) => tacticOrder(a) - tacticOrder(b))
      .map(([tactic, items]) => ({
        tactic,
        tacticZh: tacticZh(tactic),
        phase: TACTIC_PHASE[tactic] ?? 'prepare',
        // 组内也按编号排：父技术永远紧邻其子技术
        items: items
          .slice()
          .sort((x, y) => x.atlas_id.localeCompare(y.atlas_id))
          .map((t) => ({ ...t, isSub: t.atlas_id.includes('.') })),
      }))
  }, [techs])

  return (
    <div>
      {/* ---- 筛选栏 ---- */}
      <Card size="small" style={{ marginBottom: 12 }}>
        <Row gutter={[10, 10]} align="middle">
          <Col xs={24} md={8}>
            <Input
              placeholder="搜索漏洞标题…"
              prefix={<SearchOutlined />}
              allowClear
              /* 草稿态输入：只有回车/点搜索才写进 URL——
                 每输入一个字都写会让浏览器后退历史被逐字淹没。*/
              value={keywordDraft}
              onChange={(e) => setKeywordDraft(e.target.value)}
              onPressEnter={() => {
                commitQuery({ q: keywordDraft })
                void load()
              }}
              onBlur={() => {
                // 离开输入框时同步一次，保证 URL 与界面一致
                if (keywordDraft !== keyword) {
                  commitQuery({ q: keywordDraft })
                  void load()
                }
              }}
            />
          </Col>
          <Col xs={12} md={5}>
            <Select
              allowClear
              placeholder="AI 分类"
              style={{ width: '100%' }}
              value={category}
              options={categoryOptions}
              /* 不需要再跟 setPage(1)：commitQuery 对非翻页的变化
                 会自动重置页码。连写两次反而会用旧 params 覆盖前一次。 */
              onChange={(v) => setCategory(v)}
              showSearch
            />
          </Col>
          <Col xs={12} md={4}>
            <Select
              allowClear
              placeholder="严重程度"
              style={{ width: '100%' }}
              value={severity}
              options={['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map((s) => ({ label: s, value: s }))}
              onChange={(v) => setSeverity(v)}
            />
          </Col>
          <Col xs={24} md={7}>
            <Space>
              <Switch checked={aiOnly} onChange={setAiOnly} size="small" />
              <Text type="secondary" style={{ fontSize: 13 }}>
                仅看 AI 安全相关
              </Text>
              <Text type="secondary" style={{ fontSize: 12, marginLeft: 'auto' }}>
                共 {total} 条
              </Text>
              <Popconfirm
                title="批量富化"
                description="将对前 20 条 AI 安全漏洞执行 7 维富化，约需 3-4 分钟。"
                okText="开始"
                cancelText="取消"
                onConfirm={() => void runBatchEnrich()}
              >
                <Button size="small" icon={<ThunderboltOutlined />} loading={batchRunning}>
                  批量富化
                </Button>
              </Popconfirm>
              <Button
                size="small"
                icon={<DownloadOutlined />}
                loading={exporting}
                onClick={() => void handleExportCsv()}
                title="按当前筛选导出全部为 CSV"
              >
                导出 CSV
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      {/* ---- 情报表格 ---- */}
      {/* 用 div 包一层而不是给 Card 挂 ref：AntD Card 不透传 ref，
          而 useInnerScrollRef 需要一个真实 DOM 节点来 querySelector
          内部的 .ant-table-content。 */}
      <div ref={tableWrapRef} className="intel-vuln-wrap">
        <Card size="small" className="intel-vuln-card">
        <Table
          size="small"
          rowKey="cve_id"
          loading={loading}
          dataSource={items}
          // 逐行错峰滑入：把行号写进 --row-i，CSS 据此算animation-delay。
          // 只对前 12 行生效——再多的行延迟过长会显得卡顿。
          // index 在类型上可能为 undefined，故用 ?? 0兜底。
          rowClassName={(_, index) =>
            index !== undefined && index < 12 ? 'sentinel-row-in' : ''
          }
          onRow={(_, index) =>
            index !== undefined && index < 12
              ? ({ style: { '--row-i': index } } as never)
              : {}
          }
          /**
           * `scroll.x` 是**窄屏兜底**：11 列固定宽合计约 1070px，
           * 视口窄于此值时必须能横向滚动，否则列会被挤扁、标题换行错乱。
           *
           * 横向滚动条的**常驻显示**由 CSS 完成
           * （`.intel-vuln-table` 把 overflow-x 从 auto 提为 scroll）。
           * 原因：AntD 用 `overflow: auto`，浏览器会按系统设置
           * 「自动隐藏滚动条，悬停才显示」—— 用户反馈「时隐时现」。
           * `scroll` 强制轨道常驻，不再依赖悬停。
           */
          scroll={{ x: 1180 }}
          className="intel-vuln-table"
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: false,
            showTotal: (t) => `共 ${t} 条`,
          }}
          onChange={(p) => setPage(p.current ?? 1)}
          columns={[
            {
              title: '编号',
              dataIndex: 'cve_id',
              width: 150,
              render: (v: string, r: VulnItem) => (
                <a href={r.url ?? '#'} target="_blank" rel="noreferrer">
                  {v}
                </a>
              ),
            },
            {
              title: '描述',
              dataIndex: 'title',
              ellipsis: true,
              render: (v: string) => {
                /* 标注「重复公告」。
                 *
                 * GitHub 会对同一漏洞发多条公告，其中一条标题以
                 * "Duplicate Advisory:" 开头，并在 references 里引用正式编号。
                 * 搜索覆盖 references 后，搜某个 CVE 会同时命中它 ——
                 * 看起来像「同一个编号出现了两条」。数据本身没错
                 * （那条公告真实存在），故只标注、不过滤。
                 *
                 * 标签放在文字**前面**：本列是单行截断的，
                 * 放句尾时会被长标题一起截掉，等于没标。 */
                const dup = /^duplicate advisory:\s*/i.exec(v ?? '')
                if (!dup) {
                  return (
                    <Tooltip title={v}>
                      <span style={{ fontSize: 13 }}>{v}</span>
                    </Tooltip>
                  )
                }
                return (
                  <Tooltip title={v}>
                    <span style={{ fontSize: 13 }}>
                      <Tag color="orange" style={{ fontSize: 11, marginInlineEnd: 6 }}>
                        重复公告
                      </Tag>
                      {v.slice(dup[0].length)}
                    </span>
                  </Tooltip>
                )
              },
            },
            {
              title: 'CVSS',
              dataIndex: 'cvss_score',
              width: 76,
              align: 'right' as const,
              render: (v: number | null) => (v ? <b>{v.toFixed(1)}</b> : <Text type="secondary">—</Text>),
            },
            {
              title: '严重度',
              dataIndex: 'severity',
              width: 92,
              render: (v: string | null) =>
                v ? (
                  <Tag className={SEVERITY_CLASS[v] ?? ''} color={SEVERITY_TAG[v] ?? 'default'}>
                    {v}
                  </Tag>
                ) : (
                  <Text type="secondary">—</Text>
                ),
            },
            {
              title: 'AI 分类',
              dataIndex: 'ai_category',
              width: 170,
              render: (v: string | null, r: VulnItem) =>
                v ? (
                  <Tag
                    className={CATEGORY_CLASS[v] ?? ''}
                    color={CATEGORY_COLOR[v] ?? 'default'}
                    style={{ fontSize: 11 }}
                  >
                    {v}
                  </Tag>
                ) : r.ai_relevance > 0 ? (
                  <Tag>待复核</Tag>
                ) : (
                  <Text type="secondary">—</Text>
                ),
            },
            {
              title: '相关度',
              dataIndex: 'ai_relevance',
              width: 84,
              align: 'right' as const,
              render: (v: number, r: VulnItem) => (
                <Tooltip title={`判定方式：${r.ai_method ?? '—'}`}>
                  <Text
                    type={v >= 0.8 ? 'success' : v >= 0.5 ? 'warning' : 'secondary'}
                    style={{ fontSize: 12 }}
                  >
                    {v.toFixed(2)}
                  </Text>
                </Tooltip>
              ),
            },
            {
              title: '风险',
              dataIndex: 'risk_priority',
              width: 96,
              render: (v: string | null, r: VulnItem) =>
                v ? (
                  <Tooltip title={`风险等级：${r.risk_level ?? '—'}`}>
                    <Space size={4}>
                      <Tag
                        className={PRIORITY_CLASS[v] ?? ''}
                        color={PRIORITY_COLOR[v] ?? 'default'}
                        style={{ fontSize: 11 }}
                      >
                        {v}
                      </Tag>
                    </Space>
                  </Tooltip>
                ) : (
                  <Text type="secondary">—</Text>
                ),
            },
            {
              title: '富化',
              dataIndex: 'enrichment_status',
              width: 92,
              render: (v: string, r: VulnItem) =>
                v === 'completed' ? (
                  <Button
                    size="small"
                    type="link"
                    style={{ padding: '0 4px' }}
                    onClick={() => void openEnrichment(r.cve_id)}
                  >
                    查看详情
                  </Button>
                ) : (
                  <Button
                    size="small"
                    type="link"
                    loading={enriching === r.cve_id}
                    style={{ padding: '0 4px' }}
                    onClick={() => void runEnrich(r.cve_id)}
                  >
                    富化
                  </Button>
                ),
            },
            {
              title: '来源',
              dataIndex: 'source',
              width: 100,
              render: (v: string) => (
                <Tag style={{ fontSize: 11 }}>{v}</Tag>
              ),
            },
            {
              title: '采集延迟',
              dataIndex: 'latency_seconds',
              width: 100,
              align: 'right' as const,
              render: (v: number | null) => (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {formatDuration(v)}
                </Text>
              ),
            },
            {
              title: '发布时间',
              dataIndex: 'published_at',
              width: 110,
              render: (v: string | null) => (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {v ? dayjs(v).format('YYYY-MM-DD') : '—'}
                </Text>
              ),
            },
          ]}
        />
        {/* 主表的横向滚动条同样自绘 —— 系统自动隐藏会让它一闪一闪。
            AntD 内部的滚动容器（.ant-table-content）不是我们渲染的，
            所以用绑定式：拿 ref 找到它，把滑块覆盖上去。 */}
        <CustomScrollbar targetRef={tableScrollRef} dir="x" />
        </Card>
      </div>

      {/* ---- 论文与技术 ---- */}
      {/* 等高：论文与 ATLAS 两卡条目数不同、行高也不同，
          固定 height 会错位。由 CSS 统一拉伸对齐。 */}
      <Row gutter={[12, 12]} style={{ marginTop: 12 }} align="stretch">
        <Col xs={24} lg={12} className="sentinel-equal-col">
          <Card
            className="sentinel-equal-card"
            title="AI 安全学术论文"
            size="small"
            extra={
              <Space size={8} style={{ fontSize: 12 }}>
                <Text type="secondary">arXiv · 共 {kbTotal.papers} 篇</Text>
                <FreshnessBadge
                  item={freshness.papers}
                  justUpdated={!!pulse.papers}
                />
              </Space>
            }
          >
            {/* 检索区：论文已有 205 篇，只给「最新 N 条」等于
                把199 篇藏起来，用户既找不到也看不出有多少。
                给出搜索 + 主题过滤 + 可滚动全量列表。 */}
            <div className="intel-paper-filter">
              <Input
                size="small"
                allowClear
                placeholder="搜索标题或作者…"
                prefix={<SearchOutlined />}
                value={paperKeyword}
                onChange={(e) => {
                  setPaperKeyword(e.target.value)
                  setPaperPage(1)
                }}
                onPressEnter={() => {
                  setPaperPage(1)
                  // 必须显式传 1：setState 异步，此刻 paperPage 仍是旧值
                  void loadPapers(undefined, 1)
                }}
              />
              {/* 主题标签来自真实数据分布（RAG 安全 60 / 数据投毒 57 /
                  提示词注入 45 …），按热度取前 8 个作为快捷入口。 */}
              <Select
                size="small"
                allowClear
                placeholder="按主题筛选"
                style={{ width: 132, flexShrink: 0 }}
                value={paperTopic}
                onChange={(v) => {
                  setPaperTopic(v)
                  setPaperPage(1)
                  void loadPapers(v, 1)
                }}
                options={paperTopics.map((t) => ({ label: t, value: t }))}
              />
            </div>

            {papers.length === 0 ? (
              <Text type="secondary">
                {paperKeyword || paperTopic ? '没有匹配的论文，试试换个关键词' : '暂无数据'}
              </Text>
            ) : (
              <>
                <CustomScrollArea dir="y" className="intel-paper-list">
                  {papers.map((p) => (
                    <div key={p.arxiv_id} className="intel-paper-item">
                      {/* 时间与标题同一行右侧：读者的视线在标题上，
                          时间只需「可扫到」而非「被阅读」。 */}
                      <div className="intel-paper-head">
                        <a
                          href={p.pdf_url ?? '#'}
                          target="_blank"
                          rel="noreferrer"
                          className="intel-paper-title"
                        >
                          {p.title}
                        </a>
                        <Tooltip title={dayjs(p.published_at).format('YYYY-MM-DD HH:mm:ss')}>
                          <Text type="secondary" className="intel-paper-time">
                            {freshLabel(p.published_at)}
                          </Text>
                        </Tooltip>
                      </div>
                      {p.ai_topics && p.ai_topics.length > 0 && (
                        <div className="intel-paper-topics">
                          {p.ai_topics.slice(0, 3).map((t) => (
                            <Tag
                              key={t}
                              className="intel-paper-topic"
                              onClick={() => {
                                setPaperTopic(t)
                                setPaperPage(1)
                                void loadPapers(t, 1)
                              }}
                            >
                              {t}
                            </Tag>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </CustomScrollArea>
                {/* 分页：205 篇不可能一次铺完，滚动 + 分页是必要的。
                    每页 20 条，与主表 pageSize 一致。 */}
                {kbTotal.papers > paperPageSize && (
                  <div className="intel-paper-pager">
                    <Pagination
                      size="small"
                      simple
                      current={paperPage}
                      pageSize={paperPageSize}
                      total={kbTotal.papers}
                      onChange={(p) => {
                        setPaperPage(p)
                        // 必须显式传目标页 p，否则读到的还是旧页码
                        void loadPapers(undefined, p)
                      }}
                    />
                  </div>
                )}
              </>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={12} className="sentinel-equal-col">
          <Card
            className="sentinel-equal-card"
            title="MITRE ATLAS 攻击链"
            size="small"
            extra={
              <Space size={8} style={{ fontSize: 12 }}>
                <Text type="secondary">AI 攻击基线 · 共 {kbTotal.techniques} 项</Text>
                <FreshnessBadge
                  item={freshness.techniques}
                  justUpdated={!!pulse.techniques}
                />
              </Space>
            }
          >
            <div className="atlas-intro">
              MITRE ATLAS 是专门针对 <b>AI 系统</b> 的攻击知识库。
              下面按<b>攻击链顺序</b>列出攻击者从侦察到造成影响的完整流程——
              编号即推进顺序，每一项都是 AI 攻击者真实使用的手段。
            </div>
            {techGroups.length === 0 ? (
              <Text type="secondary">暂无数据</Text>
            ) : (
              /*按攻击链分组呈现。
                原来是把 6 条技术平铺，用户看到的是一串无解释的
                英文名 + 编号，完全读不出「这是什么、怎么发生的」。
                改为按 ATLAS 战术（即攻击链阶段）分组后，
                顺着看下去就是一条完整的 AI 攻击叙事：
                侦察 → 资源开发 → 初始访问 → 执行 → …… → 外泄 → 影响。 */
              <CustomScrollArea dir="y" className="atlas-chain">
                {techGroups.map((g) => (
                  <div key={g.tactic} className="atlas-chain-group">
                    <div className="atlas-chain-head">
                      {/* 阶段色块：用颜色区分准备/渗透/收割三段 */}
                      <span className={`atlas-chain-dot atlas-phase-${g.phase}`} />
                      <Text strong style={{ fontSize: 13 }}>
                        {g.tacticZh}
                      </Text>
                      <Tooltip title={`${g.tactic} · ${g.items.length} 项技术`}>
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          {g.tactic} · {g.items.length} 项
                        </Text>
                      </Tooltip>
                    </div>
                    <div className="atlas-chain-items">
                      {g.items.map((t) => (
                        <div key={t.atlas_id} className="atlas-chain-item">
                          <Tag className="atlas-id-tag">{t.atlas_id}</Tag>
                          <Tooltip
                            title={
                              <div>
                                <div style={{ marginBottom: 2 }}>{t.name}</div>
                                {t.description && (
                                  <div style={{ fontSize: 11, opacity: 0.8 }}>
                                    {t.description}
                                  </div>
                                )}
                                <div style={{ fontSize: 11, opacity: 0.7, marginTop: 2 }}>
                                  成熟度：{MATURITY[t.maturity ?? '']?.label ?? t.maturity ?? '—'}
                                </div>
                              </div>
                            }
                          >
                            {/* 父技术用白色、子技术用次级色 —— 视觉上直接分出层级 */}
                            <span className={t.isSub ? 'atlas-tech-sub' : 'atlas-tech-main'}>
                              {techZh(t.atlas_id, t.name)}
                            </span>
                          </Tooltip>
                          {/* 成熟度：把「这招是否已被真实观测到」直接标出来，
                              这是 ATLAS 作为威胁库最有价值的判断依据。 */}
                          {t.maturity && MATURITY[t.maturity] && (
                            <span
                              className={`atlas-maturity ${MATURITY[t.maturity].cls}`}
                              title={`成熟度：${MATURITY[t.maturity].label}`}
                            >
                              {MATURITY[t.maturity].label}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </CustomScrollArea>
            )}
          </Card>
        </Col>
      </Row>

      {/* ---- 政策法规（赛题赛题三类知识底座之一）---- */}
      <Row gutter={[12, 12]} style={{ marginTop: 12 }}>
        <Col xs={24} className="sentinel-equal-col">
          <Card
            className="sentinel-equal-card"
            title="AI 安全政策法规"
            size="small"
            extra={
              <Space size={8} style={{ fontSize: 12 }}>
                <Text type="secondary">共 {kbTotal.policies} 条</Text>
                <FreshnessBadge
                  item={freshness.policies}
                  justUpdated={!!pulse.policies}
                />
              </Space>
            }
          >
            {/* 与论文卡同理：给出筛选入口，让「共 22 条」条条可达。
                政策的自然维度是「属性」——政策/法规/标准/观点。 */}
            {policyKindOptions.length > 1 && (
              <div className="intel-paper-filter">
                <Select
                  size="small"
                  allowClear
                  placeholder="按属性筛选"
                  style={{ width: 132 }}
                  value={policyKind}
                  onChange={(v) => {
                    setPolicyKind(v)
                    void api
                      .policies(50, v)
                      .then((r) => {
                        setPolicies(r.items)
                        setKbTotal((s) => ({ ...s, policies: r.total }))
                      })
                      .catch(() => undefined)
                  }}
                  options={policyKindOptions.map((k) => ({ label: k, value: k }))}
                />
              </div>
            )}
            {policies.length === 0 ? (
              <Text type="secondary">
                {policyKind
                  ? '该属性下暂无数据'
                  : '暂无数据。可在监控看板点击「AI 安全政策法规」源的采集按钮手动拉取。'}
              </Text>
            ) : (
              <CustomScrollArea dir="y" className="intel-policy-list">
                <Row gutter={[10, 10]}>
                  {policies.map((p) => (
                    <Col key={p.doc_key} xs={24} md={12} lg={8}>
                      <div className="intel-policy-card">
                      {/* 标题与时间同行：时间右对齐但不抢视线，
                          与论文卡保持一致的读法。 */}
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'baseline',
                          gap: 8,
                          marginBottom: 4,
                        }}
                      >
                        <Space size={4} wrap style={{ flex: 1, minWidth: 0 }}>
                          {p.policy_kind && (
                            <Tag color="gold" style={{ fontSize: 11, margin: 0 }}>
                              {p.policy_kind}
                            </Tag>
                          )}
                          {p.org && (
                            <Tag style={{ fontSize: 11, margin: 0 }}>{p.org}</Tag>
                          )}
                          {p.region && (
                            <Tag color="cyan" style={{ fontSize: 11, margin: 0 }}>
                              {p.region}
                            </Tag>
                          )}
                        </Space>
                        <Tooltip
                          title={
                            p.published_at
                              ? dayjs(p.published_at).format('YYYY-MM-DD HH:mm:ss')
                              : '该来源未提供发布时间'
                          }
                        >
                          <Text
                            type="secondary"
                            style={{ fontSize: 11, whiteSpace: 'nowrap', flexShrink: 0 }}
                          >
                            {freshLabel(p.published_at)}
                          </Text>
                        </Tooltip>
                      </div>
                      <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>
                        {p.source_url ? (
                          <a href={p.source_url} target="_blank" rel="noreferrer">
                            {p.title.length > 78 ? p.title.slice(0, 78) + '…' : p.title}
                          </a>
                        ) : (
                          <Text>{p.title}</Text>
                        )}
                      </div>
                      {p.doc_numbers.length > 0 && (
                        <div style={{ marginTop: 4 }}>
                          {p.doc_numbers.slice(0, 2).map((n) => (
                            <Tag
                              key={n}
                              color="green"
                              style={{ fontSize: 11, marginRight: 4 }}
                            >
                              {n}
                            </Tag>
                          ))}
                        </div>
                      )}
                    </div>
                  </Col>
                  ))}
                </Row>
              </CustomScrollArea>
            )}
          </Card>
        </Col>
      </Row>
      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        width={560}
        title={
          <div style={{ fontSize: 14 }}>
            7 维富化详情
            {enrichData && (
              <Tag color="blue" style={{ marginLeft: 8, fontSize: 11 }}>
                {enrichData.cve_id}
              </Tag>
            )}
          </div>
        }
      >
        <EnrichmentPanel data={enrichData} loading={enrichLoading} />
      </Drawer>
    </div>
  )
}
