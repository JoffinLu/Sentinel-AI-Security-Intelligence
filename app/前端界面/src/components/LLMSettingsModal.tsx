/**
 * LLM 模型设置弹窗 · 两级视图（模型列表 ↔ 添加/编辑表单）
 * ------------------------------------------------------------
 * 视图 1（列表，默认）：
 *   ① 当前生效卡片：模型名 + 「测试连接 / 编辑 / 删除」
 *      - 测试连接走 health 接口的实时 LLM 探测（当前 Key 后端不回传，无法直测）
 *      - 删除 = 恢复出厂配置（后端是单配置模型，删除当前即回到默认 DeepSeek）
 *   ② 「已添加的模型」列表（localStorage，含已存 Key）：
 *      - 「使用」一键切换生效；「编辑」进表单预填；「删除」移出列表
 *   ③ 「添加模型」→ 视图 2
 * 视图 2（表单）：
 *   - 常用服务商快捷填入 / URL / 模型名 / Key（留空沿用）/ 测试连接
 *   - 保存成功自动记入历史，返回列表
 *
 * 设计要点（沿袭）：
 * ① Key 只显示掩码（后端不回传完整值），留空保存 = 沿用当前 Key
 * ② 历史配置存 localStorage —— 本地部署场景 Key 本就明文落 .env，安全模型一致
 * ③ 切换/删除等破坏性操作均有显式确认或可回退（恢复默认随时可点）
 */

import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Input,
  Modal,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ApiOutlined,
  CheckCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  HistoryOutlined,
  LeftOutlined,
  PlusOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'

import { api } from '@/api/client'
import type { LLMConfig } from '@/api/client'

const { Text } = Typography

/* ---------- 历史配置（= 已添加的模型）----------
 * 存储在浏览器 localStorage：本地部署场景下 Key 本就以明文落在 .env，
 * 安全模型一致（弹窗内的安全提示也已说明）。
 * 去重键 = api_base + model；同组合并时更新 Key 与时间；最新在前，上限 8 条。 */
interface LLMHistoryItem {
  api_base: string
  model: string
  api_key: string
  savedAt: number
}
const HISTORY_KEY = 'sentinel-llm-history'
const HISTORY_MAX = 8

function readHistory(): LLMHistoryItem[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    const list = raw ? (JSON.parse(raw) as LLMHistoryItem[]) : []
    return Array.isArray(list) ? list : []
  } catch {
    return []
  }
}

function writeHistory(list: LLMHistoryItem[]) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, HISTORY_MAX)))
  } catch {
    /* 隐私模式/空间不足：历史是增强功能，静默失败不影响主流程 */
  }
}

/** 取 URL 的主机名用于列表展示；解析失败回退原串 */
function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

interface Props {
  open: boolean
  onClose: () => void
  /** 保存成功后通知外层刷新 health，让顶栏标签同步 */
  onSaved: () => void
}

