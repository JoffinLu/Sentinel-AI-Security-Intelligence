/**
 * 态势总览
 * =================================================================
 * 面向「情报态势」的分析大屏，与监控看板（面向运维）分开：
 *   · 监控看板回答「系统现在能不能用、调度跑没跑」
 *   · 本页回答「最近这段时间情报了什么、有哪些高危、来源健康吗」
 *
 * 时间范围只作用于本页（状态留在组件内部，不进全局）：
 * 24 小时按小时分桶，7/30 天与全周期按天分桶。
 *
 * 配色：全部沿用 sentinel-theme 的既有变量，不引入新色值。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Col, Empty, Row, Spin, Tooltip } from 'antd'
import ReactECharts from 'echarts-for-react'
import { chartPalette } from '@/lib/chartPalette'
import { useTheme } from '@/lib/theme'
import {
  RadarChartOutlined,
  DatabaseOutlined,
  ThunderboltOutlined,
  ShareAltOutlined,
  DeploymentUnitOutlined,
  AlertOutlined,
  ArrowRightOutlined,
  DownloadOutlined,
  TagsOutlined,
  CheckCircleFilled,
} from '@ant-design/icons'
import { api, type SituationOverview } from '@/api/client'

/** 时间范围选项。key 为传给后端的 hours，undefined 表示全周期 */
const RANGES: Array<{ key: string; label: string; hours?: number }> = [
  { key: '24h', label: '24小时', hours: 24 },
  { key: '7d', label: '7天', hours: 24 * 7 },
  { key: '30d', label: '30天', hours: 24 * 30 },
  { key: 'all', label: '全部' },
]

/** 严重度配色：暗色沿用既有 severity 色板（逐字不动）。
 *  白天版整体加深一档 —— 这几个色在白底上偏浅，
 *  作为色块尚可分辨，但图表/徽章里的可读性会下降。 */
const SEVERITY_COLOR_DARK: Record<string, string> = {
  CRITICAL: '#bd1f36',
  HIGH: '#a100f2',
  MEDIUM: '#1dd3b0',
  MODERATE: '#1dd3b0',
  LOW: '#ff9505',
  NONE: '#0ead69',
}
const SEVERITY_COLOR_LIGHT: Record<string, string> = {
  CRITICAL: '#be123c',
  HIGH: '#7c3aed',
  MEDIUM: '#0f766e',
  MODERATE: '#0f766e',
  LOW: '#c2410c',
  NONE: '#047d57',
}

/** AI 风险四分类配色（暗色沿用看板同一套） */
const CATEGORY_COLOR_DARK: Record<string, string> = {
  'AI基础设施/推理框架风险': '#1dd3b0',
  '模型与数据层风险': '#a100f2',
  'AI应用与智能体风险': '#4cd7f6',
  '供应链风险': '#ff9505',
}
const CATEGORY_COLOR_LIGHT: Record<string, string> = {
  'AI基础设施/推理框架风险': '#0f766e',
  '模型与数据层风险': '#7c3aed',
  'AI应用与智能体风险': '#0e7490',
  '供应链风险': '#c2410c',
}

const FALLBACK_COLOR_DARK = '#5f8f7d'
const FALLBACK_COLOR_LIGHT = '#63746b'

/** 按主题取色板（JSX 内联样式也要用，所以做成组件内计算的常量） */
function useSeverityColors(isDark: boolean) {
  return {
    SEVERITY: isDark ? SEVERITY_COLOR_DARK : SEVERITY_COLOR_LIGHT,
    CATEGORY: isDark ? CATEGORY_COLOR_DARK : CATEGORY_COLOR_LIGHT,
    FALLBACK: isDark ? FALLBACK_COLOR_DARK : FALLBACK_COLOR_LIGHT,
  }
}

/* 图表 tooltip 统一深色。
 * ECharts 默认是白底黑字，在暗色大屏上像一块脏补丁——
 * 与监控看板保持同一套样式（那里已验证过可读性最好）。 */
const TOOLTIP_BG = 'rgba(14, 19, 17, 0.96)'
/* tooltip 样式按主题取：白天用白底深字（暗底的浅字在白背景上不可读）。
   改成函数的原因：模块级常量在首渲染时就固化了，拿不到主题。 */
