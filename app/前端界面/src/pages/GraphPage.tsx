/**
 * 知识图谱
 * =================================================================
 * 把 Neo4j 里的关联关系可视化：一张可交互的力导向图 +
 * 右侧的节点详情、多跳链路与关系分布。
 *
 * 交互（全部交给 ECharts 的 graph 系列，不自己实现拖拽逻辑）：
 *   · 滚轮缩放 / 空白拖动平移 —— roam
 *   · 拖拽节点               —— forceLayout.draggable + emphasis 聚焦
 *   · 点击节点选中           —— 联动右侧详情面板
 *
 * 节点规模控制：
 *   全图两千多个节点，直接丢给浏览器跑力导向会卡死（O(n²) 弹簧力）。
 *   后端按连接度取 topN，前端再按「最小连接度」二次过滤，
 *   用户可自己决定看多稠密的子图。
 *
 * 配色沿用 sentinel 设计令牌，未引入新色值。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Card, Empty, Select, Spin, Tooltip } from 'antd'
import ReactECharts from 'echarts-for-react'
import { useTheme } from '@/lib/theme'
import {
  NodeIndexOutlined,
  ShareAltOutlined,
  DeploymentUnitOutlined,
  RadarChartOutlined,
  AimOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import {
  api,
  type GraphNodeItem,
  type SituationGraphNode,
  type SituationGraphOverview,
  type SituationGraphPath,
} from '@/api/client'

/* 节点配色：与情报中心严重度色板、态势总览分类色板保持同一套语言 */
const LABEL_COLOR: Record<string, string> = {
  Vulnerability: '#bd1f36',
  Component: '#8a9a9a',
  Technique: '#4cd7f6',
  POC: '#ff9505',
  Paper: '#4edea3',
  Policy: '#c084fc',
  CWE: '#fbbf24',
}

/** 白天版节点语义色：同色相加深。
 *  实测原色在白底上 Technique 1.67 / Paper 1.68 / CWE 1.64 / POC 2.16 —— 全部不合格，
 *  加深后全部 ≥4.5:1。图谱是密集图形，低于 3:1 就会糊成一片分不清节点类型。 */
const LABEL_COLOR_LIGHT: Record<string, string> = {
  Vulnerability: '#a01328',
  Component: '#55645c',
  Technique: '#0a7490',
  POC: '#b85409',
  Paper: '#04785a',
  Policy: '#7c3aed',
  CWE: '#a06a00',
}

const FALLBACK_NODE_COLOR = '#5f8f7d'
const FALLBACK_NODE_COLOR_LIGHT = '#3d6b5c'

/** 图谱配色集中在一处，按主题取值。 */
function graphColors(isDark: boolean) {
  const map = isDark ? LABEL_COLOR : LABEL_COLOR_LIGHT
  return {
    node: (label: string) => map[label] ?? (isDark ? FALLBACK_NODE_COLOR : FALLBACK_NODE_COLOR_LIGHT),
    /** 选中节点描边：暗色用近白，白天用近黑 */
    selectBorder: isDark ? '#e9f0ea' : '#12211a',
    /** 路径高亮色：荧光绿白底只有 1.5:1，白天必须换深绿 */
    path: isDark ? '#b6f34a' : '#047d57',
    /** 普通节点描边 */
    border: isDark ? 'rgba(255,255,255,.14)' : 'rgba(23,58,43,.16)',
    /** 节点标签文字（挂在节点旁，属于正文级） */
    label: isDark ? '#c8d4cd' : '#46584f',
    /** 连线 */
    link: isDark ? 'rgba(120,180,200,.22)' : 'rgba(14,116,144,.24)',
  }
}

const TOOLTIP_STYLE = (isDark: boolean) => ({
  backgroundColor: isDark ? 'rgba(14, 19, 17, 0.96)' : '#05825c',
  borderColor: isDark ? 'rgba(180, 220, 195, 0.14)' : 'rgba(255,255,255,0.28)',
  borderWidth: 1,
  className: 'sentinel-chart-tooltip',
  textStyle: { color: isDark ? '#e9f0ea' : '#ffffff', fontSize: 12 },
  extraCssText:
    'backdrop-filter: blur(16px); border-radius: 13px; box-shadow: ' +
    (isDark ? '0 8px 28px rgba(0,0,0,.35);' : '0 8px 24px rgba(4,90,64,.4);'),
})

