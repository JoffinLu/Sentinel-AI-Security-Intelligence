/**
 * 首屏 · 最新情报滚动条
 * =================================================================
 * 无缝跑马灯：轨道宽度由内容撑开，条目复制到 ≥2 倍容器宽，
 * 动画位移 translateX(-50%) 正好走过一份内容的距离，
 * 循环点画面完全一致 → 肉眼无缝。
 * 时长按「内容宽度 ÷ 速度」算，所以增删条目后速度依然恒定。
 *
 * 内容用**真实数据**（最新入库的高危漏洞），接口失败时降级为
 * 静态能力词——首屏摆出编造的威胁情报是最不能接受的事。
 */
import { useEffect, useRef, useState } from 'react'
import { api } from '@/api/client'

/** 滚动速度（像素/秒）—— 与参考实现一致 */
const SPEED = 85
/** 时长下限：条目很少时不至于一闪而过 */
const MIN_DURATION = 24

interface Item {
  sev: string
  text: string
}

/** 降级文案：接口不可用时展示系统能力，不编造事件 */
const FALLBACK: Item[] = [
  { sev: 'CRITICAL', text: '多源采集：9 类数据源自动汇聚' },
  { sev: 'HIGH', text: '三级过滤：粗化 → 去重 → 富化' },
  { sev: 'HIGH', text: '七维富化：补齐漏洞证据链' },
  { sev: 'MEDIUM', text: '知识图谱：实体关联与多跳推理' },
  { sev: 'MEDIUM', text: '混合检索：BM25 与向量 RRF 融合' },
  { sev: 'LOW', text: '智能问答：每条结论可溯源核验' },
  { sev: 'INFO', text: '熔断自愈：Agent 失败自动隔离重试' },
  { sev: 'INFO', text: '实时态势：采集与富化全链路可视' },
]

const SEV_CLASS: Record<string, string> = {
  CRITICAL: 'crit',
  HIGH: 'warn',
  MEDIUM: 'med',
  LOW: 'info',
  NONE: 'info',
}

export function Ticker() {
  const trackRef = useRef<HTMLDivElement>(null)
  const [items, setItems] = useState<Item[]>(FALLBACK)

  /* 取最新的高危情报；失败就用降级文案，不阻塞首屏 */
  useEffect(() => {
    let alive = true
    void api
      .vulns({ limit: 12 })
      .then((res) => {
        if (!alive) return
        const list = res?.items ?? []
        if (!Array.isArray(list) || list.length === 0) return
        const rows: Item[] = list
          .filter((v) => v.cve_id)
          .slice(0, 12)
          .map((v) => ({
            sev: (v.severity || 'INFO').toUpperCase(),
            text: `${v.cve_id}　${(v.title || '').slice(0, 52) || '（无标题）'}`,
          }))
        if (rows.length >= 2) setItems(rows)
      })
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [])

  /* 无缝：条目在 JSX 里渲染两份（[...items, ...items]），
     位移 -50% 正好走过一份内容，循环点画面一致 → 无缝。

     为什么不用 innerHTML 复制：
     那样会篡改 React 自己管理的 DOM，items 更新后 React 仍按
     旧的 children 去 diff，状态与真实 DOM 脱节——表现为
     「接口拿到了 12 条，界面却还是降级文案」。 */
  useEffect(() => {
    const track = trackRef.current
    if (!track) return
    // scrollWidth 已含两份，所以时长翻倍、速度不变
    const duration = Math.max(MIN_DURATION, track.scrollWidth / SPEED)
    track.style.animationDuration = `${duration}s`
  }, [items])

  return (
    <div className="landing-ticker" aria-label="最新入库高危情报">
      <span className="landing-ticker-tag" aria-hidden>
        最新情报
      </span>
      <div className="landing-ticker-viewport">
        <div className="landing-ticker-track" ref={trackRef}>
          {/* 两份拼接：位移 -50% 时接缝完全对齐，这是无缝的关键 */}
          {[0, 1].map((copy) =>
            items.map((it, i) => (
              <span className="landing-tk" key={`${copy}-${i}`}>
                <i
                  className={`landing-tk-chip ${SEV_CLASS[it.sev] ?? 'info'}`}
                >
                  {it.sev}
                </i>
                {it.text}
              </span>
            )),
          )}
        </div>
      </div>
    </div>
  )
}