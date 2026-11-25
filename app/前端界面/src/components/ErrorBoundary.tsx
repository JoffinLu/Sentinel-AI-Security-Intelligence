/**
 * 页面级错误边界（优化复用手册 3.7）
 *
 * 为什么需要：
 * 单个图表或详情页渲染异常时，如果直接抛给 React，
 * 整棵子树会卸载 → 用户看到的是**整页白屏**，只能刷新。
 * 加一层边界后，出错的区域降级为一张提示卡，
 * 侧栏与顶栏（工作空间外壳）保持可用——用户可以切到别的模块继续用。
 *
 * 与 QAPage 内的 MessageBoundary 是两层：
 *   · MessageBoundary  —— 单条回答的降级（已在 QAPage 内）
 *   · PageErrorBoundary —— 整个页面区域的降级（这里）
 */
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Alert, Button, Space } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'

interface Props {
  children: ReactNode
  /** 降级时显示的区域名，便于定位是哪一块坏了 */
  area?: string
  /** 是否提供「重新加载」按钮（整站级边界需要，局部边界可关闭） */
  reloadable?: boolean
}

interface State {
  failed: boolean
  msg: string
}

export class PageErrorBoundary extends Component<Props, State> {
  state: State = { failed: false, msg: '' }

  static getDerivedStateFromError(err: unknown): State {
    return {
      failed: true,
      msg: err instanceof Error ? err.message : String(err),
    }
  }

  componentDidCatch(err: Error, info: ErrorInfo) {
    // 留日志便于排查；不弹 toast —— 此刻界面可能正在重建
    console.error(
      `[ErrorBoundary] ${this.props.area ?? '页面'}渲染失败:`,
      err.message,
      info.componentStack,
    )
  }

  render() {
    if (!this.state.failed) return this.props.children

    return (
      <div style={{ padding: '48px 24px' }}>
        <Alert
          type="warning"
          showIcon
          message={`${this.props.area ?? '该区域'}暂时无法显示`}
          description={
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <span style={{ fontSize: 12.5, opacity: 0.85 }}>
                错误信息：{this.state.msg.slice(0, 200)}
              </span>
              <span style={{ fontSize: 12.5, opacity: 0.7 }}>
                其他模块不受影响，可切换左侧导航继续使用。
              </span>
            </Space>
          }
          action={
            this.props.reloadable === false ? undefined : (
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={() => window.location.reload()}
              >
                重新加载
              </Button>
            )
          }
        />
      </div>
    )
  }
}