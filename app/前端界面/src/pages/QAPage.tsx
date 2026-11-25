/**
 * 情报问答页
 *
 * 赛题要求核心能力的可视化载体。设计要点：
 *   ① 答案与引用分离——答案区只讲结论，引用区给出可点击的溯源
 *   ② 推理链可见——展示意图判定、检索、图谱多跳各步骤，让结论可解释
 *   ③ 流式输出——答案逐段渲染，体感延迟低
 *   ④ 推荐追问——基于图谱邻接节点，一键深入
 *   ⑤ 左侧对话历史——多会话切换，历史存localStorage，刷新不丢
 *
 * 布局参考 DeepSeek 式对话界面：**左历史栏 + 右对话区**，
 * 历史栏可折叠（窄屏默认折叠），对话区占满剩余宽度。
 */

import { Component, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Alert,
  Button,
  Card,
  Col,
  Input,
  Row,
  Space,
  Spin,
  Switch,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  SendOutlined,
  ClearOutlined,
  ThunderboltOutlined,
  NodeIndexOutlined,
  FileTextOutlined,
  LinkOutlined,
  PlusOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  DeleteOutlined,
  CopyOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'

import { qaApi } from '@/api/qa'
import type { QAResult, Citation, GraphPath, ReasoningStep, ToolCall } from '@/api/qa'
import { api } from '@/api/client'
import type { KnowledgeBaseStats } from '@/api/client'
import { QaRobotIcon } from '@/components/QaRobotIcon'
import { qaHistory, timeLabel, type StoredSession } from '@/lib/qaHistory'
import { useTheme } from '@/lib/theme'

const { Text } = Typography

/** 会话内的一条消息 */
interface Message {
  role: 'user' | 'assistant'
  content: string
  result?: QAResult
  /** Agent 模式下 LLM 实际调用的工具轨迹 */
  toolCalls?: ToolCall[]
  /** 流式过程中先到的骨架数据 */
  meta?: {
    intent_label?: string
    confidence?: number
    citations?: Citation[]
    reasoning?: ReasoningStep[]
    graph_paths?: GraphPath[]
  }
  streaming?: boolean
  error?: string
  /** 用户主动停止或流中断：保留已收到的部分答案（手册 3.4） */
  stopped?: boolean
}

const INTENT_COLOR: Record<string, string> = {
  fact: 'blue',
  trend: 'purple',
  relation: 'magenta',
  detail: 'orange',
}

export default function QAPage() {
  const { isDark } = useTheme()
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  /**
   * Agent 模式开关 —— **默认开启**。
   * 关掉则退回改造前的「固定意图 + 模板」路径，
   * 用于对照演示，也用于赛题要求现场需要严格 P95 时临时切换。
   */
  const [agentMode, setAgentMode] = useState(true)
  /**
   * 会话 id 的唯一真源是 state（不是 ref）。
   * 因为它要被 UI 读取（高亮当前会话、新建/切换时改写），
   * 放进 ref 会导致「值变了但界面不更新」。
   * `sessionRef` 只作为流式请求里的快照引用。
   */
  /**
   * 会话 id 的唯一真源是 state（不是 ref）。
   * 因为它要被UI 读取（高亮当前会话、新建/切换时改写），
   * 放进 ref 会导致「值变了但界面不更新」。
   *
   * ⚠ 修复「刷新一次就多一个新对话」：
   * 原写成 `useState(() => qaHistory.create)`，
   * 而 `create` 会**往localStorage 追加一条记录** ——
   * 于是每次挂载（= 每次刷新）都新增一条空会话，
   * 历史列表越刷越长。
   *
   * 正确做法：**只在没有任何历史时才创建**；
   * 有历史则复用最近一条（继续上次没说完的对话），
   * 这也更符合 ChatGPT/DeepSeek 的行为。
   */
  const [sessionId, setSessionId] = useState(() => {
    const recent = qaHistory.list()[0]
    if (recent) return recent.id
    return qaHistory.create()
  })
  const sessionRef = useRef(sessionId)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  /* 请求身份（优化手册 3.4）：
     仅 abort() 不够——被取消的请求，其已排队的回调仍可能执行，
     写进 UI 就是「切到会话 B 后，A 的答案串进 B」。
     每次发问分配递增身份，写入前核对「我是最新请求吗 + 会话没换 + 未取消」。*/
  const requestIdRef = useRef(0)

  // 历史栏：会话列表 + 折叠态。窄屏默认折叠，避免挤占对话区。
  const [sessions, setSessions] = useState<StoredSession[]>([])
  const [historyOpen, setHistoryOpen] = useState(
     () => typeof window !== 'undefined' && window.innerWidth >= 900,
  )

  /** 刷新历史列表（排序由 qaHistory.list 负责，最新在前） */
  const refreshSessions = useCallback(() => setSessions(qaHistory.list()), [])

  useEffect(() => {
    refreshSessions()
    // 挂载时把「最近一条会话」的消息恢复到界面 ——
    // 刷新后应接着上次继续，而不是看到一个空白页。
    // 只在该会话确实有内容时恢复，空会话仍显示空态引导。
    const recent = qaHistory.list()[0]
    if (recent && recent.messages.length > 0) {
      // 恢复完整消息：工具轨迹、错误与停止状态一并带回，
      // 引用恢复到 meta（与实时渲染同一条路径），
      // 否则刷新后「这条结论的依据是什么」就答不上来了（手册 3.6）
      setMessages(
        recent.messages.map((m) => ({
          role: m.role,
          content: m.content,
          error: m.error,
          stopped: m.stopped,
          meta: m.result?.citations?.length
            ? { citations: m.result.citations as Citation[] }
            : undefined,
          toolCalls: m.result?.tool_calls as ToolCall[] | undefined,
        })),
      )
    }
  }, [refreshSessions])

  /**
   * 消息变化 → 写入 localStorage
   *
   * 用 `[messages]` 依赖即可：流式过程中 messages 频繁变化，
   * 这里做防抖避免每帧都写一次存储。
   */
  useEffect(() => {
    if (messages.length === 0) return
    const t = setTimeout(() => {
      qaHistory.save(
        sessionId,
        messages.map((m) => ({
          role: m.role,
          content: m.content,
          error: m.error,
          stopped: m.stopped,
          /* 只留可展示的证据：答案、引用标题与链接、工具轨迹。
           * 完整 result 里还有检索骨架与图谱路径，体积大且恢复时用不上。 */
          result: m.result
            ? {
                answer: m.result.answer,
                citations: m.result.citations?.map((c) => ({
                  title: c.title,
                  url: c.url ?? undefined,
                })),
                tool_calls: m.toolCalls?.map((tc) => ({
                  name: tc.tool,
                  result_preview: tc.result_preview,
                })),
                backend: (m.result as { backend?: string } | undefined)?.backend,
              }
            : undefined,
        })),
      )
      refreshSessions()
    }, 600)
    return () => clearTimeout(t)
  }, [messages, sessionId, refreshSessions])

  /** 新建会话 */
  const newSession = useCallback(() => {
    const id = qaHistory.create()
    setSessionId(id)
    sessionRef.current = id
    setMessages([])
    setHistoryOpen(true)
  }, [])

  /** 切换到某个历史会话 */
  const switchSession = useCallback(
    (id: string) => {
      if (id === sessionId) return
      const s = qaHistory.get(id)
      setSessionId(id)
      sessionRef.current = id
      // 从存储还原为页面消息结构
      setMessages(
        (s?.messages ?? []).map((m) => ({ role: m.role, content: m.content })),
      )
    },
    [sessionId],
  )

  /** 删除某个历史会话（删当前会话时自动开新会话） */
  const removeSession = useCallback(
    (id: string) => {
      qaHistory.remove(id)
      refreshSessions()
      if (id === sessionId) {
        const fresh = qaHistory.create()
        setSessionId(fresh)
        sessionRef.current = fresh
        setMessages([])
      }
    },
    [sessionId, refreshSessions],
  )

  /**
   * 知识底座规模——从后端实时取，不硬编码。
   * 硬编码的问题是数据增长后页面还在说"4710 条"，答辩现场被问到会很被动。
   */
  const [kb, setKb] = useState<KnowledgeBaseStats | null>(null)

  useEffect(() => {
    api
      .knowledgeBase()
      .then(setKb)
      .catch(() => setKb(null))
  }, [])

  // 自动滚动到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  /** 按「今天 / 昨天 / 更早」给历史列表分组，与主流聊天应用一致 */
  const grouped = useMemo(() => {
    const now = Date.now()
    const day = 86_400_000
    const startOfToday = new Date(now).setHours(0, 0, 0, 0)
    const buckets: Array<{ label: string; items: StoredSession[] }> = [
      { label: '今天', items: [] },
      { label: '昨天', items: [] },
      { label: '更早', items: [] },
    ]
    for (const s of sessions) {
      if (s.updatedAt >= startOfToday) buckets[0].items.push(s)
      else if (s.updatedAt >= startOfToday - day) buckets[1].items.push(s)
      else buckets[2].items.push(s)
    }
    return buckets.filter((b) => b.items.length > 0)
  }, [sessions])

  const ask = async (question: string) => {
    if (!question.trim() || loading) return

    const q = question.trim()
    /* 会话身份在发问这一刻定格——之后即使用户切了会话，
       本次请求的回调也会被 isCurrent() 拦下（手册 3.4）。 */
    const sessionIdAtStart = sessionRef.current
    setInput('')
    setMessages((prev) => [...prev, { role: 'user', content: q }])
    setLoading(true)

    // 先插入一条空的助手消息，用于流式填充
    const assistantIdx = messages.length + 1
    setMessages((prev) => [
      ...prev,
      { role: 'assistant', content: '', streaming: true },
    ])

    const controller = new AbortController()
    abortRef.current = controller
    const requestId = ++requestIdRef.current
    /** 回调守卫：只有「最新请求 + 同一会话 + 未取消」才允许写 UI */
    const isCurrent = () =>
      requestIdRef.current === requestId &&
      sessionRef.current === sessionIdAtStart &&
      !controller.signal.aborted

    let acc = ''
    try {
      await qaApi.stream(
        q,
        sessionRef.current,
        {
          onMeta: (meta) => {
            if (!isCurrent()) return
            setMessages((prev) => {
              const next = [...prev]
              if (next[assistantIdx]) {
                next[assistantIdx] = { ...next[assistantIdx], meta }
              }
              return next
            })
          },
          onDelta: (chunk) => {
            if (!isCurrent()) return
            acc += chunk
            setMessages((prev) => {
              const next = [...prev]
              if (next[assistantIdx]) {
                next[assistantIdx] = { ...next[assistantIdx], content: acc }
              }
              return next
            })
          },
          onDone: (result) => {
            if (!isCurrent()) return
            setMessages((prev) => {
              const next = [...prev]
              if (next[assistantIdx]) {
                next[assistantIdx] = {
                  role: 'assistant',
                  content: result.answer,
                  result,
                  streaming: false,
                }
              }
              return next
            })
          },
          onError: (err) => {
            if (!isCurrent()) return
            setMessages((prev) => {
              const next = [...prev]
              if (next[assistantIdx]) {
                /* 手册要求：出错时**保留已收到的部分答案**，
                   只标错误——直接清空会让用户白等一场。 */
                next[assistantIdx] = {
                  role: 'assistant',
                  content: acc,
                  error: err,
                  stopped: true,
                  streaming: false,
                }
              }
              return next
            })
            message.error(`问答失败：${err}`)
          },
        },
        controller.signal,
      )
    } catch (e) {
      if ((e as Error).name !== 'AbortError' && isCurrent()) {
        message.error('请求失败：' + (e as Error).message)
      }
    } finally {
      if (isCurrent()) {
        setMessages((prev) => {
          const next = [...prev]
          if (next[assistantIdx]?.streaming) {
            next[assistantIdx] = { ...next[assistantIdx], streaming: false }
          }
          return next
        })
        setLoading(false)
      }
      if (abortRef.current === controller) abortRef.current = null
    }
  }

  /**
   * Agent 模式提问 —— 让 LLM 自己查库回答
   *
   * 与 ask（SSE 固定意图路径）的分工：
   *   · 固定意图（CVE 详情、趋势统计…）→ ask，流式快
   *   · 自由问题（哪个最多、字段含义…）→ askAgent，非流式但能现算
   *
   * 走的是后端 auto 分流：后端规则层认得出的固定意图仍走快路径，
   * 其余转Agent，所以前端只需要决定「是否启用 Agent」。
   */
  const askAgent = async (question: string) => {
    if (!question.trim() || loading) return
    const q = question.trim()
    const sessionIdAtStart = sessionRef.current
    setInput('')
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: q },
      { role: 'assistant', content: '', streaming: true },
    ])
    setLoading(true)
    const assistantIdx = messages.length + 1

    /* 走流式：Agent 要 8~20 秒，非流式时界面整段空白，
       用户分不清是在跑还是卡住了。*/
    const controller = new AbortController()
    abortRef.current = controller
    const requestId = ++requestIdRef.current
    const isCurrent = () =>
      requestIdRef.current === requestId &&
      sessionRef.current === sessionIdAtStart &&
      !controller.signal.aborted
    let acc = ''
    try {
      await qaApi.askFreeStream(
        q,
        sessionRef.current,
        {
          onDelta: (chunk) => {
            if (!isCurrent()) return
            acc += chunk
            setMessages((prev) => {
              const next = [...prev]
              if (next[assistantIdx]) {
                next[assistantIdx] = { ...next[assistantIdx], content: acc }
              }
              return next
            })
          },
          // 中间轮的过渡语要撤回 —— 后端边收边推，
          // 但只有等这轮结束才知道它是不是中间轮。
          // 不清零的话「好的，我来查询…」会留在最终答案里。
          onReset: () => {
            if (!isCurrent()) return
            acc = ''
            setMessages((prev) => {
              const next = [...prev]
              if (next[assistantIdx]) {
                next[assistantIdx] = { ...next[assistantIdx], content: '' }
              }
              return next
            })
          },
          onDone: (r) => {
            if (!isCurrent()) return
            setMessages((prev) => {
              const next = [...prev]
              if (next[assistantIdx]) {
                next[assistantIdx] = {
                  role: 'assistant',
                  content: r.answer,
                  result: r,
                  toolCalls: r.tool_calls,
                  streaming: false,
                }
              }
              return next
            })
          },
          onError: (e) => {
            if (isCurrent()) message.error('问答失败：' + e)
          },
        },
        controller.signal,
      )
    } catch (e) {
      if ((e as Error).name !== 'AbortError' && isCurrent()) {
        message.error('问答失败：' + (e as Error).message)
      }
      if (isCurrent()) {
        /* 失败/取消都保留已收到的部分答案（手册 3.4），
           stopped 标记让 UI 显示「已停止」而不是「还在生成」。 */
        setMessages((prev) => {
          const next = [...prev]
          if (next[assistantIdx]?.streaming) {
            next[assistantIdx] = {
              ...next[assistantIdx],
              content: acc || '（未获得回答）',
              stopped: true,
              streaming: false,
            }
          }
          return next
        })
      }
    } finally {
      if (isCurrent()) {
        setMessages((prev) => {
          const next = [...prev]
          if (next[assistantIdx]?.streaming) {
            next[assistantIdx] = { ...next[assistantIdx], streaming: false }
          }
          return next
        })
        setLoading(false)
      }
      if (abortRef.current === controller) abortRef.current = null
    }
  }

  /** 统一入口：按开关决定走 Agent 还是固定意图路径 */
  const askQuestion = async (question: string) => {
    if (agentMode) await askAgent(question)
    else await ask(question)
  }

  /**
   * 停止生成（手册 3.4）
   *
   * 只调 abort() 不够：网络请求被取消后，已排队的回调仍可能执行并写入 UI。
   * 所以先**递增请求身份**让所有在途回调立刻失效，再 abort 网络请求，
   * 最后把当前这条助手消息标记为 stopped——保留已收到的部分答案。
   */
  const stopGeneration = () => {
    if (!loading) return
    requestIdRef.current += 1 // 让所有在途回调失效
    abortRef.current?.abort()
    abortRef.current = null
    setMessages((prev) => {
      const next = [...prev]
      const last = next.length - 1
      if (last >= 0 && next[last]?.role === 'assistant' && next[last].streaming) {
        next[last] = {
          ...next[last],
          content: next[last].content || '（已停止，未获得回答）',
          stopped: true,
          streaming: false,
        }
      }
      return next
    })
    setLoading(false)
  }

  const clearAll = () => {
    requestIdRef.current += 1
    abortRef.current?.abort()
    abortRef.current = null
    setMessages([])
    // 清空当前会话要连历史一起清，并另开一个新会话 id，
    // 否则会复用已被后端清空的 session（上下文丢失、无法多轮）。
    qaHistory.remove(sessionId)
    const fresh = qaHistory.create()
    setSessionId(fresh)
    sessionRef.current = fresh
    refreshSessions()
    void qaApi.clearSession(fresh)
    message.success('当前对话已清空')
  }

  const onFollowUp = (q: string) => {
    setInput(q)
    void askQuestion(q)
  }

  return (
    /* 两栏布局：左历史 + 右对话。
       用 flex 而非 AntD Row/Col —— 历史栏需要**独立滚动且高度受控**，
       Row/Col 的栅格在「两栏各自滚动」这件事上反而麻烦。 */
    <div className="sentinel-qa-shell">
      {/* ---- 左：对话历史栏 ---- */}
      {historyOpen && (
        <aside className="sentinel-qa-history">
          <Button
            type="primary"
            icon={<PlusOutlined />}
            block
            onClick={newSession}
            className="sentinel-qa-new-btn"
          >
            开启新对话
          </Button>

          <div className="sentinel-qa-history-list">
            {grouped.length === 0 && (
              <div className="sentinel-qa-history-empty">暂无历史对话</div>
            )}
            {grouped.map((g) => (
              <div key={g.label}>
                <div className="sentinel-qa-history-group">{g.label}</div>
                {g.items.map((s) => (
                  <div
                    key={s.id}
                    className={`sentinel-qa-history-item${
                      s.id === sessionId ? ' is-active' : ''
                    }`}
                    onClick={() => switchSession(s.id)}
                    title={s.title}
                  >
                    <span className="sentinel-qa-history-title">{s.title}</span>
                    <Tooltip title="删除该对话">
                      <span
                        className="sentinel-qa-history-del"
                        onClick={(e) => {
                          // 冒泡到删除按钮，别触发切换
                          e.stopPropagation
                          removeSession(s.id)
                        }}
                      >
                        <DeleteOutlined />
                      </span>
                    </Tooltip>
                    <span className="sentinel-qa-history-time">
                      {timeLabel(s.updatedAt)}
                    </span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </aside>
      )}

      {/* ---- 右：对话区 ---- */}
      <div className="sentinel-qa-main">
      {/* ---- 顶部标题栏：折叠按钮 + 页面标题 + 副标题 + 新对话（手册布局规范） ---- */}
      <div className="sentinel-qa-topbar">
        <div className="sentinel-qa-topbar-left">
          <Tooltip title={historyOpen ? '收起历史对话' : '展开历史对话'}>
            <button
              type="button"
              className="sentinel-qa-history-toggle"
              onClick={() => setHistoryOpen((v) => !v)}
              aria-label={historyOpen ? '收起历史对话' : '展开历史对话'}
              aria-expanded={historyOpen}
            >
              {historyOpen ? <MenuFoldOutlined /> : <MenuUnfoldOutlined />}
            </button>
          </Tooltip>
          <div className="sentinel-qa-titles">
            <h1>情报问答</h1>
          </div>
        </div>
      </div>
      {/* 纵向滚动容器。横向滚动条已在 CSS 里隐藏 ——
          这层只需纵向滚动，但推荐问题卡里的长标题会把它
          撑出横向溢出，在底部出现一条无用的拖动栏。 */}
      <div className="sentinel-qa-scroll">
        {messages.length === 0 ? (
          <div style={{ paddingTop: 32 }}>
            <div style={{ maxWidth: 720, margin: '0 auto' }}>
              {/* 空态图标：AntD 的 Empty 图形是灰色箱子轮廓，
                  在暗色背景上辨识度低且与AI 助手语义不符。
                  改用自绘的青色机器人 logo。 */}
              <div className="sentinel-qa-logo">
                <QaRobotIcon size={64} />
              </div>
              <h2 className="sentinel-qa-welcome-title">
                智能问答
              </h2>
              {/* 说明文字：原用 Text type="secondary"（暗色下灰度过低，
                  截图里几乎看不清）。改用 --text-2 并提亮字号。 */}
              <div className="sentinel-qa-welcome-sub">
                基于知识库中
                <b style={{ color: 'var(--cyan)' }}> {kb?.vulnerabilities ?? '—'} </b>
                条真实情报支持多轮对话与多跳关联推理，
                所有回答均附带可点击的引用溯源。
              </div>

                  {/* 知识底座构成已移入副标题一句话（情报总数），
            这里不再重复三条色带——小视口下它会把起步问题卡顶出可视区。 */}
                </div>

              {/* 推荐问题：覆盖事实查询/关联推理/趋势/处置/政策五类意图，
                让操作员知道这个系统不只能查单条漏洞 */}
            <Row gutter={[10, 10]} justify="center" style={{ marginTop: 14 }}>
              {[
                { q: 'CVE-2021-43831 是什么漏洞？', tag: '事实查询' },
                { q: 'mlflow 有哪些已知漏洞？', tag: '关联推理' },
                { q: '哪些 AI 组件的漏洞已经有可用 POC？', tag: '关联推理' },
                { q: '当前 AI 安全的整体态势如何？', tag: '趋势分析' },
                { q: '最近有哪些高危 AI 漏洞？', tag: '事实查询' },
                { q: '欧盟 AI 法案对大模型有哪些合规要求？', tag: '政策底座' },
              ].map(({ q, tag }, i) => (
                <Col key={q} xs={24} sm={12} md={8} lg={6}>
                  <Card
                    size="small"
                    hoverable
                    // 卡片扫光 + 错峰入场 + 悬停浮起
                    className="sentinel-qa-card stagger-in"
                    style={{
                      cursor: 'pointer',
                      height: '100%',
                      animationDelay: `${0.06 * i}s`,
                    }}
                    onClick={() => void ask(q)}
                  >
                    <Space direction="vertical" size={4} style={{ width: '100%' }}>
                      {/* 文字色随主题：黑夜=白（透明黑卡对比度），白天=近黑
                          （白卡上白字完全不可见——用户截图反馈） */}
                      <Text style={{ fontSize: 12.5, color: isDark ? '#ffffff' : '#12211a' }}>
                        {q}
                      </Text>
                      <Tag
                        className="tag-info"
                        color="blue"
                        bordered={false}
                        style={{ fontSize: 11, margin: 0, lineHeight: '16px' }}
                      >
                        {tag}
                      </Tag>
                    </Space>
                  </Card>
                </Col>
              ))}
            </Row>
          </div>
        ) : (
          <div>
            {messages.map((m, i) =>
              m.role === 'user' ? (
                /* ---------- 用户提问：靠右的气泡卡 ---------- */
                <div key={i} className="sentinel-qa-msg-row is-user">
                  <div className="sentinel-qa-bubble is-user">{m.content}</div>
                </div>
              ) : (
                /* ---------- AI 回答：靠左的同款卡片 ----------
                   提问在右、回答在左，视觉上成对；
                   两者共用同一套卡片语言（圆角/边框/底色），
                   靠**位置 + 强调色**区分主次，而不是一个带框一个不带。 */
                <div key={i} className="sentinel-qa-msg-row is-assistant">
                  {m.error ? (
                    <Alert type="error" showIcon message="问答失败" description={m.error} />
                  ) : (
                    /* 消息级边界：单条渲染崩了不应拖垮整页 */
                    <div className="sentinel-qa-bubble is-assistant">
                      <MessageBoundary>
                        <AnswerCard
                          msg={m}
                          onFollowUp={onFollowUp}
                          streaming={m.streaming ?? false}
                        />
                      </MessageBoundary>
                    </div>
                  )}
                </div>
              ),
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {/* ---- 输入区 ---- */}
      <Card size="small" style={{ marginTop: 10, flexShrink: 0 }}>
        <Space.Compact style={{ width: '100%' }}>
          <Input.TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPressEnter={(e) => {
              if (!e.shiftKey) {
                e.preventDefault()
                void askQuestion(input)
              }
            }}
            placeholder="输入安全情报问题，例如：有哪些需要优先处理的高危漏洞？"
            maxLength={2000}
            autoSize={{ minRows: 2, maxRows: 6 }}
            style={{ fontSize: 13.5 }}
            disabled={loading}
          />
          {loading ? (
            <Button
              icon={<ClearOutlined />}
              onClick={stopGeneration}
              danger
              title="停止生成（保留已收到的内容）"
            >
              停止
            </Button>
          ) : (
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={() => void askQuestion(input)}
              loading={loading}
            >
              发送
            </Button>
          )}
          <Button icon={<ClearOutlined />} onClick={clearAll} disabled={loading}>
            清空
          </Button>
        </Space.Compact>

        {/* ---- 工具行：模式开关 + 字数 + 复制 + 快捷键（对齐信息密集型控制台习惯） ---- */}
        <div className="sentinel-qa-toolbar">
          <span className="sentinel-qa-toolbar-switch">
            <Switch
              size="small"
              checked={agentMode}
              onChange={setAgentMode}
              disabled={loading}
            />
            <span className={agentMode ? 'is-on' : ''}>Agent 自主查库</span>
          </span>

          <Tooltip title="复制当前输入">
            <Button
              type="text"
              size="small"
              icon={<CopyOutlined />}
              disabled={!input || loading}
              onClick={() => {
                void navigator.clipboard
                  ?.writeText(input)
                  .then(() => message.success('已复制到剪贴板'))
                  .catch(() => message.error('复制失败，请手动选择文本'))
              }}
              aria-label="复制输入内容"
            />
          </Tooltip>

          <span className="sentinel-qa-counter">
            {input.length}/2000
          </span>
        </div>

        <div className="sentinel-qa-hint">
          <span>Enter 发送 · Shift + Enter 换行</span>
          <span>AI 回答仅供辅助研判，请核验后引用来源</span>
        </div>
      </Card>
      </div>
    </div>
  )
}

/** 答案卡片：答案 + 引用 + 推理链 + 追问 */
function AnswerCard({
  msg,
  onFollowUp,
  streaming,
}: {
  msg: Message
  onFollowUp: (q: string) => void
  streaming: boolean
}) {
  const r = msg.result
  const meta = msg.meta
  /*
   * ⚠️ 这里的每个数组都必须兜底成 []。
   *
   * 后端返回的结构**随意图/路径变化**：不是每种情况都带
   * citations / reasoning / graph_paths / papers。
   * 任何一处写成 `x.length` 或 `x.map` 而 x 可能为 undefined，
   * 都会抛「Cannot read properties of undefined」→ **整块白屏**。
   *
   * 这个坑踩过两次（tool_calls.args、papers），
   * 所以统一在这里收口，渲染层不再需要各自判断。
   */
  const citations = r?.citations ?? meta?.citations ?? []
  const reasoning = r?.reasoning ?? meta?.reasoning ?? []
  const graphPaths = r?.graph_paths ?? meta?.graph_paths ?? []
  const papers = r?.papers ?? []
  const recommendedQuestions = r?.recommended_questions ?? []
  const intentLabel = r?.intent_label ?? meta?.intent_label
  const intent = r?.intent
  const toolCalls = msg.toolCalls ?? []

  return (
    <div>
      {/* ---------- Agent 工具轨迹 ----------
          让「AI 到底查了什么」可见 —— 这是 Agent 模式与
          固定模板模式最直观的区别，也是可信度来源。
          折叠展示，避免长轨迹把答案挤下去。*/}
      {toolCalls.length > 0 && (
        <details className="sentinel-qa-tools">
          <summary>
            <ThunderboltOutlined /> AI 查询了知识库（{toolCalls.length} 次工具调用）
          </summary>
          <div className="sentinel-qa-tools-list">
            {toolCalls.map((t, i) => {
              /**
               * args 可能是 undefined / null。
               *
               * ---------- 为什么会这样 ----------
               * 后端 `trace` 里是 `"args": call["args"]` 直接透传
               * LLM 返回的 arguments。部分网关在参数为空时
               * 会返回 `null` 而不是 `{}`。
               * 之前这里写 `Object.keys(t.args).length`，
               * 遇到 null 就抛
               * 「TypeError: Cannot read properties of undefined」，
               * **整个回答区白屏** —— 用户看到的就是「AI 没输出」。
               *
               * 所以这里统一兜底成{}，渲染层不必再担心。
               */
              const args = (t as { args?: Record<string, unknown> | null }).args ?? {}
              const argKeys = Object.keys(args)
              return (
                <div key={i} className="sentinel-qa-tool">
                  <div className="sentinel-qa-tool-head">
                    <Tag color="cyan" style={{ fontSize: 10, margin: 0 }}>
                      {t.tool}
                    </Tag>
                    <span className="sentinel-qa-tool-time">{t.elapsed_ms}ms</span>
                  </div>
                  {argKeys.length > 0 && (
                    <pre className="sentinel-qa-tool-args">
                      {JSON.stringify(args, null, 2)}
                    </pre>
                  )}
                  <div className="sentinel-qa-tool-result">{t.result_preview}</div>
                </div>
              )
            })}
          </div>
        </details>
      )}

      {/* 状态条 */}
      {(intentLabel || r) && (
        <Space size={6} wrap style={{ marginBottom: 8 }}>
          {intentLabel && (
            <Tag color={INTENT_COLOR[intent ?? ''] ?? 'default'} style={{ fontSize: 11 }}>
              {intentLabel}
            </Tag>
          )}
          {r && (
            <Tooltip title="意图判定置信度">
              <Tag style={{ fontSize: 11 }}>置信 {(r.confidence * 100).toFixed(0)}%</Tag>
            </Tooltip>
          )}
          {r && (
            <Tag style={{ fontSize: 11 }}>
              召回 {r.retrieval_hits} 条 · {r.elapsed_ms}ms
            </Tag>
          )}
          {r?.degraded && (
            <Tooltip title="LLM 服务限流，已自动切换为规则模式作答以保证响应速度">
              <Tag color="warning" style={{ fontSize: 11 }}>
                规则降级模式
              </Tag>
            </Tooltip>
          )}
        </Space>
      )}

      {/* 答案正文 */}
      <div
        style={{
          background: 'rgba(255, 255, 255, 0.028)',
          border: '1px solid var(--border)',
          borderRadius: 13,
          padding: '13px 15px',
          fontSize: 13.5,
          lineHeight: 1.85,
          minHeight: streaming ? 60 : undefined,
        }}
      >
        {msg.content ? (
          /* 用 Markdown 渲染而非 pre-wrap ——
             LLM 输出的是标准 Markdown（## 标题、**加粗**、表格、
             代码块），pre-wrap 会把这些标记原样显示出来，
             看起来就是一堆多余的符号。 */
          <div className="sentinel-qa-md">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                // 表格横向可滚动，避免列多时被挤破
                table: ({ children }: { children?: ReactNode }) => (
                  <div className="sentinel-qa-md-tablewrap">
                    <table>{children}</table>
                  </div>
                ),
                a: ({ children, href }: { children?: ReactNode; href?: string }) => (
                  <a href={href} target="_blank" rel="noreferrer">
                    {children}
                  </a>
                ),
              }}
            >
              {msg.content}
            </ReactMarkdown>
          </div>
        ) : streaming ? (
          <Space>
            <Spin size="small" />
            <Text type="secondary" style={{ fontSize: 12.5 }}>
              正在检索与分析…
            </Text>
          </Space>
        ) : null}
        {streaming && msg.content && (
          <Text style={{ color: 'var(--cyan)', marginLeft: 2 }}>
            ▋
          </Text>
        )}
      </div>

      {/* 推理链 */}
      {reasoning.length > 0 && !streaming && (
        <Card
          size="small"
          title={
            <Space size={6}>
              <NodeIndexOutlined style={{ color: '#8b5cf6' }} />
              <span style={{ fontSize: 13 }}>推理链</span>
            </Space>
          }
          style={{ marginTop: 8 }}
          styles={{ body: { padding: '10px 12px' } }}
        >
          {reasoning.map((s, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                gap: 8,
                padding: '5px 0',
                fontSize: 12.5,
                borderBottom:
                  i < reasoning.length - 1 ? '1px solid rgba(0,0,0,0.05)' : 'none',
              }}
            >
              <span style={{ color: '#8b5cf6', flexShrink: 0 }}>
                {i + 1}.
              </span>
              <div style={{ flex: 1 }}>
                <Text strong style={{ fontSize: 12.5 }}>
                  {s.step}
                </Text>
                <div style={{ color: 'var(--inline-text-1b)' }}>{s.detail}</div>
                {s.evidence && (
                  <div style={{ color: 'var(--muted)', fontSize: 11.5, marginTop: 2 }}>
                    依据：{s.evidence}
                  </div>
                )}
              </div>
            </div>
          ))}
        </Card>
      )}

      {/* 图谱路径 */}
      {graphPaths.length > 0 && !streaming && (
        <Card
          size="small"
          title={
            <Space size={6}>
              <NodeIndexOutlined style={{ color: '#10b981' }} />
              <span style={{ fontSize: 13 }}>知识图谱关联路径</span>
            </Space>
          }
          style={{ marginTop: 8 }}
          styles={{ body: { padding: '10px 12px' } }}
        >
          {graphPaths.slice(0, 3).map((p, i) => (
            <div key={i} style={{ fontSize: 12, marginBottom: 6 }}>
              {/* 同理：graph_paths 是后端拼的结构，
                  nodes 缺失时不能直接 .map —— 否则又是整块白屏 */}
              {(p.nodes ?? []).map((n, j) => (
                <span key={j}>
                  {j > 0 && (
                    <Tag
                      color="green"
                      style={{ fontSize: 10, margin: '0 3px' }}
                    >
                      {p.relations[j - 1]?.type}
                    </Tag>
                  )}
                  <span style={{ color: '#065f46', fontWeight: 500 }}>
                    {n.name || n.id}
                  </span>
                </span>
              ))}
            </div>
          ))}
        </Card>
      )}

      {/* 引用溯源 */}
      {citations.length > 0 && !streaming && (
        <Card
          size="small"
          title={
            <Space size={6}>
              <LinkOutlined style={{ color: 'var(--cyan)' }} />
              <span style={{ fontSize: 13 }}>引用溯源</span>
              <Tag style={{ fontSize: 10 }}>{citations.length} 条</Tag>
            </Space>
          }
          style={{ marginTop: 8 }}
          styles={{ body: { padding: '8px 12px' } }}
        >
          {citations.map((c) => (
            <div
              key={c.cve_id}
              style={{
                padding: '6px 0',
                borderBottom: '1px solid rgba(0,0,0,0.05)',
              }}
            >
              <Space size={6} wrap>
                <a
                  href={c.url ?? '#'}
                  target="_blank"
                  rel="noreferrer"
                  style={{ fontSize: 12, fontFamily: 'monospace' }}
                >
                  {c.cve_id}
                </a>
                {c.evidence_type === 'graph' && (
                  <Tag color="green" style={{ fontSize: 10 }}>
                    图谱推理
                  </Tag>
                )}
                {c.source_name && (
                  <Tag style={{ fontSize: 10 }}>{c.source_name}</Tag>
                )}
                {c.severity_text && (
                  <Tag color="red" style={{ fontSize: 10 }}>
                    {c.severity_text}
                  </Tag>
                )}
              </Space>
              <div
                style={{
                  fontSize: 12,
                  color: 'var(--inline-text-1b)',
                  marginTop: 2,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {c.title}
              </div>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 1 }}>
                {c.origin_source}
                {c.published_at && ` · 发布 ${dayjs(c.published_at).format('YYYY-MM-DD')}`}
                {c.ingest_at && ` · 采集 ${dayjs(c.ingest_at).format('MM-DD HH:mm')}`}
                {c.latency_seconds != null &&
                  ` · 延迟 ${formatLatency(c.latency_seconds)}`}
              </div>
            </div>
          ))}
        </Card>
      )}

      {/* 关联论文 */}
      {/*
        关联学术论文。
         `papers` 是可选字段 —— 不是每种意图都会带它
         （寒暄/ 模板降级 / 图谱多跳路径都不返回）。
         之前写 `r.papers.length`，缺字段时直接抛
         「Cannot read properties of undefined (reading 'length')」，
         整个回答区白屏。必须兜底成 []。
       */}
      {papers.length > 0 && (
        <Card
          size="small"
          title={
            <Space size={6}>
              <FileTextOutlined style={{ color: '#8b5cf6' }} />
              <span style={{ fontSize: 13 }}>关联学术论文</span>
            </Space>
          }
          style={{ marginTop: 8 }}
          styles={{ body: { padding: '8px 12px' } }}
        >
          {papers.map((p) => (
            <div key={p.arxiv_id} style={{ marginBottom: 6, fontSize: 12 }}>
              <a href={p.pdf_url ?? '#'} target="_blank" rel="noreferrer">
                {/* 标题也可能为 null —— 同一类问题，一并兜住 */}
                {p.title?.length > 70 ? p.title.slice(0, 70) + '…' : p.title}
              </a>
              {p.ai_topics.slice(0, 2).map((t) => (
                <Tag key={t} style={{ fontSize: 10, marginLeft: 4 }}>
                  {t}
                </Tag>
              ))}
            </div>
          ))}
        </Card>
      )}

      {/* 推荐追问 */}
      {recommendedQuestions.length > 0 && !streaming && (
        <div style={{ marginTop: 10 }}>
          <Space size={6} wrap>
            <ThunderboltOutlined style={{ color: '#f59e0b' }} />
            <Text type="secondary" style={{ fontSize: 12 }}>
              推荐追问：
            </Text>
            {recommendedQuestions.map((q) => (
              <Tag
                key={q}
                color="warning"
                style={{ cursor: 'pointer', fontSize: 11.5 }}
                onClick={() => onFollowUp(q)}
              >
                {q}
              </Tag>
            ))}
          </Space>
        </div>
      )}
    </div>
  )
}

/** 延迟格式化 */
function formatLatency(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)} 小时`
  return `${(seconds / 86400).toFixed(1)} 天`
}


/**
 * 消息级错误边界
 * ------------------------------------------------------------
 * 为什么要这个：
 * 回答区里有大量 `arr.map(...)` / `arr.length` 的渲染。
 * 只要后端某次返回的结构缺一个字段（比如工具调用的 args 为 null），
 * 整个页面就会抛错变**白屏** —— 用户看到的现象是「AI 没有输出答案」，
 * 根本看不出是渲染崩了。
 *
 * 加了边界后：坏的那条消息显示一行错误提示，
 * 其余历史消息、输入框、侧栏**全部照常可用**。
 *
 * ---------- 为什么不做全页 ErrorBoundary ----------
 * 全页边界会把整个问答页替换成一句「出错了」，
 * 用户连重试都要重新进页面。这里刻意只兜消息这一层。
 */
class MessageBoundary extends Component<
  { children: ReactNode },
  { failed: boolean; msg: string }
> {
  constructor(props: { children: ReactNode }) {
    super(props)
    this.state = { failed: false, msg: '' }
  }

  static getDerivedStateFromError(err: unknown): { failed: boolean; msg: string } {
    return {
      failed: true,
      msg: err instanceof Error ? err.message : String(err),
    }
  }

  componentDidCatch(err: Error, info: ErrorInfo) {
    // 留日志便于排查；不弹 toast —— 此刻界面可能已在重建中
    console.error('[QAPage] 消息渲染失败:', err.message, info.componentStack)
  }

  render() {
    if (this.state.failed) {
      return (
        <Alert
          type="warning"
          showIcon
          message="这条回答渲染失败"
          description={
            <span style={{ fontSize: 12.5 }}>
              原始错误：{this.state.msg.slice(0, 160)}
              <br />
              这条消息的内容结构异常（通常是接口返回缺字段），
              其他功能不受影响，可继续提问。
            </span>
          }
        />
      )
    }
    return this.props.children
  }
}
