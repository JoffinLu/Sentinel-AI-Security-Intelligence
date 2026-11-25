/**
 * ROEN 网络架构图（双线程融合 · 自绘 SVG）
 * =================================================================
 * 替代论文白底截图（与暗色主题冲突、信息层级过密）。画法与运维页
 * 「链路拓扑」卡片同一套视觉语言：
 *   · 光点方向 = 数据流向（三层线：光晕 + 渐变主体 + 流动光点）
 *   · 虚线     = 优化回环（LOT 回环优化 / MCS 运动捕获反馈）
 *   · 虚线框   = 数据输入（不属于模型本体）
 *
 * 结构（对应论文 Fig.2 的简化重绘，保留两条线程与全局融合的语义）：
 *   显式线程  Y 序列 → MLP → SAFC → LOT → 显式表征   ↺ 回环优化
 *   隐式线程  X 序列 → MLP → TET → MCS → 隐式表征   ↺ 运动捕获
 *   全局融合  Xt·Yt + 双表征 → SAMLP → 流量分类（BENIGN/DDoS/PortScan）
 */
import { useTheme } from '@/lib/theme'

type Tone = 'main' | 'ok' | 'warm'

const TONE: Record<Tone, { dark: string; light: string }> = {
  main: { dark: '#4cd7f6', light: '#0e7490' },
  ok: { dark: '#4edea3', light: '#04785a' },
  warm: { dark: '#ff8a5c', light: '#b85409' },
}

/** 节点文字：暗色近白 / 白天同色相加深 */
const TEXT: Record<Tone, { dark: [string, string]; light: [string, string] }> = {
  main: {
    dark: ['#e9f8ff', 'rgba(150,190,208,.78)'],
    light: ['#0d3b48', 'rgba(13,59,72,.78)'],
  },
  ok: {
    dark: ['#e8fff6', 'rgba(150,205,180,.78)'],
    light: ['#0d4636', 'rgba(13,70,54,.78)'],
  },
  warm: {
    dark: ['#fff1ea', 'rgba(215,170,150,.80)'],
    light: ['#7a3608', 'rgba(122,54,8,.78)'],
  },
}

interface NodeDef {
  key: string
  x: number
  y: number
  w: number
  h: number
  label: string
  sub: string
  tone: Tone
  /** 数据输入（虚线框，非模型组件） */
  outside?: boolean
  /** 融合核心（加描边光晕） */
  strong?: boolean
}

const BOX_H = 40

/** 双线程两级色：显式=青（统计）、隐式=绿（拓扑）、融合=橙 */
const NODES: NodeDef[] = [
  // ---- 显式线程（统计） ----
  { key: 'y-in', x: 44, y: 60, w: 108, h: BOX_H, label: 'Y 序列', sub: 'Yt-n … Yt', tone: 'main', outside: true },
  { key: 'y-mlp', x: 184, y: 60, w: 88, h: BOX_H, label: 'MLP', sub: '多层感知', tone: 'main' },
  { key: 'y-safc', x: 308, y: 60, w: 88, h: BOX_H, label: 'SAFC', sub: '自适应提取', tone: 'main' },
  { key: 'y-lot', x: 432, y: 60, w: 88, h: BOX_H, label: 'LOT', sub: '循环优化', tone: 'main' },
  { key: 'y-out', x: 552, y: 60, w: 88, h: BOX_H, label: 'Yt+T', sub: '显式表征', tone: 'main' },
  // ---- 隐式线程（拓扑） ----
  { key: 'x-in', x: 44, y: 300, w: 108, h: BOX_H, label: 'X 序列', sub: 'Xt-n … Xt', tone: 'ok', outside: true },
  { key: 'x-mlp', x: 184, y: 300, w: 88, h: BOX_H, label: 'MLP', sub: '多层感知', tone: 'ok' },
  { key: 'x-tet', x: 308, y: 300, w: 88, h: BOX_H, label: 'TET', sub: '拓扑提取', tone: 'ok' },
  { key: 'x-mcs', x: 432, y: 300, w: 88, h: BOX_H, label: 'MCS', sub: '运动捕获', tone: 'ok' },
  { key: 'x-out', x: 552, y: 300, w: 88, h: BOX_H, label: 'Xt+T', sub: '隐式表征', tone: 'ok' },
  // ---- 全局融合 ----
  { key: 'input', x: 44, y: 182, w: 108, h: BOX_H, label: '当前窗口', sub: 'Xt · Yt', tone: 'warm', outside: true },
  { key: 'samlp', x: 521, y: 180, w: 150, h: 44, label: 'SAMLP', sub: '全局融合引擎', tone: 'warm', strong: true },
  { key: 'cls', x: 740, y: 182, w: 152, h: BOX_H, label: '流量分类', sub: 'BENIGN / DDoS / PortScan', tone: 'main' },
]

