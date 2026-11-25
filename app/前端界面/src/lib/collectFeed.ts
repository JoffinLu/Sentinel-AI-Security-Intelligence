/**
 * 采集动态 Hook
 * ------------------------------------------------------------
 * 用户诉求（很具体）：
 *   ① 列表要显示**具体采到了什么数据**，不只是「新增 N 条」
 *   ② 显示采集时间
 *   ③ 有多少条未读就展示多少条
 *   ④ 点「已读」**只清角标数字，不清列表**（像微信：
 *      未读数显示在图标右上角，已读后角标消失但消息还在）
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/api/client'

/** 与调度表一致的源显示名 */
const SOURCE_NAMES: Record<string, string> = {
  nvd: 'NVD CVE 2.0',
  ghsa: 'GitHub 安全公告',
  osv: 'OSV 开源漏洞库',
  cisa_kev: 'CISA KEV 在野利用',
  arxiv: 'arXiv 学术论文',
  poc_repo: 'POC 验证代码库',
  rss: '安全社区与厂商博客',
  atlas: 'MITRE ATLAS 技术库',
  policy: 'AI 安全政策法规',
}

/** 一条具体采集到的数据 */
interface FeedItem {
  id: string
  title: string
  severity: string | null
  cvss: number | null
  url: string | null
}

/** 一次采集（可能对应多条数据） */
interface FeedGroup {
  logId: number
  source: string
  sourceName: string
  newCount: number
  /** 本次抓到的总量（去重前） */
  fetchedCount: number
  at: string
  items: FeedItem[]
}

/** 「N 分钟前」 */
function ago(iso: string): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  if (diff < 0) return '刚刚'
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

export function useCollectFeed() {
  /** 已读角标数——**只是未读数**，已读后归零但列表保留 */
  const [unread, setUnread] = useState(0)
  /** 采集记录（含具体条目），**已读不清空** */
  const [groups, setGroups] = useState<FeedGroup[]>([])
  const [loading, setLoading] = useState(false)
  /** 新到的高亮提示（3 秒后自动消退） */
  const [flash, setFlash] = useState(false)

  /** 已见过的采集日志 id —— 用于判断哪些是「新的」 */
  const seenRef = useRef<Set<number>>(new Set)
  /** 首次加载完成标记：首屏的历史不该被算成未读 */
  const initedRef = useRef(false)

  const load = useCallback(async (isFirst: boolean) => {
    try {
      setLoading(true)
      // 回看 7 天（10080 分钟）：各源调度间隔差异极大 ——
      // rss 1 小时、nvd/ghsa 6 小时、cisa_kev/policy 12 小时、atlas 7 天。
      // 只看 1 天的话高频源刷满、低频源一个都不出现，
      // 用户会误以为「只有安全社区在采集」。
      // 实测：窗口 1 天 → 仅 4 个源；改 7 天 + 后端按源收敛 → 9 个源全部可见。
      const r = await api.collectFeed(10_080)
      const mapped: FeedGroup[] = r.groups.map((g) => ({
        logId: g.log_id,
        source: g.source,
        sourceName: SOURCE_NAMES[g.source] ?? g.source,
        newCount: g.new_count,
        fetchedCount: g.fetched_count ?? 0,
        at: g.finished_at ?? '',
        items: g.items ?? [],
      }))

      // 找出「本次会话之前没见过」的采集
      const fresh = mapped.filter((g) => !seenRef.current.has(g.logId))

      // 首屏：只建基线。已有历史不该被当成未读 ——
      // 否则一进页面就冒出一堆红点，与真实情况不符。
      if (isFirst || !initedRef.current) {
        initedRef.current = true
        for (const g of mapped) seenRef.current.add(g.logId)
        setGroups(mapped)
        setUnread(0)
        return
      }

      if (fresh.length === 0) {
        setGroups(mapped)
        return
      }
      for (const g of fresh) seenRef.current.add(g.logId)
      setGroups(mapped)
      setUnread((prev) => prev + fresh.reduce((s, g) => s + g.newCount, 0))
      setFlash(true)
    } catch {
      // 静默降级：采集动态是辅助信息，取不到不该影响主流程
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load(true)
    const timer = setInterval(() => void load(false), 30_000)
    return () => clearInterval(timer)
  }, [load])

  // 闪动 3 秒后自动熄灭
  useEffect(() => {
    if (!flash) return
    const t = setTimeout(() => setFlash(false), 3000)
    return () => clearTimeout(t)
  }, [flash])

  /**
   * 标记已读 —— **只清角标，列表保留**。
   * 这正是微信的行为：看过之后红点消失，但消息仍在会话里可回看。
   */
  const markRead = useCallback(() => setUnread(0), [])

  return { unread, groups, loading, flash, markRead }
}

export { SOURCE_NAMES, ago }
export type { FeedGroup, FeedItem }