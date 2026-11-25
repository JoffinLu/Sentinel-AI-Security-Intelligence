/**
 * 指标卡片
 *
 * 用于看板顶部的关键指标展示，对应赛题性能指标的直观呈现。
 *
 * 主题：SENTINEL 设计语言 · 黑夜主题
 * 玻璃拟态面板 + tone 语义色描边与辉光，数字用等宽字体防跳动。
 * 布局结构未改动，仅换肤。
 */

import { Statistic } from 'antd'
import type { ReactNode } from 'react'
import { IconTile } from '@/components/SentinelLogo'
import { useTheme } from '@/lib/theme'

interface MetricCardProps {
  title: string
  value: ReactNode
  suffix?: ReactNode
  hint?: string
  tone?: 'default' | 'success' | 'warning' | 'danger'
  /** 卡片图标（建议配 IconTile 使用） */
  icon?: ReactNode
  /** 图标底座色调 */
  iconTone?: 'primary' | 'secondary' | 'accent' | 'rose'
  /** 鼠标悬停时显示的微光颜色（默认跟随 tone） */
  glow?: string
}

/**
 * 语气 → SENTINEL 语义色
 *
 * 沿用设计语言的状态色体系：翡翠绿（达标）/ 琥珀（注意）/ 玫红（告急）。
 * 只给数字与左侧色条上色，卡片本体保持统一玻璃质感——
 * 否则四张卡颜色各异会显得杂乱，反而削弱指标的严肃性。
 */
const TONE_COLOR: Record<string, string> = {
  default: '#a7b3ab',
  success: '#3ddc97',
  warning: '#ffc857',
  danger: '#ff5c7a',
}

/** 白天版（白卡深字）：同色相加深，对白底/卡片底均 ≥4.5。
 * （00:15 曾按用户要求改过绿底+浅色数字，00:24 用户看实效果后改回白卡，
 *   数字随之回到深色系。） */
const TONE_COLOR_LIGHT: Record<string, string> = {
  default: '#55645c',
  success: '#04785a',
  warning: '#a06a00',
  danger: '#c2273a',
}

export function MetricCard({
  title,
  value,
  suffix,
  hint,
  tone = 'default',
  icon,
  iconTone = 'primary',
  glow,
  delay = 0,
}: MetricCardProps & { delay?: number }) {
  /* 主题：数字/色条色与卡片背景都要分白天/暗色两套 */
  const { isDark } = useTheme()
  const color = (isDark ? TONE_COLOR : TONE_COLOR_LIGHT)[tone]
    ?? (isDark ? TONE_COLOR : TONE_COLOR_LIGHT).default
  const glowColor = glow ?? color
  return (
    <div
      className="sentinel-metric stagger-in"
      /* ---------- 为什么背景改成 CSS 变量而不是继续内联 ----------
       * 原来这里是写死的深色渐变（内联 style），CSS 选择器**盖不住内联样式**，
       * 于是白天主题下四张指标卡仍是深墨绿 → 用户反馈「卡片颜色不统一」。
       * 改成 var(--metric-bg) 后：
       *   - 暗色：变量默认值 = 原深色渐变，视觉逐字不变
       *   - 白天：在 tokens 里换成白底，同一张卡自动变白
       * 边框/阴影同理，都换成变量，两套主题各自成立。
       */
      style={{
        background: 'var(--metric-bg)',
        borderRadius: 18,
        padding: '14px 16px',
        border: '1px solid var(--metric-border)',
        boxShadow: 'var(--metric-shadow)',
        height: '100%',
        position: 'relative',
        overflow: 'hidden',
        animationDelay: `${delay}s`,
        backdropFilter: 'var(--metric-blur)',
        WebkitBackdropFilter: 'var(--metric-blur)',
        transition: 'transform 340ms cubic-bezier(0.16, 1, 0.3, 1), box-shadow 300ms, border-color 300ms',
      }}
    >
      {/* 左侧语气色条：一眼看出该指标是否达标 */}
      <span
        aria-hidden
        style={{
          position: 'absolute',
          left: 0,
          top: 14,
          bottom: 14,
          width: 3,
          borderRadius: '0 3px 3px 0',
          background: color,
          boxShadow: `0 0 10px ${color}66`,
        }}
      />

      {/* HUD 角标括号：作战室取景框质感，悬停时点亮 */}
      <span className="corner-mark tl" aria-hidden />
      <span className="corner-mark br" aria-hidden />

      {/* 悬停微光：跟随鼠标位置的柔光斑（--mx/--my 由 JS 写入） */}
      <span
        aria-hidden
        className="sentinel-metric-glow"
        style={{
          background: `radial-gradient(340px circle at var(--mx, -400px) var(--my, -400px), ${glowColor}22, transparent 62%)`,
        }}
      />

      <div style={{ position: 'relative', display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        {icon && <IconTile tone={iconTone}>{icon}</IconTile>}
        <div style={{ flex: 1, minWidth: 0 }}>
          <Statistic
            title={
              <span
                style={{
                  fontSize: 12.5,
                  color: 'var(--metric-fg-muted)',
                  fontWeight: 500,
                  letterSpacing: 0.3,
                }}
              >
                {title}
              </span>
            }
            value={value as never}
            suffix={
              suffix ? (
                <span style={{ fontSize: 13, color: 'var(--metric-fg-muted)' }}>{suffix}</span>
              ) : undefined
            }
            valueStyle={{
              fontSize: 26,
              fontWeight: 700,
              color,
              fontFamily: 'var(--mono)',
              fontVariantNumeric: 'tabular-nums',
              letterSpacing: -0.5,
              textShadow: `0 0 22px ${color}33`,
            }}
          />
          {hint && (
            <div style={{ fontSize: 12, color: 'var(--metric-fg-muted)', marginTop: 4 }}>{hint}</div>
          )}
        </div>
      </div>
    </div>
  )
}

/** 把秒数格式化为人类可读时长 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  if (seconds < 60) return `${Math.round(seconds)} 秒`
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)} 分钟`
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} 小时`
  return `${(seconds / 86400).toFixed(1)} 天`
}
