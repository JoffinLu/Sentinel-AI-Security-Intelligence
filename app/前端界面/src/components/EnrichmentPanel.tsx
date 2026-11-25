/**
 * 富化详情抽屉
 *
 * 展示单个漏洞的 7 维富化结果与置信度分层，
 * 对应「精准与关联推理」评分项的可视化举证。
 */

import { Descriptions, Empty, Progress, Spin, Tag, Tooltip, Alert } from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  FileProtectOutlined,
  MedicineBoxOutlined,
  CodeOutlined,
  BookOutlined,
  AimOutlined,
  ThunderboltOutlined,
  WarningOutlined,
} from '@ant-design/icons'

/** 7 个富化维度的展示配置 */
export const DIMENSION_META: Record<
  string,
  { label: string; icon: React.ReactNode; color: string }
> = {
  cvss: { label: 'CVSS 评分', icon: <FileProtectOutlined />, color: '#dc2626' },
  asset: { label: '受影响资产', icon: <MedicineBoxOutlined />, color: '#ea580c' },
  poc: { label: 'POC 关联', icon: <CodeOutlined />, color: '#ca8a04' },
  paper: { label: '关联论文', icon: <BookOutlined />, color: '#8b5cf6' },
  fix: { label: '修复建议', icon: <AimOutlined />, color: '#06b6d4' },
  technique: { label: '攻击技映射', icon: <ThunderboltOutlined />, color: '#ec4899' },
  risk: { label: 'AI 风险定级', icon: <WarningOutlined />, color: 'var(--inline-ok)' },
}

export interface EnrichDimension {
  dimension: string
  label: string
  status: 'success' | 'failed'
  confidence: number
  quality_tier: 'high' | 'medium' | 'low'
  content: Record<string, unknown> | null
  error: string | null
  elapsed_ms: number | null
}

export interface EnrichResult {
  cve_id: string
  title: string
  completeness: number
  success_count: number
  total_count: number
  avg_confidence: number
  enrichment_status: string
  dimensions: EnrichDimension[]
}

const TIER_COLOR: Record<string, string> = { high: 'success', medium: 'warning', low: 'default' }
const TIER_LABEL: Record<string, string> = { high: '高置信', medium: '中置信', low: '低置信' }

/** 优先级配色 */
const PRIORITY_COLOR: Record<string, string> = {
  P1: 'red',
  P2: 'volcano',
  P3: 'gold',
  P4: 'default',
}

