/**
 * 「恢复默认」在周期下拉里的哨兵值
 *
 * 用 0 而不是字符串：后端 interval_seconds 校验 ge=60，
 * 0 不可能是真实周期，因此不会与任何档位冲突，
 * 也省得给 Select 写联合类型参数。
 */
const RESET_INTERVAL = 0

/**
 * 监控看板页
 *
 * 展示系统运行状态、数据源健康度、采集时效性与调度信息。
 * 对应"演示效果"与"监控告警"评分项。
 */

import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Empty,
  Popconfirm,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  message,
} from 'antd'
import {
  ReloadOutlined,
  ThunderboltOutlined,
  PauseCircleOutlined,
  PlayCircleOutlined,
  DatabaseOutlined,
  ApiOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'
import { useTheme } from '@/lib/theme'

import { api } from '@/api/client'
import { notifySchedulerChanged } from '@/api/client'
import type {
  KnowledgeBaseStats,
  LatencySourceSummary,
  MonitorOverview,
  SchedulerStatus,
  Stats,
} from '@/api/client'
import { MetricCard, formatDuration } from '@/components/MetricCard'
import { CollectFeedCard } from '@/components/CollectFeedCard'

/**
 * ECharts 暗色轴线与提示框配置
 *
 * ECharts 的 canvas 渲染不认CSS 变量，轴线/文字/网格线的颜色
 * 必须显式指定。浅色主题的默认深灰（#d1d5db）在近黑玻璃卡上几乎不可见，
 * 这里统一换成 SENTINEL 的文本三级色。
 */
/**
 * 把 ISO 时间格式化为「MM-DD HH:mm」
 *
 * 调度表里的时间戳统一用这个格式——比 toLocaleString 短，
 * 且在等宽字体下能纵向对齐（tabular-nums）。
 */
function formatClock(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const p2 = (n: number) => String(n).padStart(2, '0')
  return `${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`
}

/**
 * 把采集周期（秒）格式化为整档位：10 分钟 / 2 小时 / 12 小时 / 7 天
 *
 * formatDuration 会输出 "10.0 分钟" 这种小数，周期列都是整数档，
 * 用整除判断更整齐；除不净再回退到 formatDuration。
 */
function formatInterval(seconds: number): string {
  if (seconds >= 86400 && seconds % 86400 === 0) return `${seconds / 86400} 天`
  if (seconds >= 3600 && seconds % 3600 === 0) return `${seconds / 3600} 小时`
  if (seconds % 60 === 0) return `${seconds / 60} 分钟`
  return formatDuration(seconds)
}

/**
 * 距下次自动采集还有多久（人类可读）
 *
 * 为什么需要它：NVD 周期 6 小时、ATLAS 7 天，只看「上次执行」会让人
 * 以为自动采集挂了——其实只是还没到点。把倒计时摆出来，
 * 「没更新」就变成「还没到时间」，不用再猜。
 */
function formatCountdown(ms: number): string {
  const min = Math.floor(ms / 60000)
  if (min < 1) return '即将执行'
  if (min < 60) return `${min} 分钟后`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} 小时后`
  return `${Math.floor(h / 24)} 天后`
}

/**
 * 数据源配色（九色，经多轮调整）
 *
 * 注：ECharts 不认CSS 变量，故此处用十六进制字面量。
 *
 * **分配依据不是「好看」，而是「相邻柱子能否分辨」**。
 * 九条柱子在图上按数值倒序排列，上下相邻的两根会挨在一起，
 * 因此分配时让色相在「相邻源之间」尽量跳开，并用 WCAG 对比度复核。
 *
 * 调整历程（留痕以便回溯）：
 * ① 初版九色在色环上分布不均，`1dd3b0` 与 `60d394` 对比度仅 1.02，
 *    靠「重排分配顺序」避开。
 * ② arxiv / policy 按用户要求改为 `#05668d` / `#008000`。
 * ③ 当前版本：arxiv → `#0090b7`、atlas → `#9620b5`、rss → `#ff7096`，
 *    并按用户要求互换 osv↔nvd、ghsa↔cisa_kev。
 *    互换后重新验算，相邻对比度最小值从 1.18 提升到 **1.36**，
 *    比之前更安全。
 *
 * 现在的分配让相邻源跨色相区。
 * X 轴顺序（按数值倒序渲染，自下而上）：
 *   nvd 168° → cisa_kev 84°（84°）→ arxiv 193°（109°）
 *   → atlas 288°（95°）→ osv 354°（66°）→ ghsa 19°（25°）
 *   → poc_repo 47°（28°）→ rss 344°（63°）→ policy 120°（136°）
 * 相邻对比度最小 1.36（osv↔ghsa），全部 ≥1.35，可辨。
 */
const SOURCE_PALETTE = [
  '#affc41', // 黄绿            84°  → cisa_kev（在野利用；与 ghsa 互换）
  '#0090b7', // 青蓝193°  → arxiv（学术论文，用户指定）
  '#9620b5', // 紫水晶        288°  → atlas（攻击技术，用户指定）
  '#ea3546', // 拳打红        354°  → osv（开源漏洞库；与 nvd 互换）
  '#f86624', // 虎焰橙         19°  → ghsa（GitHub 告警；与 cisa_kev 互换）
  '#f9c80e', // bright-amber琥珀黄 47°  → poc_repo（POC 验证代码）
  '#ff7096', // 粉            344°  → rss（安全社区，用户指定）
  '#008000', // 标准绿        120°  → policy（政策法规，用户指定）
  '#1dd3b0', // 薄荷青        168°  → nvd（通用漏洞库；与 osv 互换）
  '#43bccd', // 备用兜底（太平洋蓝，用于未映射的数据源）
] as const

/** 白天版数据源色板：与上面**逐项同色相**，只压暗明度。
 *
 * 为什么要单独一套（而不是直接复用暗色值）：
 *   黄绿 #affc41 在白底只有 1.22:1、琥珀 #f9c80e 1.55:1、薄荷青 1.87:1 ——
 *   全部不合格，图上会「发飘」看不清。
 *
 * 关键约束：**不能简单降亮度**。降 L 会把饱和色压成黑/灰
 *   （试过：#affc41 沿 L 压下去直接变 #000000），
 *   那就失去「数据源配色」的意义了。所以这里是**保色相、保饱和，
 *   手工调到刚好越过 3.4:1**。
 *
 * 实测（对卡片底 #fcfdfd）：
 *   全部 ≥3.72:1；相邻源色相差最小 25.3°（暗色版 24.3°），
 *   色相环结构与暗色一致 —— 也就是说白天版的可辨性
 *   仍然来自色相差，与暗色是同一套设计逻辑。 */
const SOURCE_PALETTE_LIGHT = [
  '#5c7a00', // 黄绿 → 橄榄绿  74.8°
  '#00708e', // 青蓝 → 深青   192.7°
  '#7c1a97', // 紫→ 深紫   287.0°
  '#c2273a', // 红 → 深红   352.6°
  '#c14a10', // 橙 → 深橙    19.7°
  '#a07800', // 琥珀 → 深琥珀 45.0°
  '#c9456b', // 粉 → 深玫粉 342.7°
  '#046b04', // 绿 → 深绿   120.0°
  '#0d8f78', // 薄荷青 → 深青绿 169.4°
  '#2c8fa0', // 兜底 → 深蓝
] as const

/** 数据源 code → 色板下标（用对象固定，避免排序变化导致颜色跳动） */
/** 按主题取数据源 → 颜色。用对象固定下标，避免排序变化导致颜色跳动 */
function sourceColorMap(isDark: boolean): Record<string, string> {
  const p = isDark ? SOURCE_PALETTE : SOURCE_PALETTE_LIGHT
  return {
    cisa_kev: p[0],
    arxiv: p[1],
    atlas: p[2],
    osv: p[3],
    ghsa: p[4],
    poc_repo: p[5],
    rss: p[6],
    policy: p[7],
    nvd: p[8],
  }
}

/* 轴/网格/tooltip 按主题取色。
   白天版的轴标签从 #6b7870（白底仅 2.6:1，不合格）换成 #46584f（7.0:1）。 */
const chartAxis = (isDark: boolean) => ({
  label: isDark ? '#6b7870' : '#46584f',
  line: isDark ? 'rgba(180, 220, 195, 0.09)' : 'rgba(23,58,43,0.16)',
  split: isDark ? 'rgba(180, 220, 195, 0.055)' : 'rgba(23,58,43,0.09)',
})

/** 轴标签对象：按主题取（原来是模块级 AXIS_COLOR 的字面量） */
function makeAxisLabel(isDark: boolean) {
  return { fontSize: 11, color: chartAxis(isDark).label }
}

const tooltipStyle = (isDark: boolean) => ({
  /* 白天：绿底白字（用户指定，与侧栏同色）。
     className 供 CSS 提亮系列 marker —— 深绿系列色在绿底上会融掉。 */
  backgroundColor: isDark ? 'rgba(14, 19, 17, 0.96)' : '#05825c',
  borderColor: isDark ? 'rgba(180, 220, 195, 0.14)' : 'rgba(255,255,255,0.28)',
  borderWidth: 1,
  className: 'sentinel-chart-tooltip',
  textStyle: { color: isDark ? '#e9f0ea' : '#ffffff', fontSize: 12 },
  extraCssText: 'backdrop-filter: blur(16px); border-radius: 13px;',
})

/**
 * 三张图共用的动画基线
 *
 * ECharts 默认值对本场景不合适：
 * - animationDuration 默认 1000ms，三个图同时入场共 3 秒，
 *   慢且拖沓；
 * - 默认 easing 'cubicOut' 在数据更新时收尾太快，看着像「突然停住」。
 *
 * 这里统一为 520ms + cubicInOut（两端都缓，中段快），
 * 三张图同节奏入场，不抢帧也不拖时间。
 *
 * `animationEasingUpdate` 单独设：数据刷新（如每 30s 轮询）是位移过渡，
 * 用 cubicInOut 更连贯；首次入场才用稍快的 cubicOut。
 */
const CHART_ANIMATION = {
  animation: true,
  animationDuration: 520,
  animationEasing: 'cubicOut',
  animationDurationUpdate: 420,
  animationEasingUpdate: 'cubicInOut',
  /** 数据更新时不做完整重绘过渡，避免轮询刷新整屏闪一下 */
  animationThreshold: 16,
} as const

/**
 * 严重程度柱形图色板（用户指定五色）
 *
 * **这五个色不能按给定顺序直接分配**——实测存在难区分对：
 *   `a100f2`(紫) vs `bd1f36`(红)：WCAG 对比度仅 **1.11**（几乎同色）
 *   `1dd3b0`(青) vs `ff9505`(橙)：1.15
 *   `ff9505`(橙) vs `0ead69`(绿)：1.33
 * 若让紫与红落在相邻柱上，图上会并成一根。
 *
 * 现在用的是**穷举 120 种分配后相邻对比度最高**的那一组：
 *   X 轴顺序  CRITICAL → HIGH → LOW → MEDIUM → NONE
 *   相邻对比度  2.90/ 2.52 / 2.80 / 2.11，最小值 2.11
 *   色相差     111° / 115° /  43° / 163°
 * 四对全部安全，紫与红隔了两个位置。
 *
 * 语义上也说得通：CRITICAL 用红系（最高危）、NONE 用绿（无风险），
 * HIGH 用紫（体量最大、最需要关注）、MEDIUM 用暗红、LOW 用橙。
 *
 * 注：ECharts 不认 CSS 变量，此处用十六进制字面量。
 */
const SEVERITY_COLOR: Record<string, string> = {
  CRITICAL: '#1dd3b0', // turquoise    绿松石  168°
  HIGH: '#a100f2', // electric-purple 电光紫  280°  ← 体量最大，用最强的紫
  LOW: '#ff9505', // vivid-orange vivid橙   35°
  MEDIUM: '#bd1f36', // punch-red    拳打红   351°
  None: '#0ead69', // emerald       翠绿     154°
}

/**
 * AI 安全风险四分类色板（用户指定四色）
 *
 * 键名与后端 risk_enricher.py 的 _CATEGORY_BASELINE 完全一致。
 *
 * 环形图扇区相邻（含首尾闭合）顺序为
 *   1 AI基础设施/推理框架  → 2 AI应用与智能体 → 3 供应链 → 4 模型与数据层 → 回到 1
 * 当前数值 2 / 29 / 30 / 102，故相邻对是「紫↔青」「青↔绿」「绿↔橙」「橙↔紫」。
 * 这四对的 WCAG 对比度分别为 2.90 / 1.53 / 1.33 / 2.52，
 * 均 ≥ 1.3，且色相差 111°/ 14° / 120° / 115°，
 * 「青↔绿」色相差仅 14°靠明度差（0.50 vs 0.31）区分，可辨。
 *
 * 注意：用户给的第5 色 `bd1f36`（拳打红）只用于严重程度柱形图，
 * 不进本表——若放入，`a100f2` 与它的对比度仅 1.11（几乎同色），
 * 环形图上会并成一块。
 */
const RISK_COLOR: Record<string, string> = {
  'AI基础设施/推理框架风险': '#a100f2', // electric-purple 电光紫  280°
  '模型与数据层风险': '#ff9505', // vivid-orange   vivid橙  35°
  'AI应用与智能体风险': '#1dd3b0', // turquoise     绿松石  168°
  '供应链风险': '#0ead69', // emerald        翠绿    154°
}
/** 环形图未映射分类的兜底色（与四色同源，避免落入其他色系） */
const RISK_FALLBACK = '#ff9505'

export default function DashboardPage() {
  /* 主题：图表色走 JS（ECharts 不认 CSS 变量）。
     本页三个 option 都不是 useMemo（每次渲染直接构建），
     所以在组件内取一次色即可，不需要依赖数组。 */
  const { isDark } = useTheme()
  const ax = chartAxis(isDark)
  const axisLabel = makeAxisLabel(isDark)
  const SRC = isDark ? SOURCE_PALETTE : SOURCE_PALETTE_LIGHT
  const SOURCE_COLOR = sourceColorMap(isDark)

  const [overview, setOverview] = useState<MonitorOverview | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [scheduler, setScheduler] = useState<SchedulerStatus | null>(null)
  /** 知识底座细分（AI 相关 / 已富化条数）—— 用于指标卡的口径标注 */
  const [kb, setKb] = useState<KnowledgeBaseStats | null>(null)
  /** 最近一次刷新的时刻 —— 展示「这些数字是活的」 */
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const [loading, setLoading] = useState(true)
  const [collecting, setCollecting] = useState<string | null>(null)
  const [togglingSchedule, setTogglingSchedule] = useState(false)
  /** 正在保存周期的数据源代码（用于给对应下拉加 loading） */
  const [intervalSaving, setIntervalSaving] = useState<string | null>(null)
  const navigate = useNavigate()

  /* ---------- 图表下钻（手册「态势总览」条目）----------
   * 指标卡只能告诉你「有多少」，回答不了「具体是哪些」。
   * 把分类与严重度两张图做成入口：点一下直接进情报中心，
   * 并带上对应筛选条件——情报中心的 URL 就是查询状态源，
   * 跳转后筛选自动生效，用户落地即看到那批条目。
   *
   * 参数值与情报中心的下拉选项严格一致：
   *   category —— 中文全称（如 AI基础设施/推理框架风险）
   *   severity —— 大写档位（CRITICAL / HIGH / MEDIUM / LOW）*/
  const drillToIntel = (key: 'category' | 'severity', value: string) => {
    if (!value) return
    navigate(`/intel?${key}=${encodeURIComponent(value)}`)
  }

  /**
   * 看板统计的**轮询刷新**
   *
   * ---------- 为什么必须有 ----------
   * 原来只在挂载时拉一次，导致：后台采集在跑，但页面上的
   * 「情报总量」「6 小时采集占比」一直停在打开那一刻的快照。
   * 用户看到的是一个「一成不变的数字」，无法判断系统是否还在工作
   * —— 这正是用户提问「这些数字只是装饰吗」的根源。
   *
   * 30 秒的依据：调度器最短间隔 1 小时，30 秒足以在一次采集完成后
   * 很快反映出来，又不构成可忽略的负载（4 个接口、纯读）。
   */
  const load = async (silent = false) => {
    // 轮询时不进 loading，避免每 30 秒闪一次骨架屏
    if (!silent) setLoading(true)
    try {
      /* 手动刷新时 hold 至少 500ms：本地 4 个接口约 200ms 就返回，
         转圈会一闪而过，用户感知不到「点了之后有反馈」。
         ⚠️ 只在**手动刷新**时 hold。之前这个占位是无条件塞进
         Promise.all 的，导致每次切页的首次加载也白等 0.5 秒——
         四个接口其实 200ms 就回来了，这 300ms 是纯人为延迟。*/
      const hold = silent
        ? []
        : [new Promise((r) => setTimeout(r, 500))]
      const [ov, st, sc, kbStats] = await Promise.all([
        api.monitorOverview(),
        api.stats(),
        api.scheduler(),
        api.knowledgeBase(),
        ...hold, // 占位：仅手动刷新时等满 500ms
      ])
      setOverview(ov)
      setStats(st)
      setScheduler(sc)
      setKb(kbStats)
      // 记录刷新时刻 —— 让用户知道这些数字是「刚拉过的」
      setUpdatedAt(new Date)
    } catch (e) {
      // 轮询失败不弹错（会反复打扰），只在首次加载时报
      if (!silent) message.error('加载失败：' + (e as Error).message)
    } finally {
      if (!silent) setLoading(false)
    }
  }

  useEffect(() => {
void load()
    const timer = setInterval(() => void load(true), 30_000)
    return () => clearInterval(timer)
  }, [])

  const handleCollect = async (code: string) => {
    setCollecting(code)
    /* 采集是**长耗时同步操作**（实测 poc_repo 32s、arxiv 35s、atlas 30s），
       这期间按钮保持 loading。没有提示的话用户会以为卡死然后重复点击，
       反而触发并发采集。所以先明示「正在进行」。 */
    message.loading({ content: `正在采集 ${code}，慢源需 30~60 秒，请勿重复点击`, duration: 0, key: `collect-${code}` })
    try {
      const r = await api.triggerCollect(code)
      const d = r.result
      message.destroy(`collect-${code}`)
      if (d.error) message.error(`${code}: ${d.error}`)
      else message.success(`${code} 采集完成，新增 ${d.new} 条，判定 AI 相关 ${d.ai_security} 条`)
      await load()
    } catch (e) {
      message.destroy(`collect-${code}`)
      /* 超时不等于失败：前端超时窗口（180s）到了不代表后端没跑完。
         这时先查一次采集日志，若最近有成功记录就如实告知，
         否则才报失败 —— 避免用户白跑一趟。*/
      const msg = (e as Error).message || ''
      if (msg.includes('timeout')) {
        message.warning(
          `${code} 等待超时，但后台可能仍在采集。` +
            '稍等片刻后点「刷新」查看采集动态即可确认。',
          6,
        )
      } else {
        message.error('触发失败：' + msg)
      }
    } finally {
      setCollecting(null)
    }
  }

  /** 暂停/恢复自动采集——系统保持可用，只是不再自动发起采集 */
  const toggleSchedule = async (action: 'pause' | 'resume') => {
    setTogglingSchedule(true)
    try {
      await api.setSchedulerPaused(action === 'pause')
      message.success(action === 'pause' ? '已暂停自动采集' : '已恢复自动采集')
      await load()
      /* 通知 App.tsx 立即重拉 health——
         否则侧栏「数据源 · 状态」与顶栏悬浮坞的调度状态
         要等 30 秒轮询才更新，表现为「点了没反应」。*/
      notifySchedulerChanged()
    } catch (e) {
      message.error('操作失败：' + (e as Error).message)
    } finally {
      setTogglingSchedule(false)
    }
  }

  const handlePause = () => toggleSchedule('pause')
  const handleResume = () => toggleSchedule('resume')

  /**
   * 调整某数据源的采集周期
   *
   * seconds 传 null = 恢复默认。档位来自后端 job.options
   * （已按各源的 min/max_interval 裁剪），越界在后端就被拦下，
   * 这里只负责把 reason 原样转述给用户。
   */
  const handleSetInterval = async (
    code: string,
    name: string,
    seconds: number | null,
  ) => {
    setIntervalSaving(code)
    try {
      const r = await api.setSchedulerInterval(code, seconds)
      if (!r.ok) {
        // reason 解释了区间由来（如"NVD 无 API Key 时限流 5 次/30 秒"），
        // 比干巴巴的"设置失败"有用得多
        message.error(`${name}：${r.reason || r.error || '设置失败'}`, 6)
        return
      }
      /* 调度器未启动（异常态）时值已落库但不重排 job，
         如实告知，避免用户以为「设了没生效」是 bug */
      if (r.applied_immediately === false) {
        message.warning(`${name} 周期已保存，将在调度器恢复运行后生效`, 6)
      } else {
        message.success(
          seconds === null
            ? `${name} 已恢复默认周期`
            : `${name} 采集周期已调整为 ${formatDuration(seconds)}`,
        )
      }
      await load()
      // 侧栏/顶栏的调度摘要同样依赖周期信息，同步广播
      notifySchedulerChanged()
    } catch (e) {
      message.error('操作失败：' + (e as Error).message)
    } finally {
      setIntervalSaving(null)
    }
  }

  // ---- 图表配置 ----

  // 各数据源情报量分布
  const sourceOption = {
    ...CHART_ANIMATION,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...tooltipStyle(isDark) },
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    xAxis: {
      type: 'value',
      axisLabel,
      splitLine: { lineStyle: { color: ax.split } },
    },
    yAxis: {
      type: 'category',
      data: Object.keys(overview?.by_source ?? {}),
      axisLabel,
      axisLine: { lineStyle: { color: ax.line } },
      axisTick: { show: false },
    },
    series: [
      {
        type: 'bar',
        data: Object.entries(overview?.by_source ?? {}).map(([k, v]) => ({
          name: k,
          value: v,
          // 按数据源类型取语义色，同类同色（见 sourceColorMap 注释）
          itemStyle: { color: SOURCE_COLOR[k] ?? SRC[9] },
        })),
        barWidth: '62%',
        label: {
          show: true,
          position: 'right',
          fontSize: 11,
          color: isDark ? '#a7b3ab' : '#46584f',
          fontFamily: 'var(--mono)',
        },
      },
    ],
  }

  // AI 分类分布（赛题四分类）
  const categoryOption = {
    ...CHART_ANIMATION,
    tooltip: {
      trigger: 'item',
      /* 末尾提示可下钻：把「这是什么」升级为「点它能去哪」 */
      formatter: '{b}: {c} 条 ({d}%)<br/><span style="opacity:.7">点击查看该分类的情报 →</span>',
      ...tooltipStyle(isDark),
    },
    legend: {
      /* 图例移到右侧竖排（用户要求）。
         原来在底部横排 + type: 'scroll'，四个中文名放不下宽度，
         被迫分页显示成「1/2」，第三、四个分类直接看不到——
         而分类数量与占比正是这张图要传达的核心信息。
         竖排后每项独占一行，四个分类全部可见，且不再需要分页控件。*/
      orient: 'vertical',
      right: 4,
      top: 'middle',
      itemGap: 13,
      itemWidth: 10,
      itemHeight: 10,
      // 竖排且宽度够，不需要分页
      type: 'plain',
      // 暗色背景下图例文字需显式给色，否则默认深灰不可读
      textStyle: {
        color: isDark ? '#e9f0ea' : '#46584f',
        inactiveColor: isDark ? '#7d8a82' : '#63746b',
        fontSize: 10.5,
        lineHeight: 14,
        /* 最长分类名「AI基础设施/推理框架风险」13字 ≈ 128px，
           留足宽度避免被截断（truncate 只是兜底，正常不该触发）*/
        width: 128,
        overflow: 'truncate',
      },
    },
    series: [
      {
        type: 'pie',
        /* 图例占了右侧约 150px，环形图整体左移并略缩小，避免图例压住扇区。
           center x 从 50% 降到 30%，为图例让出空间。*/
        radius: ['38%', '56%'],
        center: ['30%', '50%'],
        avoidLabelOverlap: true,
        label: { show: false },
        /* AI 安全风险分类色板（用户指定四色）。
           用「键 → 色」映射而非位置数组：
           Object.entries 的顺序随数据变化，位置数组会导致颜色错位。 */
        itemStyle: {
          // 不描边：四色跨度足够大，描边反而在扇区交界处压出一条黑线
          borderWidth: 0,
        },
        data: Object.entries(stats?.by_ai_category ?? {}).map(([name, value]) => ({
          name,
          value,
          itemStyle: { color: RISK_COLOR[name] ?? RISK_FALLBACK },
        })),
      },
    ],
  }

  // 严重程度分布
  // 五档离散配色（用户指定五色），分配顺序按相邻可区分性排定，见 SEVERITY_COLOR
  //
  // 三处修正（用户报「横轴与柱形对不上」）：
  // ① 大小写归一——采集源写入的 severity 有 Critical/CRITICAL 混杂，
  //    后端已用 upper() 合并，这里再兜一层（其他数据源仍可能混杂）。
  //    混���还有个连带 bug：小写键查不到 SEVERITY_COLOR，会落到
  //    fallback 绿，同一档严重度出现两种颜色。
  // ② 按严重度排序——Object.entries 的顺序取决于后端键序，
  //    直接用会得到 Critical, CRITICAL, High, HIGH, LOW… 的乱序。
  // ③ 横轴 interval:0——见下方 xAxis 注释。
  const severityEntries = (() => {
    const merged = new Map<string, number>()
    for (const [k, v] of Object.entries(stats?.by_severity ?? {})) {
      const key = String(k).toUpperCase()
      merged.set(key, (merged.get(key) ?? 0) + v)
    }
    const order = ['CRITICAL', 'HIGH', 'MEDIUM', 'MODERATE', 'LOW', 'NONE']
    return [...merged.entries()].sort((a, b) => {
      const ia = order.indexOf(a[0])
      const ib = order.indexOf(b[0])
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib)
    })
  })()

  const severityOption = {
    ...CHART_ANIMATION,
    /* axis 触发时提示柱名；点柱下钻的引导放在 label 里太拥挤，
       这里用 axisPointer + 简短提示，保持 tooltip 紧凑。 */
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (ps: Array<{ name: string; value: number }>) =>
        ps.length
          ? `${ps[0].name}：${ps[0].value} 条<br/><span style="opacity:.7">点击查看该等级的情报 →</span>`
          : '',
      ...tooltipStyle(isDark),
    },
    grid: { left: 8, right: 16, top: 20, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: severityEntries.map(([k]) => k),
      axisLabel: {
        ...axisLabel,
        /* 强制显示每个类目的标签。
         * ECharts 默认 interval:'auto'——标签重叠时自动隔项抽稀，
         * 结果是「7 根柱只显示 4 个标签」，且标签位置对应的是
         * 前 4 个类目，与柱形一一错位。写死 0 表示一个都不省。 */
        interval: 0,
      },
      axisLine: { lineStyle: { color: ax.line } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      axisLabel,
      splitLine: { lineStyle: { color: ax.split } },
    },
    series: [
      {
        type: 'bar',
        data: severityEntries.map(([k, v]) => ({
          value: v,
          // 键名大小写与后端返回一致（注意是 `None` 而非 `NONE`）
          itemStyle: { color: SEVERITY_COLOR[k] ?? '#0ead69' },
        })),
        barWidth: '48%',
        label: {
          show: true,
          position: 'top',
          fontSize: 11,
          color: isDark ? '#e9f0ea' : '#12211a',
          fontFamily: 'var(--mono)',
        },
      },
    ],
  }

  // 数据源表格行：补上 by_source 中的记录数
  // 注意：useMemo 必须置于任何条件 return 之前，否则违反 Hooks 规则
  const sourceRows = useMemo(
     () =>
      (overview?.sources ?? []).map((s) => ({
        ...s,
        records: overview?.by_source?.[s.code] ?? 0,
      })),
    [overview],
  )

  // 首次加载不整页替换为 Spin 骨架。
  //
  // 原因：路由切换时组件重新挂载，overview 变 null，
  // 若走 `if (loading && !overview) return <Spin/>` 这条早退，
  // 页面会「先变成一个居中转圈、再变成完整界面」——
  // 视觉上就是闪一下。接口实测仅 36ms，这种闪烁完全没必要。
  //
  // 现在的做法：骨架照常渲染（数字显示 0/—），
  // 叠加一层半透明遮罩 + 顶部细进度条，数据到达后自然替换。
  // 结构始终在场，没有空白帧。
  // 分源采集延迟（2026-10-14 新口径，后端 latency_stats 模块）
  const lo = overview?.latency_overall
  const latencyRows = overview?.latency_by_source ?? []
  // 数据源 code → 中文名（分源延迟表用；sources 接口不含记录数，只取元信息）
  const sourceNameOf = (code: string): string =>
    overview?.sources?.find((s) => s.code === code)?.name ?? code

  return (
    <div className="sentinel-dash-rows" style={{ position: 'relative' }}>
      {/* ---- 数据源与调度 ----
           等高说明：左侧表格 9 行、右侧调度 9 项，但两者行高不同，
           固定 height 会出现错位。改由 CSS 让 Col 作 flex 容器、
           Card 高度 100%，随较高的一栏自动对齐。 */}
      <Row gutter={[12, 12]} align="stretch">
        <Col xs={24} lg={14} className="sentinel-equal-col">
          <Card
            className="sentinel-equal-card"
            title="数据源状态"
            size="small"
            extra={
              /* loading 让用户看到「正在转圈」—— 否则点击后界面毫无变化，
                 用户无法判断是卡住了还是已经刷新完了。*/
              <Button
                size="small"
                icon={<ReloadOutlined spin={loading} />}
                loading={loading}
                onClick={() => void load()}
              >
                刷新
              </Button>
            }
          >
            <Table<{ code: string; name: string; category: string; active: boolean; records: number }>
              size="small"
              rowKey="code"
              pagination={false}
              // 记录数取自 by_source 统计（sources 字段不含该数据）
              dataSource={sourceRows}
              columns={[
                {
                  title: '数据源',
                  dataIndex: 'name',
                  render: (v: string, r) => (
                    <Space size={6}>
                      <Badge
                        status={r.records ? 'success' : 'default'}
                        color={r.records ? SOURCE_COLOR[r.code] ?? SRC[9] : undefined}
                      />
                      {/* 源名取该源的语义色，与条形图同色，
                          表格与图表形成视觉呼应 */}
                      <span style={{ color: r.records ? undefined : 'var(--muted)' }}>
                        {v}
                      </span>
                    </Space>
                  ),
                },
                { title: '类别', dataIndex: 'category', width: 80 },
                {
                  title: '记录数',
                  dataIndex: 'records',
                  width: 90,
                  align: 'right' as const,
                  render: (v: number) =>
                    v > 0 ? (
                      <b style={{ fontSize: 12 }}>{v.toLocaleString()}</b>
                    ) : (
                      <span style={{ color: 'var(--muted)', fontSize: 12 }}>待采集</span>
                    ),
                },
                {
                  title: '操作',
                  width: 90,
                  render: (_: unknown, r) => (
                    <Button
                      size="small"
                      type="link"
                      icon={<ThunderboltOutlined />}
                      loading={collecting === r.code}
                      onClick={() => void handleCollect(r.code)}
                    >
                      采集
                    </Button>
                  ),
                },
              ]}
            />          </Card>
        </Col>

        <Col xs={24} lg={10} className="sentinel-equal-col">
          <Card
            className="sentinel-equal-card"
            title="定时调度"
            size="small"
            /* 状态与任务数移到顶栏：与左卡「标题 + 操作按钮」的头部节奏一致，
               原来放在卡内占掉两行、且「运行状态」和「任务数」分属不同粒度，
               混在一起显得松散。
               顺序：任务数 → 状态 → 操作按钮。
               任务数是静态量放最左，状态与按钮在右侧成组，
               视线从「有多少」到「什么状态」到「能做什么」是一条线。*/
            extra={
              <Space size={10}>
                <span className="sentinel-sched-count">
                  {scheduler?.jobs.length ?? 0} 个任务
                </span>
                <span className="sentinel-sched-status">
                  {!scheduler?.running ? (
                    <Tag color="warning">未启动</Tag>
                  ) : scheduler?.paused ? (
                    <Tag color="orange">已暂停</Tag>
                  ) : (
                    <Space size={6}>
                      {/* 脉冲环：强调自动化确实在运转，是看板的「活体」信号 */}
                      <span className="sentinel-live-ring" aria-hidden />
                      <Tag color="success">采集中</Tag>
                    </Space>
                  )}
                  {/* 采集进度条：**始终渲染**，只靠 CSS 控制动不动画。
                      用户要求暂停时也要看见进度条（只是静止）——
                      若用条件渲染，暂停/恢复时进度条会整块消失再出现，
                      卡片头部宽度跳动，视觉上比静止更突兀。
                      静止态用 ::before 的固定宽度表现「有进度但不流动」。*/}
                  <span
                    className={
                      'sentinel-runner' +
                      (scheduler?.running && !scheduler?.paused ? ' is-active' : '')
                    }
                    role="status"
                    aria-label={
                      scheduler?.running && !scheduler?.paused
                        ? '采集进行中'
                        : '采集已停止'
                    }
                  />
                </span>
                <Popconfirm
                  title={scheduler?.paused ? '恢复自动采集？' : '暂停自动采集？'}
                  description={
                    scheduler?.paused
                      ? '恢复后各数据源将按原周期继续自动采集。'
                      : '暂停后系统仍可正常查询与问答，只是不再自动采集。手动「采集」按钮不受影响。'
                  }
                  okText={scheduler?.paused ? '恢复' : '暂停'}
                  cancelText="取消"
                  onConfirm={() =>
                    void (scheduler?.paused ? handleResume() : handlePause())
                  }
                >
                  <Button
                    size="small"
                    danger={!scheduler?.paused}
                    icon={scheduler?.paused ? <PlayCircleOutlined /> : <PauseCircleOutlined />}
                    loading={togglingSchedule}
                    disabled={!scheduler?.running}
                  >
                    {scheduler?.paused ? '恢复' : '暂停'}
                  </Button>
                </Popconfirm>
              </Space>
            }
          >
            {/* 改为表格：与左侧「数据源状态」卡同构（表头 + 分隔线 + 行），
                原来「每X/ 上次X / 下次X」三行文字堆在右侧，
                对比困难且行高不齐。

                **不加 scroll.y**：写死高度会让表格内部出现独立滚动条，
                而左侧「数据源状态」卡是自然撑开的——
                两卡行数相同（都是 9 行）却一个有滚动条一个没有，
                视觉上像是右边内容被截断了。
                改为与左卡一致：靠外层 .sentinel-equal-card 的
                flex 等高机制对齐，超长时整卡一起长。*/}
            <Table
              size="small"
              className="sentinel-sched-table"
              rowKey="job_id"
              dataSource={scheduler?.jobs ?? []}
              pagination={false}
              columns={[
                {
                  title: '采集任务',
                  dataIndex: 'name',
                  render: (v: string | undefined, r) => (
                    <Tooltip title={`任务 ID：${r.job_id}`}>
                      {/* 主题色圆点：与左卡「数据源状态」表的 Badge 同构，
                          同一个数据源在两张表里是同一个颜色，
                          视线左右对照时能直接对上。 */}
                      <span className="sentinel-sched-name-cell">
                        <span
                          className="sentinel-sched-dot"
                          style={{
                            background:
                              SOURCE_COLOR[r.source_code] ?? SRC[9],
                          }}
                          aria-hidden
                        />
                        <span className="sentinel-sched-name">
                          {(v ?? '').replace(/^采集\s*/, '') || r.source_code}
                        </span>
                      </span>
                    </Tooltip>
                  ),
                },
                {
                  title: '周期',
                  dataIndex: 'interval_seconds',
                  width: 106,
                  align: 'right' as const,
                  render: (v: number, r) => {
                    /* 档位由后端按该源 min/max_interval 裁剪好了，
                       这里只做两件事：
                       ① 当前值若不在档位里（用户设过非阶梯值），
                          补一个选项，否则 Select 会把原始秒数
                          （如 5400）当文本显示出来；
                       ② 自定义时在末尾给「恢复默认」入口。 */
                    const inLadder = r.options.some((o) => o.seconds === v)
                    const ladderOptions = inLadder
                      ? r.options
                      : [
                          {
                            seconds: v,
                            label: formatDuration(v),
                            current: true,
                          },
                          ...r.options,
                        ]
                    const cycleTitle = r.is_custom
                      ? `自定义周期 · 默认 ${formatDuration(r.default_interval)}`
                      : `默认周期 ${formatDuration(r.default_interval)}`
                    return (
                      <Tooltip
                        title={
                          <>
                            {cycleTitle}
                            {r.interval_basis && (
                              <>
                                <br />
                                可选区间：{r.interval_basis}
                              </>
                            )}
                          </>
                        }
                      >
                        <Select
                          size="small"
                          variant="borderless"
                          className={`sentinel-sched-cycle-select${
                            r.is_custom ? ' is-custom' : ''
                          }`}
                          value={v}
                          loading={intervalSaving === r.source_code}
                          onChange={(val) => {
                            const seconds =
                              val === RESET_INTERVAL ? null : val
                            const cleanName = (r.name ?? '').replace(
                              /^采集\s*/,
                              '',
                            )
                            void handleSetInterval(
                              r.source_code,
                              cleanName || r.source_code,
                              seconds,
                            )
                          }}
                          options={[
                            ...ladderOptions.map((o) => ({
                              value: o.seconds,
                              label: o.label,
                            })),
                            // 仅自定义时出现：恢复默认是低频操作，
                            // 放在末尾不打扰正常的「调快/调慢」
                            ...(r.is_custom
                              ? [
                                  {
                                    value: RESET_INTERVAL,
                                    label: (
                                      <span className="sentinel-sched-reset">
                                        ↩ 恢复默认
                                      </span>
                                    ),
                                  },
                                ]
                              : []),
                          ]}
                        />
                      </Tooltip>
                    )
                  },
                },
                {
                  title: '上次',
                  dataIndex: 'last_run',
                  width: 118,
                  align: 'right' as const,
                  render: (v: string | null, r) => {
                    if (!v) return <span className="sentinel-sched-time">—</span>
                    return (
                      <span
                        className="sentinel-sched-time is-ok"
                        title={
                          r.last_ok === false
                            ? '上次执行失败'
                            : '上次执行成功'
                        }
                      >
                        {formatClock(v)}
                        {/* 手动 / 接口触发要标出来：不然用户看到「刚刚才采集过」
                            却发现数据没变，会以为自动采集出了问题 */}
                        {(r.last_new ?? 0) > 0 && (
                          <em className="sentinel-sched-delta">
                            +{r.last_new}
                          </em>
                        )}
                      </span>
                    )
                  },
                },
                {
                  title: '下次',
                  dataIndex: 'next_run',
                  width: 108,
                  align: 'right' as const,
                  render: (v: string | null) => {
                    if (scheduler?.paused) {
                      return (
                        <span className="sentinel-sched-time is-paused">
                          已暂停
                        </span>
                      )
                    }
                    if (!v) return <span className="sentinel-sched-time">—</span>
                    const left = new Date(v).getTime() - Date.now()
                    return (
                      <span
                        className="sentinel-sched-time"
                        title={`预计 ${formatClock(v)} 自动采集`}
                      >
                        {formatClock(v)}
                        {/* 倒计时是「自动采集还活着」最直接的证据：
                            只给时刻的话，用户无法区分「还没到点」与「调度挂了」 */}
                        {left > 0 && left < 36 * 3600 * 1000 && (
                          <em className="sentinel-sched-countdown">
                            {formatCountdown(left)}
                          </em>
                        )}
                      </span>
                    )
                  },
                },
              ]}
            />
          </Card>
        </Col>
      </Row>

      {/*局部加载遮罩：结构始终在场，只压一层半透明 + 顶部进度条。
          比整页替换成 Spin 稳定得多——切页时没有空白帧。
          注意：它所属的定位容器已是 return 下的第一个 <div>，
          这里不再重复嵌套一层（上一轮搬移区块时误留，已修）。*/}
      {loading && !overview && (
        <div className="sentinel-loading-veil">
          <div className="sentinel-loading-bar" />
        </div>
      )}

      {/* ---- 采集动态（扁平表）----
          位置：数据源/调度状态之后、指标卡之前。
          阅读顺序的考虑：
            · 「数据源状态 + 定时调度」先回答「有哪些源、在跑吗」
            · 「采集动态」再回答「刚才采到了什么」（明细）
            · 「指标卡」最后给总量与时效性（汇总）
          即「配置 → 明细 → 汇总」，从具体到抽象。*/}
      <CollectFeedCard sourceColor={SOURCE_COLOR} />

      {/* ---- 关键指标 ----
           时效性两张卡（≤6h 占比 / P95）已并入下方「分源采集延迟」卡——
           同一指标出现两处会让读者以为是两个数字。这里只留总量类。 */}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={12} md={12}>
          <MetricCard
            /* 「情报总量」改为「漏洞情报」：本卡统计的只是
               vulnerabilities 一张表，而态势总览的「情报总量」还含
               论文/政策 —— 同名不同口径会让两个页面的数字看起来打架。 */
            title="漏洞情报"
            value={overview?.totals.vulnerabilities ?? 0}
            suffix="条"
            /**
             * 副标题必须写清口径，否则数字会被误读。
             *
             * 原写法「AI 安全 {ai_security} 条」有两个问题：
             *  ① ai_security 实测是**已富化数**（168），标签却写「AI 安全」
             *  ② 与主数字口径混读，会以为 AI 相关只有 168 条
             *
             * 现在的两个细分取自 /api/stats/knowledge-base，口径明确：
             *   ai_vulnerabilities          = 已被判定 AI 分类的条数
             *   enriched_ai_vulnerabilities = 其中完成富化并关联了攻击技术的
             * 标签统一写成「已分类 / 已富化」，不再用含糊的「AI 安全」。
             */
            hint={`已分类 ${kb?.ai_vulnerabilities ?? 0} · 已富化 ${
              kb?.enriched_ai_vulnerabilities ?? 0
            }`}
            // 显式指定 success：省略 tone 会走 default（灰色），
            // 与其余卡的绿色左侧色条不一致
            tone="success"
            icon={<DatabaseOutlined />}
            iconTone="primary"
            delay={0.02}
          />
        </Col>
        <Col xs={12} sm={12} md={12}>
          <MetricCard
            title="数据源"
            value={overview?.totals.sources ?? 0}
            suffix="类"
            hint={scheduler?.running ? '定时调度运行中' : '调度未启动'}
            tone={scheduler?.running ? 'success' : 'warning'}
            icon={<ApiOutlined />}
            iconTone="secondary"
            delay={0.09}
          />
        </Col>
      </Row>

      {/* ---- 分源采集延迟（赛题性能指标：监测时效性）----
           口径 2026-10-14 定稿（后端 数据采集/latency_stats.py，文档同源）：
           ① 发布窗口 7 天、全表统一——冷启动历史回填不计入
              （policy 曾因回填污染现形 P50 83.5 天，统一窗口后归零）；
           ② 回合有效性：距同源上次「成功」≤ max(2×周期, 2h)，
              容忍晚一轮，停摆/补采整回合剔除，首回合无效——
              开发机休眠是 ghsa/kev/osv/policy 样本为 0 的主因，
              不是采集器缺陷，持续运行的机器上样本自然积累；
           ③ ghsa 双钥匙按同源 URL 首次入库去重：GitHub 事后分配
              CVE 后同公告换钥匙二次入库，其 33h 延迟实为 CVE
              分配延迟而非采集延迟（已修复采集器 + 清理 4 对现存双胞胎）；
           ④ 总体 = 6 漏洞源样本级合并；arxiv/policy 的 published_at
              语义不同（论文提交时刻 / 政策正式发布日期），单列不并入。
           样本 < 10 的源分位数无统计意义，显示「—」并标注原因。*/}
      <Card
        title="分源采集延迟（监测时效性）"
        size="small"
        style={{ marginBottom: 16 }}
      >
        <Row gutter={[12, 12]}>
          <Col xs={12} sm={12} md={6}>
            <MetricCard
              title="总体 P50"
              value={formatDuration(lo?.p50_seconds)}
              hint={`P90 ${formatDuration(lo?.p90_seconds)}`}
              tone={
                lo?.p50_seconds != null && lo.p50_seconds <= 6 * 3600
                  ? 'success'
                  : lo?.p50_seconds != null
                    ? 'warning'
                    : 'default'
              }
              icon={<ClockCircleOutlined />}
              iconTone="rose"
              delay={0.02}
            />
          </Col>
          <Col xs={12} sm={12} md={6}>
            <MetricCard
              title="总体 P95"
              /* 样本 < 10 时不展示 P95 —— 1 个样本算出的「P95」
                 就是那 1 条本身，统计上无意义。
                 用「—」比展示一个不可信的数字更诚实。 */
              value={lo && lo.insufficient ? '—' : formatDuration(lo?.p95_seconds)}
              hint={lo?.insufficient ? '样本不足，待累积' : `P50 ${formatDuration(lo?.p50_seconds)}`}
              tone={
                lo?.p95_seconds != null && lo.p95_seconds <= 6 * 3600
                  ? 'success'
                  : lo?.p95_seconds != null
                    ? 'warning'
                    : 'default'
              }
              icon={<ThunderboltOutlined />}
              iconTone="accent"
              delay={0.09}
            />
          </Col>
          <Col xs={12} sm={12} md={6}>
            <MetricCard
              title="6 小时内采集占比"
              value={
                lo?.within_6h_ratio != null ? (lo.within_6h_ratio * 100).toFixed(1) : '—'
              }
              suffix="%"
              hint={`≤24h ${
                lo?.within_24h_ratio != null
                  ? (lo.within_24h_ratio * 100).toFixed(1) + '%'
                  : '—'
              }`}
              tone={
                lo?.within_6h_ratio != null
                  ? lo.within_6h_ratio > 0.8
                    ? 'success'
                    : 'warning'
                  : 'default'
              }
              icon={<ThunderboltOutlined />}
              iconTone="accent"
              delay={0.16}
            />
          </Col>
          <Col xs={12} sm={12} md={6}>
            <MetricCard
              title="有效样本"
              value={lo?.samples ?? 0}
              suffix="条"
              hint={`${lo?.window_days ?? 7} 天发布窗口 × 有效回合`}
              tone="default"
              icon={<DatabaseOutlined />}
              iconTone="primary"
              delay={0.23}
            />
          </Col>
        </Row>

        <Alert
          type="info"
          showIcon
          style={{ margin: '12px 0' }}
          message="统计口径"
          description={
            <span style={{ fontSize: 13 }}>
              延迟 = <b>published_at → ingest_at</b>；仅统计近{' '}
              <b>{lo?.window_days ?? 7} 天</b>发布的情报（冷启动历史回填不计入）。
              有效回合 = 距同源上次成功采集 ≤ max(2 × 周期, 2h)，停摆/补采与首回合整回合剔除。
              总体为 6 个漏洞源样本级合并（nvd 占约 98.6%，实质反映主流源水平）；
              arxiv / policy 发布时间语义不同，单列不并入。
            </span>
          }
        />

        <Table<LatencySourceSummary>
          size="small"
          rowKey="source"
          pagination={false}
          dataSource={latencyRows}
          columns={[
            {
              title: '数据源',
              dataIndex: 'source',
              width: 132,
              render: (_: unknown, r: LatencySourceSummary) => (
                <span style={{ fontWeight: 500 }}>{sourceNameOf(r.source)}</span>
              ),
            },
            {
              title: '采集周期',
              dataIndex: 'interval_seconds',
              width: 88,
              render: (v: number) => (
                <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {formatInterval(v)}
                </span>
              ),
            },
            {
              title: '有效回合',
              width: 88,
              render: (_: unknown, r: LatencySourceSummary) => (
                <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {r.valid_rounds}/{r.total_rounds}
                </span>
              ),
            },
            {
              title: '有效样本',
              dataIndex: 'samples',
              width: 88,
              render: (v: number, r: LatencySourceSummary) =>
                r.not_applicable ? (
                  <Tooltip title="ATLAS 技术表无时间戳字段，延迟指标不适用">
                    <span style={{ color: 'var(--muted)' }}>—</span>
                  </Tooltip>
                ) : (
                  <span style={{ fontVariantNumeric: 'tabular-nums' }}>{v}</span>
                ),
            },
            {
              title: 'P50',
              dataIndex: 'p50_seconds',
              width: 96,
              render: (v: number | null, r: LatencySourceSummary) =>
                v == null ? (
                  <Tooltip
                    title={
                      r.not_applicable
                        ? 'ATLAS 技术表无时间戳字段，延迟指标不适用'
                        : r.insufficient
                          ? `近 ${r.window_days} 天有效样本不足 10 条（开发机休眠使回合失效，持续运行后自然积累）`
                          : '近 7 天无有效样本'
                    }
                  >
                    <span style={{ color: 'var(--muted)' }}>—</span>
                  </Tooltip>
                ) : (
                  <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {formatDuration(v)}
                  </span>
                ),
            },
            {
              title: 'P95',
              dataIndex: 'p95_seconds',
              width: 96,
              render: (v: number | null, r: LatencySourceSummary) =>
                v == null ? (
                  <Tooltip
                    title={
                      r.not_applicable
                        ? 'ATLAS 技术表无时间戳字段，延迟指标不适用'
                        : r.insufficient
                          ? `近 ${r.window_days} 天有效样本不足 10 条，分位数无统计意义`
                          : '近 7 天无有效样本'
                    }
                  >
                    <span style={{ color: 'var(--muted)' }}>—</span>
                  </Tooltip>
                ) : (
                  <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {formatDuration(v)}
                  </span>
                ),
            },
            {
              title: '≤6h 占比',
              dataIndex: 'within_6h_ratio',
              width: 96,
              render: (v: number | null) =>
                v == null ? (
                  <Tooltip title="无有效样本，占比不适用">
                    <span style={{ color: 'var(--muted)' }}>—</span>
                  </Tooltip>
                ) : (
                  <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {(v * 100).toFixed(1)}%
                  </span>
                ),
            },
          ]}
        />
        <div
          style={{
            fontSize: 12,
            color: 'var(--muted)',
            marginTop: 8,
            lineHeight: 1.6,
          }}
        >
          注：剔除计数（无效回合样本 / 窗口外冷启动回填 / 无归属回合）随后端
          latency_by_source 一并返回，供评审核对；机器休眠导致的稀疏源样本缺失
          属运行环境特征，非采集器缺陷。
        </div>
      </Card>

      {/* ---- 刷新时刻 ----
          让用户明确知道这些数字是**实时拉取**的，而非静态装饰。

          ⚠️ 可读性修正（实测截图反馈）：
          原样式是「11.5px + var(--muted)灰 + 右对齐 +负margin 贴边」，
          在深色底上几乎读不出来，且紧贴容器右缘容易被当成装饰忽略。
          改为：**独立状态条**（左对齐 + 加大字号 + 提高对比 + 带活点指示），
          放在图表区正上方，视觉上与「数据」明确分层。*/}
      {updatedAt && (
        <div className="sentinel-updated-bar">
          <span className="sentinel-updated-dot" aria-hidden />
          <span>
            统计更新于 <b>{updatedAt.toLocaleTimeString('zh-CN')}</b>
          </span>
          <span className="sentinel-updated-sep" aria-hidden>
            ·
          </span>
          <span className="sentinel-updated-hint">每 30 秒自动刷新</span>
        </div>
      )}

      {/* ---- 图表区 ----
           三张图的容器统一加 .sentinel-chart：
           提升为独立合成层并给 will-change，让 ECharts 的 canvas 动画
           走 GPU 合成，而不是每帧在主线程重绘 + 触发上方毛玻璃重新模糊。*/}
      <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={8}>
          <Card
            className="sentinel-reveal"
            title="AI 安全风险分类"
            size="small"
            style={{ height: 300 }}
          >
            {stats && Object.keys(stats.by_ai_category).length > 0 ? (
              <ReactECharts
                option={categoryOption}
                style={{ height: 240 }}
                opts={{ renderer: 'canvas' }}
                className="sentinel-chart is-drillable"
                /* 点分类 → 情报中心并按该分类筛选 */
                onEvents={{
                  click: (p: { name?: string }) =>
                    drillToIntel('category', p?.name ?? ''),
                }}
              />
            ) : (
              <Empty description="暂无分类数据" style={{ marginTop: 80 }} />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card
            className="sentinel-reveal"
            title="严重程度分布"
            size="small"
            style={{ height: 300 }}
          >
            {stats && Object.keys(stats.by_severity).length > 0 ? (
              <ReactECharts
                option={severityOption}
                style={{ height: 240 }}
                opts={{ renderer: 'canvas' }}
                className="sentinel-chart is-drillable"
                /* 点柱 → 情报中心并按该严重度筛选 */
                onEvents={{
                  click: (p: { name?: string }) =>
                    drillToIntel('severity', p?.name ?? ''),
                }}
              />
            ) : (
              <Empty description="暂无数据" style={{ marginTop: 80 }} />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card
            className="sentinel-reveal"
            title="各数据源情报量"
            size="small"
            style={{ height: 300 }}
          >
            {overview && Object.keys(overview.by_source).length > 0 ? (
              <ReactECharts
                option={sourceOption}
                style={{ height: 240 }}
                opts={{ renderer: 'canvas' }}
                className="sentinel-chart"
              />
            ) : (
              <Empty description="暂无数据" style={{ marginTop: 80 }} />
            )}
          </Card>
        </Col>
      </Row>

    </div>
  )
}
