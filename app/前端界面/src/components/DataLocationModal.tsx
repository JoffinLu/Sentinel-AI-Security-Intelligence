/**
 * 数据存储位置设置弹窗（系统设置）
 * ------------------------------------------------------------
 * 功能：
 *   ① 展示当前数据存储位置（只读）
 *   ② 修改为新位置：手工输入 或 「浏览…」调起系统文件夹选择对话框
 *   ③ 保存 → 记录设定值 → 提示"重启程序后自动迁移数据"
 *
 * 设计要点：
 *   · **迁移在重启后执行**：程序运行时数据库文件被占用，无法安全搬动；
 *     启动器会在下次启动时（数据库未启动的最安全时机）自动完成迁移。
 *   · 路径必须为纯英文/数字：Windows 版 PostgreSQL 的 initdb 不支持
 *     中文路径（引擎限制）。前端预检 + 后端强校验双重把关。
 *   · 「浏览…」按钮走 pywebview 原生接口（window.pywebview.api.pick_folder）——
 *     网页本身拿不到本机绝对路径，必须由原生层调起系统对话框。
 *     浏览器模式（非窗口）下该能力不可用，自动退回手工输入。
 */

import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Input, Modal, Space, Spin, Typography, message } from 'antd'
import { FolderOpenOutlined, HddOutlined, SaveOutlined } from '@ant-design/icons'

import { api } from '@/api/client'
import type { DataLocationInfo } from '@/api/client'

const { Text, Paragraph } = Typography

/** pywebview 原生桥（仅在其窗口内存在；浏览器模式为 undefined） */
declare global {
  interface Window {
    pywebview?: {
      api?: {
        pick_folder?: () => Promise<string>
      }
    }
  }
}

interface Props {
  open: boolean
  onClose: () => void
}

export function DataLocationModal({ open, onClose }: Props) {
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [info, setInfo] = useState<DataLocationInfo | null>(null)
  const [value, setValue] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.dataLocation()
      setInfo(data)
      setValue(data.configured_dir)
    } catch {
      message.error('读取数据位置失败（后端可能未就绪）')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  /** 调起系统「选择文件夹」对话框（pywebview 原生能力） */
  const pickFolder = async () => {
    const pick = window.pywebview?.api?.pick_folder
    if (!pick) {
      message.info('当前环境不支持系统文件夹选择（浏览器模式），请手工输入路径')
      return
    }
    try {
      const picked = await pick()
      if (picked) setValue(picked)
    } catch {
      message.warning('选择文件夹失败，请手工输入路径')
    }
  }

  const save = async () => {
    const target = value.trim().replace(/^["']|["']$/g, '')
    if (!target) {
      message.warning('请输入目标路径')
      return
    }
    if (!/^[\x20-\x7e]+$/.test(target)) {
      message.error('路径含中文或特殊字符（数据库引擎不支持），请使用纯英文/数字路径')
      return
    }
    setSaving(true)
    try {
      const res = await api.setDataLocation(target)
      if (res.restart_required) {
        Modal.info({
          title: '设置已保存',
          content: (
            <div>
              <p>{res.message}</p>
              <p style={{ marginBottom: 0 }}>
                操作方法：<Text strong>关闭本程序窗口</Text> →{' '}
                <Text strong>重新双击 Sentinel.exe</Text>
                ——启动时将自动把数据迁移到新位置（约 1~2 分钟，原位置数据会保留）。
              </p>
            </div>
          ),
          okText: '知道了',
          onOk: onClose,
        })
      } else {
        message.success(res.message)
        onClose()
      }
      void load()
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        '保存失败，请检查路径后重试'
      message.error(detail)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title={
        <Space>
          <HddOutlined />
          数据存储设置
        </Space>
      }
      open={open}
      onCancel={onClose}
      footer={null}
      width={560}
      destroyOnClose
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: '32px 0' }}>
          <Spin />
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, paddingTop: 8 }}>
          {info?.restart_required && (
            <Alert
              type="warning"
              showIcon
              message="有待生效的变更"
              description="已保存新的存储位置——关闭并重新打开程序后将自动完成数据迁移。"
            />
          )}

          <div>
            <Text type="secondary">当前存储位置</Text>
            <Input value={info?.current_dir || ''} readOnly style={{ marginTop: 6 }} />
          </div>

          <div>
            <Text type="secondary">存储位置改为</Text>
            <Space.Compact style={{ width: '100%', marginTop: 6 }}>
              <Input
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="例如 D:\SentinelData"
                onPressEnter={() => void save()}
              />
              <Button icon={<FolderOpenOutlined />} onClick={() => void pickFolder()}>
                浏览…
              </Button>
            </Space.Compact>
            <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
              · 建议使用 D 盘等数据盘，并为**纯英文/数字路径**（数据库引擎不支持中文路径）
              <br />· 保存后**重启程序**，数据将自动迁移到新位置（原位置数据会保留一份）
              <br />· 想恢复默认：把位置改回上方"当前存储位置"并保存即可
            </Paragraph>
          </div>

          <div style={{ textAlign: 'right' }}>
            <Space>
              <Button onClick={onClose}>取消</Button>
              <Button
                type="primary"
                icon={<SaveOutlined />}
                loading={saving}
                onClick={() => void save()}
              >
                保存
              </Button>
            </Space>
          </div>
        </div>
      )}
    </Modal>
  )
}
