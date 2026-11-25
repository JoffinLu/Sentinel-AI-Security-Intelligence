/**
 * 主题状态（黑夜 / 白天）· 全站单一数据源
 * ------------------------------------------------------------
 * 切换时同步写三处：
 *   ① React state——驱动 ConfigProvider 与开关 UI；
 *   ② <html data-theme>——CSS 变量与背景脚本的驱动开关；
 *   ③ localStorage 'sentinel-theme'——刷新后保持。
 *
 * 为什么初始值从 DOM 读：
 *   index.html 里的防闪烁内联脚本已在样式加载前写好了 data-theme，
 *   这里以它为准，保证 React 状态与页面实际显示的主题一致——
 *   避免「先按默认渲染、effect 里再改」造成的一帧闪烁。
 *
 * 注意：背景脚本（sentinel-bg-react.js）自行监听 data-theme 属性，
 * 不依赖本模块——两条链路互不耦合。
 */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from 'react'
import type { ReactNode } from 'react'

export type ThemeMode = 'dark' | 'light'

const STORAGE_KEY = 'sentinel-theme'
const ATTR = 'data-theme'

/** 初始主题：以 <html data-theme> 为准，localStorage 兜底，默认黑夜 */
export function readInitialTheme(): ThemeMode {
  const fromDom = document.documentElement.getAttribute(ATTR)
  if (fromDom === 'light' || fromDom === 'dark') return fromDom
  try {
    return localStorage.getItem(STORAGE_KEY) === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

/** 把主题写到 DOM 与 localStorage（React 状态之外的两处） */
function applyToDom(mode: ThemeMode) {
  document.documentElement.setAttribute(ATTR, mode)
  try {
    localStorage.setItem(STORAGE_KEY, mode)
  } catch {
    /* 隐私模式 / 禁用存储：忽略即可，不影响本次会话内切换 */
  }
}

interface ThemeContextValue {
  /** 当前主题 */
  mode: ThemeMode
  /** 是否黑夜（写起来比 mode === 'dark' 顺手） */
  isDark: boolean
  /** 指定主题（外部一般用 toggle，此口子留给以后可能的跟随系统等需求） */
  setMode: (mode: ThemeMode) => void
  /** 在黑夜 / 白天之间切换 */
  toggle: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(readInitialTheme)

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next)
    applyToDom(next)
  }, [])

  const toggle = useCallback(() => {
    setModeState((prev) => {
      const next: ThemeMode = prev === 'dark' ? 'light' : 'dark'
      applyToDom(next)
      return next
    })
  }, [])

  const value = useMemo<ThemeContextValue>(
    () => ({ mode, isDark: mode === 'dark', setMode, toggle }),
    [mode, setMode, toggle],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

/** 读取主题状态；必须在 <ThemeProvider> 内使用 */
export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme 必须在 <ThemeProvider> 内使用')
  return ctx
}
