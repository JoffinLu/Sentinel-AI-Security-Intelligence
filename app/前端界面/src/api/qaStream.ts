/**
 * 通用 SSE 增量解析器（优化复用手册 3.3）
 *
 * 为什么需要它：
 * 流式接口不能假设「一个网络分块 = 一个事件」。实际会遇到的坑——
 *   ① 分包：一个 SSE 事件被拆进多个 TCP 分块（最常见）
 *   ② CRLF：代理把 \n 换成 \r\n，按 '\n\n' 切分会切错
 *   ③ 跨块 UTF-8：一个中文字符被拆在两个分块里
 *   ④ 多行 data:：一个大 JSON 被拆成多行 data:
 *   ⑤ 注释心跳：`: keep-alive` 每 10 秒一次，不能当业务事件
 *   ⑥ 提前断流：连接断了但没收到 done，用户会以为"回答完了"
 *
 * 本实现对以上六种情况都有明确处理，且带 2MB 上限保护。
 */

export interface StreamHandlers<T> {
  /** 会话 / 检索元信息（答案前的骨架） */
  onMeta?: (meta: Partial<T>) => void
  /** 增量文本 */
  onDelta?: (text: string) => void
  /** 撤回已推送内容（Agent 中间轮过渡语） */
  onReset?: () => void
  /** 完整结果（用服务端最终结果校准前端） */
  onDone?: (result: T) => void
}

const KNOWN_EVENTS = ['meta', 'delta', 'reset', 'done', 'error']

/** 单次流式响应的最大字节数（2MB），超过即视为异常 */
const MAX_STREAM_BYTES = 2_000_000

/**
 * 消费一个 SSE 响应流。
 *
 * @param body   响应体（fetch 的 resp.body）
 * @param handlers 事件回调
 * @param signal 取消信号（AbortController.signal）
 * @throws 连接中断 / 格式异常 / error 事件时抛出，调用方负责展示
 */
export async function consumeStream<T extends { answer: string }>(
  body: ReadableStream<Uint8Array>,
  handlers: StreamHandlers<T>,
  signal?: AbortSignal,
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let event = 'message'
  let data: string[] = []
  let size = 0
  let completed = false

  const abort = () => {
    void reader.cancel().catch(() => undefined)
  }

  const checkAbort = () => {
    if (signal?.aborted) {
      throw new DOMException('请求已取消', 'AbortError')
    }
  }

  /** 一个事件块收齐了，解析并分发 */
  const dispatch = () => {
    if (!data.length) {
      event = 'message'
      return
    }
    const current = event
    const raw = data.join('\n')
    event = 'message'
    data = []
    size = 0

    // 未知事件直接忽略（向前兼容后端新增事件类型）
    if (!KNOWN_EVENTS.includes(current)) return

    let payload: Record<string, unknown>
    try {
      payload = JSON.parse(raw)
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new Error('not an object')
      }
    } catch {
      // 手册要求：格式异常必须显式失败，不能静默丢弃——
      // 静默丢弃会让"回答少了一截"表现为 mysteriously 结束
      throw new Error('流式数据格式异常')
    }

    if (current === 'error') {
      throw new Error(String(payload.error || '服务暂不可用'))
    }
    if (current === 'meta') handlers.onMeta?.(payload as Partial<T>)
    if (current === 'reset') handlers.onReset?.()
    if (current === 'delta') {
      if (typeof payload.delta !== 'string') throw new Error('回答片段格式异常')
      handlers.onDelta?.(payload.delta)
    }
    if (current === 'done') {
      if (typeof payload.answer !== 'string') throw new Error('缺少完整回答')
      completed = true
      handlers.onDone?.(payload as unknown as T)
    }
  }

  /** 处理一行（已去掉行尾符） */
  const acceptLine = (line: string) => {
    if (!line) {
      dispatch()
      return
    }
    // 注释行（`: keep-alive`）是心跳，直接跳过
    if (line.startsWith(':')) return

    const colon = line.indexOf(':')
    const field = colon < 0 ? line : line.slice(0, colon)
    // SSE 规定：冒号后紧跟的单个空格要去掉
    const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '')
    if (field === 'event') event = value
    if (field === 'data') {
      size += value.length
      if (size > MAX_STREAM_BYTES) throw new Error('流式数据超过上限')
      data.push(value)
    }
  }

  /** 从 buffer 中切出所有完整行；eof 时冲刷剩余 */
  const drain = (eof = false) => {
    while (!completed) {
      // 同时支持 \r\n / \r / \n 三种换行（CRLF 代理问题）
      const match = /\r\n|\r|\n/.exec(buffer)
      if (!match) break
      // 结尾的孤立 \r 可能是 CRLF 被切开的前半，等下一块
      if (!eof && match[0] === '\r' && match.index === buffer.length - 1) break
      acceptLine(buffer.slice(0, match.index))
      buffer = buffer.slice(match.index + match[0].length)
    }
    if (buffer.length > MAX_STREAM_BYTES) throw new Error('流式数据超过上限')
    if (eof && !completed) {
      // 连接结束时把残块冲出去（最后一个事件可能没有结尾空行）
      if (buffer) acceptLine(buffer)
      buffer = ''
      dispatch()
    }
  }

  signal?.addEventListener('abort', abort, { once: true })
  try {
    checkAbort()
    while (!completed) {
      const chunk = await reader.read()
      checkAbort()
      // `stream: true` 让 TextDecoder 保留跨块的多字节字符
      buffer += chunk.done
        ? decoder.decode()
        : decoder.decode(chunk.value, { stream: true })
      drain(chunk.done)
      if (chunk.done) break
    }
    if (!completed) {
      throw new Error('连接已中断，已保留收到的内容')
    }
  } finally {
    signal?.removeEventListener('abort', abort)
    await reader.cancel().catch(() => undefined)
    reader.releaseLock()
  }
}