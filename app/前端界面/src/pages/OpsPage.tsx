/**
 * 系统运维与自愈
 * =================================================================
 * 观测整条链路（编排 Agent → 采集器 → 依赖）的运行状态，
 * 以及刚刚发生的自愈动作与告警。
 *
 * 数据全部来自真实运行：
 *   · 编排节点 → ops_history 环形缓冲 + BaseAgent.health()
 *   · 采集器   → collection_logs 聚合
 *   · 资源     → psutil 实时采样
 *   · 依赖     → /api/health 探测
 *
 * 一个原则：**没跑过就显示「未执行」，不显示 0%**——
 * 运维页最怕的是「一切正常」的假象。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Card, Col, Empty, Row, Spin, Tooltip } from 'antd'
import ReactECharts from 'echarts-for-react'
import { chartPalette } from '@/lib/chartPalette'
import { useTheme } from '@/lib/theme'
import {
  ApiOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
  WarningFilled,
  CheckCircleFilled,
  ThunderboltTwoTone,
  ExperimentOutlined,
  DeploymentUnitOutlined,
} from '@ant-design/icons'
import {
  api,
  type OpsComponents,
  type OpsEvents,
  type OpsNode,
  type OpsResources,
  type OpsTimeline,
} from '@/api/client'
import PipelineFlow from '@/components/PipelineFlow'

/** tooltip 底色（暗色）——白天用白底，见下方 TOOLTIP_STYLE */
const TOOLTIP_BG = 'rgba(14, 19, 17, 0.96)'

/* tooltip 按主题取色：白天白底深字。
   改成函数是因为模块级常量拿不到运行时主题。 */
const TOOLTIP_STYLE = (isDark: boolean) => ({
  backgroundColor: isDark ? TOOLTIP_BG : '#05825c',
  borderColor: isDark ? 'rgba(180,220,195,.14)' : 'rgba(255,255,255,0.28)',
  borderWidth: 1,
  className: 'sentinel-chart-tooltip',
  textStyle: { color: isDark ? '#e9f0ea' : '#ffffff', fontSize: 12 },
  extraCssText:
    'backdrop-filter: blur(16px); border-radius: 13px; box-shadow: ' +
    (isDark ? '0 8px 28px rgba(0,0,0,.35);' : '0 8px 24px rgba(4,90,64,.4);'),
})

const NODE_ICON: Record<string, JSX.Element> = {
  monitor: <ApiOutlined />,
  triage: <ExperimentOutlined />,
  enrich: <ThunderboltOutlined />,
  skip_enrich: <CheckCircleFilled />,
  qa: <ThunderboltTwoTone />,
  supervisor: <WarningFilled />,
  reflect: <ReloadOutlined />,
  scheduler: <ClockCircleOutlined />,
}

/** 毫秒 → 可读 */
function ms(v: number | null | undefined): string {
  if (v == null) return '—'
  if (v < 1000) return `${Math.round(v)} ms`
  if (v < 60_000) return `${(v / 1000).toFixed(1)} s`
  return `${(v / 60_000).toFixed(1)} 分`
}

/** 秒 → 可读时长 */
function uptime(sec: number | null | undefined): string {
  if (!sec) return '—'
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (d > 0) return `${d} 天 ${h} 小时`
  if (h > 0) return `${h} 小时 ${m} 分`
  return `${m} 分钟`
}

