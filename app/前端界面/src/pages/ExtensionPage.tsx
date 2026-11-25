/**
 * 拓展 · 网络入侵检测（ROEN）
 * =================================================================
 * 移植自已归档的 MD1.0 / ROEN 科研项目（论文见页内引用）。
 * 三块能力：ROEN 流量检测（GCN+LSTM 双线程）· 流量模拟器 ·
 * SHA-512+混沌映射图像加密。
 *
 * 演示口径（对评委如实）：
 *   · 实时监控的每条判定都是模型真实推理，结果如实上屏；
 *   · 「模拟一次攻击事件」是演练触发，记录中带「演练」标识；
 *   · 加密/解密为真实算法往返（SHA-512 → Logistic/Tent → XOR）。
 *
 * 配色：全部沿用 sentinel-theme 既有变量，不引入新色值。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Empty,
  Input,
  Row,
  Slider,
  Table,
  Tag,
  Tooltip,
  message,
} from 'antd'
import ReactECharts from 'echarts-for-react'
import {
  DeploymentUnitOutlined,
  EyeOutlined,
  KeyOutlined,
  LockOutlined,
  PlayCircleOutlined,
  SafetyCertificateOutlined,
  StopOutlined,
  SyncOutlined,
  ThunderboltOutlined,
  UnlockOutlined,
  WarningFilled,
} from '@ant-design/icons'
import { chartPalette } from '@/lib/chartPalette'
import { useTheme } from '@/lib/theme'
import { api, type RoenHistoryItem, type RoenModelInfo, type RoenStatus } from '@/api/client'
import { MetricCard } from '@/components/MetricCard'
import { RoenArchDiagram } from '@/components/RoenArchDiagram'

const POLL_MS = 2000

const TOOLTIP_BG = 'rgba(14, 19, 17, 0.96)'
const tooltipStyle = (isDark: boolean) => ({
  backgroundColor: isDark ? TOOLTIP_BG : '#05825c',
  borderColor: isDark ? 'rgba(180, 220, 195, 0.14)' : 'rgba(255,255,255,0.28)',
  borderWidth: 1,
  className: 'sentinel-chart-tooltip',
  textStyle: { color: isDark ? '#e9f0ea' : '#ffffff', fontSize: 12 },
  extraCssText:
    'backdrop-filter: blur(16px); border-radius: 13px; box-shadow: ' +
    (isDark ? '0 8px 28px rgba(0,0,0,.35);' : '0 8px 24px rgba(4,90,64,.4);'),
})

/** 判定标签配色：BENIGN 翡翠绿 / DDoS、PortScan 玫红 */
function attackTag(attack: string) {
  if (attack === 'BENIGN') return <Tag color="green">BENIGN</Tag>
  return <Tag color="red">{attack}</Tag>
}

function timeOf(iso: string) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}