/** 正向连线（箭头 + 流动光点） */
const EDGES: Array<{ d: string; tone: Tone }> = [
  { d: 'M152 80 H184', tone: 'main' },
  { d: 'M272 80 H308', tone: 'main' },
  { d: 'M396 80 H432', tone: 'main' },
  { d: 'M520 80 H552', tone: 'main' },
  { d: 'M152 320 H184', tone: 'ok' },
  { d: 'M272 320 H308', tone: 'ok' },
  { d: 'M396 320 H432', tone: 'ok' },
  { d: 'M520 320 H552', tone: 'ok' },
  { d: 'M596 100 V180', tone: 'warm' }, // 显式表征 ↓ 融合
  { d: 'M596 300 V224', tone: 'warm' }, // 隐式表征 ↑ 融合
  { d: 'M152 202 H521', tone: 'warm' }, // 当前窗口 → 融合
  { d: 'M671 202 H740', tone: 'warm' }, // 融合 → 分类
  { d: 'M892 202 H910', tone: 'main' }, // 分类 → 终点
]

/** 优化回环（虚线：输出端回到输入端） */
const LOOPS: Array<{ d: string; tone: Tone; label: string; lx: number; ly: number }> = [
  { d: 'M596 60 V22 H98 V60', tone: 'main', label: '回环优化 · 反馈调整', lx: 347, ly: 13 },
  { d: 'M596 340 V378 H98 V340', tone: 'ok', label: '运动捕获 · 拓扑演化反馈', lx: 347, ly: 393 },
]

