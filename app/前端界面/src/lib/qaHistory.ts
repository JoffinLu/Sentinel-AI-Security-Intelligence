/**
 * 问答对话历史（前端本地持久化）
 * ------------------------------------------------------------
 * ---------- 为什么放在前端而不是后端 ----------
 *
 * 后端只有 `DELETE /api/qa/session/{id}`（清空上下文），
 * **没有查询历史会话的接口**，问答模块也不落库消息。
 * 为此新建后端表+ 接口属于改动数据契约；
 * 而对话历史是**单机操作员的使用痕迹**，不上传也合理。
 *
 * 因此：localStorage 持久化。
 * 收益是**刷新不丢**——答辩时手滑F5 历史仍在。
 *
 * ---------- 存储结构 ----------
 * 单key 存全量会话数组，每项：
 *   { id, title, createdAt, updatedAt, messages: Message[] }
 *
 * `title` 取**第一条用户提问的前 18 字**——
 * 聊天列表里用提问当标题，比"新对话"有用得多。
 */

/** 与 QAPage 的 Message 结构保持一致（不含流式中间态） */
export interface StoredMessage {
  role: 'user' | 'assistant'
  content: string
  /** 完整结果：手册 3.6 要求**持久化证据链**——
   *  只存答案文字的话，刷新后引用、推理链与工具轨迹全部丢失，
   *  「这条结论的依据是什么」就再也答不上来了。 */
  result?: {
    answer: string
    citations?: Array<{ title: string; url?: string }>
    tool_calls?: Array<{ name: string; result_preview?: string }>
    backend?: string
  }
  /** 出错时的可展示信息（刷新后仍能看到失败原因） */
  error?: string
  /** 用户主动停止或流中断 */
  stopped?: boolean
}

export interface StoredSession {
  id: string
  /** 取首条提问的前18 字 */
  title: string
  createdAt: number
  updatedAt: number
  messages: StoredMessage[]
}

const KEY = 'sentinel.qa.sessions.v1'

/** 最多保留的会话数（超出丢弃最旧的） */
const MAX_SESSIONS = 30
/** 单个会话最多保留的消息条数，防止localStorage 无限膨胀 */
const MAX_MESSAGES = 60

function read(): StoredSession[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    // 结构校验：旧版本可能存了别的形状，逐项过滤而不是整个丢弃
    return (parsed as StoredSession[]).filter(
      (s) => s && typeof s.id === 'string' && Array.isArray(s.messages),
    )
  } catch {
    // 隐私模式 / 配额满 / 数据损坏 —— 一律退回空列表，
    // 绝不让localStorage 异常影响问答主流程
    return []
  }
}

/**
 * 写入会话列表（手册 3.6：最近优先 + 超限淘汰 + 内存兜底）
 *
 * 原实现只 try 一次：配额不足时直接放弃，
 * 用户表现为「聊到后面历史突然没了」。
 * 现在逐条丢弃最旧的会话重试，直到写得下为止；
 * 全部写不下时返回 false，调用方保留内存副本，最新内容不丢。
 *
 * @returns 是否成功持久化
 */
function write(list: StoredSession[]): boolean {
  // 最近优先：updatedAt 倒序
  const recent = [...list]
    .sort((a, b) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0))
    .slice(0, MAX_SESSIONS)

  for (let count = recent.length; count >= 1; count--) {
    try {
      localStorage.setItem(KEY, JSON.stringify(recent.slice(0, count)))
      return true
    } catch {
      // 配额不足：再丢一条最旧的，最新内容始终优先保住
    }
  }
  return false
}

/** 生成会话标题：首条提问的前 18 字 */
export function deriveTitle(messages: StoredMessage[]): string {
  const firstUser = messages.find((m) => m.role === 'user')
  if (!firstUser) return '新对话'
  const t = firstUser.content.replace(/\s+/g, ' ').trim()
  if (!t) return '新对话'
  return t.length > 18 ? t.slice(0, 18) + '…' : t
}

export const qaHistory = {
  list(): StoredSession[]  {
    // 按最近更新排序（最新的在前）
    return read().sort((a, b) => b.updatedAt - a.updatedAt)
  },

  /** 新建一个空会话并返回 id */
  create(): string  {
    const id = `sess-${Date.now()}-${Math.floor(Math.random() * 1e4)}`
    const now = Date.now()
    const next = [...read(), { id, title: '新对话', createdAt: now, updatedAt: now, messages: [] }]
    write(next.slice(-MAX_SESSIONS))
    return id
  },

  /** 覆盖写入某个会话；不存在则新建 */
  save(id: string, messages: StoredMessage[]): void {
    const list = read()
    const now = Date.now()
    const trimmed = messages.slice(-MAX_MESSAGES)
    const idx = list.findIndex((s) => s.id === id)
    const title = deriveTitle(trimmed)
    if (idx >= 0) {
      list[idx] = { ...list[idx], title, updatedAt: now, messages: trimmed }
    } else {
      list.push({ id, title, createdAt: now, updatedAt: now, messages: trimmed })
    }
    write(list.slice(-MAX_SESSIONS))
  },

  get(id: string): StoredSession | undefined {
    return read().find((s) => s.id === id)
  },

  remove(id: string): void {
    write(read().filter((s) => s.id !== id))
  },

  /** 清空全部历史（区别于「清空当前会话」——后者只清消息） */
  clearAll(): void  {
    write([])
  },
}

/** 把「N 天前 / 昨天 / HH:mm」这种相对时间写得更适合列表展示 */
export function timeLabel(ts: number): string {
  const diff = Date.now() - ts
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days === 1) return '昨天'
  if (days < 7) return `${days} 天前`
  return new Date(ts).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}