/** 单个维度内容渲染 */
function DimensionContent({ dim }: { dim: EnrichDimension }) {
  const c = dim.content
  if (!c) {
    return (
      <div style={{ color: 'var(--inline-text-3)', fontSize: 12 }}>
        {dim.error ? `富化失败：${dim.error}` : '无内容'}
      </div>
    )
  }

  switch (dim.dimension) {
    case 'cvss': {
      const score = c.score as number | null
      return (
        <div>
          <Descriptions size="small" column={2} colon={false}>
            <Descriptions.Item label="基础分">
              <b style={{ fontSize: 16 }}>{score?.toFixed(1) ?? '—'}</b>
            </Descriptions.Item>
            <Descriptions.Item label="定级">
              <Tag color={score && score >= 9 ? 'red' : score && score >= 7 ? 'volcano' : 'gold'}>
                {(c.severity as string) ?? '—'}
              </Tag>
            </Descriptions.Item>
          </Descriptions>
          {c.explanation ? (
            <div style={{ fontSize: 12, color: 'var(--inline-text-2)', marginTop: 4 }}>
              {c.explanation as string}
            </div>
          ) : null}
          <div style={{ fontSize: 11, color: 'var(--inline-text-3)', marginTop: 4 }}>
            路径：{c.method as string}
            {c.version ? ` · CVSS ${c.version}` : ''}
          </div>
        </div>
      )
    }

    case 'asset': {
      const assets = (c.assets as Array<Record<string, unknown>>) ?? []
      if (!assets.length) return <div style={{ color: 'var(--inline-text-3)', fontSize: 12 }}>{c.summary as string}</div>
      return (
        <div>
          {assets.slice(0, 4).map((a, i) => (
            <div key={i} style={{ fontSize: 12, marginBottom: 4 }}>
              <Tag color={a.is_ai_component ? 'purple' : 'default'} style={{ fontSize: 11 }}>
                {a.product as string}
              </Tag>
              {a.ecosystem ? <Tag style={{ fontSize: 11 }}>{a.ecosystem as string}</Tag> : null}
              {a.asset_type ? <Tag style={{ fontSize: 11 }}>{a.asset_type as string}</Tag> : null}
              {a.version_fixed ? (
                <span style={{ color: 'var(--inline-ok)', marginLeft: 4 }}>
                  修复于 {a.version_fixed as string}
                </span>
              ) : null}
            </div>
          ))}
          {assets.length > 4 && (
            <div style={{ fontSize: 11, color: 'var(--inline-text-3)' }}>另有 {assets.length - 4} 个组件</div>
          )}
        </div>
      )
    }

    case 'poc': {
      const pocs = (c.pocs as Array<Record<string, unknown>>) ?? []
      return (
        <div>
          <div style={{ marginBottom: 6 }}>
            <Tag color={c.exploitability === '已有可用POC' ? 'red' : c.exploitability === '存在POC但成熟度低' ? 'gold' : 'default'}>
              可用性：{c.exploitability as string}
            </Tag>
            {c.mentioned_in_description ? (
              <Tooltip title="漏洞描述中明确提及 POC/Exploit 等字样">
                <Tag color="blue">描述提及</Tag>
              </Tooltip>
            ) : null}
          </div>
          {pocs.slice(0, 3).map((p, i) => (
            <div key={i} style={{ fontSize: 12, marginBottom: 3 }}>
              <a href={p.url as string} target="_blank" rel="noreferrer">
                {(p.name as string) ?? 'POC'}
              </a>
              {p.stars ? <span style={{ color: 'var(--inline-text-3)' }}> ★{p.stars as number}</span> : null}
              <span style={{ color: 'var(--inline-text-3)', marginLeft: 6 }}>
                相关度 {(p.relevance as number)?.toFixed(2)}
              </span>
            </div>
          ))}
          {!pocs.length && (
            <div style={{ fontSize: 12, color: 'var(--inline-text-3)' }}>未匹配到公开 POC</div>
          )}
        </div>
      )
    }

    case 'paper': {
      const papers = (c.papers as Array<Record<string, unknown>>) ?? []
      return (
        <div>
          <Tag color={c.research_depth === '研究充分' ? 'green' : c.research_depth === '有少量研究' ? 'gold' : 'default'}>
            研究深度：{c.research_depth as string}
          </Tag>
          {papers.slice(0, 3).map((p, i) => (
            <div key={i} style={{ fontSize: 12, marginTop: 4 }}>
              <a href={p.pdf_url as string} target="_blank" rel="noreferrer">
                {((p.title as string) ?? '').slice(0, 70)}
              </a>
              {((p.ai_topics as string[]) ?? []).slice(0, 2).map((t) => (
                <Tag key={t} style={{ fontSize: 10, marginLeft: 4 }}>
                  {t}
                </Tag>
              ))}
            </div>
          ))}
        </div>
      )
    }

    case 'fix': {
      const g = c.guidance as Record<string, unknown> | undefined
      return (
        <div style={{ fontSize: 12 }}>
          {c.patch_version ? (
            <div style={{ marginBottom: 4 }}>
              <Tag color="green">补丁版本 {c.patch_version as string}</Tag>
              <span style={{ color: 'var(--inline-ok)' }}>{c.mitigation as string}</span>
            </div>
          ) : (
            <div style={{ marginBottom: 4 }}>
              <Tag color="orange">暂无官方补丁</Tag>
              <span>{c.mitigation as string}</span>
            </div>
          )}
          {g?.summary ? (
            <div style={{ color: 'var(--inline-text)', marginTop: 4 }}>{g.summary as string}</div>
          ) : null}
          {((g?.fix_steps as string[]) ?? []).length > 0 && (
            <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
              {(g?.fix_steps as string[]).slice(0, 3).map((s, j) => (
                <li key={j} style={{ color: 'var(--inline-text-1b)' }}>
                  {s}
                </li>
              ))}
            </ul>
          )}
        </div>
      )
    }

    case 'technique': {
      const techs = (c.techniques as Array<Record<string, unknown>>) ?? []
      return (
        <div>
          <div style={{ fontSize: 11, color: 'var(--inline-text-3)', marginBottom: 4 }}>
            {c.mapping_method as string}
          </div>
          {techs.map((t, i) => (
            <div key={i} style={{ fontSize: 12, marginBottom: 4 }}>
              <Tag color="blue" style={{ fontSize: 11 }}>
                {t.atlas_id as string}
              </Tag>
              {t.name as string}
              {t.tactic ? (
                <Tag style={{ fontSize: 10, marginLeft: 4 }}>{t.tactic as string}</Tag>
              ) : null}
            </div>
          ))}
          {!techs.length && (
            <div style={{ fontSize: 12, color: 'var(--inline-text-3)' }}>未匹配到 ATLAS 技术</div>
          )}
        </div>
      )
    }

    case 'risk': {
      return (
        <div style={{ fontSize: 12 }}>
          <div style={{ marginBottom: 6, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <Tag color={PRIORITY_COLOR[c.priority as string] ?? 'default'} style={{ fontSize: 12 }}>
              {c.priority as string} · {c.priority_label as string}
            </Tag>
            <Tag color="default">风险分 {(c.risk_score as number)?.toFixed(0)}</Tag>
            {c.category ? <Tag color="purple">{c.category as string}</Tag> : null}
            {c.actively_exploited ? <Tag color="red">在野利用</Tag> : null}
            {c.ransomware_related ? <Tag color="darkred">勒索相关</Tag> : null}
          </div>
          <div style={{ color: 'var(--inline-text-1b)', marginBottom: 4 }}>
            <b>定级依据：</b>
            {c.priority_reason as string}
          </div>
          <div style={{ color: 'var(--inline-text-2)', marginBottom: 4 }}>
            <b>业务影响：</b>
            {c.business_impact as string}
          </div>
          {((c.recommendation as string[]) ?? []).length > 0 && (
            <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
              {(c.recommendation as string[]).slice(0, 3).map((r, i) => (
                <li key={i} style={{ color: '#059669' }}>
                  {r}
                </li>
              ))}
            </ul>
          )}
        </div>
      )
    }

    default:
      return (
        <pre style={{ fontSize: 11, margin: 0, whiteSpace: 'pre-wrap' }}>
          {JSON.stringify(c, null, 2).slice(0, 400)}
        </pre>
      )
  }
}

/** 富化详情主体 */
export function EnrichmentPanel({
  data,
  loading,
}: {
  data: EnrichResult | null
  loading?: boolean
}) {
  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 40 }}>
        <Spin tip="富化计算中…">
          <div style={{ height: 80 }} />
        </Spin>
      </div>
    )
  }
  if (!data) {
    return <Empty description="暂无富化数据" style={{ padding: 40 }} />
  }

  const passed = data.completeness >= 0.8

  return (
    <div>
      {/* ---- 完整度概览 ---- */}
      <div
        style={{
          background: passed ? 'var(--inline-surface-ok)' : 'var(--inline-surface-warn)',
          borderRadius: 8,
          padding: 14,
          marginBottom: 14,
          border: '0.5px solid var(--inline-border)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
          <span style={{ fontSize: 13, color: 'var(--inline-text)', fontWeight: 500 }}>
            7 维富化完整度
          </span>
          <Tag color={passed ? 'success' : 'warning'}>
            {data.success_count}/{data.total_count} 维
          </Tag>
          <span style={{ fontSize: 12, color: 'var(--inline-text-2)' }}>
            平均置信度 {data.avg_confidence.toFixed(2)}
          </span>
        </div>
        <Progress
          percent={Math.round(data.completeness * 100)}
          strokeColor={passed ? 'var(--inline-ok)' : 'var(--inline-warn)'}
          size="small"
        />
      </div>

      {!passed && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12, fontSize: 12 }}
          message="部分维度未成功富化"
          description="未成功的维度可能因数据源缺失或 LLM 服务降级导致，可重新触发富化。"
        />
      )}

      {/* ---- 各维度详情 ---- */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {data.dimensions.map((dim) => {
          const meta = DIMENSION_META[dim.dimension] ?? {
            label: dim.label,
            icon: null,
            color: 'var(--inline-text-2)',
          }
          return (
            <div
              key={dim.dimension}
              style={{
                border: '0.5px solid var(--inline-border)',
                borderRadius: 8,
                padding: 12,
                background: dim.status === 'success' ? 'var(--inline-surface)' : 'var(--inline-surface-2)',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  marginBottom: 8,
                }}
              >
                <span style={{ color: meta.color, fontSize: 14 }}>{meta.icon}</span>
                <span style={{ fontSize: 13, fontWeight: 500 }}>{meta.label}</span>
                {dim.status === 'success' ? (
                  <CheckCircleOutlined style={{ color: 'var(--inline-ok)', fontSize: 12 }} />
                ) : (
                  <CloseCircleOutlined style={{ color: 'var(--inline-danger)', fontSize: 12 }} />
                )}
                <span style={{ flex: 1 }} />
                {dim.status === 'success' && (
                  <>
                    <Tag color={TIER_COLOR[dim.quality_tier]} style={{ fontSize: 11 }}>
                      {TIER_LABEL[dim.quality_tier]}
                    </Tag>
                    <Tooltip title="置信度：≥0.8 高置信可直接采信，<0.5 需人工核验">
                      <span style={{ fontSize: 11, color: 'var(--inline-text-3)' }}>
                        {dim.confidence.toFixed(2)}
                      </span>
                    </Tooltip>
                  </>
                )}
                {dim.elapsed_ms ? (
                  <span style={{ fontSize: 11, color: 'var(--inline-text-4)' }}>{dim.elapsed_ms}ms</span>
                ) : null}
              </div>
              <DimensionContent dim={dim} />
            </div>
          )
        })}
      </div>
    </div>
  )
}
