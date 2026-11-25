/**
 * 图表色板（ECharts 专用）· 双主题
 * ------------------------------------------------------------
 * 为什么必须单独做这个模块：
 *   ECharts **不认 CSS 变量**——option 里写 var(--accent) 会被当作
 *   非法颜色而回退（实测渲染成黑色/透明）。所以图表色只能走 JS 取值，
 *   在切主题时用 setOption 重新着色。
 *
 *   （这是项目里唯一必须「用 JS 读主题」的地方；
 *     其余部分都用 CSS 变量自动翻转。）
 *
 * ---------- 白天主题的关键约束：文字类必须加深 ----------
 * 暗色主题里常用的轴标签色 #6b7870 在白底上只有 2.6:1，图表会「糊成一片」；
 * 网格线 rgba(180,220,195,.08) 在白底几乎不可见。
 * 所以白天的轴标签/标题/图例一律用实测 ≥4.5:1 的深墨绿。
 *
 * 用法：
 *   import { chartPalette } from '@/lib/chartPalette'
 *   const p = chartPalette(isDark)
 *   option = { ..., axisLabel: { color: p.axisLabel }, ... }
 *
 * 切换主题时**不要重建图表实例**（记忆里的坑：用 key 重建会取消动画），
 *   只需 setOption({ ...option }) 让颜色生效。
 */

/** 暗色主题：保持 SENTINEL 原有配色，逐字不动 */
const DARK = {
  /* 轴与网格 */
  axisLabel: '#6b7870',
  axisLabelStrong: '#a7b3ab',
  axisLine: 'rgba(180,220,195,0.12)',
  splitLine: 'rgba(180,220,195,0.08)',
  /* 文字 */
  text: '#e9f0ea',
  textMuted: '#6b7870',
  title: '#e9f0ea',
  legend: '#a7b3ab',
  tooltipBg: 'rgba(17,23,20,0.96)',
  tooltipBorder: 'rgba(182,243,74,0.28)',
  tooltipText: '#e9f0ea',
  /* 系列 */
  series: ['#4edea3', '#4cd7f6', '#ff5c7a', '#ffc857', '#b6f34a', '#a100f2'],
  green: '#4edea3',
  cyan: '#4cd7f6',
  red: '#ff5c7a',
  amber: '#ffc857',
  lime: '#b6f34a',
  purple: '#a100f2',
} as const

/**
 * 亮色主题（白绿）
 * 轴标签 #46584f（7.0:1 AAA）、网格线用墨绿低透（白底可见但不抢眼）。
 * 系列色全部加深一档，保证在白底上仍是可辨识的深色。
 */
const LIGHT = {
  axisLabel: '#46584f',
  axisLabelStrong: '#12211a',
  axisLine: 'rgba(23,58,43,0.18)',
  splitLine: 'rgba(23,58,43,0.09)',
  text: '#12211a',
  textMuted: '#63746b',
  title: '#12211a',
  legend: '#46584f',
  /* 白天 tooltip 也是绿底白字（用户指定，与侧栏/antd 浮层一致） */
  tooltipBg: '#05825c',
  tooltipBorder: 'rgba(255,255,255,0.28)',
  tooltipText: '#ffffff',
  series: ['#047d57', '#0e7490', '#d81b40', '#b45309', '#059669', '#7c3aed'],
  green: '#047d57',
  cyan: '#0e7490',
  red: '#d81b40',
  amber: '#b45309',
  lime: '#059669',
  purple: '#7c3aed',
} as const

export type ChartPalette = {
  axisLabel: string
  axisLabelStrong: string
  axisLine: string
  splitLine: string
  text: string
  textMuted: string
  title: string
  legend: string
  tooltipBg: string
  tooltipBorder: string
  tooltipText: string
  series: readonly string[]
  green: string
  cyan: string
  red: string
  amber: string
  lime: string
  purple: string
}

/** 按主题取色板。isDark 来自 useTheme()。 */
export function chartPalette(isDark: boolean): ChartPalette {
  return isDark ? DARK : LIGHT
}

/** 严重度色：白天全部加深（暗色的玫红/琥珀在白底偏浅） */
export function severityPalette(isDark: boolean) {
  return isDark
    ? {
        critical: '#ff5c7a',
        high: '#ff9505',
        medium: '#ffc857',
        low: '#4cd7f6',
        info: '#8a9a9a',
      }
    : {
        critical: '#be123c',
        high: '#c2410c',
        medium: '#b45309',
        low: '#0e7490',
        info: '#63746b',
      }
}
