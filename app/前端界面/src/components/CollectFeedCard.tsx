/**
 * 采集动态卡片（监控看板）
 * ------------------------------------------------------------
 * 形态：**一张扁平表**，而不是按采集次数分组的嵌套列表。
 * 列：序号 / 采集时间 / 采集到的数据 / 数据源
 *
 * 为什么要扁平：
 * 嵌套结构（每次采集一个分组、分组里若干条）看着有层次，
 * 但用户真正想知道的是「**刚才采到了什么**」——
 * 一个能按时间倒序扫读、序号连续的列表，比分组更好用。
 * 而且分组会让「序号」在跨组时变得没有意义。
 *
 * 配色沿用看板既有的 SOURCE_COLOR（九色），
 * 与下方「数据源状态」表、侧栏色点完全一致 ——
 * 同一个数据源在任何位置都是同一个颜色。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Card, Space, Table, Tag, Tooltip, Typography } from 'antd'
import { BellOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'

import { api } from '@/api/client'
import type { CollectFeedRow } from '@/api/client'
import { useTheme } from '@/lib/theme'

const { Text } = Typography

/** 数据源 code → 中文名（与调度表一致） */
const SOURCE_NAMES: Record<string, string> = {
  nvd: 'NVD CVE',
  ghsa: 'GitHub 公告',
  osv: 'OSV 开源库',
  cisa_kev: 'CISA KEV',
  arxiv: 'arXiv 论文',
  poc_repo: 'POC 仓库',
  rss: '社区博客',
  atlas: 'ATLAS 技术',
  policy: '政策法规',
}

const SEV_COLOR: Record<string, string> = {
  CRITICAL: '#ff7096',
  HIGH: '#f9c80e',
  MEDIUM: '#0090b7',
  LOW: '#7f8a82',
}

/** 白天版：同色相加深。
 *  实测原色在白底CRITICAL 2.4 / HIGH 1.6 / MEDIUM 3.2 / LOW 3.4 —— HIGH 明显发飘，
 *  圆点会看不见「严重度在变化」这个信号。 */
const SEV_COLOR_LIGHT: Record<string, string> = {
  CRITICAL: '#c9456b',
  HIGH: '#a07800',
  MEDIUM: '#00708e',
  LOW: '#55645c',
}

/** 各严重度的补充说明（悬停提示） */
const SEV_TIPS: Record<string, string> = {
  CRITICAL: '严重：可被直接利用导致系统沦陷或数据泄露，出现在在野利用目录',
  HIGH: '高危：可造成实质影响，需尽快修复',
  MEDIUM: '中危：影响范围有限，建议纳入修复计划',
  LOW: '低危：利用条件苛刻，实际风险较低',
}

/**
 * 取严重度对应的颜色。
 *
 * `toUpperCase` 是**容错**而非多此一举：
 * 数据层已有 `_normalize_severity`，但那是入库时归一，
 * 历史数据 / 未来新增源仍可能送来 `High`、`Medium` 这类写法。
 * 显示层再做一次归一，任何大小写都能正确上色 ——
 * **数据清洗是治标，展示层容错才是兜底**。
 */
function sevColor(severity: string | null | undefined, isDark: boolean): string {
  const key = (severity ?? '').trim().toUpperCase();
  return (isDark ? SEV_COLOR : SEV_COLOR_LIGHT)[key] ?? 'transparent';
}

interface Props {
  /** 数据源配色表（从看板传入，保证全站一致） */
  sourceColor: Record<string, string>
}

