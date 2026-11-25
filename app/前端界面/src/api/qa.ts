/**
 * 问答接口客户端
 *
 * 除普通请求外，另提供 SSE 流式接口——
 * 赛题要求流式输出 + 首字延迟 <1.5s。
 */

import { http } from './client'
import { consumeStream } from './qaStream'

/** 引用溯源 */
export interface Citation {
  cve_id: string
  title: string
  url: string | null
  source_name: string
  origin_source: string
  published_at: string | null
  ingest_at: string | null
  latency_seconds: number | null
  evidence_type: 'graph' | 'retrieval' | 'inference'
  /** 严重程度（部分接口返回） */
  severity_text?: string | null
}

/** 推理链的一步 */
export interface ReasoningStep {
  step: string
  detail: string
  evidence: string
  data: Record<string, unknown> | null
}

/** 图谱路径 */
export interface GraphPath {
  nodes: Array<{ id: string; labels: string[]; name: string }>
  relations: Array<{ type: string }>
  hops: number
  description: string
}

/** 问答结果 */
export interface QAResult {
  query: string
  answer: string
  intent: string
  intent_label: string
  confidence: number
  citations: Citation[]
  reasoning: ReasoningStep[]
  graph_paths: GraphPath[]
  recommended_questions: string[]
  papers: Array<{
    arxiv_id: string
    title: string
    authors: string[]
    ai_topics: string[]
    pdf_url: string | null
  }>
  elapsed_ms: number
  degraded: boolean
  retrieval_hits: number
  session_id: string
}

/** 检索结果（调试用） */
export interface SearchResult {
  query: string
  hits: Array<{
    cve_id: string
    title: string
    score: number
    similarity: number | null
    severity: string | null
    cvss_score: number | null
    ai_category: string | null
    match_reason: string
    url: string | null
  }>
  elapsed_ms: number
  vector_used: boolean
  keyword_used: boolean
  total_candidates: number
}

/** 图谱统计 */
export interface GraphStats {
  available: boolean
  nodes?: number
  relations?: number
  total_nodes?: number
  error?: string
}

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

/** Agent 工具调用轨迹（前端用于展示「AI 查了什么」） */
export interface ToolCall {
  turn: number
  tool: string
  args: Record<string, unknown>
  result_preview: string
  elapsed_ms: number
}

/** 自由问答结果（在 QAResult 基础上多工具轨迹与模式标识） */
export interface FreeQAResult extends QAResult {
  /** classic=固定意图路径 / agent=Agent 自主查库 */
  mode?: 'classic' | 'agent'
  tool_calls?: ToolCall[]
  /** Agent 判定问题与知识库无关 */
  refused?: boolean
}

export const qaApi = {
  /** 普通问答 */
  ask: (question: string, sessionId = 'default') =>
    http
      .post<QAResult>('/api/qa/ask', null, {
        params: { question, session_id: sessionId, use_graph: true },
      })
      .then((r) => r.data),

  /**
   * 自由问答（Agent 模式）
   *
   * 与 `ask` 的区别：`ask` 只能答固定意图（正则匹配 + 模板），
   * `askFree` 让 LLM 拿着数据地图自己查库，能回答
   * 「哪个最多」「有哪些属性含义」这类需要现算的问题。
   *
   * mode=auto 时后端会智能分流：固定意图走快路径，其余走 Agent。
   */
  askFree: (question: string, sessionId = 'default', mode: 'auto' | 'agent' = 'auto') =>
    http
      .post<FreeQAResult>('/api/qa/free', null, {
        params: { question, session_id: sessionId, mode },
        timeout: 120_000,
      })
      .then((r) => r.data),

  /**
   * SSE 流式问答
   *
   * 事件序列：meta（检索骨架）→ delta（逐段答案）→ done（完整数据）
   * 用 fetch + ReadableStream 而非 EventSource——因为需要 POST 请求体。
   */
  stream: async (
    question: string,
    sessionId: string,
    handlers: {
      onMeta?: (meta: Partial<QAResult>) => void
      onDelta?: (chunk: string) => void
      onDone?: (result: QAResult) => void
      onError?: (err: string) => void
    },
    signal?: AbortSignal,
  ): Promise<void> => {
    const url = `${API_BASE}/api/qa/stream?question=${encodeURIComponent(
      question,
    )}&session_id=${encodeURIComponent(sessionId)}`

    const resp = await fetch(url, { method: 'POST', signal })
    if (!resp.ok || !resp.body) {
      handlers.onError?.(`请求失败 HTTP ${resp.status}`)
      return
    }

    /* 解析交给通用解析器（手册 3.3）——处理分包 / CRLF / 跨块 UTF-8 /
       多行 data / 心跳注释 / 2MB 上限 / 提前断流。
       错误不在这里吞掉，由调用方决定如何展示（问答页要保留已收到的部分）。 */
    try {
      await consumeStream<QAResult>(resp.body, handlers, signal)
    } catch (err) {
      if ((err as Error)?.name === 'AbortError') return
      handlers.onError?.((err as Error).message || '流式响应异常')
    }
  },

  /**
   * Agent 模式 SSE 流式问答
   *
   * 事件：`delta`（增量）/ `reset`（撤回）/ `done`（完成）。
   * 与 `stream`（classic）共用同一套解析骨架，只多一个 reset 分支。
   *
   * 为什么 Agent 也需要流式：
   * Agent 要 1~2 次 LLM 往返，体感 8~20 秒；
   * 非流式时界面整段空白，用户分不清「在跑」还是「卡住了」。
   *
   * ⚠️ reset 的含义：
   * 后端边收边推（真流式），但 OpenAI 协议里 tool_calls 与 content
   * 属于同一轮增量，只有等这轮结束才知道是不是中间轮。
   * 所以中间轮的过渡语（「好的，我来查询…」）会先推出来，
   * 再用 reset 通知清空 —— 收到 reset 必须把已累积文本归零。
   */
  askFreeStream: async (
    question: string,
    sessionId: string,
    handlers: {
      onDelta?: (chunk: string) => void
      onReset?: () => void
      onDone?: (result: FreeQAResult) => void
      onError?: (err: string) => void
    },
    signal?: AbortSignal,
  ): Promise<void> => {
    const url = `${API_BASE}/api/qa/free/stream?question=${encodeURIComponent(
      question,
    )}&session_id=${encodeURIComponent(sessionId)}`
    const resp = await fetch(url, { method: 'POST', signal })
    if (!resp.ok || !resp.body) {
      handlers.onError?.(`请求失败 HTTP ${resp.status}`)
      return
    }
    /* 与普通问答共用同一个解析器——两份内联解析器曾在 CRLF 与
       提前断流上表现不一致（表现为“Agent 偶尔答到一半就停”）。 */
    try {
      await consumeStream<FreeQAResult>(resp.body, handlers, signal)
    } catch (err) {
      if ((err as Error)?.name === 'AbortError') return
      handlers.onError?.((err as Error).message || '流式响应异常')
    }
  },

  /** 仅检索（调试） */
  search: (q: string, topK = 5) =>
    http.get<SearchResult>('/api/qa/search', { params: { q, top_k: topK } }).then((r) => r.data),

  /** 图谱统计 */
  graphStats: () => http.get<GraphStats>('/api/graph/stats').then((r) => r.data),
  /** 重建图谱 */
  rebuildGraph: () => http.post<Record<string, number>>('/api/graph/rebuild').then((r) => r.data),
  /** 清空会话 */
  clearSession: (sessionId: string) =>
    http.delete(`/api/qa/session/${sessionId}`).then((r) => r.data),
}