/** HH:MM:SS——用于展示「最近一次成功刷新的时刻」 */
function clock(d: Date | null): string {
  if (!d) return '—'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** 相对时间：「刚刚 / N 分钟前 / N 小时前 / N 天前」——Agent 触发时间用 */
function timeAgo(iso: string | null | undefined): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const diff = Math.max(0, Date.now() - t)
  const m = Math.floor(diff / 60_000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.floor(h / 24)} 天前`
}

/** 成功率 → 颜色类 */
function rateClass(rate: number | null | undefined, runs?: number): string {
  if (runs === 0) return 'is-idle'
  if (rate == null) return 'is-idle'
  if (rate >= 95) return 'is-good'
  if (rate >= 80) return 'is-warn'
  return 'is-bad'
}

/** 环形进度（纯 SVG，不引图表库） */
function Ring({
  percent,
  label,
  sub,
  tone,
}: {
  percent: number
  label: string
  sub: string
  tone: string
}) {
  const r = 34
  const c = 2 * Math.PI * r
  const p = Math.max(0, Math.min(100, percent))
  return (
    <div className="ops-ring">
      {/* 80 → 108 → 128：内圈直径 = 128 × (1 − 2×7/80) ≈ 106px。
          历次放大都是因为副文本被环壁压住：
          80px 时内圈仅 66px，「19.8/27.8 GB」这类文本宽约 66px 正好
          横穿描边；108px 时内圈 89px 勉强放下；128px 后余量充足，
          副文本可以再放大一档字号而不压环。 */}
      <svg viewBox="0 0 80 80" width="128" height="128">
        <circle cx="40" cy="40" r={r} fill="none" stroke="rgba(255,255,255,.07)" strokeWidth="7" />
        <circle
          cx="40"
          cy="40"
          r={r}
          fill="none"
          stroke={tone}
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={`${(c * p) / 100} ${c}`}
          transform="rotate(-90 40 40)"
        />
      </svg>
      <div className="ops-ring-txt">
        <b>{Math.round(p)}%</b>
        <small>{label}</small>
        <em>{sub}</em>
      </div>
    </div>
  )
}

export default function OpsPage() {
  const [comp, setComp] = useState<OpsComponents | null>(null)
  const [res, setRes] = useState<OpsResources | null>(null)
  const [tl, setTl] = useState<OpsTimeline | null>(null)
  const [ev, setEv] = useState<OpsEvents | null>(null)
  const [loading, setLoading] = useState(true)
  // 本轮刷新是否失败：数据仍是上一轮的，但必须让用户知道
  const [stale, setStale] = useState(false)
  // 最近一次**成功**刷新的时刻，用于在界面上明示数据新鲜度
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  // load 需读它判断是否首次加载，又不能把它放进依赖（会重建回调）
  const compRef = useRef<OpsComponents | null>(null)
  // 每分钟重渲染一次，让「X 分钟前触发」自己走动。
  // 否则页面静止不动时，相对时间会停在某个值上——
  // 用户看到的就不是实时状态了。
  const [, setTick] = useState(0)

  const load = useCallback(async () => {
    // 静默刷新：只有首次加载才转圈。
    // 每 30 秒都setLoading(true) 会让整页反复闪 loading，
    // 反而看不出数字在变——「实时」的感觉是被转圈动画毁掉的。
    setLoading((prev) => (compRef.current ? prev : true))
    try {
      const [c, r, t, e] = await Promise.all([
        api.opsComponents(),
        api.opsResources(),
        api.opsTimeline(24),
        api.opsEvents(60),
      ])
      setComp(c)
      compRef.current = c
      setRes(r)
      setTl(t)
      setEv(e)
      setStale(false)
      setUpdatedAt(new Date())
    } catch {
      // 单轮失败不能停掉整个轮询（否则页面就此冻结，
      // 数字再也不变——那才是真的「不实时」）。标记为陈旧即可。
      setStale(true)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const t = setInterval(() => {
      // 标签页在后台时跳过：既省流量，也避免回来时看到
      // 一堆过期数据一次性跳变。
      if (document.visibilityState === 'visible') void load()
    }, 30_000)
    // 从后台切回前台立刻补一次刷新，别让用户盯着旧数字
    const onVis = () => {
      if (document.visibilityState === 'visible') void load()
    }
    document.addEventListener('visibilitychange', onVis)
    // 相对时间自走：每 15秒 触发一次重渲染
    const tick = setInterval(() => setTick((n) => n + 1), 15_000)
    return () => {
      clearInterval(t)
      clearInterval(tick)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [load])

  /* 主题：图表色走 JS（ECharts 不认 CSS 变量），见 lib/chartPalette */
  const { isDark } = useTheme()

  /* ---------- 健康总判 ---------- */
  const health = useMemo(() => {
    if (!comp) return { label: '检测中', tone: 'var(--muted)' }
    const circuitOpen = comp.nodes.some((n) => n.circuit_open)
    const paused = comp.scheduler.paused
    const errCount = ev?.counts.error ?? 0
    if (circuitOpen) return { label: '已熔断', tone: 'var(--danger)' }
    if (errCount > 0) return { label: '有告警', tone: 'var(--warn)' }
    if (paused) return { label: '调度已暂停', tone: 'var(--warn)' }
    return { label: '运行正常', tone: 'var(--accent)' }
  }, [comp, ev])

  /* ---------- 队列趋势图 ---------- */
  const timelineOption = useMemo(() => {
    const p = chartPalette(isDark)
    if (!tl) return null
    return {
      grid: { left: 8, right: 14, top: 30, bottom: 4, containLabel: true },
      legend: {
        data: ['采集执行', '采集失败', '编排执行', '平均延迟 (秒)'],
        right: 0,
        top: 0,
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { color: p.legend, fontSize: 11 },
      },
      tooltip: { trigger: 'axis', ...TOOLTIP_STYLE(isDark) },
      xAxis: {
        type: 'category',
        data: tl.labels,
        axisLabel: { color: p.axisLabel, fontSize: 10 },
        axisLine: { lineStyle: { color: p.axisLine } },
        axisTick: { show: false },
      },
      yAxis: [
        {
          type: 'value',
          name: '次',
          nameTextStyle: { color: p.axisLabel, fontSize: 10 },
          axisLabel: { color: p.axisLabel, fontSize: 10 },
          splitLine: { lineStyle: { color: p.splitLine } },
        },
        {
          /* 不设 name：它会渲染在轴顶，正好压住 legend 的「平均延迟」。
             单位写进 legend 文案里，信息不丢。 */
          type: 'value',
          axisLabel: {
            color: '#6b7870',
            fontSize: 10,
            formatter: (v: number) => `${Math.round(v / 1000)}s`,
          },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: '采集执行',
          type: 'bar',
          data: tl.collector_runs,
          itemStyle: { color: isDark ? 'rgba(77,222,163,.55)' : 'rgba(4,125,87,.5)' },
          barMaxWidth: 12,
        },
        {
          name: '采集失败',
          type: 'bar',
          data: tl.collector_fails,
          itemStyle: { color: 'var(--danger)' },
          barMaxWidth: 12,
        },
        {
          name: '编排执行',
          type: 'line',
          smooth: true,
          symbol: 'circle',
          symbolSize: 4,
          data: tl.agent_runs,
          lineStyle: { color: p.cyan, width: 2 },
          itemStyle: { color: p.cyan },
        },
        {
          name: '平均延迟 (秒)',
          type: 'line',
          yAxisIndex: 1,
          smooth: true,
          symbol: 'none',
          connectNulls: true,
          data: tl.avg_latency_ms,
          lineStyle: { color: '#ffc857', width: 1.6, type: 'dashed' },
          itemStyle: { color: '#ffc857' },
        },
      ],
    }
  }, [tl, isDark])

  const dbPercent =
    res?.database.connections != null && res.database.max_connections
      ? (res.database.connections / res.database.max_connections) * 100
      : 0

  return (
    <div className="sentinel-ops">
      {/* ---- 顶部 ---- */}
      <div className="sentinel-ops-head">
        <div>
          <h1>系统运维与自愈</h1>
          <p>
            编排监控 · 熔断隔离 · 反思重试 · 资源水位 —— 全部来自真实运行数据
          </p>
        </div>
        <div className="sentinel-ops-actions">
          <span className="ops-health" style={{ color: health.tone }}>
            <i className="dot" style={{ background: health.tone }} />
            {health.label}
          </span>
          <span className="ops-uptime">
            运行 {uptime(res?.process.uptime_seconds)}
          </span>
          {/* 明示数据新鲜度：数字在动是「实时」，用户得能自己确认 */}
          <Tooltip
            title={
              stale
                ? '最近一次刷新失败，当前显示的是上一次成功的数据'
                : '每 30 秒自动刷新'
            }
          >
            <span className={'ops-updated' + (stale ? ' is-stale' : '')}>
              {stale ? '数据陈旧' : `${clock(updatedAt)} 更新`}
            </span>
          </Tooltip>
          <Tooltip title="立即刷新">
            <button
              type="button"
              className="sentinel-ops-refresh"
              onClick={() => void load()}
              aria-label="刷新"
            >
              <ReloadOutlined />
            </button>
          </Tooltip>
        </div>
      </div>

      <Spin spinning={loading && !comp}>
        {/* ---- 编排链路 ---- */}
        <Card className="ops-card" size="small" title="编排链路 · LangGraph 状态机">
          <div className="ops-node-grid">
            {(comp?.nodes ?? []).map((n: OpsNode) => (
              <div
                key={n.key}
                className={
                  'ops-node' +
                  (n.circuit_open ? ' is-circuit' : n.healthy === false ? ' is-down' : '')
                }
              >
                <div className="ops-node-top">
                  <span className="ops-node-ico">{NODE_ICON[n.key] ?? <ApiOutlined />}</span>
                  {n.kind === 'scheduler' ? (
                    <span className={'ops-node-state ' + (n.paused ? 'is-warn' : 'is-good')}>
                      {n.paused ? '已暂停' : '运行中'}
                    </span>
                  ) : n.circuit_open ? (
                    <span className="ops-node-state is-bad">熔断</span>
                  ) : (
                    /* 有触发记录 → 显示「多久前触发」；
                       从未有记录才显示「待触发」。
                       纯文字「待触发」让人无法分辨系统是否活动过 */
                    <span
                      className={
                        'ops-node-state ' +
                        (timeAgo(n.last_run_at) ? 'is-good' : 'is-idle')
                      }
                    >
                      {timeAgo(n.last_run_at)
                        ? `${timeAgo(n.last_run_at)}触发`
                        : '待触发'}
                    </span>
                  )}
                </div>
                <b className="ops-node-name">{n.name}</b>
                <div className="ops-node-metrics">
                  {n.kind === 'scheduler' ? (
                    <>
                      <span>
                        <em>数据源</em>
                        <strong>{comp?.scheduler.count ?? 0}</strong>
                      </span>
                      <span>
                        <em>状态</em>
                        <strong>{n.paused ? '暂停' : '运行'}</strong>
                      </span>
                    </>
                  ) : (n.runs ?? 0) > 0 ? (
                    <>
                      <span>
                        <em>执行</em>
                        <strong>{fmt(n.runs)}</strong>
                      </span>
                      <span>
                        <em>成功率</em>
                        <strong className={rateClass(n.success_rate, n.runs)}>
                          {n.success_rate == null ? '—' : `${n.success_rate}%`}
                        </strong>
                      </span>
                      <span>
                        <em>均耗时</em>
                        <strong>{ms(n.avg_ms)}</strong>
                      </span>
                    </>
                  ) : timeAgo(n.last_run_at) ? (
                    /* 无累计执行但有触发时间（进程内记录被重启清零）——
                       仍如实显示触发时间，悬停看触发条件与记录范围 */
                    <Tooltip
                      title={
                        (n.trigger ? `触发条件：${n.trigger}` : '') +
                        (n.record_scope ? `（${n.record_scope}）` : '')
                      }
                    >
                      <span className="ops-node-idle">
                        {timeAgo(n.last_run_at)}触发过 · 见说明
                      </span>
                    </Tooltip>
                  ) : (
                    /* 从未有触发记录 */
                    <Tooltip title={n.trigger ?? '等待首次触发'}>
                      <span className="ops-node-idle">
                        {n.trigger ? '本场景不触发 · 见说明' : '等待首次触发'}
                      </span>
                    </Tooltip>
                  )}
                </div>
                {n.last_error && (
                  <Tooltip title={n.last_error}>
                    <span className="ops-node-err">{n.last_error}</span>
                  </Tooltip>
                )}
              </div>
            ))}
          </div>

          {/* ---- 链路流程图 ----
              画的是 orchestrator._build_graph() 里真实的图结构，
              不是示意图。放在节点卡片下方是因为上面那排卡片只回答
              「现在跑得怎么样」，而这张图回答「整体怎么串起来」——
              两个视角缺一不可：光看卡片看不出 triage 会分叉，
              也不知道 supervisor 与 reflect 是回环关系。 */}
          <PipelineFlow nodes={comp?.nodes ?? []} schedulerCount={comp?.scheduler.count ?? 0} />
        </Card>

        {/* ---- 采集器 ---- */}
        <Card
          className="ops-card"
          size="small"
          title="数据源采集"
          extra={
            <span className="ops-extra">
              累计执行 {fmt(comp?.totals.collector_runs ?? 0)} 次
            </span>
          }
        >
          <div className="ops-collector-grid">
            {(comp?.collectors ?? []).map((x) => (
              <div className="ops-collector" key={x.key}>
                <div className="head">
                  <span className="name" title={x.name}>
                    {x.name}
                  </span>
                  <span className={'rate ' + rateClass(x.success_rate, x.runs)}>
                    {x.runs === 0
                      ? '未采集'
                      : x.success_rate == null
                        ? '—'
                        : `${x.success_rate}%`}
                  </span>
                </div>
                <div className="ops-collector-bar">
                  <i
                    style={{
                      width: `${Math.min(100, ((x.success_rate ?? 0) / 100) * 100)}%`,
                    }}
                  />
                </div>
                <div className="foot">
                  <span>
                    {fmt(x.runs)} 次 · 新增 {fmt(x.new_items)}
                  </span>
                  <span>{ms(x.avg_ms)}</span>
                </div>
              </div>
            ))}
          </div>
        </Card>

        {/* ---- 趋势 + 资源 ---- */}
        <Row gutter={[10, 10]} className="ops-pair-row">
          <Col xs={24} xl={16}>
            <Card className="ops-card" size="small" title="队列与延迟趋势 · 近 24 小时">
              {timelineOption ? (
                <ReactECharts
                  option={timelineOption}
                  style={{ height: 262 }}
                  opts={{ renderer: 'canvas' }}
                />
              ) : (
                <Empty description="暂无数据" style={{ marginTop: 80 }} />
              )}
            </Card>
          </Col>
          <Col xs={24} xl={8}>
            <Card className="ops-card" size="small" title="资源水位">
              <div className="ops-rings">
                <Ring
                  percent={res?.cpu.percent ?? 0}
                  label="CPU"
                  sub={`${res?.cpu.cores ?? '—'} 核`}
                  tone="#4edea3"
                />
                <Ring
                  percent={res?.memory.percent ?? 0}
                  label="内存"
                  sub={
                    res ? `${res.memory.used_gb}/${res.memory.total_gb} GB` : '—'
                  }
                  tone="#4cd7f6"
                />
                <Ring
                  percent={res?.disk.percent ?? 0}
                  label="磁盘"
                  sub={res ? `${res.disk.used_gb}/${res.disk.total_gb} GB` : '—'}
                  tone="#ffc857"
                />
                <Ring
                  percent={dbPercent}
                  label="数据库连接"
                  sub={
                    res?.database.connections != null
                      ? `${res.database.connections}/${res.database.max_connections}`
                      : '—'
                  }
                  tone="#b6f34a"
                />
              </div>
            </Card>
          </Col>
        </Row>

        {/* ---- 自愈事件 + 告警 ---- */}
        <Row gutter={[10, 10]} className="ops-pair-row">
          <Col xs={24} xl={16}>
            <Card
              className="ops-card"
              size="small"
              title="自愈事件时间线"
              extra={
                <span className="ops-extra">
                  熔断 · 隔离 · 重试 · 恢复 —— 来自编排器与调度器
                </span>
              }
            >
              {ev?.events.length ? (
                <ul className="ops-timeline">
                  {ev.events.slice(0, 20).map((e, i) => (
                    <li key={i} className={'is-' + e.level}>
                      <span className="dot" />
                      <div className="body">
                        <b>{e.title}</b>
                        {e.detail && <small>{e.detail}</small>}
                      </div>
                      <time>
                        {e.ts
                          ? new Date(e.ts * 1000).toLocaleTimeString('zh-CN', {
                              hour: '2-digit',
                              minute: '2-digit',
                              second: '2-digit',
                            })
                          : '—'}
                      </time>
                    </li>
                  ))}
                </ul>
              ) : (
                /* 空状态要说清「这是好事」——
                   用 Empty 的默认插画会被当成"加载失败/功能坏了"，
                   而这里其实是「一切正常，无需自愈」。 */
                <div className="ops-healthy">
                  <CheckCircleFilled className="ico" />
                  <b>一切正常，无需自愈</b>
                  <small>
                    近24 小时无熔断、无重试。
                    Agent 失败会自动重试并记录在这里，当前采集成功率{' '}
                    {tl && tl.totals.collector_runs > 0
                      ? `${Math.round(
                          ((tl.totals.collector_runs - tl.totals.collector_fails) /
                            tl.totals.collector_runs) *
                            100,
                        )}%`
                      : '—'}
                    。
                  </small>
                </div>
              )}
            </Card>
          </Col>
          <Col xs={24} xl={8}>
            <Card
              className="ops-card"
              size="small"
              title="告警中心"
              extra={
                <span className="ops-extra">
                  {ev?.counts.error ?? 0} 严重 · {ev?.counts.warn ?? 0} 警告
                </span>
              }
            >
              {ev?.alerts.length ? (
                <ul className="ops-alerts">
                  {ev.alerts.map((a) => (
                    <li key={a.source} className={'is-' + a.level}>
                      <DatabaseOutlined />
                      <span className="nm">{a.name}</span>
                      <b>{a.fails}</b>
                      <small className="tm">
                        {a.last_at
                          ? new Date(a.last_at).toLocaleString('zh-CN', {
                              month: '2-digit',
                              day: '2-digit',
                              hour: '2-digit',
                              minute: '2-digit',
                            })
                          : '—'}
                      </small>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="ops-healthy">
                  <CheckCircleFilled className="ico" />
                  <b>近 24 小时零失败</b>
                  <small>
                    {tl?.totals.collector_runs ?? 0} 次采集全部成功。
                    数据源出现失败或熔断时会在这里列出。
                  </small>
                </div>
              )}
              <p className="ops-note">
                <DeploymentUnitOutlined /> 图数据库
                {comp?.graph?.available
                  ? ` 正常 · ${fmt(comp.graph.nodes ?? 0)} 节点`
                  : ' 不可用'}
              </p>
            </Card>
          </Col>
        </Row>
      </Spin>
    </div>
  )
}

function fmt(n: number | null | undefined): string {
  return (n ?? 0).toLocaleString('en-US')
}