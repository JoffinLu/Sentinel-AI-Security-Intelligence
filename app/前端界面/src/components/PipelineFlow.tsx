/**
 * 编排链路流程图（LangGraph 状态机可视化）
 * =================================================================
 * 画的是**代码里真实的图结构**，对应
 * `项目源码/智能体编排/orchestrator.py::_build_graph`：
 *
 *   START → monitor → triage ─┬─(值得富化)→ enrich ─┐
 *                              └─(低价值)──→ skip_enrich ─┤
 *                     (qa 在 fanout 内与 enrich 并行)      ├→ fanout → supervisor ─┬→ END
 *                                                          └→ reflect ↺
 *
 * 四个容易画错的地方（都已在图上体现）：
 *   1. **`qa` 在图上是孤儿节点**——`grep add_edge` 找不到任何指向它的边。
 *      真正的问答在 `_node_join`（fanout）内部与富化 `asyncio.gather`
 *      并行执行。所以 `qa → fanout` 那根线画成**虚线**，表示「非独立调度」。
 *   2. **富化与问答不能用两条入边做并行**——LangGraph 的入边是「竞争」
 *      而非「汇合」，enrich 一完成就立刻触发 join，qa 分支被跳过。
 *   3. **supervisor ↔ reflect 是条件回环**（虚线 + 双向），不是单向边。
 *   4. **triage 有两条出边**，是分叉点而非顺序节点。
 *
 * 尺寸基准（为什么用 960×260 而不是 720×200）：
 *   画布越大、节点尺寸不跟着放大，渲染到页面上时框和字就越小。
 *   节点 112×40、主标题 10.5px、副标题 8.5px —— 常规宽度下约合
 *   11px 字号、117px 宽的框，与运维页其它卡片的比例才协调。
 *   （上一版 720 画布 + 12px 字号，实际渲染到 ~17px，框显得又大又空。）
 *
 * 线条的「高级感」来自三层叠加：
 *   ① 光晕层：粗 4px、透明度 .07 —— 模拟辉光，避免线条干巴巴
 *   ② 主体层：细 1.1px、左淡右浓的线性渐变 —— 暗示流向
 *   ③ 流动光点：pathLength 归一化，各路径速度一致
 *   走线全部用三次贝塞尔，不用直角折线。
 */
import { useMemo, useState } from 'react'
import type { OpsNode } from '@/api/client'
import { useTheme } from '@/lib/theme'

interface Props {
  nodes: OpsNode[]
  /** 调度器管理的采集源数量（来自 /api/ops/components 顶层字段） */
  schedulerCount?: number
}

/** 节点几何（viewBox 960×260）。主链路中心线 y=130
 *
 * `scheduler` 之所以画在图里却单独放在左上角：它是 **图外组件**，
 * 不属于 LangGraph 的状态机（`_build_graph` 里没有它）。
 * 但 `_node_monitor` 内部会调 `scheduler.collect_all()` 执行采集，
 * 且 APScheduler 也在定时驱动——所以它是整条链路的**触发源**。
 * 放在主链路之外 + 虚线框，表达的正是「图外、但驱动这张图」。 */
const BOX = {
  scheduler:   { x: 44,  y: 26,  w: 112, h: 40 },
  monitor:     { x: 44,  y: 110, w: 112, h: 40 },
  triage:      { x: 196, y: 110, w: 124, h: 40 },
  enrich:      { x: 430, y: 34,  w: 112, h: 40 },
  qa:          { x: 608, y: 34,  w: 112, h: 40 },
  skip_enrich: { x: 430, y: 186, w: 112, h: 40 },
  fanout:      { x: 608, y: 110, w: 112, h: 40 },
  supervisor:  { x: 790, y: 110, w: 112, h: 40 },
  reflect:     { x: 790, y: 186, w: 112, h: 40 },
} as const

type BoxKey = keyof typeof BOX

/** 相对时间。组件内独立实现——OpsPage 那个是私有的，
 *  且两者对「未知时间」的兜底文案不同（一个显示「—」，一个留空） */
