/**
 * 顶栏状态悬浮坞（Floating Dock 交互）
 *
 * 借鉴 Aceternity Floating Dock 的动画模型：图标常驻，
 * 鼠标经过时该项放大、**相邻项按距离衰减联动**，
 * 整个坞随鼠标位置连续插值 —— 而不是各图标独立切换。
 *
 * 为什么不用 CSS :hover：
 * CSS hover 是「命中/未命中」的二元切换，快速连续划过时
 * 每项各自独立跳变，看起来一格一格地闪，很不连贯。
 * 原版用 motion 的 spring 动画做连续插值，这里用原生实现同样的模型。
 *
 * 具体做法：
 *   · 监听鼠标在坞上的 x 坐标（一次监听，不是每项一个）
 *   · 每帧计算鼠标到各项中心的距离，据此算出 0~1 的「影响权重」
 *   · 权重经弹性缓动曲线整形后写入 CSS 变量 --w
 *   · CSS 侧用 --w 插值缩放、底色、辉光
 *   权重是连续的 → 鼠标划过时整条坞平滑起伏，而非逐项跳变。
 *
 * 降级：prefers-reduced-motion 时不做插值，仅保留静态样式。
 */

import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'

export interface DockItem {
  /** 文字标签（常显）*/
  label: string
  /** 图标（AntD 图标组件实例）*/
  icon: ReactNode
  /** 状态色：正常/警告/异常 */
  tone?: 'ok' | 'warn' | 'error' | 'idle'
  /** 点击行为；不传则仅作展示 */
  onClick?: () => void
  /**
   * 悬停提示文案（原生 title）。
   * 可点击的项应该说明「点它能做什么」，
   * 否则用户不知道这个标签不是纯展示。
   */
  title?: string
}

const TONE_COLOR: Record<string, string> = {
  ok: 'var(--ok, #3ddc97)',
  warn: 'var(--warn, #ffc857)',
  error: 'var(--danger, #ff5c7a)',
  idle: 'var(--muted, #6b7870)',
}

/** 影响半径（px）：鼠标在此距离内该项开始响应 */
const RANGE = 78
export function StatusDock({ items }: { items: DockItem[] }) {
  const dockRef = useRef<HTMLDivElement>(null)
  // 当前鼠标 x；null 表示鼠标不在坞内
  const mouseX = useRef<number | null>(null)
  // 每项的 DOM 引用，用于测量中心点
  const itemRefs = useRef<(HTMLElement | null)[]>([])
  // rAF 句柄，避免事件里反复起帧
  const raf = useRef(0)


  useEffect(() => {
    const dock = dockRef.current
    if (!dock) return

    // 尊重「减弱动态效果」偏好
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)')
    if (reduce.matches) return

    /**
     * 每帧更新：算出各项权重并写入 CSS 变量
     *
     * 权重公式：weight = 1 - clamp(dist / RANGE)
     * 再过一条「弹性」曲线让靠近时有轻微过冲感，
     * 这是原版 spring 手感的来源。
     */
    function tick() {
      const els = itemRefs.current
      const mx = mouseX.current

      if (mx === null) {
        // 鼠标离开：所有项回落到静止权重
        els.forEach((el) => el?.style.setProperty('--w', '0'))
        return
      }

      els.forEach((el) => {
        if (!el) return
        const r = el.getBoundingClientRect()
        const center = r.left + r.width / 2
        const dist = Math.abs(mx - center)
        // 距离归一化：0（在中心）→ 1（超出半径）
        const t = Math.min(1, dist / RANGE)
        // 余弦缓动：比线性更柔和，两端斜率为 0，不会有生硬的起止
        let w = (1 - t) * 0.5
        w = 0.5 - Math.cos(w * Math.PI) * 0.5
        el.style.setProperty('--w', w.toFixed(4))
      })
    }

    function onMove(e: PointerEvent) {
      mouseX.current = e.clientX
      if (!raf.current) {
        raf.current = requestAnimationFrame(() => {
          tick
          raf.current = 0
        })
      }
    }

    function onLeave() {
      mouseX.current = null
      if (!raf.current) {
        raf.current = requestAnimationFrame(() => {
          tick
          raf.current = 0
        })
      }
    }

    dock.addEventListener('pointermove', onMove)
    dock.addEventListener('pointerleave', onLeave)

    return () => {
      dock.removeEventListener('pointermove', onMove)
      dock.removeEventListener('pointerleave', onLeave)
      if (raf.current) cancelAnimationFrame(raf.current)
    }
  }, [items.length])

  return (
    <div
      ref={dockRef}
      className="sentinel-dock"
      role="group"
      aria-label="系统状态"
    >
      {items.map((it, i) => {
        const color = TONE_COLOR[it.tone ?? 'idle']
        const Tag = it.onClick ? 'button' : 'span'
        return (
          <Tag
            key={it.label}
            ref={
              ((el: HTMLElement | null) => {
                itemRefs.current[i] = el
              }) as never
            }
            type={it.onClick ? 'button' : undefined}
            className="sentinel-dock-item"
            onClick={it.onClick}
            title={it.title}
            style={{ ['--tone' as string]: color }}
          >
            <span className="sentinel-dock-icon" aria-hidden="true">
              {it.icon}
              {/* 状态光点：右下角小圆点，不读文字也能判断状态 */}
              <i className="sentinel-dock-dot" />
            </span>
            <span className="sentinel-dock-label">{it.label}</span>
          </Tag>
        )
      })}
    </div>
  )
}
