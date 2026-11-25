/**
 * 应用主入口
 *
 * 侧边导航 + 路由布局。全局中文文案，适配赛题要求演示场景。
 *
 * 主题说明（SENTINEL 设计语言 · 黑夜 / 白天双主题）
 * ------------------------------------------
 * 黑夜为默认主题；白天主题（白绿）由侧栏底部的开关切换，
 * 状态统一由 <ThemeProvider>（src/lib/theme.tsx）管理：
 *   ① <html data-theme> 驱动整套 CSS 变量翻转（含 antd 覆盖层）
 *   ② ConfigProvider 换用白天版 token（见下方 LIGHT_THEME）
 *   ③ 背景脚本自行监听 data-theme —— 白天主题不使用动态背景
 * 刷新闪烁由 index.html 的内联脚本兜住（先写属性、再加载样式）。
 * 黑夜主题的视觉与动效是全站基准，改造期间只许新增不许改动。
 */

import { useCallback, useDeferredValue, useEffect, useState } from 'react'
import { Layout, Menu, ConfigProvider, theme, Spin } from 'antd'
import type { ThemeConfig } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import {
  DashboardOutlined,
  DatabaseOutlined,
  ApiOutlined,
  FolderOpenOutlined,
  ThunderboltOutlined,
  RobotOutlined,
  MenuFoldOutlined,
  RadarChartOutlined,
  ShareAltOutlined,
  ClockCircleOutlined,
  MenuUnfoldOutlined,
  CheckCircleFilled,
  WarningFilled,
  SearchOutlined,
  MoonOutlined,
  SunOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons'
import { BrowserRouter, Routes, Route, useLocation, useNavigate } from 'react-router-dom'

import LandingPage from '@/pages/LandingPage'
import DashboardPage from '@/pages/DashboardPage'
import SituationPage from '@/pages/SituationPage'
import GraphPage from '@/pages/GraphPage'
import OpsPage from '@/pages/OpsPage'
import IntelPage from '@/pages/IntelPage'
import QAPage from '@/pages/QAPage'
import ExtensionPage from '@/pages/ExtensionPage'
import { api } from '@/api/client'
import { SCHED_CHANGED_EVENT } from '@/api/client'
import type { HealthStatus, KnowledgeBaseStats } from '@/api/client'
import { SentinelLogo } from '@/components/SentinelLogo'
import { StatusDock } from '@/components/StatusDock'
import { LLMSettingsModal } from '@/components/LLMSettingsModal'
import { DataLocationModal } from '@/components/DataLocationModal'
import { PageErrorBoundary } from '@/components/ErrorBoundary'
import { useLiveResource } from '@/lib/useLiveResource'
import { useTheme } from '@/lib/theme'

const { Header, Sider, Content } = Layout

/** 侧栏宽度——需与 sentinel-theme.css 里的变量保持一致 */
const SIDER_WIDTH = 224
const SIDER_COLLAPSED_WIDTH = 64

/**
 * 导航配置（单一数据源）
 *
 * 侧栏菜单与顶栏标题共用这一份定义——
 * 顶栏图标必须与侧栏菜单图标一致，否则用户会以为是两套标识。
 * icon 里存组件类型而非元素，渲染时再按 size 实例化，
 * 这样两处的尺寸可以不同（菜单 14px / 顶栏 18px）但图形相同。
 *
 * 布局借鉴「AEGIS」参考设计：菜单按语义分两组（工作台 / 管理），
 * 组标题带英文小字，形成清晰的层级而不是一长串平铺项。
 */
const NAV_GROUPS = [
  {
    key: 'g-workspace',
    label: '工作台',
    labelEn: 'WORKSPACE',
    items: [
      { key: '/situation', label: '态势总览', Icon: RadarChartOutlined },
      { key: '/dashboard', label: '监控看板', Icon: DashboardOutlined },
      { key: '/intel', label: '情报中心', Icon: DatabaseOutlined },
      { key: '/qa', label: '智能问答', Icon: RobotOutlined },
      { key: '/graph', label: '知识图谱', Icon: ShareAltOutlined },
      { key: '/ops', label: '运维与自愈', Icon: DashboardOutlined },
    ],
  },
  {
    key: 'g-manage',
    label: '管理',
    labelEn: 'MANAGEMENT',
    items: [
      { key: '__llm', label: 'LLM 模型设置', Icon: ApiOutlined },
      { key: '__datadir', label: '数据存储设置', Icon: FolderOpenOutlined },
    ],
  },
  {
    key: 'g-extend',
    label: '拓展',
    labelEn: 'EXTENSION',
    items: [
      // 拓展模块：ROEN 网络入侵检测（移植自 MD1.0 科研项目）
      { key: '/extension', label: '网络入侵检测', Icon: SafetyCertificateOutlined },
    ],
  },
] as const

/** 导航项的显式类型。
 *  NAV_GROUPS 用了 as const，flatMap 结果是 readonly 元组，
 *  直接给 find() 用会把推断带偏（TS18046 unknown）。 */
interface NavEntry {
  key: string
  label: string
  Icon: typeof DashboardOutlined
}

/** AntD Menu 的 items：模块级常量。
 *  之前写在 JSX 里，每次 Shell 重渲染（切页就会触发）都用 map
 *  重建整个数组与其中的 React 元素，侧栏整棵子树跟着重建。
 *  菜单是静态配置，提到模块级后身份稳定，AntD 才能跳过无谓 diff。 */
const NAV_MENU_ITEMS = NAV_GROUPS.map((g) => ({
  type: 'group' as const,
  label: (
    <span className="sentinel-menu-group">
      {g.label}
      <em>{g.labelEn}</em>
    </span>
  ),
  children: g.items.map(({ key, label, Icon }) => ({
    key,
    label,
    icon: <Icon />,
  })),
}))

/** 扁平化的导航项（顶栏标题查找用） */
const NAV_ITEMS: NavEntry[] = NAV_GROUPS.flatMap((g) =>
  g.items.map((i) => ({ ...i })),
)

/** 顶栏时钟：拆成日期与时间两段，便于用不同字重区分层级 */
function formatClock(now: Date): { date: string; time: string } {
  const p = (n: number) => String(n).padStart(2, '0')
  return {
    // 点号分隔的数字日期（2026.10.08）——比「2026年10月08日」更紧凑，
    // 在顶栏有限宽度里不挤压后面的状态标签
    date: `${now.getFullYear()}.${p(now.getMonth() + 1)}.${p(now.getDate())}`,
    time: `${p(now.getHours())}:${p(now.getMinutes())}:${p(now.getSeconds())}`,
  }
}

/**
 * 顶栏实时时钟（独立组件，自管state）
 *
 * 为什么必须独立：
 *   之前它写在 Shell 里，每秒 setState 会让整个 Shell 重渲染——
 *   侧栏、顶栏、以及**当前页面的上千个 DOM 节点**全部跟着重跑。
 *   实测（PerformanceObserver longtask，情报中心 2002 个节点）：
 *   页面静置 3 秒、什么都不做，就自己产生了 3 个 ~150ms 的长任务。
 *   抽成独立组件后，每秒只有这十几行会更新，页面完全不受影响。
 */
function ClockChip() {
  const [clock, setClock] = useState(() => formatClock(new Date()))
  useEffect(() => {
    const timer = window.setInterval(
      () => setClock(formatClock(new Date())),
      1000,
    )
    return () => window.clearInterval(timer)
  }, [])
  return (
    <span className="sentinel-clock" aria-label="当前时间">
      <ClockCircleOutlined />
      <b className="sentinel-clock-date">{clock.date}</b>
      <span className="sentinel-clock-time">{clock.time}</span>
    </span>
  )
}

const Shell = () => {
  const [collapsed, setCollapsed] = useState(false)
  /** LLM 设置弹窗开关（顶栏点击 LLM 标签打开） */
  const [llmModalOpen, setLlmModalOpen] = useState(false)
  /** 数据存储设置弹窗开关（侧栏入口打开） */
  const [dataDirModalOpen, setDataDirModalOpen] = useState(false)
  /** 主题状态：驱动侧栏底部的主题开关；ConfigProvider 在 App 层已按主题切换 */
  const { mode, toggle } = useTheme()
  const location = useLocation()
  const navigate = useNavigate()

  /* 内容区用「延后版location」渲染（React 18 useDeferredValue）。
   * 切页时外壳（面包屑、状态栏、时钟）立即响应，页面组件的挂载
   * 稍后进行——React 会把这次重渲染标记为可中断的低优先级任务，
   * 于是 200~300ms 的挂载耗时被拆散，不再表现为「点了没反应」。
   * 页面上显示的是延后值，所以过渡期间旧页面会多留一两帧，
   * 这正是我们想要的「先响应、后换内容」。 */
  const deferredLocation = useDeferredValue(location)

  /* 手册 3.2：健康状态与知识库统计改用可靠轮询——
     失败时保留最后一次成功快照（顶栏不会瞬间变问号），
     切到后台暂停轮询，返回前台或恢复联网时立即补拉。*/
  const healthRes = useLiveResource<HealthStatus>(() => api.health(), 30_000)
  const kbRes = useLiveResource<KnowledgeBaseStats>(
    () => api.knowledgeBase(),
    60_000,
  )
  const health = healthRes.data
  const kb = kbRes.data
  /** 调度状态是否过期（连续刷新失败——此时顶栏数据是旧快照） */
  const healthStale = Boolean(healthRes.error && health)

  /** 拉取健康状态与知识库统计。
   * 提成组件级函数：除了自动轮询，LLM 设置保存后也要立刻调一次，
   * 让顶栏的「LLM xxx」标签同步显示新模型名。*/
  const refreshHealth = useCallback(() => {
    void healthRes.refresh()
    void kbRes.refresh()
  }, [healthRes, kbRes])

  useEffect(() => {
    /* 调度状态变更事件：看板点「暂停/恢复」后立即重拉health。
       少了这一条，侧栏与顶栏要等最多 30 秒（轮询周期）才更新，
       表现为「点了暂停但那两个地方没反应」。
       （轮询本身已由 useLiveResource 负责，这里只处理即时刷新。） */
    const onSchedChanged = () => {
      void healthRes.refresh()
    }
    window.addEventListener(SCHED_CHANGED_EVENT, onSchedChanged)
    return () => {
      window.removeEventListener(SCHED_CHANGED_EVENT, onSchedChanged)
    }
  }, [healthRes])

  const healthy = health?.status === 'healthy'

  /* 顶栏与侧栏读取的健康字段——逐层安全取值：
     快照在极端时序下可能是**不完整对象**（重渲染窗口/部分响应），
     任何一层缺失都回退为安全默认值，而不是让整页渲染崩溃
     （历史问题：切换时间范围时偶发
     "Cannot read properties of undefined (reading 'available')"）。 */
  const hComponents = health?.components
  const dbAvailable = hComponents?.database?.available ?? false
  const llmPrimary = hComponents?.llm?.primary
  const llmPrimaryAvailable = llmPrimary?.available ?? false
  const llmDegradedMode = hComponents?.llm?.degraded_mode ?? false
  const schedRunning = hComponents?.scheduler?.running ?? false
  const schedPaused = hComponents?.scheduler?.paused ?? false
  const collectorsRegistered = hComponents?.collectors?.registered


  // 侧栏改为 fixed 后，右侧内容区需要知道该让出多少宽度。
  // 把当前宽度写进 CSS 变量，CSS 侧据此设 margin-left，
  // 这样折叠/展开时无需重复写死数值。
  const siderWidth = collapsed ? SIDER_COLLAPSED_WIDTH : SIDER_WIDTH

  return (
    <Layout
      style={{ minHeight: '100vh' }}
      // CSS 变量挂在根 Layout 上，供 .ant-layout-has-sider > .ant-layout 读取
      ref={(el) => {
        if (el) el.style.setProperty('--sentinel-sider-width', `${siderWidth}px`)
      }}
    >
      {/* Sider 内部用 flex 列布局：Logo 在上、菜单居中、数据卡吸底。
          侧栏被 CSS 设为 position:fixed（否则会随内容滚走），
          因此必须用 width 属性把宽度同步给 CSS 变量，
          右侧内容区据此留出对应的 margin-left。 */}
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        theme="dark"
        width={SIDER_WIDTH}
        collapsedWidth={SIDER_COLLAPSED_WIDTH}
        // 禁用 AntD 默认 trigger（它只能是整宽的窄条，装不下
        //「胶囊 + 圆底图标 + 文字」这套结构），改为自建按钮
        trigger={null}
      >
        {/* 品牌区：横向「图标 + 文字」布局（借鉴 AEGIS 参考设计）。
            文字分主副两行——SENTINEL 是品牌，AI 安全情报中枢是定位，
            比原来纯一行小字更像成熟产品。

            **整个品牌区可点击回首屏**（用户要求）：
            做成 button 而非 div，保证键盘可达（Tab + Enter），
            并补上 title 与 aria-label，避免无障碍缺口。*/}
        <button
          type="button"
          className="sentinel-logo sentinel-logo-btn"
          onClick={() => navigate('/')}
          title="返回首屏"
          aria-label="返回首屏"
        >
          <SentinelLogo size={collapsed ? 26 : 32} />
          {!collapsed && (
            <span className="sentinel-logo-text">
              <b>SENTINEL</b>
              <small>
                AI 安全情报中枢 <em className="logo-ver">v1.0</em>
              </small>
            </span>
          )}
        </button>

        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          onClick={({ key }) => {
            /* 「LLM 模型设置」「数据存储设置」不是路由，点击直接开弹窗 */
            if (key === '__llm') setLlmModalOpen(true)
            else if (key === '__datadir') setDataDirModalOpen(true)
            else navigate(key)
          }}
          items={NAV_MENU_ITEMS}
          // 底部微光条需要铺满剩余高度，故外层用 flex 列布局
          style={{ flex: 1, borderInlineEnd: 'none' }}
        />

        {/* 侧栏底部容器：数据卡 + 折叠按钮
            为什么包一层：AntD Menu 的内部样式会覆盖外层的 flex:1，
            导致菜单不撑开剩余高度。若只靠 auto 外边距吸底，
            按钮的位置会随内容高度漂移。
            由这个容器整体 margin-top:auto 吸底，两块内容
            永远贴在侧栏底部，位置稳定。 */}
        <div className="sentinel-side-bottom">
        {/* 侧栏底部：系统概览仪表盘
            原设计是三行纯文字，太普通。改为：
              ① 顶部小标题 + 分隔线
              ② 每项配图标 + 数值（等宽字体）
              ③ AI 占比用进度条可视化
              ④ 调度状态带脉冲灯
            动效克制——常亮的只有调度脉冲点，其余静态。 */}
        {!collapsed && (
          <div className="sentinel-side-foot">
            <div className="sentinel-side-foot-title">
              <span>系统概览</span>
              <span className="sentinel-side-foot-line" />
            </div>

            <div className="sentinel-stat-row">
              <span className="sentinel-stat-ico">
                <DatabaseOutlined />
              </span>
              <span className="sentinel-stat-label">情报总量</span>
              <b className="sentinel-stat-value">
                {kb?.vulnerabilities ?? '—'}
              </b>
            </div>

            <div className="sentinel-stat-row">
              <span className="sentinel-stat-ico sentinel-ico-cyan">
                <ThunderboltOutlined />
              </span>
              {/**
               * 标签写「已分类」而非「AI 相关」。
               * `ai_vulnerabilities` 实测 163，而库里 AI 相关条目有 2980 ——
               * 该字段只统计**已完成 AI 分类判定**的部分。
               * 叫「AI 相关」会让人以为系统只识别出 163 条，与事实差 18 倍。
               */}
              <span className="sentinel-stat-label">已分类</span>
              <b className="sentinel-stat-value sentinel-val-cyan">
                {kb?.ai_vulnerabilities ?? '—'}
              </b>
            </div>

            {/* 已分类 / 总量 的比例可视化 */}
            {kb && kb.vulnerabilities > 0 && (
              <div className="sentinel-ratio">
                <div className="sentinel-ratio-bar">
                  <span
                    style={{
                      width: `${Math.min(
                        100,
                        (kb.ai_vulnerabilities / kb.vulnerabilities) * 100,
                      )}%`,
                    }}
                  />
                </div>
                <span className="sentinel-ratio-text">
                  已分类占比{' '}
                  {((kb.ai_vulnerabilities / kb.vulnerabilities) * 100).toFixed(1)}%
                </span>
              </div>
            )}

            <div className="sentinel-stat-row sentinel-stat-row-last">
              <span className="sentinel-stat-ico sentinel-ico-amber">
                <ApiOutlined />
              </span>
              <span className="sentinel-stat-label">数据源</span>
              <b className="sentinel-stat-value">
                {collectorsRegistered ?? '—'}
              </b>
              {/* 调度状态：文案与圆点颜色一起跟随状态。
                  三态分别用 is-running / is-paused / is-off，
                  避免「文字写着已暂停、圆点还在绿闪」的矛盾。 */}
              <span
                className={
                  'sentinel-live-text ' +
                  (!health
                    ? 'is-off'
                    : !schedRunning
                      ? 'is-off'
                      : schedPaused
                        ? 'is-paused'
                        : 'is-running')
                }
              >
                {!health
                  ? '—'
                  : !schedRunning
                    ? '未启动'
                    : schedPaused
                      ? '已暂停'
                      : '采集中'}
              </span>
            </div>
          </div>
        )}

        {/* 主题开关：黑夜 ↔ 白天（白绿）。
            位置在「收起侧栏」正上方（用户指定），与折叠按钮同为
            胶囊玻璃风；收起侧栏时退化为圆形图标按钮。
            图标与文字显示的是「点击后将切换到」的目标主题。 */}
        <button
          type="button"
          className={
            'sentinel-theme-toggle' + (mode === 'dark' ? ' is-dark' : ' is-light')
          }
          onClick={toggle}
          role="switch"
          aria-checked={mode === 'light'}
          aria-label={mode === 'dark' ? '切换到白天主题' : '切换到黑夜主题'}
          title={mode === 'dark' ? '切换到白天主题' : '切换到黑夜主题'}
        >
          <span className="sentinel-theme-ico" aria-hidden="true">
            <SunOutlined className="ico-sun" />
            <MoonOutlined className="ico-moon" />
          </span>
          {!collapsed && (
            <span className="sentinel-theme-text">
              {mode === 'dark' ? '白天主题' : '黑夜主题'}
            </span>
          )}
        </button>

        {/* 侧栏折叠按钮（Uiverse 胶囊样式）
            结构：胶囊容器 + 圆形图标底座 + 文字
            动效：hover 时背景由墨绿转深、圆底由白转荧光绿并变黑字，
                  箭头做双图标斜向接力位移（同原版的 copy 动效逻辑，
                  但换成「收起/展开」两个方向的箭头） */}
        <button
          type="button"
          className="sentinel-collapse-btn"
          onClick={() => setCollapsed((v) => !v)}
          aria-label={collapsed ? '展开侧栏' : '收起侧栏'}
          aria-expanded={!collapsed}
        >
          <span className="sentinel-collapse-ico">
            {/* 两个箭头做接力位移：常态显示收起箭头，
                hover 时它滑出、展开箭头从对角滑入 */}
            <MenuFoldOutlined className="ico-fold" />
            <MenuUnfoldOutlined className="ico-unfold" />
          </span>
          {!collapsed && <span className="sentinel-collapse-text">收起侧栏</span>}
        </button>
        </div>
      </Sider>

      <Layout>
        <Header
          style={{
            padding: '0 20px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
        {/* 面包屑：借鉴 AEGIS 的 topbar 结构——
            「工作空间 / 当前页」两级 + 状态药丸，
            比原来孤零零一个页面标题更有空间感与方位感。 */}
        <div className="sentinel-page-title" style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          {(() => {
            const cur =
              NAV_ITEMS.find((i) => i.key === location.pathname) ?? NAV_ITEMS[0]
            const Icon = cur.Icon
            return (
              <>
                <span className="sentinel-breadcrumb-root">工作空间</span>
                <span className="sentinel-breadcrumb-slash">/</span>
                <span
                  key={location.pathname}
                  className="sentinel-page-icon"
                  aria-hidden="true"
                >
                  <Icon style={{ fontSize: 15 }} />
                </span>
                <span className="sentinel-breadcrumb-current">{cur.label}</span>
              </>
            )
          })()}
        </div>

          {/* 状态坞：顶栏右侧。包一层容器是为了和标题区明确分开，
              顶栏是 space-between，容器即「右对齐区」的边界。 */}
          <div className="sentinel-header-right">
          {/* 全局搜索入口：借鉴 AEGIS 的 ⌘K 搜索框样式。
              本系统暂不做全局弹窗搜索，点击直接进情报中心，
              那里有完整的筛选与搜索——按钮只做「视觉与导航」，不承诺假的搜索。 */}
          <button
            type="button"
            className="sentinel-global-search"
            onClick={() => navigate('/intel')}
            title="搜索漏洞、论文、技术与法规"
          >
            <SearchOutlined />
            <span>搜索情报</span>
            <kbd>⌘K</kbd>
          </button>
          {health && (
            <StatusDock
              items={[
                {
                  /* 手册 3.2 / 5.3：接口连不上时**保留旧快照**，
                     但必须显式告知「已失联」——否则顶栏会一直
                     显示几分钟前的「系统正常」，等于假状态。 */
                  label: healthStale
                    ? '已失联'
                    : healthy
                      ? '系统正常'
                      : '降级运行',
                  icon: healthStale || !healthy ? (
                    <WarningFilled />
                  ) : (
                    <CheckCircleFilled />
                  ),
                  tone: healthStale ? 'error' : healthy ? 'ok' : 'warn',
                  ...(healthStale
                    ? { title: '无法连接后端，以下为最后一次成功获取的状态' }
                    : {}),
                },
                {
                  label: `数据库 ${dbAvailable ? '在线' : '离线'}`,
                  icon: <DatabaseOutlined />,
                  tone: dbAvailable ? 'ok' : 'error',
                },
                {
                  label: `LLM ${
                    llmPrimaryAvailable
                      ? (llmPrimary?.model ?? '').replace('-Free', '') || '在线'
                      : '降级'
                  }`,
                  icon: <RobotOutlined />,
                  tone: llmPrimaryAvailable
                    ? 'ok'
                    : llmDegradedMode
                      ? 'warn'
                      : 'error',
                  /* 点击打开 LLM 设置 —— 用户要能在这里换成自己的 Key，
                     公共网关限流时不必去改 .env 文件。
                     title 同时作为 Tooltip 文案，让用户知道能点。*/
                  title: '点击设置自己的 API Key 与模型',
                  onClick: () => {
                    setLlmModalOpen(true);
                  },
                },
                {
                  /* 同侧栏：running 为 True 不代表正在采集（pause 后仍是 True），
                     必须看paused。真实「在采集」= running && !paused。 */
                  label: `调度 ${
                    !schedRunning
                      ? '未启动'
                      : schedPaused
                        ? '已暂停'
                        : '采集中'
                  } · ${collectorsRegistered ?? '—'} 源`,
                  icon: <ApiOutlined />,
                  tone: schedRunning && !schedPaused ? 'ok' : 'warn',
                },
              ]}
            />
          )}
          {/* 引擎健康（从侧栏移来，位次：右二= 时钟左侧）。
              回答的是「现在能不能放心用」——与旁边的数据库/LLM
              分项状态不同，这里是全局结论，所以放在它们之后收口。 */}
          <span
            className="sentinel-engine-chip"
            title={healthy ? '全部引擎在线' : '部分依赖不可用，功能可能降级'}
          >
            <i className={'dot' + (healthy ? '' : ' is-warn')} />
            {healthy ? '所有引擎运行正常' : '部分引擎降级'}
          </span>

          {/* 实时时钟：独立组件，自身每秒更新。
              放在最右端——视线扫完一排状态标签后落在时间上收尾。*/}
          <ClockChip />
          </div>
        </Header>

        {/* sentinel-content：View Transitions 的过渡主体。
            侧栏/顶栏/背景层显式 view-transition-name:none，
            切页时它们保持静止，只有内容区做推移。 */}
        <Content className="sentinel-content" style={{ padding: 16 }}>
          <Routes location={deferredLocation}>
            {/*系统内路由（带侧栏顶栏） */}
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/situation" element={<SituationPage />} />
            <Route path="/graph" element={<GraphPage />} />
            <Route path="/ops" element={<OpsPage />} />
            <Route path="/intel" element={<IntelPage />} />
            <Route path="/qa" element={<QAPage />} />
            <Route path="/extension" element={<ExtensionPage />} />
          </Routes>
        </Content>
      </Layout>

      {/* LLM 模型设置：点击顶栏的「LLM xxx」标签打开。
          保存后立即生效并刷新 health，顶栏标签会同步显示新模型名。*/}
      <LLMSettingsModal
        open={llmModalOpen}
        onClose={() => setLlmModalOpen(false)}
        onSaved={() => void refreshHealth()}
      />

      {/* 数据存储设置：应用内修改数据目录，重启程序后自动迁移 */}
      <DataLocationModal open={dataDirModalOpen} onClose={() => setDataDirModalOpen(false)} />
    </Layout>
  )
}

/**
 * 根路由分发
 *
 * 首屏 `/` 不放在 Shell 里 —— Shell 强制带侧栏与顶栏，
 * 而首屏要的是全屏无边框的光柱舞台。
 * 所以这里按路径决定是否套 Shell。
 */
function RootRoutes() {
  const location = useLocation()
  // 首屏：顶栏/侧栏都不要
  const isLanding = location.pathname === '/'
  if (isLanding) return <LandingPage />
  return <Shell />
}

/** 黑夜主题（默认）：AntD 暗色算法 + 翡翠绿主色。
 *  这套值自 SENTINEL 换肤起沿用至今——白天主题改造期间保持逐字不变，
 *  黑夜是全站基准，只许新增、不许改动。 */
const DARK_THEME: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    // 主色改用设计语言的翡翠绿
    colorPrimary: '#4edea3',
    colorInfo: '#4cd7f6',
    colorSuccess: '#3ddc97',
    colorWarning: '#ffc857',
    colorError: '#ff5c7a',
    colorBgBase: '#060807',
    colorBgContainer: 'rgba(17, 23, 20, 0.72)',
    colorBgElevated: '#0e1311',
    colorBorder: 'rgba(180, 220, 195, 0.09)',
    colorBorderSecondary: 'rgba(180, 220, 195, 0.06)',
    colorText: '#e9f0ea',
    colorTextSecondary: '#a7b3ab',
    colorTextTertiary: '#6b7870',
    // 圆角取设计语言的 r-md
    borderRadius: 13,
    fontSize: 14,
    fontFamily:
      '"Inter", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
  },
  components: {
    Layout: {
      headerBg: 'rgba(17, 23, 20, 0.72)',
      siderBg: 'rgba(17, 23, 20, 0.72)',
      bodyBg: 'transparent',
      headerHeight: 62,
    },
    Menu: {
      darkItemBg: 'transparent',
      darkSubMenuItemBg: 'transparent',
      darkItemSelectedBg: 'rgba(182, 243, 74, 0.10)',
      darkItemSelectedColor: '#b6f34a',
      darkItemColor: '#a7b3ab',
      darkItemHoverBg: 'rgba(255, 255, 255, 0.045)',
      itemMarginInline: 10,
      itemBorderRadius: 9,
    },
    Card: {
      colorBgContainer: 'rgba(17, 23, 20, 0.72)',
      headerBg: 'transparent',
      headerFontSize: 14,
    },
    Table: {
      headerBg: 'rgba(255, 255, 255, 0.028)',
      headerColor: '#6b7870',
      rowHoverBg: 'rgba(182, 243, 74, 0.045)',
      borderColor: 'rgba(180, 220, 195, 0.05)',
      cellPaddingBlock: 10,
    },
    Tag: {
      defaultBg: 'rgba(255, 255, 255, 0.05)',
      defaultColor: '#a7b3ab',
    },
  },
}

/** 白天主题（白绿）：亮色算法 + 深绿主色。
 *  与 sentinel-design-tokens.css 的 [data-theme="light"] 白绿变量配套；
 *  AntD 不认 CSS 变量，所以这里必须给第二套完整 token。
 *  侧栏 Menu 保持 theme="dark" 标记（自定义 CSS 层按 .ant-menu-dark
 *  选择器统一换肤），因此 Menu 的覆盖写在 dark* 系列上、按主题取值。 */
const LIGHT_THEME: ThemeConfig = {
  algorithm: theme.defaultAlgorithm,
  token: {
    colorPrimary: '#059669',
    colorInfo: '#0e7490',
    colorSuccess: '#047857',
    colorWarning: '#b45309',
    colorError: '#e11d48',
    /* 链接色：antd 默认蓝 (#1677ff) 与白绿主题不符（情报流卡片标题等） */
    colorLink: '#047d57',
    colorBgBase: '#f3f7f4',
    colorBgContainer: 'rgba(255, 255, 255, 0.78)',
    colorBgElevated: '#ffffff',
    colorBorder: 'rgba(23, 58, 43, 0.10)',
    colorBorderSecondary: 'rgba(23, 58, 43, 0.06)',
    colorText: '#12211a',
    colorTextSecondary: '#46584f',
    colorTextTertiary: '#8a9a91',
    borderRadius: 13,
    fontSize: 14,
    fontFamily:
      '"Inter", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
  },
  components: {
    Layout: {
      headerBg: 'rgba(255, 255, 255, 0.78)',
      siderBg: 'rgba(255, 255, 255, 0.78)',
      bodyBg: 'transparent',
      headerHeight: 62,
    },
    Menu: {
      darkItemBg: 'transparent',
      darkSubMenuItemBg: 'transparent',
      darkItemSelectedBg: 'rgba(5, 150, 105, 0.12)',
      darkItemSelectedColor: '#047857',
      darkItemColor: '#46584f',
      darkItemHoverBg: 'rgba(23, 58, 43, 0.06)',
      itemMarginInline: 10,
      itemBorderRadius: 9,
    },
    Card: {
      colorBgContainer: 'rgba(255, 255, 255, 0.78)',
      headerBg: 'transparent',
      headerFontSize: 14,
    },
    Table: {
      headerBg: 'rgba(23, 58, 43, 0.035)',
      headerColor: '#8a9a91',
      rowHoverBg: 'rgba(5, 150, 105, 0.05)',
      borderColor: 'rgba(23, 58, 43, 0.06)',
      cellPaddingBlock: 10,
    },
    Tag: {
      defaultBg: 'rgba(23, 58, 43, 0.05)',
      defaultColor: '#46584f',
    },
  },
}

export default function App() {
  /* 主题：黑夜（默认）/ 白天（白绿）——状态与持久化见 src/lib/theme.tsx。
     背景层是全站单例（main.tsx 副作用引入），不随路由重建；
     <html data-theme> 由 ThemeProvider 写入，背景脚本自行监听。 */
  const { mode } = useTheme()

  return (
    <ConfigProvider
      locale={zhCN}
      theme={mode === 'dark' ? DARK_THEME : LIGHT_THEME}
    >
      <BrowserRouter>
        {/* 手册 3.7：整站级错误边界。任一页面渲染异常时，
            这里给出可读提示与重新加载入口，而不是整页白屏。 */}
        <PageErrorBoundary area="系统界面">
          <RootRoutes />
        </PageErrorBoundary>
      </BrowserRouter>
    </ConfigProvider>
  )
}

/** 应用挂载入口 */
export function Root() {
  return (
    <Spin fullscreen tip="启动中…">
      <App />
    </Spin>
  )
}
