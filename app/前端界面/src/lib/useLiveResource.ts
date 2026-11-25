/**
 * 可靠轮询 Hook（优化复用手册 3.2）
 *
 * 解决三个常见问题：
 *   ① 刷新失败时把已有数据清空 → 界面瞬间空白；
 *   ② 新一轮请求发出时，上一轮还在路上 → 旧响应覆盖新结果（竞态）；
 *   ③ 标签页切到后台还在轮询 → 浪费资源并唤醒连接。
 *
 * 语义（与手册 5.3 的新鲜度约定对应）：
 *   data      —— 最后一次**成功**的快照（失败时保持不变）
 *   loading   —— 首次加载中（还没有任何快照）
 *   error     —— 最近一次刷新失败（有旧快照时界面仍展示旧数据）
 *   updatedAt —— 最后成功时间，供界面显示"数据陈旧了多久"
 */
import { useCallback, useEffect, useRef, useState } from 'react'

export interface LiveResource<T> {
  data: T | null
  loading: boolean
  error: string | null
  updatedAt: Date | null
  refresh: () => void
}

export function useLiveResource<T>(
  loader: (signal?: AbortSignal) => Promise<T>,
  interval = 30_000,
): LiveResource<T> {
  const loaderRef = useRef(loader)
  loaderRef.current = loader

  const request = useRef<AbortController | null>(null)
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)

  /* 用 ref 读最新 data：refresh 身份保持稳定，
     既能读���「是否已有快照」，又不会因 data 变化而让
     useEffect([interval]) 拿到过期闭包。 */
  const dataRef = useRef<T | null>(null)
  dataRef.current = data

  const refresh = useCallback(async () => {
    // 手册 3.2：新一轮开始前先中止上一轮——
    // 否则先发后到的旧响应会覆盖新结果
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller

    // 只有"从来没有过快照"才显示 loading；
    // 已有数据时保持界面稳定，只在后台静默刷新
    setLoading(dataRef.current === null)

    try {
      const result = await loaderRef.current(controller.signal)
      if (controller.signal.aborted) return
      setData(result)
      setError(null)
      setUpdatedAt(new Date())
    } catch (cause) {
      if (!controller.signal.aborted) {
        // 失败**不清空** data：旧快照继续可看，界面标注"已陈旧"
        setError(cause instanceof Error ? cause.message : '刷新失败')
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
    // 后台标签页不轮询：切回前台或恢复联网时立即补一次
    const whenVisible = () => {
      if (document.visibilityState === 'visible') void refresh()
    }
    const timer = window.setInterval(whenVisible, interval)
    document.addEventListener('visibilitychange', whenVisible)
    window.addEventListener('online', whenVisible)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', whenVisible)
      window.removeEventListener('online', whenVisible)
      request.current?.abort()
    }
  }, [refresh, interval])

  return { data, loading, error, updatedAt, refresh }
}

/** 五态新鲜度（手册 5.3）：界面据此决定提示文案，不只是红绿灯 */
export type LiveState =
  | 'connecting'
  | 'healthy'
  | 'degraded'
  | 'stale'
  | 'offline'

export function resolveLiveState(
  hasData: boolean,
  loading: boolean,
  error?: string | null,
  degraded?: boolean,
): LiveState {
  if (loading && !hasData) return 'connecting'
  if (error && !hasData) return 'offline'
  if (error && hasData) return 'stale'
  if (degraded) return 'degraded'
  return 'healthy'
}