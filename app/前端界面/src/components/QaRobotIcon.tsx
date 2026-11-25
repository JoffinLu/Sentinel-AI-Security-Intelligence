/**
 * 智能问答页标志 —— 青色机器人
 *
 * 设计要点：
 *   · 圆角方形头身 + 天线 + 双目，识别度高，一眼看出是「AI 助手」
 *   · 线性描边风格（stroke 而非实心），与全站细线图标语言一致
 *   · 配色用设计语言的 secondary「情报青」（--cyan），呼应看板图表
 *   · 双眼带 2.4s 呼吸辉光，让静止图标也有「在待机工作」的生气
 *
 * 尺寸自适应：传入 size 即可（viewBox 固定 64x64）。
 */

interface QaRobotIconProps {
  size?: number
  /** 是否让双眼呼吸（静态场景可关）*/
  animated?: boolean
  className?: string
}

export function QaRobotIcon({ size = 64, animated = true, className }: QaRobotIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      className={className}
      role="presentation"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="qa-body" x1="12" y1="20" x2="52" y2="56">
          <stop offset="0%" stopColor="var(--cyan, #4be1ec)" />
          <stop offset="100%" stopColor="var(--primary, #4edea3)" />
        </linearGradient>
      </defs>

      {/* ---- 天线 ---- */}
      <path
        d="M32 10V16"
        stroke="url(#qa-body)"
        strokeWidth="2.6"
        strokeLinecap="round"
      />
      <circle cx="32" cy="8" r="3" fill="url(#qa-body)" />

      {/* ---- 头身主体（圆角矩形）---- */}
      <rect
        x="10"
        y="18"
        width="44"
        height="32"
        rx="10"
        stroke="url(#qa-body)"
        strokeWidth="2.8"
        fill="rgba(75, 225, 236, 0.06)"
      />

      {/* ---- 双目 ---- */}
      {animated ? (
        <>
          <circle cx="24" cy="33" r="3.4" fill="var(--cyan, #4be1ec)">
            <animate
              attributeName="opacity"
              values="1;0.35;1"
              dur="2.4s"
              repeatCount="indefinite"
            />
          </circle>
          <circle cx="40" cy="33" r="3.4" fill="var(--cyan, #4be1ec)">
            <animate
              attributeName="opacity"
              values="1;0.35;1"
              dur="2.4s"
              repeatCount="indefinite"
            />
          </circle>
        </>
      ) : (
        <>
          <circle cx="24" cy="33" r="3.4" fill="var(--cyan, #4be1ec)" />
          <circle cx="40" cy="33" r="3.4" fill="var(--cyan, #4be1ec)" />
        </>
      )}

      {/* ---- 嘴部（一条横线，简洁但有表情）---- */}
      <path
        d="M26 42H38"
        stroke="url(#qa-body)"
        strokeWidth="2.4"
        strokeLinecap="round"
        opacity="0.75"
      />

      {/* ---- 颈部 ---- */}
      <path
        d="M32 50V56"
        stroke="url(#qa-body)"
        strokeWidth="2.6"
        strokeLinecap="round"
        opacity="0.7"
      />
      {/* ---- 肩部基座 ---- */}
      <path
        d="M20 56H44"
        stroke="url(#qa-body)"
        strokeWidth="2.6"
        strokeLinecap="round"
        opacity="0.55"
      />

      {/* ---- 两侧耳（接收器造型，暗示情报采集）---- */}
      <path
        d="M4 30H9"
        stroke="url(#qa-body)"
        strokeWidth="2.4"
        strokeLinecap="round"
        opacity="0.6"
      />
      <path
        d="M55 30H60"
        stroke="url(#qa-body)"
        strokeWidth="2.4"
        strokeLinecap="round"
        opacity="0.6"
      />
    </svg>
  )
}