/** 图例数据（标签 → 中文名 + 颜色） */
function buildLegend(nodes: GraphNodeItem[], colorOf: (label: string) => string) {
  const seen = new Map<string, number>()
  nodes.forEach((n) => seen.set(n.label, (seen.get(n.label) ?? 0) + 1))
  return [...seen.entries()]
    .map(([label, count]) => ({
      label,
      name: nodes.find((n) => n.label === label)?.label_cn ?? label,
      count,
      color: colorOf(label),
    }))
    .sort((a, b) => b.count - a.count)
}

export default function GraphPage() {
  /* 主题：图谱是密集图形，节点色/标签/连线必须按主题换——
     暗色那套在白底上大量 <3:1（见 LABEL_COLOR_LIGHT 注释）。 */
  const { isDark } = useTheme()
  const gc = graphColors(isDark)

  const [data, setData] = useState<SituationGraphOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  /* 子图密度：节点数 + 最小连接度 */
  const [limit, setLimit] = useState(120)
  const [minDegree, setMinDegree] = useState(1)
  /** 过滤掉的标签（不参与渲染） */
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  /** 选中节点与其详情 */
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<SituationGraphNode | null>(null)
  /** 多跳链路 */
  const [pathFrom, setPathFrom] = useState<string | null>(null)
  const [pathTo, setPathTo] = useState<string | null>(null)
  const [path, setPath] = useState<SituationGraphPath | null>(null)
  const [pathLoading, setPathLoading] = useState(false)
  const chartRef = useRef<ReactECharts | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await api.graphOverview(limit, minDegree))
    } catch (e) {
      setError((e as Error).message || '图数据库不可用')
    } finally {
      setLoading(false)
    }
  }, [limit, minDegree])

  useEffect(() => {
    void load()
  }, [load])

  /* 选中节点 → 拉详情 */
  useEffect(() => {
    if (!selected) {
      setDetail(null)
      return
    }
    let alive = true
    void api
      .graphNode(selected)
      .then((d) => {
        if (alive) setDetail(d)
      })
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [selected])

  const nodes = data?.nodes ?? []
  const links = data?.links ?? []
  const legend = useMemo(() => buildLegend(nodes, gc.node), [nodes, isDark])

  /* 标签过滤后的可见集合 */
  const visibleNodes = useMemo(
    () => nodes.filter((n) => !hidden.has(n.label)),
    [nodes, hidden],
  )
  const visibleIds = useMemo(
    () => new Set(visibleNodes.map((n) => n.id)),
    [visibleNodes],
  )
  const visibleLinks = useMemo(
    () => links.filter((l) => visibleIds.has(l.source) && visibleIds.has(l.target)),
    [links, visibleIds],
  )

  /** 路径上的节点 id 集合——用于在图上高亮链路 */
  const pathIds = useMemo(
    () => new Set((path?.nodes ?? []).map((n) => n.id)),
    [path],
  )

  const option = useMemo(() => {
    const maxDegree = Math.max(...visibleNodes.map((n) => n.degree), 1)
    const nodesOnPath = pathIds
    /* 路径高亮要在闭包里用到 path，这里先取别名：
       useMemo 依赖里有 path，但 TS 不会据此收窄闭包内的可空性。 */
    const pathEdges = path?.relations ?? []
    const isPathEdge = (from: string, to: string) =>
      pathEdges.some(
        (r) =>
          (r.from === from && r.to === to) || (r.from === to && r.to === from),
      )

    return {
      animationDuration: 800,
      animationEasingUpdate: 'quinticInOut',
      tooltip: {
        ...TOOLTIP_STYLE(isDark),
        formatter: (p: { dataType: string; data: Record<string, unknown> }) => {
          if (p.dataType === 'edge') {
            return `关系：${p.data.type_cn}`
          }
          const d = p.data as unknown as {
            name: string
            label_cn: string
            degree: number
            cvss?: number | null
            severity?: string | null
          }
          const cvss = d.cvss ? `<br/>CVSS：<b>${d.cvss}</b>` : ''
          const sev = d.severity ? `（${d.severity}）` : ''
          return `<b>${d.name}</b><br/>类型：${d.label_cn}${sev}<br/>连接度：${d.degree}${cvss}`
        },
      },
      series: [
        {
          type: 'graph',
          layout: 'force',
          /* ---- 交互三件套 ---- */
          roam: true, // 滚轮缩放 + 空白拖动平移
          draggable: true, // 拖拽节点
          zoom: 1.05,
          force: {
            repulsion: 620,
            edgeLength: [110, 230],
            gravity: 0.06,
            friction: 0.22,
            layoutAnimation: true,
          },
          categories: legend.map((l) => ({ name: l.label, itemStyle: { color: l.color } })),
          emphasis: {
            focus: 'adjacency',
            scale: 1.12,
            lineStyle: { width: 2.4, opacity: 0.9 },
            label: { show: true },
          },
          blur: { itemStyle: { opacity: 0.16 }, lineStyle: { opacity: 0.06 } },
          data: visibleNodes.map((n) => {
            const onPath = nodesOnPath.has(n.id)
            const isSel = n.id === selected
            return {
              id: n.id,
              name: n.name,
              label_cn: n.label_cn,
              degree: n.degree,
              cvss: n.cvss,
              severity: n.severity,
              category: legend.findIndex((l) => l.label === n.label),
              /* 节点大小按连接度：枢纽更大，一眼看出"核心组件" */
              symbolSize: 8 + Math.min(30, (n.degree / maxDegree) * 30),
              itemStyle: {
                color: gc.node(n.label),
                borderColor: isSel
                  ? gc.selectBorder
                  : onPath
                    ? gc.path
                    : gc.border,
                borderWidth: isSel ? 3 : onPath ? 2.4 : 1,
                shadowBlur: isSel || onPath ? 16 : 0,
                shadowColor: isSel || onPath ? gc.path : 'transparent',
              },
              label: {
                /* 一百多个节点全挂标签会糊成一片（实测过）：
                   只给枢纽、选中项与路径节点常显，其余靠 hover 出标签。 */
                show: n.degree >= 25 || isSel || onPath,
                position: 'right',
                fontSize: 10.5,
                color: gc.label,
                formatter: (p: { name: string }) => {
                  const t = p.name
                  return t.length > 20 ? t.slice(0, 19) + '…' : t
                },
              },
            }
          }),
          links: visibleLinks.map((l) => ({
            source: l.source,
            target: l.target,
            type_cn: l.type_cn,
            lineStyle: {
              color: isPathEdge(l.source, l.target) ? gc.path : gc.link,
              width: isPathEdge(l.source, l.target) ? 2.4 : 1,
              curveness: 0.08,
            },
          })),
        },
      ],
    }
  }, [visibleNodes, visibleLinks, legend, selected, path, pathIds, isDark])

  const runPath = async () => {
    if (!pathFrom || !pathTo || pathFrom === pathTo) return
    setPathLoading(true)
    try {
      setPath(await api.graphPath(pathFrom, pathTo))
    } catch {
      setPath(null)
    } finally {
      setPathLoading(false)
    }
  }

  const toggleLabel = (label: string) => {
    setHidden((prev) => {
      const next = new Set(prev)
      if (next.has(label)) next.delete(label)
      else next.add(label)
      return next
    })
  }

  const stats = data?.stats
  const nodeOptions = visibleNodes.map((n) => ({
    value: n.id,
    label: `${n.label_cn} · ${n.name.slice(0, 26)}`,
  }))

  return (
    <div className="sentinel-graph">
      {/* ---- 顶部标题 ---- */}
      <div className="sentinel-graph-head">
        <div>
          <h1>知识图谱</h1>
          <p>
            实体关联的可视化推理网络 · 当前展示 {visibleNodes.length} 个节点 /{' '}
            {visibleLinks.length} 条关系
          </p>
        </div>
        <div className="sentinel-graph-filters">
          <label>
            节点数
            <Select
              size="small"
              value={limit}
              onChange={setLimit}
              options={[
                { value: 60, label: '60（清晰）' },
                { value: 120, label: '120（推荐）' },
                { value: 200, label: '200（密集）' },
              ]}
              style={{ width: 116 }}
            />
          </label>
          <label>
            最低连接度
            <Select
              size="small"
              value={minDegree}
              onChange={setMinDegree}
              options={[
                { value: 1, label: '≥ 1' },
                { value: 3, label: '≥ 3' },
                { value: 6, label: '≥ 6' },
                { value: 10, label: '≥ 10（核心）' },
              ]}
              style={{ width: 110 }}
            />
          </label>
          <Tooltip title="重新拉取图谱">
            <button
              type="button"
              className="sentinel-graph-refresh"
              onClick={() => void load()}
              aria-label="刷新"
            >
              <ReloadOutlined />
            </button>
          </Tooltip>
        </div>
      </div>

      {/* ---- 指标卡 ---- */}
      {/* 指标卡行与下方主体共用同一套列定义（1fr + 320px）：
          前三张落在左列、右端与图谱卡右端严格对齐；
          第四张落在右列、左端与右侧三张卡的左端严格对齐。
          之前指标卡是 4 等分、主体是 1fr+320px，两套列宽必然错位。 */}
      <div className="sentinel-metric-row">
        <div className="sentinel-metric-left">
        <Card className="sentinel-metric" size="small">
          <span className="sentinel-metric-ico">
            <NodeIndexOutlined />
          </span>
          <span className="sentinel-metric-label">知识节点</span>
          <b className="sentinel-metric-value">
            {(stats?.nodes ?? 0).toLocaleString()}
          </b>
          <small className="sentinel-metric-sub">
            当前展示 {stats?.shown_nodes ?? 0} 个
          </small>
        </Card>
        <Card className="sentinel-metric" size="small">
          <span className="sentinel-metric-ico">
            <ShareAltOutlined />
          </span>
          <span className="sentinel-metric-label">关联边</span>
          <b className="sentinel-metric-value">
            {(stats?.relationships ?? 0).toLocaleString()}
          </b>
          <small className="sentinel-metric-sub">
            当前展示 {stats?.shown_relationships ?? 0} 条
          </small>
        </Card>
        <Card className="sentinel-metric" size="small">
          <span className="sentinel-metric-ico">
            <DeploymentUnitOutlined />
          </span>
          <span className="sentinel-metric-label">平均连接度</span>
          <b className="sentinel-metric-value">{stats?.avg_degree ?? 0}</b>
          <small className="sentinel-metric-sub">
            最高 {stats?.max_degree ?? 0}
          </small>
        </Card>
        </div>
        <div className="sentinel-metric-right">
        <Card className="sentinel-metric" size="small">
          <span className="sentinel-metric-ico">
            <RadarChartOutlined />
          </span>
          <span className="sentinel-metric-label">多跳路径</span>
          <b className="sentinel-metric-value">
            {path ? (path.hops ?? 0) : '—'}
          </b>
          <small className="sentinel-metric-sub">
            {path ? `跳（上限 3）` : '选中两点后查询'}
          </small>
        </Card>
        </div>
      </div>

      <div className="sentinel-graph-body">
        {/* ---- 图谱画布 ---- */}
        <Card
          className="sentinel-graph-canvas"
          size="small"
          title="AI 安全知识图谱"
          extra={
            <div className="sentinel-graph-legend">
              {legend.map((l) => (
                <button
                  key={l.label}
                  type="button"
                  className={
                    'sentinel-lg-item' + (hidden.has(l.label) ? ' is-off' : '')
                  }
                  onClick={() => toggleLabel(l.label)}
                  title={
                    hidden.has(l.label)
                      ? `点击显示${l.name}（${l.count}）`
                      : `点击隐藏${l.name}（${l.count}）`
                  }
                >
                  <i style={{ background: l.color }} />
                  {l.name}
                </button>
              ))}
            </div>
          }
          styles={{ body: { padding: '6px 8px 10px' } }}
        >
          <Spin spinning={loading && !data}>
            {error ? (
              <Empty description={error} style={{ marginTop: 120 }} />
            ) : visibleNodes.length ? (
              <ReactECharts
                ref={chartRef}
                option={option}
                style={{ height: 560 }}
                opts={{ renderer: 'canvas' }}
                onEvents={{
                  /* 点击节点：选中并联动右侧面板 */
                  click: (p: { dataType: string; data: { id?: string } }) => {
                    if (p.dataType === 'node' && p.data?.id) {
                      setSelected(p.data.id)
                      setPath(null)
                    }
                  },
                }}
              />
            ) : (
              <Empty description="没有符合条件��节点" style={{ marginTop: 120 }} />
            )}
          </Spin>
          <p className="sentinel-graph-hint">
            滚轮缩放 · 拖拽节点 · 空白拖动平移 · 点击节点查看详情 · 图例可点击过滤类型
          </p>
        </Card>

        {/* ---- 右侧栏 ---- */}
        <div className="sentinel-graph-side">
          <Card size="small" title="节点详情">
            {detail ? (
              <div className="sentinel-gnode">
                <span
                  className="sentinel-gnode-tag"
                  style={{
                    background:
                      gc.node(detail.node.label),
                  }}
                >
                  {detail.node.label_cn}
                </span>
                <b className="sentinel-gnode-name" title={detail.node.name}>
                  {detail.node.name}
                </b>
                <div className="sentinel-gnode-meta">
                  <span>连接度 {detail.node.degree}</span>
                  {detail.node.cvss != null && (
                    <span>
                      CVSS <b style={{ color: 'var(--accent)' }}>{detail.node.cvss}</b>
                    </span>
                  )}
                  {detail.node.severity && <span>{detail.node.severity}</span>}
                </div>
                {detail.neighbors.map((g) => (
                  <div className="sentinel-ggroup" key={g.type}>
                    <div className="sentinel-ggroup-head">
                      {g.type_cn}
                      <em>{g.items.length}</em>
                    </div>
                    {g.items.map((it) => (
                      <button
                        key={it.id}
                        type="button"
                        className="sentinel-gitem"
                        onClick={() => {
                          setSelected(it.id)
                          setPath(null)
                          if (pathTo) setPathFrom(it.id)
                        }}
                        title="在图上定位并查看该节点"
                      >
                        <i
                          style={{
                            background:
                              gc.node(it.label),
                          }}
                        />
                        <span className="nm">{it.name}</span>
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            ) : (
              <Empty
                image={null}
                description="点击图中节点查看详情"
                style={{ marginTop: 24 }}
              />
            )}
          </Card>

          <Card
            size="small"
            title="多跳链路探索"
            extra={
              <button
                type="button"
                className="sentinel-link-btn"
                onClick={() => void runPath()}
                disabled={!pathFrom || !pathTo || pathFrom === pathTo}
              >
                <AimOutlined /> 查路径
              </button>
            }
          >
            <div className="sentinel-gpath">
              <Select
                size="small"
                showSearch
                placeholder="起点节点"
                value={pathFrom ?? undefined}
                onChange={setPathFrom}
                options={nodeOptions}
                optionFilterProp="label"
              />
              <Select
                size="small"
                showSearch
                placeholder="终点节点"
                value={pathTo ?? undefined}
                onChange={setPathTo}
                options={nodeOptions}
                optionFilterProp="label"
              />
              <Spin spinning={pathLoading}>
                {path ? (
                  path.found ? (
                    <div className="sentinel-gpath-result">
                      <span className="sentinel-gpath-hops">
                        {path.hops} 跳可达
                      </span>
                      {path.nodes.map((n, i) => (
                        <div className="sentinel-gpath-node" key={n.id}>
                          <span
                            className="dot"
                            style={{
                              background:
                                gc.node(n.label),
                            }}
                          />
                          <span className="nm">{n.name}</span>
                          {i < path.nodes.length - 1 && (
                            <em className="via">
                              {path.relations[i]?.type_cn ?? '—'}
                            </em>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="sentinel-gpath-empty">
                      两点之间 3 跳内没有可达路径
                    </p>
                  )
                ) : (
                  <p className="sentinel-gpath-empty">
                    选择起点与终点，查询最短关联链路
                  </p>
                )}
              </Spin>
            </div>
          </Card>

          <Card size="small" title="关系类型分布">
            {data?.relation_types.length ? (
              <div className="sentinel-grel">
                {data.relation_types.map((r) => {
                  const max = data.relation_types[0].count || 1
                  return (
                    <div className="sentinel-grel-row" key={r.type}>
                      <span className="nm">{r.type_cn}</span>
                      <div className="track">
                        <i style={{ width: `${(r.count / max) * 100}%` }} />
                      </div>
                      <b>{r.count}</b>
                    </div>
                  )
                })}
              </div>
            ) : (
              <Empty image={null} description="暂无关系数据" />
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}