function timeAgo(iso: string | null | undefined): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const diff = Date.now() - t
  if (diff < 60_000) return '刚刚'
  const m = Math.floor(diff / 60_000)
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.floor(h / 24)} 天前`
}

/** 四个色系：青=主链路 / 绿=处理链 / 灰=轻量旁路 / 橙红=重试 */
type Tone = 'main' | 'ok' | 'dim' | 'warm'

const TONE_COLOR: Record<Tone, string> = {
  main: '#4cd7f6',
  ok: '#4edea3',
  dim: '#8caa9b',
  warm: '#ff8a5c',
}

/** 白天版色系：同色相加深（青 1.7→5.3 / 绿 1.6→5.4 / 灰 2.9→6.1 / 橙红 2.2→4.8）。
 *  这些色在SVG 里画节点描边与连线，低于 3:1 在白底上会看不清拓扑。 */
const TONE_COLOR_LIGHT: Record<Tone, string> = {
  main: '#0e7490',
  ok: '#04785a',
  dim: '#55645c',
  warm: '#b85409',
}

const toneColor = (tone: Tone, isDark: boolean) =>
  (isDark ? TONE_COLOR : TONE_COLOR_LIGHT)[tone]

interface NodeStyle {
  label: string
  sub: string
  color: string
  text: string
  subText: string
}

const META: Record<BoxKey, NodeStyle> = {
  scheduler:   { label: '调度器',   sub: '定时驱动采集', color: '#7dd3c0', text: '#e4fbf5', subText: 'rgba(150,200,190,.76)' },
  monitor:     { label: '监测采集', sub: '情报入库',    color: '#4cd7f6', text: '#e9f8ff', subText: 'rgba(150,190,208,.78)' },
  triage:      { label: '优先级判定', sub: '按风险分流', color: '#4cd7f6', text: '#e9f8ff', subText: 'rgba(150,190,208,.78)' },
  enrich:      { label: '富化',     sub: '七维补证据',  color: '#4edea3', text: '#e8fff6', subText: 'rgba(150,205,180,.78)' },
  qa:          { label: '问答',     sub: '检索增强生成', color: '#4edea3', text: '#e8fff6', subText: 'rgba(150,205,180,.78)' },
  skip_enrich: { label: '跳过富化', sub: '低价值直通',  color: '#8caa9b', text: '#dce8e2', subText: 'rgba(150,175,165,.72)' },
  fanout:      { label: '并行汇聚', sub: 'gather 汇合', color: '#4cd7f6', text: '#e9f8ff', subText: 'rgba(150,190,208,.78)' },
  supervisor:  { label: '主管',     sub: '可恢复判定',  color: '#f5c451', text: '#fff9ea', subText: 'rgba(205,178,120,.82)' },
  reflect:     { label: '反思重试', sub: '上限 3 次',   color: '#ff8a5c', text: '#fff1ea', subText: 'rgba(215,150,150,.78)' },
}

/** 白天版阶段文字色。
 *
 * 原text/subText 是「近白字 + 半透明白副标」这套暗底配方，
 * 白天直接用会白字白底（不可读）。这里按同色系给深色版本：
 *   主色文字用 #12211a/ 次级用 #46584f，均为 WCAG AAA。
 */
const STAGE_TEXT_LIGHT: Record<string, { text: string; subText: string }> = {
  main: { text: '#0d3b48', subText: 'rgba(13,59,72,.78)' },
  ok: { text: '#0d4636', subText: 'rgba(13,70,54,.78)' },
  dim: { text: '#3d4a44', subText: 'rgba(61,74,68,.75)' },
  warm: { text: '#7a3608', subText: 'rgba(122,54,8,.78)' },
  supervisor: { text: '#6d4a06', subText: 'rgba(109,74,6,.8)' },
  reflect: { text: '#7a2f1e', subText: 'rgba(122,47,30,.78)' },
}

/** 阶段色系 → 白天文字色的映射 */
const STAGE_TONE: Record<string, Tone> = {
  monitor: 'main', triage: 'main', fanout: 'main',
  enrich: 'ok', qa: 'ok',
  skip_enrich: 'dim',
  supervisor: 'warm',
  reflect: 'warm',
}

/** 取阶段文字色（白天走 STAGE_TEXT_LIGHT） */
function stageText(key: string, isDark: boolean) {
  if (isDark) return null
  return STAGE_TEXT_LIGHT[STAGE_TONE[key] ?? 'dim']
}

/** 节点职责（详情条与无障碍标签共用） */
const DUTY: Record<BoxKey, string> = {
  scheduler:   'APScheduler 按各源周期定时触发采集（rss 1h / nvd 6h / atlas 7d 等），也是 monitor 节点内部调用的采集入口',
  monitor:     '识别新增情报并判定优先级',
  triage:      '按风险与时效分流，决定是否值得七维富化',
  enrich:      '基础 / 修复 / 风险 / 技术 / 论文 / PoC / 资产 / CVSS 多维补齐证据链',
  qa:          '检索增强生成，基于向量库与知识图谱回答提问',
  skip_enrich: '低价值条目直接放行，避免无谓的富化开销',
  fanout:      '富化与问答在同一节点内 asyncio.gather 并行执行后再汇合',
  supervisor:  '失败可恢复性判定，决定重试还是结束',
  reflect:     '不达标结果自动重跑，重试达上限后结束',
}

/** 正向连线。走线用三次贝塞尔——圆润的曲线比直角折线更耐看 */
const EDGES: Array<{ d: string; tone: Tone }> = [
  { d: 'M100 66 L100 110', tone: 'main' },                     // 调度器 → 监测（图外触发源）
  { d: 'M156 130 L196 130', tone: 'main' },                    // 监测 → 判定
  { d: 'M320 122 C368 122 358 54 430 54', tone: 'main' },      // 判定 → 富化
  { d: 'M320 138 C368 138 358 206 430 206', tone: 'dim' },     // 判定 → 跳过
  { d: 'M542 54 C590 54 566 122 608 122', tone: 'ok' },        // 富化 → 汇聚
  { d: 'M542 206 C590 206 566 138 608 138', tone: 'dim' },     // 跳过 → 汇聚
  { d: 'M720 130 L790 130', tone: 'main' },                    // 汇聚 → 主管
  { d: 'M902 130 L916 130', tone: 'main' },                    // 主管 → 终点
]

/** `qa → 汇聚`：非独立调度，单独用虚线画以示区别 */
const QA_EDGE = { d: 'M664 74 L664 110', tone: 'ok' as Tone }

/** 条件回环（双向）——主管 ⇄ 反思 */
const LOOP_DOWN = 'M826 150 L826 186'
const LOOP_UP = 'M862 186 L862 150'

export default function PipelineFlow({ nodes, schedulerCount = 0 }: Props) {
  /* 主题：SVG 的描边/连线/文字色在白底上要换（暗色那套近白字白底不可读） */
  const { isDark } = useTheme()
  const [hover, setHover] = useState<BoxKey | null>(null)

  const byKey = useMemo(() => {
    const m: Record<string, OpsNode> = {}
    for (const n of nodes) m[n.key] = n
    return m
  }, [nodes])

  /** fanout 在 API 里没有独立记录（它不是 Agent，是编排节点），按结构节点展示 */
  const stateOf = (k: BoxKey) => {
    if (k === 'fanout') {
      return { runs: 0, idle: true, down: false, struct: true, sched: false, paused: false }
    }
    const n = byKey[k]
    // 调度器不跑「工作流」，它的状态是「运行中 / 已暂停」而非「触发过几次」
    if (k === 'scheduler') {
      return {
        runs: 0, idle: false, down: false, struct: false,
        sched: true, paused: n?.paused === true,
      }
    }
    return {
      runs: n?.runs ?? 0,
      idle: !n || (n.runs ?? 0) === 0,
      down: n?.circuit_open === true,
      struct: false,
      sched: false,
      paused: false,
    }
  }

  const focus: BoxKey = hover ?? 'monitor'
  const f = META[focus]
  const fs = stateOf(focus)
  const fn = byKey[focus]
  const fAgo = timeAgo(fn?.last_run_at)

  /** 详情条的状态徽章。抽成变量而不是写在 JSX 里——
   *  四层嵌套三元（熔断/结构/调度器/普通）塞在 className 里没法读。 */
  const stateCls = fs.down
    ? 'is-bad'
    : fs.struct
      ? 'is-struct'
      : fs.sched
        ? (fs.paused ? 'is-idle' : 'is-ok')
        : (fs.idle ? 'is-idle' : 'is-ok')
  const stateText = fs.down
    ? '熔断'
    : fs.struct
      ? '结构节点'
      : fs.sched
        ? (fs.paused ? '已暂停' : '运行中')
        : (fs.idle ? '待触发' : '正常')

  return (
    <div className="ops-flow">
      <div className="ops-flow-head">
        <span className="ops-flow-title">链路拓扑</span>
        <span className="ops-flow-hint">
          光点方向即数据流向 · <i className="lg-dash" />虚线连接 = 条件分支 / 重试回环 ·
          虚线框 = 图外组件（不属状态机）
        </span>
      </div>

      <div className="ops-flow-canvas">
        <svg
          viewBox="0 0 960 260"
          role="img"
          aria-label="LangGraph 编排链路：监测采集 → 优先级判定 → 富化与问答并行或跳过富化 → 并行汇聚 → 主管 → 反思重试或结束"
        >
          <defs>
            <pattern id="pf-dots" width="24" height="24" patternUnits="userSpaceOnUse">
              <circle cx="1" cy="1" r="0.7" fill="rgba(160,205,185,.05)" />
            </pattern>
            {/* 线性渐变：左淡右浓，暗示流向（objectBoundingBox 自动贴合每条路径） */}
            {(['main', 'ok', 'dim', 'warm'] as Tone[]).map((t) => (
              <linearGradient key={t} id={`pf-g-${t}`} x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor={toneColor(t, isDark)} stopOpacity="0.1" />
                <stop offset="100%" stopColor={toneColor(t, isDark)} stopOpacity="0.6" />
              </linearGradient>
            ))}
            {/* 箭头：细长实心三角，比开放 V 形更利落 */}
            {(['main', 'ok', 'dim', 'warm'] as Tone[]).map((t) => (
              <marker
                key={t}
                id={`pf-a-${t}`}
                viewBox="0 0 10 10"
                refX="8.6"
                refY="5"
                markerWidth="5.8"
                markerHeight="5.8"
                orient="auto-start-reverse"
              >
                <path d="M1.6 2L8.6 5L1.6 8Z" fill={toneColor(t, isDark)} fillOpacity="0.9" />
              </marker>
            ))}
          </defs>

          <rect x="0" y="0" width="960" height="260" fill="url(#pf-dots)" />

          {/* ── ① 光晕层：粗而极淡，给线条一层辉光 ── */}
          <g fill="none" strokeLinecap="round">
            {EDGES.map((e, i) => (
              <path key={`gl${i}`} d={e.d} stroke={toneColor(e.tone, isDark)}
                    strokeOpacity="0.07" strokeWidth="4" />
            ))}
            <path d={QA_EDGE.d} stroke={toneColor(QA_EDGE.tone, isDark)}
                  strokeOpacity="0.07" strokeWidth="4" />
          </g>

          {/* ── ② 主体层：细线 + 渐变（左淡右浓）── */}
          <g fill="none" strokeLinecap="round">
            {EDGES.map((e, i) => (
              <path key={`bd${i}`} d={e.d} stroke={`url(#pf-g-${e.tone})`}
                    strokeWidth="1.1" markerEnd={`url(#pf-a-${e.tone})`} />
            ))}
            {/* qa 的连接：虚线，表示它没有独立入边、靠汇聚节点内部并行触发 */}
            <path d={QA_EDGE.d} stroke={toneColor(QA_EDGE.tone, isDark)}
                  strokeOpacity="0.34" strokeWidth="1"
                  strokeDasharray="2.5 2.5" markerEnd="url(#pf-a-ok)" />
          </g>

          {/* ── ③ 流动光点：pathLength 归一，各路径速度一致 ── */}
          <g fill="none" strokeLinecap="round">
            {EDGES.map((e, i) => (
              <path key={`rn${i}`} d={e.d} pathLength={100}
                    stroke={toneColor(e.tone, isDark)} strokeOpacity="0.9" strokeWidth="1.7"
                    strokeDasharray="2.2 100" className="ops-flow-runner"
                    style={{ animationDelay: `${(i * 0.38) % 2.6}s` }} />
            ))}
          </g>

          {/* ── 回环：主管 ⇄ 反思（条件重试，虚线双向）── */}
          <g fill="none" strokeLinecap="round">
            <path d={LOOP_DOWN} stroke={TONE_COLOR.warm} strokeOpacity="0.08" strokeWidth="4" />
            <path d={LOOP_UP} stroke={TONE_COLOR.warm} strokeOpacity="0.08" strokeWidth="4" />
            <path d={LOOP_DOWN} stroke={TONE_COLOR.warm} strokeOpacity="0.42"
                  strokeWidth="1" strokeDasharray="2.5 2.5" markerEnd="url(#pf-a-warm)" />
            <path d={LOOP_UP} stroke={TONE_COLOR.warm} strokeOpacity="0.42"
                  strokeWidth="1" strokeDasharray="2.5 2.5" markerEnd="url(#pf-a-warm)" />
            <path d={LOOP_DOWN} pathLength={100} stroke={TONE_COLOR.warm}
                  strokeOpacity="0.9" strokeWidth="1.7" strokeDasharray="2.2 100"
                  className="ops-flow-runner is-slow" />
            <path d={LOOP_UP} pathLength={100} stroke={TONE_COLOR.warm}
                  strokeOpacity="0.9" strokeWidth="1.7" strokeDasharray="2.2 100"
                  className="ops-flow-runner is-slow" style={{ animationDelay: '1.2s' }} />
          </g>

          {/* ── 分支路标（不透明胶囊压在线上）──
              白天：品牌绿底 + 白字（用户指定，同侧栏/浮层）。
              注意 text 必须用内联 style fill —— .ops-flow-lbl 的 CSS fill
              会盖掉 SVG 属性（00:24 的坑又来了），内联 style 才压得住。 */}
          {[
            { t: '值得富化', cx: 366, cy: 88, w: 58 },
            { t: '低价值', cx: 371, cy: 172, w: 48 },
          ].map((c) => (
            <g key={c.t}>
              <rect
                x={c.cx - c.w / 2} y={c.cy - 7.5} width={c.w} height={15} rx="7.5"
                fill={isDark ? '#0a1411' : '#05825c'}
                stroke={isDark ? 'rgba(160,205,185,.18)' : 'rgba(255,255,255,.4)'}
                strokeWidth="0.7"
              />
              <text
                x={c.cx} y={c.cy + 2.9} className="ops-flow-lbl" textAnchor="middle"
                style={isDark ? undefined : { fill: '#ffffff', fontWeight: 600 }}
              >
                {c.t}
              </text>
            </g>
          ))}

          {/* ── 节点（最后画，压在连线之上）── */}
          {(Object.keys(BOX) as BoxKey[]).map((k) => {
            const b = BOX[k]
            const m = META[k]
            /* 白天：近白字要换成深墨绿（按色系取，保证与描边同调） */
            const lt = stageText(k, isDark)
            const st = stateOf(k)
            const on = hover === k
            return (
              <g
                key={k}
                className={
                  'ops-flow-node' +
                  (st.down ? ' is-down' : st.idle ? ' is-idle' : ' is-live') +
                  (k === 'scheduler' ? ' is-outside' : '') +
                  (on ? ' is-hover' : '')
                }
                onMouseEnter={() => setHover(k)}
                onMouseLeave={() => setHover((v) => (v === k ? null : v))}
                onFocus={() => setHover(k)}
                onBlur={() => setHover(null)}
                tabIndex={0}
                role="button"
                aria-label={`${m.label}：${DUTY[k]}`}
              >
                <rect
                  x={b.x} y={b.y} width={b.w} height={b.h} rx="9"
                  fill={isDark ? 'rgba(9,17,15,.84)' : 'rgba(255,255,255,.9)'}
                  stroke={toneColor(STAGE_TONE[k] ?? 'dim', isDark)}
                  strokeOpacity={on ? 0.95 : st.idle ? 0.26 : 0.5}
                  strokeWidth={on ? 1.3 : 0.8}
                />
                <text
                  x={b.x + b.w / 2} y={b.y + b.h * 0.445}
                  className="ops-flow-t1" textAnchor="middle" style={{ fill: lt ? lt.text : m.text }}
                >
                  {m.label}
                </text>
                <text
                  x={b.x + b.w / 2} y={b.y + b.h * 0.78}
                  className="ops-flow-t2" textAnchor="middle" style={{ fill: lt ? lt.subText : m.subText }}
                >
                  {m.sub}
                </text>
              </g>
            )
          })}

          {/* 终点符号 */}
          <g>
            <circle cx="922" cy="130" r="4" fill="none"
                    stroke={toneColor('main', isDark)} strokeOpacity="0.45" strokeWidth="1" />
            <circle cx="922" cy="130" r="1.5" fill={toneColor('main', isDark)} fillOpacity="0.75" />
          </g>
        </svg>
      </div>

      {/* ── 详情条 ── */}
      <div className="ops-flow-detail" aria-live="polite">
        <span className="ops-flow-d-name" style={{ color: f.color }}>{f.label}</span>
        <span className={'ops-flow-d-state ' + stateCls}>{stateText}</span>
        <span className="ops-flow-d-duty">
          {DUTY[focus]}
          {focus === 'qa' && '（在「并行汇聚」内与富化同时执行，图上无独立入边）'}
          {focus === 'scheduler' && '（图外组件：不属状态机，但驱动整条链路）'}
        </span>
        {fs.sched ? (
          <span className="ops-flow-d-stat">
            <em>数据源</em>
            <b>{schedulerCount}</b>
            <u />
            <em>状态</em>
            <b>{fs.paused ? '暂停' : '运行'}</b>
          </span>
        ) : !fs.struct && (
          <span className="ops-flow-d-stat">
            <em>触发</em>
            <b>{fs.runs}</b>
            {fn?.avg_ms != null && fn.avg_ms > 0 && (
              <>
                <u />
                <em>均耗时</em>
                <b>{Math.round(fn.avg_ms)}</b>ms
              </>
            )}
            {fAgo && (
              <>
                <u />
                <em>最近</em>
                <b>{fAgo}</b>
              </>
            )}
          </span>
        )}
      </div>
    </div>
  )
}