export function RoenArchDiagram() {
  const { isDark } = useTheme()
  const tone = (t: Tone) => TONE[t][isDark ? 'dark' : 'light']
  const nodeText = (t: Tone) => TEXT[t][isDark ? 'dark' : 'light']
  const nodeFill = isDark ? 'rgba(9,17,15,.84)' : 'rgba(255,255,255,.9)'
  const outsideFill = isDark ? 'rgba(9,17,15,.7)' : 'rgba(255,255,255,.85)'
  const lblFill = isDark ? 'rgba(163,192,180,.92)' : '#3d4a44'

  return (
    <div className="ops-flow">
      <div className="ops-flow-head">
        <span className="ops-flow-title">网络架构 · 双线程融合</span>
        <span className="ops-flow-hint">
          光点方向即数据流向 · <i className="lg-dash" />虚线 = 优化回环 ·
          虚线框 = 数据输入（非模型组件）
        </span>
      </div>

      <div className="ops-flow-canvas">
        <svg
          viewBox="0 0 960 400"
          role="img"
          aria-label="ROEN 双线程架构：显式统计线程（MLP→SAFC→LOT）与隐式拓扑线程（MLP→TET→MCS）分别提取表征，经 SAMLP 全局融合后输出流量分类"
        >
          <defs>
            <pattern id="ra-dots" width="24" height="24" patternUnits="userSpaceOnUse">
              <circle cx="1" cy="1" r="0.7" fill="rgba(160,205,185,.05)" />
            </pattern>
            {(['main', 'ok', 'warm'] as Tone[]).map((t) => (
              <linearGradient key={t} id={`ra-g-${t}`} x1="0" y1="0" x2="1" y2="0">
                <stop offset="0%" stopColor={tone(t)} stopOpacity="0.1" />
                <stop offset="100%" stopColor={tone(t)} stopOpacity="0.6" />
              </linearGradient>
            ))}
            {(['main', 'ok', 'warm'] as Tone[]).map((t) => (
              <marker
                key={t}
                id={`ra-a-${t}`}
                viewBox="0 0 10 10"
                refX="8.6"
                refY="5"
                markerWidth="5.8"
                markerHeight="5.8"
                orient="auto-start-reverse"
              >
                <path d="M1.6 2L8.6 5L1.6 8Z" fill={tone(t)} fillOpacity="0.9" />
              </marker>
            ))}
          </defs>

          <rect x="0" y="0" width="960" height="400" fill="url(#ra-dots)" />

          {/* ── 线程分组框（虚线 = 组边界） ── */}
          <g fill="none">
            <rect x="28" y="44" width="628" height="72" rx="12"
                  stroke={tone('main')} strokeOpacity="0.32" strokeWidth="1" strokeDasharray="5 4"
                  fill={tone('main')} fillOpacity="0.03" />
            <rect x="28" y="284" width="628" height="72" rx="12"
                  stroke={tone('ok')} strokeOpacity="0.32" strokeWidth="1" strokeDasharray="5 4"
                  fill={tone('ok')} fillOpacity="0.03" />
          </g>

          {/* ── ① 光晕层 ── */}
          <g fill="none" strokeLinecap="round">
            {EDGES.map((e, i) => (
              <path key={`gl${i}`} d={e.d} stroke={tone(e.tone)} strokeOpacity="0.07" strokeWidth="4" />
            ))}
          </g>

          {/* ── ② 主体层：细线 + 渐变 ── */}
          <g fill="none" strokeLinecap="round">
            {EDGES.map((e, i) => (
              <path key={`bd${i}`} d={e.d} stroke={`url(#ra-g-${e.tone})`}
                    strokeWidth="1.1" markerEnd={`url(#ra-a-${e.tone})`} />
            ))}
          </g>

          {/* ── ③ 流动光点 ── */}
          <g fill="none" strokeLinecap="round">
            {EDGES.map((e, i) => (
              <path key={`rn${i}`} d={e.d} pathLength={100}
                    stroke={tone(e.tone)} strokeOpacity="0.9" strokeWidth="1.7"
                    strokeDasharray="2.2 100" className="ops-flow-runner"
                    style={{ animationDelay: `${(i * 0.38) % 2.6}s` }} />
            ))}
          </g>

          {/* ── 优化回环（虚线，输出端 → 输入端） ── */}
          <g fill="none" strokeLinecap="round">
            {LOOPS.map((l) => (
              <g key={l.label}>
                <path d={l.d} stroke={tone(l.tone)} strokeOpacity="0.08" strokeWidth="4" />
                <path d={l.d} stroke={tone(l.tone)} strokeOpacity="0.42" strokeWidth="1"
                      strokeDasharray="2.5 2.5" markerEnd={`url(#ra-a-${l.tone})`} />
                <path d={l.d} pathLength={100} stroke={tone(l.tone)} strokeOpacity="0.9"
                      strokeWidth="1.7" strokeDasharray="2.2 100"
                      className="ops-flow-runner is-slow" />
              </g>
            ))}
          </g>

          {/* ── 注释文字 ── */}
          <g style={{ fontFamily: 'var(--font)', fontSize: 9.5 }}>
            <text x="36" y="38" fill={tone('main')} fillOpacity="0.95">
              显式特征提取 · 统计特征（Y）
            </text>
            <text x="36" y="278" fill={tone('ok')} fillOpacity="0.95">
              隐式特征提取 · 动态拓扑（X）
            </text>
            {LOOPS.map((l) => (
              <text key={`lb${l.label}`} x={l.lx} y={l.ly} fill={lblFill} textAnchor="middle">
                {l.label}
              </text>
            ))}
          </g>

          {/* ── 节点（最后画，压在连线之上） ── */}
          {NODES.map((n) => {
            const [t1, t2] = nodeText(n.tone)
            return (
              <g key={n.key} className="ops-flow-node" style={{ cursor: 'default' }}>
                {n.strong && (
                  <rect x={n.x} y={n.y} width={n.w} height={n.h} rx="9" fill="none"
                        stroke={tone(n.tone)} strokeOpacity="0.16" strokeWidth="4" />
                )}
                <rect
                  x={n.x} y={n.y} width={n.w} height={n.h} rx="9"
                  fill={n.outside ? outsideFill : nodeFill}
                  stroke={tone(n.tone)}
                  strokeOpacity={n.strong ? 0.85 : 0.5}
                  strokeWidth={n.strong ? 1.2 : 0.8}
                  strokeDasharray={n.outside ? '3 2.5' : undefined}
                />
                <text x={n.x + n.w / 2} y={n.y + n.h * 0.445}
                      className="ops-flow-t1" textAnchor="middle" style={{ fill: t1 }}>
                  {n.label}
                </text>
                <text x={n.x + n.w / 2} y={n.y + n.h * 0.78}
                      className="ops-flow-t2" textAnchor="middle" style={{ fill: t2 }}>
                  {n.sub}
                </text>
              </g>
            )
          })}

          {/* 终点符号 */}
          <g>
            <circle cx="920" cy="202" r="4" fill="none"
                    stroke={tone('main')} strokeOpacity="0.45" strokeWidth="1" />
            <circle cx="920" cy="202" r="1.5" fill={tone('main')} fillOpacity="0.75" />
          </g>
        </svg>
      </div>
    </div>
  )
}