export function CollectFeedCard({ sourceColor }: Props) {
  /* 主题：严重度圆点与未读角标在白底上要换深色版 */
  const { isDark } = useTheme()
  const [rows, setRows] = useState<CollectFeedRow[]>([])
  const [loading, setLoading] = useState(false)
  /** 未读条数：加载之后新采集到的数据条数。点已读归零（微信语义） */
  const [unread, setUnread] = useState(0)
  /**
   * 见过的采集时间戳集合 —— 用来判断「哪些是本次轮询新增的」。
   *
   * 为什么不直接比较 rows 长度：
   * 接口按时间回看 7 天、且有采样上限，rows 数量会随采样/去重波动，
   * 长度不能代表「新增」。**时间戳是采集批次的唯一标识**。
   */
  const seenRef = useRef<Set<string>>(new Set)
  /** 首屏标记：历史数据不该被算成「未读」 */
  const initedRef = useRef(false)

  const load = useCallback(async () => {
    try {
      setLoading(true)
      /*
       * 最短显示 400ms —— 本地接口只要 ~200ms，
       * 转圈会「一闪而过」，用户根本看不到，等于没加。
       * 强行hold 一下才能让动效真正被感知。
       */
      const minShow = new Promise((r) => setTimeout(r, 400))
      const r = await Promise.all([
        api.collectFeed(10_080),
        minShow,
      ]).then(([d]) => d)
      setRows(r.rows ?? [])

      /*
       * 未读检测以**采集批次**（batches）为准，而不是数据行（rows）。
       *
       * 为什么：atlas 这类源 `new_count=0`（采集了但无新增），
       * 根本不出现在 rows 里。若拿 rows 做差集，
       * 「又采集了一次」这个事实永远检测不到 —— 实测踩过。
       */
      const stamps = new Set(
        (r.batches ?? []).map((b) => `${b.log_id}|${b.finished_at ?? ''}`),
      )
      if (!initedRef.current) {
        // 首屏：只建基线，已有历史不该让用户看到一堆红点
        initedRef.current = true
        for (const s of stamps) seenRef.current.add(s)
        return
      }
      /* 未读计数 = 累加**新增条数**（new_count），而不是采集批次数。
       *
       * 原先 `fresh += 1` 数的是批次：一次采集哪怕入库 500 条也只 +1，
       * 与「新增了多少条情报」的直觉不符 ——
       * 实测反馈：ghsa 新增 500 条，角标却显示 1。
       *
       * 无新增（new_count=0）不产生未读：采集器「跑了但没新数据」
       * 这件事由看板的「上次触发」时间体现，不该占用角标 ——
       * 否则 atlas 每次采集都会 +1，角标会长期虚高。
       * 差集判断仍基于「批次」key（log_id|finished_at），
       * 所以「又采集了一次」本身依然能被检测到，只是不再计入数字。 */
      let fresh = 0
      for (const b of r.batches ?? []) {
        const key = `${b.log_id}|${b.finished_at ?? ''}`
        if (!seenRef.current.has(key)) {
          seenRef.current.add(key)
          fresh += b.new_count ?? 0
        }
      }
      if (fresh > 0) setUnread((prev) => prev + fresh)
    } catch {
      // 静默降级：采集动态是辅助信息，取不到不该影响看板
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
void load()
    const t = setInterval(() => void load(), 30_000)
    return () => clearInterval(t)
  }, [load])

  /** 点铃铛 = 标记已读：清角标数字，**列表数据保留**（微信语义） */
  const markRead = useCallback(() => setUnread(0), [])

  return (
    <Card
      size="small"
      title={
        <Space size={6}>
          <span>采集动态</span>
          {/* 铃铛 + 未读数（微信语义）：
              数字挂在铃铛右上角，点击后归零。
              标题栏右侧不再放「已读」按钮 —— 点击铃铛本身就是「已读」。
              ⚠️ 文案必须是「点击已读」而不是「点击查看」（用户 2026-10-14 反馈）：
              点击只清角标数字、不跳转，写「查看」会让人以为能到新数据那里去。*/}
          <Tooltip
            title={
              unread > 0
                ? `新增 ${unread} 条采集数据，点击已读`
                : '暂无新增采集数据'
            }
          >
            {/* 未读数放在铃铛**右侧**，而不是 Badge 的默认右上角 ——
               右上角会让数字向左上方延伸，而标题「采集动态」就在紧邻的
               左边（Space size=6），数字会被标题压住（实测现象）。
               改用 inline-flex 把「铃铛 + 计数」排成一行，各占各的宽度。 */}
            <span
              onClick={markRead}
              role="button"
              tabIndex={0}
              aria-label={unread > 0 ? `新增 ${unread} 条采集数据，点击已读` : '暂无新增采集数据'}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  markRead()
                }
              }}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 5,
                cursor: 'pointer',
                lineHeight: 1,
              }}
            >
              <BellOutlined
                style={{
                  fontSize: 14,
                  color: unread > 0 ? 'var(--danger)' : 'var(--muted)',
                }}
              />
              {unread > 0 && (
                <span
                  style={{
                    minWidth: 16,
                    height: 16,
                    padding: '0 5px',
                    borderRadius: 8,
                    background: 'var(--danger)',
                    color: '#fff',
                    fontSize: 11,
                    lineHeight: '16px',
                    textAlign: 'center',
                    fontFamily: 'var(--mono)',
                  }}
                >
                  {unread > 999 ? '999+' : unread}
                </span>
              )}
            </span>
          </Tooltip>
        </Space>
      }
      style={{ marginBottom: 12 }}
      extra={
        <Space size={8}>
          {/* 刷新中转圈 —— 纯图标按钮没有 loading 态，
              用户点了看不到反馈，只能靠猜。*/}
          <Tooltip title={loading ? '正在刷新…' : '立即刷新'}>
            <ReloadOutlined
              onClick={() => void load()}
              spin={loading}
              style={{
                cursor: loading ? 'wait' : 'pointer',
                fontSize: 13,
                opacity: loading ? 0.7 : 1,
              }}
            />
          </Tooltip>
        </Space>
      }
    >
      {/* 图例常驻行 —— 「严重度」标签在竖线**左侧**，
          竖线右侧是各严重度的色点 + 中文解释。

          为什么必须常驻：色点是纯视觉元素，
          不配文字说明用户只能猜「这个点是什么意思」。

          为什么标签在竖线左边：竖线是「标签区」与「说明区」的分界，
          标签放在分界左侧才符合「标签 | 内容」的阅读顺序
          （用户要求调整的形态）。 */}
      <div className="sentinel-feed-legend">
        <span className="sentinel-feed-legend-label">严重度</span>
        <div className="sentinel-feed-legend-items">
          {([
            ['CRITICAL', '严重'],
            ['HIGH', '高危'],
            ['MEDIUM', '中危'],
            ['LOW', '低危'],
          ] as const).map(([k, label]) => (
            <Tooltip key={k} title={SEV_TIPS[k]}>
              <span className="sentinel-feed-legend-item">
                <i className="sentinel-feed-sev-dot" style={{ background: sevColor(k, isDark) }} />
                {label}
              </span>
            </Tooltip>
          ))}
          <Tooltip title="该条目在上游（NVD）没有严重度评级，故无色点">
            <span className="sentinel-feed-legend-item is-none">
              <i className="sentinel-feed-sev-dot is-none" />
              未评级
            </span>
          </Tooltip>
        </div>
      </div>

      <Table<CollectFeedRow>
        size="small"
        rowKey={(r) => `${r.source}-${r.id}-${r.finished_at}`}
        dataSource={rows}
        loading={loading}
        pagination={false}
        scroll={{ y: 264 }}
        // 表格线要淡：这张表是辅助信息，不该抢看板主体的注意力
        style={{ fontSize: 12.5 }}
        columns={[
          {
            title: '序号',
            key: 'idx',
            width: 54,
            // 用表格自带的行序，保证连续且不随筛选乱跳
            render: (_, __, i) => <span style={{ color: 'var(--muted)' }}>{i + 1}</span>,
          },
          {
            title: '采集时间',
            dataIndex: 'finished_at',
            key: 'finished_at',
            width: 132,
            render: (v: string | null) =>
              v ? (
                <Tooltip title={dayjs(v).format('YYYY-MM-DD HH:mm:ss')}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {dayjs(v).format('MM-DD HH:mm:ss')}
                  </Text>
                </Tooltip>
              ) : (
                <Text type="secondary">—</Text>
              ),
          },
          {
            title: '采集到的数据',
            dataIndex: 'title',
            key: 'title',
            ellipsis: true,
            render: (v: string, r) => (
              <Tooltip title={r.title}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  {/* 严重度色点：与情报中心表格一致 */}
                  <span
                    style={{
                      width: 4,
                      height: 4,
                      borderRadius: '50%',
                      flexShrink: 0,
                      background: sevColor(r.severity, isDark),
                    }}
                  />
                  {r.url ? (
                    <a
                      href={r.url}
                      target="_blank"
                      rel="noreferrer"
                      style={{ fontSize: 12.5 }}
                    >
                      {v || r.id}
                    </a>
                  ) : (
                    <span style={{ fontSize: 12.5 }}>{v || r.id}</span>
                  )}
                </span>
              </Tooltip>
            ),
          },
          {
            title: '数据源',
            dataIndex: 'source',
            key: 'source',
            width: 112,
            render: (v: string) => (
              <Tag
                style={{
                  fontSize: 11,
                  margin: 0,
                  color: sourceColor[v] ?? 'var(--muted)',
                  borderColor: `${sourceColor[v] ?? 'var(--muted)'}55`,
                  background: `${sourceColor[v] ?? 'var(--muted)'}14`,
                }}
              >
                {SOURCE_NAMES[v] ?? v}
              </Tag>
            ),
          },
        ]}
      />
      {rows.length === 0 && !loading && (
        <Text type="secondary" style={{ fontSize: 12 }}>
          最近 7 天没有采集记录
        </Text>
      )}
    </Card>
  )
}