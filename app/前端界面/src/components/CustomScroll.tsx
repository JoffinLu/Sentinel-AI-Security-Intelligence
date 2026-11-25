/**
 * 自绘滚动条
 * ------------------------------------------------------------
 * ---------- 为什么必须自己画 ----------
 *
 * 用户反馈「拖动条一闪一闪」。前一轮已把 `overflow: auto` 改成
 * `scroll`，但**问题依旧** —— 因为 Windows / macOS 有系统级的
 * **「自动隐藏滚动条」**设置：页面静止时轨道淡出、鼠标移入才淡入。
 *
 * `overflow: scroll` 只能强制**轨道占位**，
 * 但无法阻止系统对**滑块本身**做淡出动画。
 * 换句话说：只要用原生滚动条，就逃不掉这个系统行为。
 *
 * 所以本组件**彻底隐藏原生条、自绘一个**：
 *   1. 原生条 `width/height: 0`（::-webkit-scrollbar + scrollbar-width）
 *   2. 自绘滑块的位置与长度由滚动容器的真实 scrollTop/scrollHeight 算出
 *   3. 支持拖动、点击轨道跳转
 *   4. **滚轮 / 触控板 / 键盘滚动照常**——因为 overflow 仍是 auto，
 *      我们只是不显示它的原生条，没有替换滚动行为
 *
 * 这样滚动条**完全由我们控制**，系统设置不再影响。
 *
 * ---------- 两种用法 ----------
 *
 * ① 包裹式（卡片内部滚动区）：
 *    <CustomScrollArea dir="y">{items}</CustomScrollArea>
 *
 * ② 绑定式（AntD 表格的横向滚动区 —— 容器是组件内部生成的，
 *    无法包裹，只能拿到 ref 后把滑块覆盖上去）：
 *    const innerRef = useTableScrollInner(wrapRef, '.ant-table-content')
 *    <CustomScrollbar targetRef={innerRef} dir="x" />
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type RefObject,
} from 'react'

type Dir = 'x' | 'y'

interface Metrics {
  /** 滑块长度（px） */
  thumb: number
  /** 滑块起点（px） */
  offset: number
  /** 内容是否溢出 */
  scrollable: boolean
}

const IDLE: Metrics = { thumb: 0, offset: 0, scrollable: false }

/** 滑块最小长度：太短就抓不住了 */
const MIN_THUMB = 32

function measure(el: HTMLElement, dir: Dir): Metrics {
  const vertical = dir === 'y'
  const scrollSize = vertical ? el.scrollHeight : el.scrollWidth
  const clientSize = vertical ? el.clientHeight : el.clientWidth
  if (scrollSize - clientSize <= 1) return IDLE
  const thumb = Math.max(MIN_THUMB, clientSize * (clientSize / scrollSize))
  const maxOffset = clientSize - thumb
  const maxScroll = scrollSize - clientSize
  const offset = maxScroll > 0 ? (el.scrollTop / maxScroll) * maxOffset : 0
  return { thumb, offset, scrollable: true }
}

interface DragState {
  /** 按下时的指针位置 */
  startPointer: number
  /** 按下时的滑块位置 */
  startOffset: number
}

/** 滚动区 + 自绘滑块的共用逻辑 */
function useCustomScroll(targetRef: RefObject<HTMLElement | null>, dir: Dir) {
  const [metrics, setMetrics] = useState<Metrics>(IDLE)
  const drag = useRef<DragState | null>(null)

  const sync = useCallback(() => {
    const el = targetRef.current
    if (el) setMetrics(measure(el, dir))
  }, [dir, targetRef])

  // 首次挂载后立即量一次。
  // 依赖数组用 [sync]：dir 变化时需要重新测量，
  // 但不能每次渲染都量（那会引发 setState 循环）。
  useLayoutEffect(sync, [sync])

  useEffect(() => {
    const el = targetRef.current
    if (!el) return
    const onScroll = () => sync
    el.addEventListener('scroll', onScroll, { passive: true })
    // 观察自身与子元素：数据异步加载、窗口缩放都会改变内容尺寸，
    // 不观察就会出现「滑块长度对不上实际可滚动范围」。
    const ro = new ResizeObserver(sync)
    ro.observe(el)
    for (const child of Array.from(el.children)) ro.observe(child)
    return () => {
      el.removeEventListener('scroll', onScroll)
      ro.disconnect
    }
  }, [sync, targetRef])

  /** 按方向读取某项尺寸 */
  const size = useCallback(
    (el: HTMLElement, vertical: boolean) => (vertical ? el.clientHeight : el.clientWidth),
    [],
  )
  const scrollSize = useCallback(
    (el: HTMLElement, vertical: boolean) => (vertical ? el.scrollHeight : el.scrollWidth),
    [],
  )
  const pointer = useCallback(
    (e: { clientX: number; clientY: number }, vertical: boolean) =>
      vertical ? e.clientY : e.clientX,
    [],
  )

  const onThumbPointerDown = useCallback(
    (e: React.PointerEvent) => {
      const el = targetRef.current
      if (!el) return
      e.preventDefault
      e.stopPropagation
      const vertical = dir === 'y'
      drag.current = {
        startPointer: pointer(e, vertical),
        startOffset: metrics.offset,
      }
      e.currentTarget.setPointerCapture?.(e.pointerId)
    },
    [dir, metrics.offset, pointer, targetRef],
  )

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      const d = drag.current
      const el = targetRef.current
      if (!d || !el) return
      const vertical = dir === 'y'
      const client = size(el, vertical)
      const maxOffset = Math.max(1, client - metrics.thumb)
      const maxScroll = Math.max(0, scrollSize(el, vertical) - client)
      const next = Math.max(0, Math.min(maxOffset, d.startOffset + (pointer(e, vertical) - d.startPointer)))
      if (vertical) el.scrollTop = (next / maxOffset) * maxScroll
      else el.scrollLeft = (next / maxOffset) * maxScroll
    },
    [dir, metrics.thumb, pointer, scrollSize, size, targetRef],
  )

  const endDrag = useCallback((e: React.PointerEvent) => {
    drag.current = null
    e.currentTarget.releasePointerCapture?.(e.pointerId)
  }, [])

  /** 点击轨道空白处 → 按比例跳转（点空白 = 翻一屏的直觉） */
  const onTrackPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.target !== e.currentTarget) return
      const el = targetRef.current
      if (!el) return
      const vertical = dir === 'y'
      const rect = e.currentTarget.getBoundingClientRect()
      const clickPos = vertical ? e.clientY - rect.top : e.clientX - rect.left
      const client = size(el, vertical)
      const maxOffset = Math.max(1, client - metrics.thumb)
      const maxScroll = Math.max(0, scrollSize(el, vertical) - client)
      // 以「点到的位置」与「滑块中心」的差值换算成滚动量
      const ratio = (clickPos - (metrics.offset + metrics.thumb / 2)) / maxOffset
      const cur = vertical ? el.scrollTop : el.scrollLeft
      const next = Math.max(0, Math.min(maxScroll, cur + ratio * maxScroll))
      if (vertical) el.scrollTop = next
      else el.scrollLeft = next
    },
    [dir, metrics.offset, metrics.thumb, scrollSize, size, targetRef],
  )

  return { metrics, handlers: { onThumbPointerDown, onPointerMove, endDrag, onTrackPointerDown } }
}