const tooltipStyle = (isDark: boolean) => ({
  backgroundColor: isDark ? TOOLTIP_BG : '#05825c',
  borderColor: isDark ? 'rgba(180, 220, 195, 0.14)' : 'rgba(255,255,255,0.28)',
  borderWidth: 1,
  className: 'sentinel-chart-tooltip',
  textStyle: { color: isDark ? '#e9f0ea' : '#ffffff', fontSize: 12 },
  extraCssText:
    'backdrop-filter: blur(16px); border-radius: 13px; box-shadow: ' +
    (isDark ? '0 8px 28px rgba(0,0,0,.35);' : '0 8px 24px rgba(4,90,64,.4);'),
})

/** 流水线各阶段图标（按顺序） */
const PIPELINE_ICONS = [
  <DownloadOutlined key="i0" />,
  <TagsOutlined key="i1" />,
  <ThunderboltOutlined key="i2" />,
  <ShareAltOutlined key="i3" />,
]

/** 秒 → 人类可读（采集延迟跨度很大：秒 → 分 → 时 → 天） */
function humanizeSeconds(value: number | null | undefined): string {
  if (!value && value !== 0) return '—'
  if (value < 60) return `${Math.round(value)} 秒`
  if (value < 3600) return `${(value / 60).toFixed(1)} 分`
  if (value < 86400) return `${(value / 3600).toFixed(1)} 小时`
  return `${(value / 86400).toFixed(1)} 天`
}


