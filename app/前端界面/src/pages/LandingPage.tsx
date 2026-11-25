/**
 * 首屏主页（Landing）
 *
 * 系统的门面：一束光柱做主视觉，配系统名、简介、能力标签与开始按钮。
 * 点「开始」进入监控看板。
 *
 * ---------- 设计取舍 ----------
 *
 * ① **光柱在内容后面，不做遮罩**
 *    `mixBlendMode: screen` 让光柱与深底叠加成辉光，
 *    不加半透明遮罩层 —— 遮罩会把光柱压灰，失去通透感。
 *    文字可读性靠 `text-shadow` 保证（见 CSS 里的 landing-title）。
 *
 * ② **首屏不挂 sentinel-bg背景层**
 *    App.tsx 里那套极光背景层 + 三团 blur(120px) 光斑是常驻动画，
 *    与光柱叠加会让 CPU 直接吃满（无 GPU 机器）。
 *    因此主页通过 `landing-root` 标记让 CSS 把背景层藏起来。
 *
 * ③ **首屏不发任何请求**
 *    能力标签（CAPABILITIES）是静态文案，不做接口自检，
 *    也不显示「9 类数据源 / N 条情报」这类数字 ——
 *    口径易错、与看板重复，且会让首屏多一个失败点。
 *    进系统后立刻会拉数据，首屏只负责立住视觉。
 *
 * ④ **动效克制**
 *    只有光柱在动，内容区是静态的入场上浮。
 *    首屏的视觉焦点应该是「光柱 + 标题」，不是一堆动效。
 */

import { useNavigate } from 'react-router-dom'
import { ArrowRightOutlined } from '@ant-design/icons'
import { SentinelLogo } from '@/components/SentinelLogo'
import LightPillar from '@/components/LightPillar'
import { useTheme } from '@/lib/theme'
import { Ticker } from './Ticker'

/** 能力标签：图标 + 名称 + 一句说明 */
const CAPABILITIES = [
  {
    key: 'collect',
    label: '多源采集',
    desc: '9 类数据源自动轮询',
    icon: '◈',
  },
  {
    key: 'enrich',
    label: '智能富化',
    desc: '7 维深度富化',
    icon: '◉',
  },
  {
    key: 'qa',
    label: '知识问答',
    desc: '混合检索 + 多跳推理',
    icon: '◆',
  },
  {
    key: 'ops',
    label: '可观测',
    desc: '调度告警 + 一键运维',
    icon: '◇',
  },
] as const

/**
 * 光柱画质档位
 *
 * 用 URL 参数 `?pillar=high` 可临时切到high画质 —— 答辩现场的机器
 * 若有独显（哪怕是较新的核显），high 会明显更锐利。
 * 默认仍是 medium：本机 16 核 CPU、无GPU，
 * 实测 high（80 次迭代 + pixelRatio 2）的计算量是 medium 的 **9.9 倍**
 * （每帧 6.6 亿次片元光栅化，纯CPU），会直接把首屏拖死。
 */
const PILLAR_QUALITY =
  new URLSearchParams(window.location.search).get('pillar') === 'high'
    ? 'high'
    : 'medium'

export default function LandingPage() {
  const navigate = useNavigate()
  /** 主题：白天不挂动态主视觉（见下方渲染处的方案 B1） */
  const { mode } = useTheme()

  /* 首屏不请求任何接口 —— 视觉为主，且进系统后立即就会拉数据。
     （曾短暂展示过数据规模条，但口径易错且与看板重复，已按用户要求移除）*/

  return (
    <div className="landing-root">
      {/* ---- 主视觉 ----
           黑夜：WebGL 光柱（LightPillar，three.js raymarch）
           白天：**不渲染任何动态背景**（用户选定方案 B1），
                 改用静态 CSS 光晕——纯装饰、零动画、零 CPU。
           注意 LightPillar 有 lightMode 参数，但那是给浅色底调整
           着色器输出的；这里选择干脆不挂组件，白天连 WebGL 上下文
           都不创建，省掉本机（无 GPU）最重的那部分开销。*/}
      {mode === 'dark' ? (
        <>
          <LightPillar
            topColor="#5533dd"
            bottomColor="#61d280"
            intensity={1.5}
            rotationSpeed={0.6}
            glowAmount={0.002}
            pillarWidth={3.9}
            pillarHeight={0.2}
            noiseIntensity={0.5}
            pillarRotation={66}
            interactive={false}
            mixBlendMode="screen"
            quality={PILLAR_QUALITY}
          />
        </>
      ) : (
        <div className="landing-static-halo" aria-hidden />
      )}

      {/* 上下柔化：让光柱与视口边缘自然衔接，不出现硬切 */}
      <div className="landing-fade landing-fade-top" aria-hidden />
      <div className="landing-fade landing-fade-bottom" aria-hidden />

      <div className="landing-content">
        {/* ---- 品牌区 ----
             Logo 放大到 92px：原先 64px 相对标题偏小，
             视觉上像"图标挂在大标题下"而非"品牌标识"。*/}
        <div className="landing-brand">
          <SentinelLogo size={92} animated title="SENTINEL 哨兵标志" />
          <div className="landing-brand-text">
            <h1 className="landing-title">AI 安全知识情报平台</h1>
            <p className="landing-subtitle">
              SENTINEL · 智能体驱动的 AI 安全情报中枢
            </p>
          </div>
        </div>

        {/* ---- 简介 ----
             去掉卡片底色，改为纯文字 + 上下细线分隔。
             原来是"文字装在盒子里"，与下方的能力卡形成两层盒子，
             视觉上碎且显小。放开后与标题连成一整块呼吸区。*/}
        <div className="landing-intro-wrap">
          <span className="landing-rule" aria-hidden />
          <p className="landing-intro">
            汇聚漏洞库、攻击技术库、学术论文与合规法规，
            由智能体自动完成采集、富化与关联，
            以知识底座支撑安全问答与处置决策。
          </p>
          <span className="landing-rule" aria-hidden />
        </div>

        {/* ---- 最新情报滚动条 ---- */}
        <Ticker />

        {/* ---- 能力标签 ---- */}
        <ul className="landing-caps">
          {CAPABILITIES.map((c) => (
            <li key={c.key} className="landing-cap">
              <span className="landing-cap-ico" aria-hidden>
                {c.icon}
              </span>
              <span className="landing-cap-body">
                <b>{c.label}</b>
                <em>{c.desc}</em>
              </span>
            </li>
          ))}
        </ul>

        {/* ---- 开始按钮 ---- */}
        <button
          type="button"
          className="landing-cta"
          onClick={() => navigate('/dashboard')}
        >
          <span className="landing-cta-label">进入系统</span>
          <ArrowRightOutlined className="landing-cta-arrow" />
          {/* 按钮底部的横向扫光，hover 时掠过 */}
          <span className="landing-cta-sweep" aria-hidden />
        </button>
      </div>
    </div>
  )
}