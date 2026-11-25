import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import { ThemeProvider } from './lib/theme'

// ============================================================
// SENTINEL 设计语言 · 样式加载顺序
// ------------------------------------------------------------
// 依赖是链式的，顺序不能调换：
//   tokens  → effects 依赖 tokens 的变量
//   effects → theme    依赖 tokens/effects 的变量与类
//   theme   → global   依赖 tokens 的字体与背景色
// 背景脚本最后加载（它注入的 DOM 需要前面的变量已就绪）
// ============================================================

// 1. 设计 Token：配色 / 字体 / 圆角 / 缓动 / 时长
import './styles/sentinel/sentinel-design-tokens.css'
// 2. 视觉效果：玻璃拟态 / 辉光 / 入场动效 / 极光光斑动画
import './styles/sentinel/sentinel-effects.css'
// 3. AntD 换肤适配层（把设计语言接到 antd 组件体系）
import './styles/sentinel-theme.css'
// 4. 全局基础重置
import './styles/global.css'
// 5. 首屏主页样式（LightPillar 容器 + 首屏版式）
//    放在 global 之后：它要覆盖 global 的基础字号/行高
import './styles/landing.css'
// 6. 极光背景层（WebGL 着色器 + 漂移光斑），脚本自执行注入 DOM
import './styles/sentinel/sentinel-bg-react.js'
// 7. 交互动效（鼠标追踪柔光 / 点击涟漪 / 数字 pop / 滚动入场）
import './styles/sentinel/sentinel-fx.js'
// 8. 内联色变量桥接：把组件 style={{}} 里写死的颜色接进主题变量
//    （放在最后：只定义变量，不产生样式，顺序不敏感）
import './styles/inline-theme.css'

const container = document.getElementById('root')
if (!container) throw new Error('未找到挂载节点 #root')

createRoot(container).render(
  <StrictMode>
    {/* 主题状态包在 App 外层：App 内的 ConfigProvider 要按主题取配置 */}
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </StrictMode>,
)