export default function ExtensionPage() {
  const { isDark } = useTheme()
  const [status, setStatus] = useState<RoenStatus | null>(null)
  const [history, setHistory] = useState<RoenHistoryItem[]>([])
  const [modelInfo, setModelInfo] = useState<RoenModelInfo | null>(null)
  const [busy, setBusy] = useState(false)
  const [unlockKey, setUnlockKey] = useState('')
  const [attackProb, setAttackProb] = useState(0.3)
  const [batchSize, setBatchSize] = useState(10)
  const [interval, setIntervalSec] = useState(1.0)
  const alive = useRef(true)

  /* ---------- 数据加载（2 秒轮询） ---------- */
  const refresh = useCallback(async () => {
    try {
      const [s, h] = await Promise.all([api.roenStatus(), api.roenHistory(50)])
      if (!alive.current) return
      setStatus(s)
      setHistory(h)
      if (!s.monitoring) {
        setAttackProb(s.config.attack_probability)
        setBatchSize(s.config.batch_size)
        setIntervalSec(s.config.interval)
      }
    } catch {
      /* 后端未就绪时静默，下次轮询重试 */
    }
  }, [])

  useEffect(() => {
    alive.current = true
    api.roenModelInfo().then(setModelInfo).catch(() => undefined)
    refresh()
    const timer = window.setInterval(refresh, POLL_MS)
    return () => {
      alive.current = false
      window.clearInterval(timer)
    }
  }, [refresh])

  /* ---------- 动作 ---------- */
  const act = useCallback(
    async (fn: () => Promise<{ ok: boolean; message: string }>, successText?: string) => {
      setBusy(true)
      try {
        const r = await fn()
        if (r.ok && successText) message.success(successText)
        else if (!r.ok) message.warning(r.message)
        await refresh()
      } catch (e) {
        message.warning(`操作失败：${String(e)}`)
      } finally {
        setBusy(false)
      }
    },
    [refresh],
  )

  const onStart = () => act(() => api.roenMonitorStart({}), '监控已启动')
  const onStop = () => act(() => api.roenMonitorStop(), '监控已停止')
  const onDetect = () => act(() => api.roenDetectOnce().then((r) => ({ ok: r.ok, message: 'ok' })), '已完成一次检测')
  const onSimulate = () =>
    act(
      () =>
        api.roenSimulateAttack().then((r) => ({
          ok: r.ok,
          message: r.ok ? `已触发安全响应（${r.result.attack_type}）` : '触发失败',
        })),
      '已触发安全响应',
    )
  const onEncrypt = () => act(() => api.roenEncryptImages(), '图像已加密')
  const onDecrypt = () => act(() => api.roenDecryptImages(), '图像已还原')
  const onUnlock = () =>
    act(() => api.roenUnlock(unlockKey), '系统已解锁').then(() => setUnlockKey(''))

  const applyConfig = (patch: { interval?: number; batch_size?: number; attack_probability?: number }) =>
    act(() => api.roenMonitorConfigure(patch))

  /* ---------- 图表 ---------- */

  /** 每批流量条数趋势 + 攻击批次标记 */
  const trendOption = useMemo(() => {
    const p = chartPalette(isDark)
    const rows = [...history].reverse().slice(-40)
    if (!rows.length) return null
    const times = rows.map((r) => timeOf(r.ts))
    return {
      grid: { left: 8, right: 16, top: 30, bottom: 4, containLabel: true },
      legend: {
        data: ['每批流量条数', '攻击批次'],
        right: 0,
        top: 0,
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { color: p.legend, fontSize: 11 },
      },
      tooltip: { trigger: 'axis', ...tooltipStyle(isDark) },
      xAxis: {
        type: 'category',
        data: times,
        axisLine: { lineStyle: { color: p.axisLine } },
        axisLabel: { color: p.axisLabel, fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: p.splitLine } },
        axisLabel: { color: p.axisLabel, fontSize: 10 },
      },
      series: [
        {
          name: '每批流量条数',
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: rows.map((r) => r.total_flows),
          lineStyle: { width: 2, color: p.series[0] },
          itemStyle: { color: p.series[0] },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: isDark ? 'rgba(61,220,151,0.28)' : 'rgba(4,120,90,0.22)' },
                { offset: 1, color: 'rgba(61,220,151,0.02)' },
              ],
            },
          },
        },
        {
          name: '攻击批次',
          type: 'scatter',
          data: rows.map((r) => (r.is_attack ? r.total_flows : null)),
          itemStyle: { color: p.red, borderColor: '#fff', borderWidth: 1 },
          symbolSize: 11,
        },
      ],
    }
  }, [history, isDark])

  /** 类别分布（最近批次的流计数聚合） */
  const distOption = useMemo(() => {
    const p = chartPalette(isDark)
    const agg: Record<string, number> = {}
    for (const r of history) {
      for (const [k, v] of Object.entries(r.class_distribution)) {
        agg[k] = (agg[k] ?? 0) + v
      }
    }
    const entries = Object.entries(agg)
    if (!entries.length) return null
    const colorMap: Record<string, string> = {
      BENIGN: p.green,
      DDoS: p.red,
      PortScan: p.amber,
    }
    return {
      tooltip: { trigger: 'item', ...tooltipStyle(isDark) },
      legend: { bottom: 0, textStyle: { color: p.legend, fontSize: 11 }, itemWidth: 10, itemHeight: 10 },
      series: [
        {
          type: 'pie',
          radius: ['46%', '70%'],
          center: ['50%', '44%'],
          avoidLabelOverlap: true,
          itemStyle: { borderColor: isDark ? '#0d1512' : '#ffffff', borderWidth: 2 },
          label: { color: p.legend, fontSize: 11 },
          data: entries.map(([name, value]) => ({
            name,
            value,
            itemStyle: { color: colorMap[name] ?? p.series[0] },
          })),
        },
      ],
    }
  }, [history, isDark])

  /* ---------- 渲染 ---------- */
  const locked = status?.locked ?? false
  const monitoring = status?.monitoring ?? false

  return (
    <div>
      {/* ---- 指标卡 ---- */}
      <Row gutter={[10, 10]}>
        <Col xs={12} md={6}>
          <MetricCard
            title="检测批次"
            value={status?.total_detections ?? 0}
            suffix="批"
            hint="模拟流量 × ROEN 真实推理"
            icon={<SyncOutlined />}
            iconTone="primary"
          />
        </Col>
        <Col xs={12} md={6}>
          <MetricCard
            title="攻击事件"
            value={(status?.total_attacks ?? 0) + (status?.simulated_events ?? 0)}
            suffix="次"
            hint={`其中演练 ${status?.simulated_events ?? 0} 次`}
            tone="warning"
            icon={<WarningFilled />}
            iconTone="rose"
          />
        </Col>
        <Col xs={12} md={6}>
          <MetricCard
            title="已加密图像"
            value={status?.total_encrypted ?? 0}
            suffix="张"
            hint="混沌加密（SHA-512 + Logistic/Tent）"
            icon={<LockOutlined />}
            iconTone="accent"
          />
        </Col>
        <Col xs={12} md={6}>
          <MetricCard
            title="系统状态"
            value={locked ? '已锁定' : '正常'}
            hint={monitoring ? '监控运行中' : '监控未启动'}
            tone={locked ? 'danger' : 'success'}
            icon={locked ? <LockOutlined /> : <SafetyCertificateOutlined />}
            iconTone={locked ? 'rose' : 'secondary'}
          />
        </Col>
      </Row>

      {/* ---- 实时监控 + 安全响应 ---- */}
      <Row gutter={[10, 10]} style={{ marginTop: 10 }}>
        <Col xs={24} xl={15}>
          {/* height:100% —— 与右列（安全响应 + 类别分布两张卡之和）等高：
              Row 的 flex 拉伸让两列同高，卡片再填满整列 */}
          <Card
            className="sentinel-reveal"
            size="small"
            style={{ height: '100%' }}
            title={
              <span>
                <DeploymentUnitOutlined /> 实时流量检测
              </span>
            }
            extra={
              <span style={{ fontSize: 12, color: 'var(--muted)' }}>
                {monitoring ? (
                  <Tag color="green">监控中</Tag>
                ) : (
                  <Tag>未启动</Tag>
                )}
                {status?.model_ready ? (
                  <Tag color="blue">模型就绪</Tag>
                ) : (
                  <Tag color="orange">模型加载中…</Tag>
                )}
              </span>
            }
          >
            {/* 控制条 */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
              {monitoring ? (
                <Button icon={<StopOutlined />} onClick={onStop} disabled={busy}>
                  停止监控
                </Button>
              ) : (
                <Button type="primary" icon={<PlayCircleOutlined />} onClick={onStart} disabled={busy}>
                  开始监控
                </Button>
              )}
              <Button icon={<SyncOutlined />} onClick={onDetect} disabled={busy || monitoring}>
                立即检测一次
              </Button>
              <Tooltip title="演练：触发一次攻击事件，走完整的安全响应链路（锁定 → 加密），记录中带「演练」标识">
                <Button danger icon={<ThunderboltOutlined />} onClick={onSimulate} disabled={busy}>
                  模拟一次攻击事件
                </Button>
              </Tooltip>
            </div>

            {/* 参数 */}
            <Row gutter={[16, 4]} style={{ marginBottom: 8 }}>
              <Col xs={24} md={8}>
                <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 2 }}>
                  攻击样本注入比例（演示） {Math.round(attackProb * 100)}%
                </div>
                <Slider
                  min={0}
                  max={1}
                  step={0.05}
                  value={attackProb}
                  onChange={setAttackProb}
                  onAfterChange={(v) => applyConfig({ attack_probability: v })}
                  tooltip={{ formatter: (v) => `${Math.round((v ?? 0) * 100)}%` }}
                />
              </Col>
              <Col xs={24} md={8}>
                <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 2 }}>
                  每批流量条数 {batchSize}
                </div>
                <Slider
                  min={5}
                  max={50}
                  step={5}
                  value={batchSize}
                  onChange={setBatchSize}
                  onAfterChange={(v) => applyConfig({ batch_size: v })}
                />
              </Col>
              <Col xs={24} md={8}>
                <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 2 }}>
                  检测间隔 {interval.toFixed(1)} 秒
                </div>
                <Slider
                  min={0.5}
                  max={10}
                  step={0.5}
                  value={interval}
                  onChange={setIntervalSec}
                  onAfterChange={(v) => applyConfig({ interval: v })}
                />
              </Col>
            </Row>

            {/* 图表 */}
            {trendOption ? (
              <ReactECharts option={trendOption} style={{ height: 200 }} notMerge />
            ) : (
              <Empty description="开始监控后，这里实时显示每批流量与攻击标记" />
            )}

            {/* 检测流表格 */}
            <Table<RoenHistoryItem>
              size="small"
              rowKey={(r) => `${r.ts}-${r.total_flows}-${Math.random()}`}
              pagination={false}
              scroll={{ y: 300 }}
              dataSource={history.slice(0, 30)}
              columns={[
                {
                  title: '时间',
                  dataIndex: 'ts',
                  width: 92,
                  render: (v: string) => <span style={{ fontFamily: 'var(--mono)' }}>{timeOf(v)}</span>,
                },
                { title: '流量条数', dataIndex: 'total_flows', width: 84 },
                {
                  title: '模型判定',
                  dataIndex: 'attack_type',
                  width: 104,
                  render: (v: string, r) =>
                    r.simulation ? <Tag color="volcano">{v}（演练）</Tag> : attackTag(v),
                },
                {
                  title: '置信度',
                  dataIndex: 'confidence_score',
                  width: 84,
                  render: (v: number) => (
                    <span style={{ fontFamily: 'var(--mono)' }}>{v.toFixed(3)}</span>
                  ),
                },
                {
                  title: '类别分布',
                  dataIndex: 'class_distribution',
                  render: (v: Record<string, number>) => (
                    <span style={{ fontSize: 12, color: 'var(--muted)' }}>
                      {Object.entries(v)
                        .map(([k, n]) => `${k}×${n}`)
                        .join('  ')}
                    </span>
                  ),
                },
              ]}
            />
          </Card>
        </Col>

        <Col xs={24} xl={9}>
          {/* 安全响应 */}
          <Card
            className="sentinel-reveal"
            size="small"
            title={
              <span>
                <SafetyCertificateOutlined /> 安全响应
              </span>
            }
          >
            <Alert
              type={locked ? 'error' : 'success'}
              showIcon
              banner
              message={locked ? '系统已锁定：检测到攻击，演示图像已加密' : '系统正常：未检测到威胁'}
              style={{ marginBottom: 12 }}
            />

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
              <Button icon={<LockOutlined />} onClick={onEncrypt} disabled={busy}>
                加密图像
              </Button>
              <Button icon={<UnlockOutlined />} onClick={onDecrypt} disabled={busy}>
                解密还原
              </Button>
            </div>

            {/* 图像预览 */}
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
              {(status?.images ?? []).map((img) => (
                <div key={img.id} style={{ width: 148 }}>
                  <div
                    style={{
                      border: '1px solid var(--border)',
                      borderRadius: 10,
                      overflow: 'hidden',
                      background: 'var(--panel)',
                    }}
                  >
                    <img
                      src={`/api/roen/images/${img.id}/preview`}
                      alt={img.filename}
                      style={{ width: '100%', display: 'block' }}
                    />
                  </div>
                  <div style={{ fontSize: 12, marginTop: 4, textAlign: 'center' }}>
                    {img.is_encrypted ? (
                      <Tag color="red">密文</Tag>
                    ) : (
                      <Tag color="green">原文</Tag>
                    )}
                  </div>
                </div>
              ))}
              {!status?.images?.length && <Empty description="暂无演示图像" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
            </div>

            {/* 解锁 */}
            <div style={{ borderTop: '1px dashed var(--border)', paddingTop: 12 }}>
              <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 6 }}>
                <KeyOutlined /> 管理员解锁（演示口令：admin123）
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <Input.Password
                  placeholder="输入管理员口令"
                  value={unlockKey}
                  onChange={(e) => setUnlockKey(e.target.value)}
                  onPressEnter={onUnlock}
                />
                <Button type="primary" onClick={onUnlock} disabled={busy || !locked}>
                  解锁
                </Button>
              </div>
            </div>
          </Card>

          {/* 类别分布 */}
          <Card className="sentinel-reveal" size="small" title="类别分布（最近批次聚合）" style={{ marginTop: 10 }}>
            {distOption ? (
              <ReactECharts option={distOption} style={{ height: 220 }} notMerge />
            ) : (
              <Empty description="暂无数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
        </Col>
      </Row>

      {/* ---- 项目介绍 ---- */}
      <Card
        className="sentinel-reveal"
        size="small"
        title={
          <span>
            <EyeOutlined /> 项目介绍 · ROEN
          </span>
        }
        style={{ marginTop: 10 }}
      >
        <Row gutter={[16, 16]}>
          <Col xs={24}>
            <p style={{ color: 'var(--muted)', fontSize: 13, lineHeight: 1.8, maxWidth: 1020 }}>
              ROEN（Recurrent Feature Fusion Evolution
              Network）是一套面向多模态混合网络流量的入侵检测模型，成果发表于
              <b> Computer Networks（Elsevier，2025）</b>。它将流量建模为动态图：节点为
              IP、边为流，用「显式统计特征（MLP+LSTM）+ 隐式动态拓扑（GCN+LSTM）」
              双线程提取表征，再由全局融合引擎完成边级分类。
            </p>
            {modelInfo?.paper && (
              <p style={{ fontSize: 12, color: 'var(--muted)' }}>
                <b>引用：</b>
                {modelInfo.paper.authors}. <i>{modelInfo.paper.title}</i>. {modelInfo.paper.venue}.
              </p>
            )}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
              {modelInfo?.params != null && <Tag color="blue">参数量 {modelInfo.params.toLocaleString()}</Tag>}
              {modelInfo?.device && <Tag color="blue">推理设备 {modelInfo.device}</Tag>}
              {(modelInfo?.classes ?? []).map((c) => (
                <Tag key={c} color={c === 'BENIGN' ? 'green' : 'red'}>
                  {c}
                </Tag>
              ))}
            </div>
          </Col>
        </Row>

        {/* 网络架构：自绘 SVG（与运维页「链路拓扑」同一视觉语言），替代论文截图 */}
        <RoenArchDiagram />

        <Collapse
          ghost
          size="small"
          style={{ marginTop: 8 }}
          items={[
            {
              key: 'pipeline',
              label: '技术管线与演示口径说明',
              children: (
                <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.9 }}>
                  <p>
                    <b>检测管线：</b>流量模拟器生成 CICIDS2017 风格的流批次（可调攻击样本注入比例）
                    → 构建动态图（节点=IP、边=流、边特征=统计量，与训练侧预处理同构）
                    → ROEN 推理 → 逐边分类，按最高威胁等级汇总。
                  </p>
                  <p>
                    <b>加密管线：</b>图像 → SHA-512 双哈希得 128 元素密钥 K → 由 K 初始化
                    Logistic / Tent 两个混沌系统 → 生成混沌序列 → 灰度 XOR 加密；
                    解密用保存的 K 还原混沌序列，逐位可逆。
                  </p>
                  <p>
                    <b>演示口径：</b>实时监控的每条判定均为模型真实推理，结果如实展示；
                    「模拟一次攻击事件」为演练触发（记录带「演练」标识），用于完整演示
                    「锁定 → 加密 → 解密 → 解锁」响应链路。
                  </p>
                </div>
              ),
            },
          ]}
        />
      </Card>
    </div>
  )
}
