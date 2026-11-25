/**
 * SENTINEL 品牌标志
 *
 * 设计意象：「哨兵」——一个正在警戒的抽象哨塔。
 * 构成（自下而上三层语义）：
 *   ① 基座   —— 稳定边界，两条横向底线
 *   ② 塔身   —— 上窄下宽的梯形，取自雷达/监控塔轮廓
 *   ③ 雷达弧 —— 三道向左上发散的弧线，代表持续扫描的信号波
 *
 * 视觉处理：
 *   · 主体用翡翠绿渐变（--accent → --cyan），呼应设计语言主色
 *   · 雷达弧带 2.4s 循环「扫描」动画，静态展示时也不呆板
 *   · 外圈 3.6s 辉光呼吸，与侧栏 Logo 节奏一致
 *
 * 尺寸自适应：传入 size 即可，内部按比例缩放。
 */

interface SentinelLogoProps {
  /** 图形尺寸（px），默认 30 */
  size?: number
  /** 是否显示雷达扫描动画 */
  animated?: boolean
  /** 无障碍标题，为空则视为装饰性图形 */
  title?: string
  className?: string
}

export function SentinelLogo({
  size = 30,
  animated = true,
  title,
  className,
}: SentinelLogoProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      className={className}
      role={title ? 'img' : 'presentation'}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      {title && <title>{title}</title>}

      <defs>
        {/* 主体渐变：翡翠绿 → 情报青，与全站主色体系一致 */}
        <linearGradient id="sentinel-body" x1="12" y1="40" x2="36" y2="8">
          <stop offset="0%" stopColor="var(--accent-2, #8ed321)" />
          <stop offset="55%" stopColor="var(--accent, #b6f34a)" />
          <stop offset="100%" stopColor="var(--cyan, #4be1ec)" />
        </linearGradient>
        {/* 雷达弧渐变：由亮到透明，形成衰减感 */}
        <linearGradient id="sentinel-radar" x1="20" y1="20" x2="8" y2="8">
          <stop offset="0%" stopColor="var(--cyan, #4be1ec)" stopOpacity="0.9" />
          <stop offset="100%" stopColor="var(--cyan, #4be1ec)" stopOpacity="0.12" />
        </linearGradient>
      </defs>

      {/* ---- 雷达扫描弧（三道，向左上发散）---- */}
      <g
        stroke="url(#sentinel-radar)"
        strokeWidth="2.4"
        strokeLinecap="round"
        fill="none"
      >
        {/* 第一道（最内，紧贴塔身） */}
        <path d="M19 21 A 7 7 0 0 0 14 16" opacity="0.9">
          {animated && (
            <animate
              attributeName="opacity"
              values="0.35;1;0.35"
              dur="2.4s"
              repeatCount="indefinite"
            />
          )}
        </path>
        {/* 第二道（中间） */}
        <path d="M19 14 A 14 14 0 0 0 8 9" opacity="0.6">
          {animated && (
            <animate
              attributeName="opacity"
              values="0.18;0.75;0.18"
              dur="2.4s"
              begin="0.35s"
              repeatCount="indefinite"
            />
          )}
        </path>
        {/* 第三道（最外） */}
        <path d="M20 7 A 21 21 0 0 0 2 2" opacity="0.35">
          {animated && (
            <animate
              attributeName="opacity"
              values="0.08;0.5;0.08"
              dur="2.4s"
              begin="0.7s"
              repeatCount="indefinite"
            />
          )}
        </path>
      </g>

      {/* ---- 塔身：上窄下宽的梯形 ---- */}
      <path
        d="M26 13 L34 40 L16 40 Z"
        fill="url(#sentinel-body)"
        fillOpacity="0.22"
        stroke="url(#sentinel-body)"
        strokeWidth="2"
        strokeLinejoin="round"
      />

      {/* 塔身内部竖向扫描线：2.4s 上下往复，强化「正在工作」的观感 */}
      {animated && (
        <clipPath id="sentinel-tower-clip">
          <path d="M26 13 L34 40 L16 40 Z" />
        </clipPath>
      )}
      {animated && (
        <rect
          x="15"
          y="12"
          width="20"
          height="4"
          fill="var(--accent, #b6f34a)"
          fillOpacity="0.55"
          clipPath="url(#sentinel-tower-clip)"
        >
          <animate
            attributeName="y"
            values="14;36;14"
            dur="2.8s"
            repeatCount="indefinite"
          />
          <animate
            attributeName="opacity"
            values="0;0.6;0"
            dur="2.8s"
            repeatCount="indefinite"
          />
        </rect>
      )}

      {/* ---- 塔顶：警戒灯（呼吸辉光）---- */}
      <circle cx="25.5" cy="10" r="2.6" fill="var(--accent, #b6f34a)">
        {animated && (
          <animate
            attributeName="opacity"
            values="1;0.35;1"
            dur="2s"
            repeatCount="indefinite"
          />
        )}
      </circle>
      {animated && (
        <circle cx="25.5" cy="10" r="2.6" fill="none" stroke="var(--accent, #b6f34a)" strokeWidth="1">
          <animate
            attributeName="r"
            values="2.6;6"
            dur="2s"
            repeatCount="indefinite"
          />
          <animate
            attributeName="opacity"
            values="0.7;0"
            dur="2s"
            repeatCount="indefinite"
          />
        </circle>
      )}

      {/* ---- 基座：两条横线，表示稳定边界 ---- */}
      <path
        d="M11 40 L38 40"
        stroke="url(#sentinel-body)"
        strokeWidth="2.4"
        strokeLinecap="round"
      />
      <path
        d="M16 44 L33 44"
        stroke="var(--accent, #b6f34a)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeOpacity="0.55"
      />
    </svg>
  )
}

/**
 * 模块图标底座
 *
 * 卡片左上角的彩色圆角底座，继承设计语言的 icon-tile 配方
 * （色×10-16% 底 + 色×26-30% 边 + 亮色图标）。
 * 四种色调对应四个功能模块，与图表色板同源。
 */
export function IconTile({
  tone = 'primary',
  children,
  size = 40,
}: {
  tone?: 'primary' | 'secondary' | 'accent' | 'rose'
  children: React.ReactNode
  size?: number
}) {
  return (
    <span className={`sentinel-icon-tile sentinel-icon-tile-${tone}`} style={{ width: size, height: size }}>
      {children}
    </span>
  )
}