function thumbStyle(m: Metrics, dir: Dir): CSSProperties {
  return dir === 'y'
    ? { height: m.thumb, transform: `translateY(${m.offset}px)` }
    : { width: m.thumb, transform: `translateX(${m.offset}px)` }
}

/**
 * 包裹式自绘滚动区
 *
 * 隐藏原生条 → 保留 overflow 让滚轮/键盘照常 → 外挂自绘滑块。
 */
export function CustomScrollArea({
  children,
  dir = 'y',
  className,
  style,
}: {
  children: ReactNode
  dir?: Dir
  className?: string
  style?: CSSProperties
}) {
  const ref = useRef<HTMLDivElement>(null)
  const { metrics, handlers } = useCustomScroll(ref, dir)

  return (
    <div className={`cs-area cs-area-${dir}${className ? ` ${className}` : ''}`} style={style}>
      {/*
        高度约束必须落在 **viewport** 上，不是外层容器。
        踩过的坑：原来 `max-height` 在 `.intel-paper-list`（外层 .cs-area），
        于是外层被限高 420px、内层 viewport 却撑到 1092px 且
        `scrollHeight === clientHeight` —— **永远不触发滚动**，
        滑块也就永远不出现。
        滚动发生在哪个元素上，max-height 就必须加在哪个元素上。
      */}
      <div ref={ref} className="cs-viewport">
        {children}
      </div>
      {metrics.scrollable && (
        <div
          className={`cs-track cs-track-${dir}`}
          onPointerDown={handlers.onTrackPointerDown}
          aria-hidden
        >
          <div
            className="cs-thumb"
            style={thumbStyle(metrics, dir)}
            onPointerDown={handlers.onThumbPointerDown}
            onPointerMove={handlers.onPointerMove}
            onPointerUp={handlers.endDrag}
            onPointerCancel={handlers.endDrag}
          />
        </div>
      )}
    </div>
  )
}

/**
 * 绑定式自绘滚动条
 *
 * 用于容器不是自己渲染的场景（如 AntD Table 内部的
 * `.ant-table-content`）：只把滑块覆盖到容器旁边，不碰内容结构。
 * 调用方需给外层一个 `position: relative` 的定位上下文。
 */
export function CustomScrollbar({
  targetRef,
  dir = 'y',
}: {
  targetRef: RefObject<HTMLElement | null>
  dir?: Dir
}) {
  const { metrics, handlers } = useCustomScroll(targetRef, dir)
  if (!metrics.scrollable) return null
  return (
    <div
      className={`cs-track cs-track-float cs-track-${dir}`}
      onPointerDown={handlers.onTrackPointerDown}
      aria-hidden
    >
      <div
        className="cs-thumb"
        style={thumbStyle(metrics, dir)}
        onPointerDown={handlers.onThumbPointerDown}
        onPointerMove={handlers.onPointerMove}
        onPointerUp={handlers.endDrag}
        onPointerCancel={handlers.endDrag}
      />
    </div>
  )
}

/**
 * 从外层容器里找到内部滚动区并返回 ref
 *
 * AntD Table 的滚动容器是它自己生成的，没法用 ref 直接拿。
 * 只能等挂载后 querySelector 一次。
 */
export function useInnerScrollRef(
  wrapperRef: RefObject<HTMLElement | null>,
  selector: string,
): RefObject<HTMLDivElement | null> {
  const innerRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    // 必须等 DOM 挂载后再查：AntD Table 的内部容器是渲染后才存在的。
    // 用 [wrapperRef, selector] 而非无依赖数组——否则每次渲染都查一次，
    // 虽然不会死循环（只写 ref），但语义上应当只在依赖变化时查。
    innerRef.current =
      (wrapperRef.current?.querySelector(selector) as HTMLDivElement | null) ?? null
  }, [wrapperRef, selector])
  return innerRef
}