export function LLMSettingsModal({ open, onClose, onSaved }: Props) {
  const [cfg, setCfg] = useState<LLMConfig | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testingCurrent, setTestingCurrent] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  /** 勾选后下次保存会清空并要求重新输入 */
  const [keyDirty, setKeyDirty] = useState(false)
  /** 历史配置：弹窗打开时从 localStorage 装载 */
  const [history, setHistory] = useState<LLMHistoryItem[]>([])

  const [form, setForm] = useState({ api_base: '', model: '', api_key: '' })

  /** 视图：list = 模型列表（默认）；form = 添加/编辑表单 */
  const [view, setView] = useState<'list' | 'form'>('list')
  const [formMode, setFormMode] = useState<'add' | 'edit'>('add')

  const load = useCallback(async () => {
    try {
      setLoading(true)
      const d = await api.llmConfig()
      setCfg(d)
      setForm({ api_base: d.api_base, model: d.model, api_key: '' })
      setKeyDirty(false)
      setTestResult(null)
    } catch (e) {
      message.error('读取配置失败：' + (e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) {
      void load()
      setHistory(readHistory())
      setView('list')
    }
  }, [open, load])

  /* ============ 公共操作 ============ */

  /** 保存成功后记录历史（仅当本次填了真实 Key —— 条目必须自包含可用） */
  const pushHistory = (item: { api_base: string; model: string; api_key: string }) => {
    setHistory((prev) => {
      const next = [
        { ...item, savedAt: Date.now() },
        ...prev.filter(
          (h) => !(h.api_base === item.api_base && h.model === item.model),
        ),
      ].slice(0, HISTORY_MAX)
      writeHistory(next)
      return next
    })
  }

  const removeHistory = (h: Pick<LLMHistoryItem, 'api_base' | 'model'>) => {
    setHistory((prev) => {
      const next = prev.filter((x) => !(x.api_base === h.api_base && x.model === h.model))
      writeHistory(next)
      return next
    })
  }

  /* ============ 视图 1：列表页操作 ============ */

  /** 测试当前生效的模型 —— health 接口带 LLM 实时探测
   *  （当前 Key 后端只回掩码，前端拿不到，所以不走 testLLMConfig） */
  const testCurrent = async () => {
    try {
      setTestingCurrent(true)
      const h = await api.health()
      const primary = h.components?.llm?.primary
      if (primary?.available) {
        message.success(`LLM 在线：${primary.model}`)
      } else {
        message.error('LLM 探测失败：' + (primary?.error ?? '未知原因'))
      }
    } catch (e) {
      message.error('探测失败：' + (e as Error).message)
    } finally {
      setTestingCurrent(false)
    }
  }

  /** 删除当前生效模型 = 恢复出厂配置（后端单配置，删除即回默认 DeepSeek） */
  const deleteCurrent = () => {
    Modal.confirm({
      title: '删除当前模型？',
      content: `将删除「${cfg?.model}」并恢复出厂默认配置（DeepSeek）。历史列表里的模型不受影响，随时可以再切换回去。`,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      onOk: async () => {
        try {
          const r = await api.restoreLLMConfig()
          if (r.ok) {
            message.success(r.message || '已恢复默认配置')
            await load()
            onSaved()
          } else {
            message.error(r.message)
          }
        } catch (e) {
          message.error('删除失败：' + (e as Error).message)
        }
      },
    })
  }

  /** 一键切换到历史里的某个模型：整套（含已存 Key）直接保存生效 */
  const useHistoryModel = async (h: LLMHistoryItem) => {
    try {
      setSaving(true)
      const r = await api.saveLLMConfig({
        api_base: h.api_base,
        model: h.model,
        api_key: h.api_key,
      })
      if (r.ok) {
        message.success(`已切换到 ${h.model}`)
        await load()
        onSaved()
      } else {
        message.error(r.message)
      }
    } catch (e) {
      message.error('切换失败：' + (e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  /* ============ 视图 2：表单操作 ============ */

  const openAdd = () => {
    setFormMode('add')
    setForm({ api_base: '', model: '', api_key: '' })
    setKeyDirty(false)
    setTestResult(null)
    setView('form')
  }

  /** 编辑：历史条目（预填含 Key）或当前生效模型（Key 留空 = 沿用） */
  const openEdit = (source: { api_base: string; model: string; api_key?: string }) => {
    setFormMode('edit')
    setForm({
      api_base: source.api_base,
      model: source.model,
      api_key: source.api_key ?? '',
    })
    setKeyDirty(false)
    setTestResult(null)
    setView('form')
  }

  const handleTest = async () => {
    if (!form.api_base.trim() || !form.model.trim()) {
      message.warning('请先填写 API 地址与模型名称')
      return
    }
    const key = form.api_key.trim()
    if (!key) {
      message.warning('测试连接需要填写 API Key（仅保存时可以留空沿用旧的）')
      return
    }
    try {
      setTesting(true)
      setTestResult(null)
      const r = await api.testLLMConfig({
        api_base: form.api_base.trim(),
        model: form.model.trim(),
        api_key: key,
      })
      setTestResult({ ok: r.ok, message: r.message })
    } catch (e) {
      setTestResult({ ok: false, message: (e as Error).message })
    } finally {
      setTesting(false)
    }
  }

  const handleSave = async () => {
    if (!form.api_base.trim() || !form.model.trim()) {
      message.warning('API 地址与模型名称不能为空')
      return
    }
    try {
      setSaving(true)
      const r = await api.saveLLMConfig({
        api_base: form.api_base.trim(),
        model: form.model.trim(),
        // 留空则传空字符串，后端会沿用当前 Key
        api_key: form.api_key.trim(),
      })
      if (r.ok) {
        message.success(r.message || '已保存并生效')
        // 模型库语义：添加/编辑过的条目**只增不自动删**——
        // 编辑改了模型名也保留旧条目（否则切换模型时会"丢失"之前的模型，
        // 用户需重新输入）。移除只能通过列表上的显式删除按钮。
        // 本次填了真实 Key 才记历史（Key 留空=沿用旧 Key，拿不到完整凭据）
        if (form.api_key.trim()) {
          pushHistory({
            api_base: form.api_base.trim(),
            model: form.model.trim(),
            api_key: form.api_key.trim(),
          })
        }
        onSaved()
        await load()
        setView('list')
      } else {
        message.error(r.message)
      }
    } catch (e) {
      message.error('保存失败：' + (e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const handleRestore = async () => {
    try {
      const r = await api.restoreLLMConfig()
      if (r.ok) {
        message.success(r.message)
        await load()
        onSaved()
      } else {
        message.error(r.message)
      }
    } catch (e) {
      message.error('恢复失败：' + (e as Error).message)
    }
  }

  const applyPreset = (p: { api_base: string; model: string }) => {
    setForm((f) => ({ ...f, api_base: p.api_base, model: p.model }))
    setTestResult(null)
  }

  /* ============ 视图 2：表单 JSX ============ */
  const formView = (
    <div>
      <div style={{ marginBottom: 12 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          常用服务商（点击填入）
        </Text>
        <div style={{ marginTop: 6 }}>
          <Space size={[6, 6]} wrap>
            {(cfg?.presets ?? []).map((p) => (
              <Tag
                key={p.name}
                style={{ cursor: 'pointer', fontSize: 11.5, margin: 0 }}
                color={form.api_base === p.api_base ? 'green' : 'default'}
                onClick={() => applyPreset(p)}
              >
                {p.name}
              </Tag>
            ))}
          </Space>
        </div>
      </div>

      <div style={{ marginBottom: 12 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          API Base URL
        </Text>
        <Input
          value={form.api_base}
          onChange={(e) => {
            setForm((f) => ({ ...f, api_base: e.target.value }))
            setTestResult(null)
          }}
          placeholder="https://api.deepseek.com/v1"
          style={{ marginTop: 4 }}
          addonBefore={<ApiOutlined />}
        />
      </div>

      <div style={{ marginBottom: 12 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          模型名称
        </Text>
        <Input
          value={form.model}
          onChange={(e) => {
            setForm((f) => ({ ...f, model: e.target.value }))
            setTestResult(null)
          }}
          placeholder="deepseek-chat"
          style={{ marginTop: 4 }}
        />
      </div>

      <div style={{ marginBottom: 12 }}>
        <Space size={6} align="center">
          <Text type="secondary" style={{ fontSize: 12 }}>
            API Key
          </Text>
          {keyDirty ? (
            <Tag color="orange" style={{ fontSize: 10.5, margin: 0 }}>
              将替换现有 Key
            </Tag>
          ) : cfg?.key_configured ? (
            <Text type="secondary" style={{ fontSize: 11 }}>
              当前 {cfg.key_masked}
            </Text>
          ) : null}
        </Space>
        <Input.Password
          value={form.api_key}
          onChange={(e) => {
            setForm((f) => ({ ...f, api_key: e.target.value }))
            setKeyDirty(true)
            setTestResult(null)
          }}
          placeholder={
            cfg?.key_configured ? '留空则沿用当前 Key' : '请输入 API Key'
          }
          style={{ marginTop: 4 }}
        />
      </div>

      {testResult && (
        <Alert
          type={testResult.ok ? 'success' : 'error'}
          showIcon
          message={testResult.ok ? '连接成功' : '连接失败'}
          description={testResult.message}
          style={{ marginBottom: 12, fontSize: 12.5 }}
        />
      )}

      {/* 安全提示 —— 必须说清楚 Key 会落在磁盘上 */}
      <Alert
        type="info"
        showIcon
        style={{ fontSize: 11.5 }}
        message="关于密钥安全"
        description={
          <>
            API Key 会以明文写入项目根目录的 <code>.env</code> 文件，
            保存前会先备份为 <code>.env.bak</code>。
            这是本地部署方案的固有做法。
            <br />
            「已添加的模型」会连同 Key 一起保存在**本浏览器**的
            localStorage 中（仅本机本浏览器可读）。
            建议：仅使用个人测试 Key，勿在演示环境输入生产密钥。
          </>
        }
      />
    </div>
  )

  /* ============ 视图 1：模型列表 JSX ============ */
  const listView = (
    <div>
      {/* 当前生效 */}
      <div
        style={{
          marginBottom: 16,
          padding: '10px 12px',
          background: 'rgba(182,243,74,0.06)',
          border: '1px solid rgba(182,243,74,0.15)',
          borderRadius: 8,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <CheckCircleOutlined style={{ color: 'var(--accent)' }} />
          <span style={{ fontSize: 12.5 }}>
            当前生效：<b>{cfg?.model}</b>
          </span>
          {cfg?.is_custom && (
            <Tag color="cyan" style={{ fontSize: 10.5, margin: 0 }}>
              已自定义
            </Tag>
          )}
        </div>
        <Space size={6} wrap>
          <Button
            size="small"
            icon={<ThunderboltOutlined />}
            loading={testingCurrent}
            onClick={testCurrent}
          >
            测试连接
          </Button>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => openEdit({ api_base: cfg?.api_base ?? '', model: cfg?.model ?? '' })}
          >
            编辑
          </Button>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={deleteCurrent}>
            删除
          </Button>
        </Space>
      </div>

      {/* 已添加的模型（历史） */}
      <div style={{ marginBottom: 14 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          已添加的模型
        </Text>
        {history.length === 0 ? (
          <div
            style={{
              marginTop: 6,
              padding: '10px 12px',
              borderRadius: 8,
              border: '1px dashed var(--border)',
              fontSize: 11.5,
              color: 'var(--muted)',
            }}
          >
            还没有添加过模型 —— 点下方「添加模型」，填好配置保存后它会出现在这里，
            以后一键切换。
          </div>
        ) : (
          <div style={{ marginTop: 6, display: 'grid', gap: 6 }}>
            {history.map((h) => {
              const isCurrent =
                cfg?.model === h.model && (cfg?.api_base ?? '') === h.api_base
              return (
                <div
                  key={h.api_base + h.model}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    padding: '8px 10px',
                    borderRadius: 8,
                    border: '1px solid var(--border)',
                    background: isCurrent ? 'rgba(182,243,74,0.07)' : 'transparent',
                    fontSize: 12,
                  }}
                >
                  <HistoryOutlined
                    style={{ color: 'var(--accent)', fontSize: 12, flexShrink: 0 }}
                  />
                  <b style={{ fontSize: 12 }}>{h.model}</b>
                  <span
                    style={{
                      fontSize: 11,
                      color: 'var(--muted)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {hostOf(h.api_base)}
                  </span>
                  {isCurrent && (
                    <Tag color="green" style={{ fontSize: 10, margin: 0, lineHeight: '16px' }}>
                      当前
                    </Tag>
                  )}
                  <span style={{ flex: 1 }} />
                  <small style={{ fontSize: 10.5, color: 'var(--muted)', flexShrink: 0 }}>
                    {dayjs(h.savedAt).format('MM-DD')}
                  </small>
                  <Button
                    size="small"
                    type="primary"
                    ghost
                    disabled={isCurrent || saving}
                    onClick={() => void useHistoryModel(h)}
                    style={{ flexShrink: 0 }}
                  >
                    使用
                  </Button>
                  <Button
                    size="small"
                    icon={<EditOutlined />}
                    disabled={saving}
                    onClick={() => openEdit(h)}
                    aria-label={`编辑 ${h.model}`}
                    style={{ flexShrink: 0 }}
                  />
                  <Button
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    disabled={saving}
                    onClick={() => removeHistory(h)}
                    aria-label={`删除 ${h.model}`}
                    style={{ flexShrink: 0 }}
                  />
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* 添加模型 */}
      <Button block type="dashed" icon={<PlusOutlined />} onClick={openAdd}>
        添加模型
      </Button>

      <div style={{ marginTop: 12 }}>
        <Button size="small" type="text" icon={<ReloadOutlined />} onClick={handleRestore}>
          恢复出厂默认配置
        </Button>
      </div>

      {/* 安全提示 */}
      <Alert
        type="info"
        showIcon
        style={{ fontSize: 11.5, marginTop: 12 }}
        message="关于密钥安全"
        description={
          <>
            API Key 会以明文写入项目根目录的 <code>.env</code> 文件，
            保存前会先备份为 <code>.env.bak</code>。这是本地部署方案的固有做法。
            <br />
            「已添加的模型」会连同 Key 一起保存在**本浏览器**的
            localStorage 中（仅本机本浏览器可读）。
            建议：仅使用个人测试 Key，勿在演示环境输入生产密钥。
          </>
        }
      />
    </div>
  )

  const isEdit = formMode === 'edit'

  return (
    <Modal
      open={open}
      onCancel={() => (view === 'form' ? setView('list') : onClose())}
      title={
        <Space size={8}>
          {view === 'form' && (
            <Button
              type="text"
              size="small"
              icon={<LeftOutlined />}
              onClick={() => setView('list')}
              aria-label="返回列表"
            />
          )}
          <ApiOutlined style={{ color: 'var(--accent)' }} />
          <span>{view === 'list' ? 'LLM 模型设置' : isEdit ? '编辑模型' : '添加模型'}</span>
        </Space>
      }
      width={560}
      footer={
        view === 'list'
          ? [
              <Button key="close" onClick={onClose}>
                关闭
              </Button>,
            ]
          : [
              <Button key="back" onClick={() => setView('list')}>
                返回
              </Button>,
              <Button
                key="test"
                icon={<ThunderboltOutlined />}
                loading={testing}
                onClick={handleTest}
              >
                测试连接
              </Button>,
              <Button key="save" type="primary" loading={saving} onClick={handleSave}>
                保存并生效
              </Button>,
            ]
      }
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: '32px 0' }}>
          <Spin />
        </div>
      ) : view === 'list' ? (
        listView
      ) : (
        formView
      )}
    </Modal>
  )
}