export default function SituationPage() {
  const [rangeKey, setRangeKey] = useState<string>('24h')
  const [data, setData] = useState<SituationOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  const hours = RANGES.find((r) => r.key === rangeKey)?.hours

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await api.situationOverview(hours))
    } finally {
      setLoading(false)
    }
  }, [hours])

  useEffect(() => {
    void load()
    // 60 秒自动刷新：态势页看的是"活的"数据，
    // 但比看板的 30 秒更缓，避免大屏频繁重绘
    const timer = setInterval(() => void load(), 60_000)
    return () => clearInterval(timer)
  }, [load])

  /**
   * 整点对齐刷新（仅 24 小时档需要）
   * ------------------------------------------------------------
   * 后端的桶是**整点对齐**的（24 小时档 = 25 个小时桶），
   * 末桶固定为「当前小时」，到整点才会多一格。
   *
   * 用户要求：**每小时变一次，不要每分钟都动**。所以：
   *   · 取消每 15 秒推进的亚像素缓动（那样看着一直在抖，很慢）
   *   · 改为「整点瞬间换数据 + 1 秒滑动动画」，1 小时 = 1 次明显变化
   *
   * 关键：60 秒的固定轮询**与整点无关**，可能在 12:00:50 拿到 11点的数据、
   * 12:01:10 又拿到 11 点的数据——用户看到的就是「不更新」。
   * 所以 24 小时档改成**对齐整点后 3 秒刷新**，让「一小时一变」
   * 名副其实；其他档（天粒度）保持 60 秒轮询即可。
   */
  const hourAligned = rangeKey === '24h'
  useEffect(() => {
    if (!hourAligned) return
    const tick = () => {
      const now = new Date()
      // 整点后 3 秒触发：留 3 秒让后端把新一小时的数据查出来，
      // 否则会拿到「标签变了但数据没变」的中间态
      if (now.getMinutes() === 0 && now.getSeconds() >= 3 && now.getSeconds() < 33) {
        void load()
      }
    }
    tick()
    const t = setInterval(tick, 30_000)
    return () => clearInterval(t)
  }, [hourAligned, load])

  /* 主题：图表色必须走 JS（ECharts 不认 CSS 变量），见 lib/chartPalette */
  const { isDark } = useTheme()
  const { SEVERITY, CATEGORY, FALLBACK } = useSeverityColors(isDark)

  const m = data?.metrics
  const trend = data?.trend
  /** 当前 X 轴粒度：后端仅在小时粒度下返回 progress，据此判定。
   *  用于给图表 React key —— 粒度一变就重建实例，避免索引错配导致
   *  曲线「先出点、后连线」。 */
  const bucketKind = trend?.progress != null ? 'hour' : 'day'

  /* ---------- 趋势图 ---------- */
  const trendOption = useMemo(() => {
    const p = chartPalette(isDark)
    if (!trend || !trend.buckets.length) return null
    const axisData = trend.buckets
    return {
      grid: { left: 8, right: 16, top: 28, bottom: 4, containLabel: true },
      legend: {
        data: ['新增', '高危', '已富化'],
        right: 0,
        top: 0,
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { color: p.legend, fontSize: 11 },
      },
      tooltip: { trigger: 'axis', ...tooltipStyle(isDark) },
      /** 切换时间档位时的过渡动画。
       *
       *为什么这么配（24h→7d 曾出现「先出点、线再跟上」）：
       *   · `symbol` 常显时，折线会先逐个弹出数据点、再连线，
       *     点多且量级跨度大（24h 是 25 点/峰值 248，7d 是 8 点/峰值 4703）
       *     时这个空档被放大到肉眼可见。
       *   · 关闭常显 symbol（只在 hover / 轴缩放时出现）后，
       *     动画只剩「线从左到右画出」一种形态，不会再有先后错位。
       *   · 用 linear 而非 cubicOut：cubicOut 的加速段会让曲线「窜」出去，
       *     切档时像是数据在跳变而不是在滑动。
       */
      animationDuration: 600,
      animationDurationUpdate: 700,
      animationEasing: 'linear',
      animationEasingUpdate: 'linear',
      xAxis: {
        type: 'category',
        data: axisData,
        axisLabel: { color: p.axisLabel, fontSize: 11 },
        axisLine: { lineStyle: { color: p.axisLine } },
        axisTick: { show: false },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: p.axisLabel, fontSize: 11 },
        splitLine: { lineStyle: { color: p.splitLine } },
      },
      series: [
        {
          name: '新增',
          type: 'line',
          smooth: true,
          // 点少时常显（看得清每个采样点），点多时关掉——
          // 常显 symbol 会让折线「先弹点再连线」，切档时错位可见。
          // 24h 档 25 个点偏密，7d/30d 档 8~31 个点需要看得见标记。
          symbol: 'circle',
          symbolSize: 5,
          showSymbol: axisData.length <= 16,
          data: trend.new,
          lineStyle: { width: 2, color: p.cyan },
          itemStyle: { color: p.cyan },
          areaStyle: { color: 'rgba(76,215,246,0.08)' },
        },
        {
          name: '高危',
          type: 'line',
          smooth: true,
          // 点少时常显（看得清每个采样点），点多时关掉——
          // 常显 symbol 会让折线「先弹点再连线」，切档时错位可见。
          // 24h 档 25 个点偏密，7d/30d 档 8~31 个点需要看得见标记。
          symbol: 'circle',
          symbolSize: 5,
          showSymbol: axisData.length <= 16,
          data: trend.high,
          lineStyle: { width: 2, color: p.red },
          itemStyle: { color: p.red },
        },
        {
          name: '已富化',
          type: 'line',
          smooth: true,
          // 点少时常显（看得清每个采样点），点多时关掉——
          // 常显 symbol 会让折线「先弹点再连线」，切档时错位可见。
          // 24h 档 25 个点偏密，7d/30d 档 8~31 个点需要看得见标记。
          symbol: 'circle',
          symbolSize: 5,
          showSymbol: axisData.length <= 16,
          data: trend.enriched,
          lineStyle: { width: 2, color: p.green },
          itemStyle: { color: p.green },
        },
      ],
    }
  }, [trend, isDark])

  /* ---------- 分类环形图 ---------- */
  const categoryOption = useMemo(() => {
    // 本图的颜色全部来自 CATEGORY/FALLBACK（已按主题取），
    // 所以这里不需要通用色板。另注意 formatter 里的形参 p 是
    // ECharts 回调参数，别与色板变量重名。
    if (!data?.by_category.length) return null
    const total = data.by_category.reduce((s, x) => s + x.value, 0)
    return {
      tooltip: {
        trigger: 'item',
        formatter: (p: { name: string; value: number; percent: number }) =>
          `${p.name}<br/>${p.value} 条（${p.percent}%）`,
        ...tooltipStyle(isDark),
      },
      series: [
        {
          type: 'pie',
          radius: ['52%', '74%'],
          center: ['50%', '50%'],
          avoidLabelOverlap: true,
          label: { show: false },
          labelLine: { show: false },
          itemStyle: { borderWidth: 0 },
          data: data.by_category.map((x) => ({
            name: x.name,
            value: x.value,
            itemStyle: {
              color: CATEGORY[x.name] ?? FALLBACK,
            },
          })),
        },
      ],
      graphic: {
        type: 'text',
        left: 'center',
        /* top 定位的是文字块的**顶部**，不是中心。
           原来写 44% 等于把两行文字（行高 22×2 ≈ 44px）的顶边放在
           44% 处，文字重心落到约 60%，看着明显偏下。
           环心在 50%，减去半个文字块高度（约 22px，图高 150px ≈ 15%）
           → 35% 才是真正居中。 */
        top: '35%',
        style: {
          text: `${total}\n情报总数`,
          textAlign: 'center',
          /* 主题：SVG text 的 fill 是属性，CSS 盖不住，必须在这里取色。
             暗色用近白，白天用深墨绿 —— 白底白字是用户反馈的问题之一。 */
          fill: isDark ? '#e9f0ea' : '#12211a',
          fontSize: 15,
          lineHeight: 22,
          fontWeight: 500,
        },
      },
    }
  }, [data, isDark])

  /* ---------- 严重程度横向条 ---------- */
  const severityOption = useMemo(() => {
    const p = chartPalette(isDark)
    if (!data?.by_severity.length) return null
    return {
      grid: { left: 8, right: 40, top: 8, bottom: 4, containLabel: true },
      tooltip: { trigger: 'axis', ...tooltipStyle(isDark) },
      xAxis: {
        type: 'value',
        axisLabel: { color: p.axisLabel, fontSize: 11 },
        splitLine: { lineStyle: { color: p.splitLine } },
      },
      yAxis: {
        type: 'category',
        data: data.by_severity.map((x) => x.name),
        axisLabel: { color: p.axisLabelStrong, fontSize: 11 },
        axisLine: { lineStyle: { color: p.axisLine } },
        axisTick: { show: false },
      },
      series: [
        {
          type: 'bar',
          data: data.by_severity.map((x) => ({
            value: x.value,
            itemStyle: { color: SEVERITY[x.name] ?? FALLBACK },
          })),
          barWidth: '58%',
          label: {
            show: true,
            position: 'right',
            /* 数值标签在条形右侧的白底卡片上 —— 暗色的近白字白天不可读 */
            color: isDark ? '#e9f0ea' : '#12211a',
            fontSize: 11,
          },
        },
      ],
    }
  }, [data, isDark])

  const metricCards = m
    ? [
        {
          /* 档位为「全部」时后端不加时间过滤、统计的是全量；
             此时仍叫「新增」会与监控看板的「情报总量」打架 ——
             两个数字都像总量却对不上（实测曾因此被当成数据矛盾）。
             故按档位切换措辞。 */
          label: rangeKey === 'all' ? '情报总量' : '新增情报',
          value: m.new_intel.toLocaleString(),
          /* 副标题写清口径：不含攻击技术库 —— ATLAS 表无时间字段，
             无法按范围过滤，已从后端统计中移除，避免口径混用。 */
          sub: `漏洞 ${m.new_vuln.toLocaleString()} · 论文/政策 ${
            Object.values(m.other).reduce((s, n) => s + n, 0)
          }`,
          icon: <DatabaseOutlined />,
        },
        {
          label: '已富化',
          value: m.enriched.toLocaleString(),
          sub: `占新增 ${m.new_vuln ? Math.round((m.enriched / m.new_vuln) * 100) : 0}%`,
          icon: <ThunderboltOutlined />,
        },
        {
          label: '高危情报',
          value: m.high_count.toLocaleString(),
          sub: `占比 ${m.high_ratio}%`,
          icon: <AlertOutlined />,
        },
        {
          label: '平均评级分',
          value: m.avg_cvss ?? '—',
          sub: `已分类 ${m.classified}`,
          icon: <RadarChartOutlined />,
        },
        {
          label: '已建索引',
          value: m.indexed.toLocaleString(),
          sub: '向量检索可用',
          icon: <ShareAltOutlined />,
        },
        {
          label: '情报源',
          value: String(m.source_count),
          sub: '本周期有产出',
          icon: <DeploymentUnitOutlined />,
        },
      ]
    : []

  const topItems = data?.top_critical ?? []
  const sources = data?.sources ?? []
  const totalSourced = sources.reduce((s, x) => s + x.count, 0) || 1

  return (
    <div className="sentinel-situation">
      {/* ---- 顶部：标题 + 时间范围（仅本页生效） ---- */}
      <div className="sentinel-situation-head">
        <div>
          <h1>态势总览</h1>
          <p>
            当前视角：{data?.range.label ?? '—'} · 按
            {data?.range.bucket === 'hour' ? '小时' : '天'}聚合
          </p>
        </div>
        <div className="sentinel-range" role="group" aria-label="时间范围">
          {RANGES.map((r) => (
            <button
              key={r.key}
              type="button"
              className={
                'sentinel-range-item' + (rangeKey === r.key ? ' is-active' : '')
              }
              aria-pressed={rangeKey === r.key}
              onClick={() => setRangeKey(r.key)}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      <Spin spinning={loading && !data}>
        {/* ---- 指标卡 ---- */}
        <Row gutter={[10, 10]} className="sentinel-metric-row">
          {metricCards.map((c) => (
            <Col xs={12} md={8} xl={4} key={c.label}>
              <Card className="sentinel-metric" size="small">
                <span className="sentinel-metric-ico">{c.icon}</span>
                <span className="sentinel-metric-label">{c.label}</span>
                <b className="sentinel-metric-value">{c.value}</b>
                <small className="sentinel-metric-sub">{c.sub}</small>
              </Card>
            </Col>
          ))}
        </Row>

        {/* ---- 趋势 + 分类 ---- */}
        <Row gutter={[10, 10]}>
          <Col xs={24} xl={16}>
            <Card
              className="sentinel-reveal"
              size="small"
              title="情报采集与富化趋势"
              style={{ height: 320 }}
            >
              {trendOption ? (
                <ReactECharts
                  /** key 里带粒度：hour↔day 切换时**重建实例**。
                   *
                   * 为什么必须重建：切档时 X 轴的 category 数据是**整批替换**
                   * （"10-09 14:00" 这种小时标签换成 "10-09" 天标签），
                   * ECharts 会尝试把新旧索引一一配对做插值动画，
                   * 但索引根本对不上，曲线就退化成「分段画」——
                   * 表现正是用户看到的「先出现数据点，线后来再跟上」。
                   *
                   * 同粒度内切档（如7d→30d，都是 day）索引能对上，
                   * 走正常的更新动画，所以本来就没问题。
                   * 所以 key 只在粒度变化时变，其余情况仍复用实例。 */
                  key={`trend-${bucketKind}`}
                  option={trendOption}
                  style={{ height: 262 }}
                  opts={{ renderer: 'canvas' }}
                />
              ) : (
                <Empty description="所选范围内暂无数据" style={{ marginTop: 80 }} />
              )}
            </Card>
          </Col>
          <Col xs={24} xl={8}>
            <Card
              className="sentinel-reveal"
              size="small"
              title="AI 风险分类分布"
              style={{ height: 320 }}
            >
              {categoryOption ? (
                <>
                  <ReactECharts
                    option={categoryOption}
                    /* 190 → 150：给下方图例让出空间。
                       分类有4 项，图例每行约 27px，全高约 108px，
                       环形图 190 时两者相加超出卡片高度，最后一行被裁掉。 */
                    style={{ height: 150 }}
                    opts={{ renderer: 'canvas' }}
                    className="sentinel-chart is-drillable"
                    onEvents={{
                      click: (p: { name?: string }) =>
                        p?.name &&
                        navigate(`/intel?category=${encodeURIComponent(p.name)}`),
                    }}
                  />
                  <ul className="sentinel-cat-legend">
                    {(data?.by_category ?? []).map((x) => (
                      <li
                        key={x.name}
                        onClick={() =>
                          navigate(`/intel?category=${encodeURIComponent(x.name)}`)
                        }
                      >
                        <span
                          className="dot"
                          style={{
                            background: CATEGORY[x.name] ?? FALLBACK,
                          }}
                        />
                        <span className="name">{x.name}</span>
                        <b>{x.value}</b>
                        <small>
                          {((x.value /
                            (data?.by_category.reduce((s, i) => s + i.value, 0) || 1)) *
                            100
                          ).toFixed(1)}%
                        </small>
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <Empty description="暂无分类数据" style={{ marginTop: 80 }} />
              )}
            </Card>
          </Col>
        </Row>

        {/* ---- 最新高危 + 严重程度 + 延迟 ---- */}
        <Row gutter={[10, 10]}>
          <Col xs={24} xl={16}>
            <Card
              className="sentinel-reveal"
              size="small"
              title="最新高危情报"
              extra={
                <button
                  type="button"
                  className="sentinel-link-btn"
                  onClick={() => navigate('/intel?severity=CRITICAL')}
                >
                  查看全部 <ArrowRightOutlined />
                </button>
              }
              style={{ height: 340 }}
            >
              {topItems.length ? (
                <div className="sentinel-top-list">
                  {topItems.map((t) => (
                    <div
                      key={t.cve_id}
                      className="sentinel-top-row"
                      role="button"
                      tabIndex={0}
                      /** 长按选词时不要跳转。
                       *  onClick 绑在整行上时，浏览器选中文本后松开鼠标
                       *  也会派发 click（选区落在元素内即触发），
                       *  用户只想复制 CVE 号却被动跳转。
                       *  用 mousedown 记录起点，mouseup 时位移够大
                       *  就判定为「拖选/框选」并吞掉这次点击。 */
                      onMouseDown={(e) => {
                        ;(e.currentTarget as unknown as {
                          _skipClick: boolean
                        })._skipClick = false
                        ;(e.currentTarget as unknown as {
                          _startX: number
                          _startY: number
                        })._startX = e.clientX
                        ;(e.currentTarget as unknown as {
                          _startY: number
                        })._startY = e.clientY
                      }}
                      onMouseUp={(e) => {
                        const el = e.currentTarget as unknown as {
                          _skipClick: boolean
                          _startX: number
                          _startY: number
                        }
                        // 移动超过 5px 即视为拖动（选择文本的典型动作）
                        if (
                          Math.abs(e.clientX - el._startX) > 5 ||
                          Math.abs(e.clientY - el._startY) > 5
                        ) {
                          el._skipClick = true
                        }
                      }}
                      onClick={() => {
                        /* 整行**不跳转**。
                         * 只有 CVE 号可点—— 严重程度标签与标题占了大半宽度，
                         * 挂在整行上会让「想读标题的人」和「想复制编号的人」
                         * 互相干扰。 */
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          navigate(`/intel?q=${encodeURIComponent(t.cve_id)}`)
                        }
                      }}
                    >
                      {/* 唯一可点击区：编号。同时也是唯一参与选词的区域，
                          「复制编号」与「点击搜索」两种意图互不干扰 */}
                      <button
                        type="button"
                        className="cid is-link"
                        title="点击搜索该编号；拖选可复制"
                        onMouseDown={(e) => e.stopPropagation()}
                        onClick={(e) => {
                          e.stopPropagation()
                          // 情报中心读的是 `q`，不是 `keyword`
                          navigate(`/intel?q=${encodeURIComponent(t.cve_id)}`)
                        }}
                      >
                        {t.cve_id}
                      </button>
                      <span
                        className="sev"
                        style={{
                          background: SEVERITY[t.severity] ?? FALLBACK,
                        }}
                      >
                        {t.severity}
                      </span>
                      <span className="title" title={t.title}>
                        {t.title || '（无标题）'}
                      </span>
                      <b className="cvss">{t.cvss ?? '—'}</b>
                      <small className="src">{t.source ?? '—'}</small>
                    </div>
                  ))}
                </div>
              ) : (
                <Empty description="暂无高危情报" style={{ marginTop: 90 }} />
              )}
            </Card>
          </Col>
          <Col xs={24} xl={8}>
            <Card
              className="sentinel-reveal"
              size="small"
              title="严重程度分布"
              style={{ height: 340 }}
            >
              {severityOption ? (
                <ReactECharts
                  option={severityOption}
                  style={{ height: 250 }}
                  opts={{ renderer: 'canvas' }}
                  className="sentinel-chart is-drillable"
                  onEvents={{
                    click: (p: { name?: string }) =>
                      p?.name &&
                      navigate(`/intel?severity=${encodeURIComponent(p.name)}`),
                  }}
                />
              ) : (
                <Empty description="暂无数据" style={{ marginTop: 90 }} />
              )}
            </Card>
          </Col>
        </Row>

        {/* ---- 流水线 ---- */}
        <Row gutter={[10, 10]}>
          <Col xs={24}>
            <Card
              className="sentinel-reveal"
              size="small"
              title="自动化处理流水线"
              style={{ height: 190 }}
            >
              <div className="sentinel-pipeline">
                {(data?.pipeline ?? []).map((p, i) => {
                  const first = data?.pipeline[0]?.count || 1
                  const ratio = p.count ? Math.max(3, (p.count / first) * 100) : 3
                  return (
                    <div className="sentinel-pipeline-step" key={p.stage}>
                      <Tooltip title="口径：数据库中该阶段的实际条目数">
                        <span className="sentinel-step-box">
                          {/* 顶部高光：让卡片有"玻璃"厚度 */}
                          <i className="sentinel-step-sheen" aria-hidden="true" />
                          <span className="sentinel-step-ico">
                            {PIPELINE_ICONS[i] ?? <CheckCircleFilled />}
                          </span>
                          <b>{p.count.toLocaleString()}</b>
                          <small>{p.stage}</small>
                          {/* 占比条：把「入库 403 → 富化 19」的漏斗可视化 */}
                          <i
                            className="sentinel-step-bar"
                            style={{ width: `${ratio}%` }}
                          />
                        </span>
                      </Tooltip>

                      {i < (data?.pipeline.length ?? 0) - 1 && (
                        /* 连接线用 SVG 而非字符箭头：
                           一条渐变轨道 + 一个沿线移动的光点，
                           方向感与"数据在流动"的暗示都强于静态箭头。 */
                        <svg
                          className="sentinel-step-link"
                          viewBox="0 0 56 16"
                          preserveAspectRatio="none"
                          aria-hidden="true"
                        >
                          <defs>
                            <linearGradient id={`pl-grad-${i}`} x1="0" y1="0" x2="1" y2="0">
                              <stop offset="0%" stopColor="#4edea3" stopOpacity="0.15" />
                              <stop offset="55%" stopColor="#4edea3" stopOpacity="0.7" />
                              <stop offset="100%" stopColor="#4be1ec" stopOpacity="0.9" />
                            </linearGradient>
                          </defs>
                          <line
                            x1="2" y1="8" x2="46" y2="8"
                            stroke={`url(#pl-grad-${i})`}
                            strokeWidth="1.4"
                            strokeLinecap="round"
                          />
                          <path d="M44 4.5 L49.5 8 L44 11.5 Z" fill="#4be1ec" opacity="0.9" />
                          <circle r="2.4" fill="#b6f34a">
                            <animateMotion
                              dur="1.9s"
                              repeatCount="indefinite"
                              path="M2,8 L46,8"
                            />
                            <animate
                              attributeName="opacity"
                              values="0;1;1;0"
                              dur="1.9s"
                              repeatCount="indefinite"
                            />
                          </circle>
                        </svg>
                      )}
                    </div>
                  )
                })}
              </div>
            </Card>
          </Col>
        </Row>

        {/* ---- 情报源健康 ---- */}
        <Card
          className="sentinel-reveal"
          size="small"
          title="情报源健康"
          style={{ marginTop: 10 }}
        >
          {sources.length ? (
            <div className="sentinel-source-grid">
              {sources.map((s) => (
                <div className="sentinel-source" key={s.code}>
                  <div className="head">
                    <span className="name" title={s.name}>
                      {s.name}
                    </span>
                    <span
                      className={
                        'rate' +
                        (s.success_rate == null
                          ? ''
                          : s.success_rate >= 90
                            ? ' is-good'
                            : s.success_rate >= 60
                              ? ' is-warn'
                              : ' is-bad')
                      }
                    >
                      {s.success_rate == null ? '—' : `${s.success_rate}%`}
                    </span>
                  </div>
                  <div className="bar">
                    <span style={{ width: `${(s.count / totalSourced) * 100}%` }} />
                  </div>
                  <div className="foot">
                    <span>{s.count.toLocaleString()} 条</span>
                    <span>
                      {s.runs} 次采集
                      {s.avg_latency != null
                        ? ` · ${humanizeSeconds(s.avg_latency / 1000)}`
                        : ''}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Empty description="所选范围内暂无采集记录" />
          )}
        </Card>
      </Spin>
    </div>
  )
}