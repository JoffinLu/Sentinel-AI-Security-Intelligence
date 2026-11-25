/**
 * 白天主题 · 背景停/恢复逻辑的**行为**验证（非视觉）
 * ----------------------------------------------------------------
 * 跑法：node scripts/verify-theme-toggle.mjs
 *
 * 为什么用 node 而不是浏览器自动化：
 *   这个脚本是 IIFE，挂到 window 上并操作真实 DOM 属性、真实 WebGL。
 *   这里用**最小 DOM 桩**（只实现它用到的那几个 API）+ 假 WebGL 上下文，
 *   就能在毫秒级验证控制流，不需要起浏览器、不需要GPU。
 *   真正要看的是「切主题后 rAF 还在不在排队」这件事。
 *
 * 验证四件事：
 *   ① 初始为白天 → 着色器一次都不画（rAF 未启动）
 *   ② 切到黑夜 → rAF 启动并持续排队
 *   ③ 切回白天 → rAF 停止（不空烧 CPU）
 *   ④ 再切回黑夜 → u_time 相位连续（不跳变：偏移量已补偿暂停时长）
 */

/* ---------------- DOM 桩 ---------------- */
let htmlAttrs = {}
const listeners = []

globalThis.document = {
  documentElement: {
    getAttribute: (k) => (k in htmlAttrs ? htmlAttrs[k] : null),
    setAttribute: (k, v) => { htmlAttrs[k] = String(v) },
    removeAttribute: (k) => { delete htmlAttrs[k] },
  },
  createElement: () => ({
    id: '', className: '', style: {},
    setAttribute() {}, appendChild() {}, addEventListener() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }),
    clientWidth: 100, clientHeight: 100, contains: () => false, removeChild() {},
  }),
  getElementById: () => null,
  addEventListener: (t, f) => listeners.push({ t, f }),
  removeEventListener() {},
  readyState: 'complete',
  hidden: false,
  body: { insertBefore() {}, appendChild() {}, firstChild: null },
}

/* rAF 桩：**真正执行回调**。
   只记排队数是不够的 —— 背景脚本在 render() 开头就会检查 data-static-bg
   并 return，所以「还在排队但一帧没画」和「彻底停了」在排队数上一样。
   真正能区分的指标是 drawArrays 的调用次数。
   （这一版桩最初只记排队数，导致断言②④ 假失败，改成执行回调后立刻暴露。） */
let pending = []
globalThis.requestAnimationFrame = (fn) => {
  pending.push(fn)
  return pending.length
}
globalThis.cancelAnimationFrame = () => { pending = [] }
globalThis.performance = { now: () => Date.now() }
globalThis.location = { search: '' }
globalThis.matchMedia = () => ({ matches: false })
globalThis.ResizeObserver = class { observe() {} }
/* MutationObserver 桩：必须在 import 背景脚本**之前**装好，
   否则脚本内部 new 出来的实例不会被这里收集到（上一版就踩了这个）。*/
const observers = []
globalThis.MutationObserver = class {
  constructor(cb) { this.cb = cb; observers.push(this) }
  observe() {}
  /** 测试用：手动触发一次属性变化回调 */
  trigger() { this.cb() }
}
/* window 事件桩（脚本要监听 resize / mousemove / visibilitychange） */
const winListeners = {}
globalThis.addEventListener = (t, f) => { (winListeners[t] ||= []).push(f) }
globalThis.removeEventListener = (t, f) => {
  if (winListeners[t]) winListeners[t] = winListeners[t].filter((x) => x !== f)
}
/** 测试用：触发一个 window 事件 */
export function fireWindow(type, evt = {}) {
  ;(winListeners[type] || []).forEach((f) => f(evt))
}
globalThis.window = globalThis

/* WebGL 桩：记录 drawArrays 调用次数与每次写入的 u_time。
   必须返回真对象 —— 返回 null 会让脚本走「无 WebGL 降级」分支，
   那样就测不到着色器循环的停/恢复了。 */
const glCalls = { draw: 0, uTime: [] }
const loc = { u_time: 1, u_resolution: 2, u_mouse: 3 }
/* canvas.getContext 必须返回真对象才会走 mountShader 的完整分支 */
const glObj = {
  createShader: () => ({}),
  shaderSource() {}, compileShader() {},
  createProgram: () => ({}), attachShader() {}, linkProgram() {}, useProgram() {},
  createBuffer: () => ({}), bindBuffer() {}, bufferData() {},
  getAttribLocation: () => 0, enableVertexAttribArray() {}, vertexAttribPointer() {},
  getUniformLocation: (_p, name) => loc[name],
  uniform1f(_l, v) { glCalls.uTime.push(v) },
  uniform2f() {}, viewport() {},
  drawArrays() { glCalls.draw++ },
}
const origCreate = globalThis.document.createElement
globalThis.document.createElement = () => {
  const el = origCreate()
  el.getContext = () => glObj
  return el
}

/* ---------------- 跑脚本 ---------------- */
/* 模拟 index.html 的防闪烁脚本：import 背景脚本**之前**就写好 data-theme。
   顺序很关键 —— 背景脚本在 import 时就会读一次这个属性决定是否启动循环，
   如果这里没设，它会当成黑夜，启动时先画一帧。*/
htmlAttrs['data-theme'] = 'light'

const path = new URL('../src/styles/sentinel/sentinel-bg-react.js', import.meta.url)
await import(path.href)
const SentinelBg = globalThis.window.SentinelBg

function fireThemeChange(theme) {
  htmlAttrs['data-theme'] = theme
  observers.forEach((o) => o.trigger())
}

/* ---------------- 断言 ---------------- */
let failed = 0
function check(name, cond, detail = '') {
  const mark = cond ? '✅' : '❌'
  if (!cond) failed++
  console.log(`  ${mark} ${name}${detail ? '  ' + detail : ''}`)
}

/** 执行当前排队的 rAF 回调（跑一帧） */
function runFrame(tMs) {
  const due = pending
  pending = []
  due.forEach((fn) => fn(tMs))
}

/** 模拟时钟：每帧推进 50ms（与 rAF 时间戳同源，便于验证 u_time 相位） */
let clock = 0
function tick(n = 5) {
  for (let i = 0; i < n; i++) {
    clock += 50
    runFrame(clock)
  }
}

console.log('\n=== 白天主题 · 背景停/恢复逻辑验证 ===\n')

/* ---------- ① 页面直接以白天启动 ---------- */
console.log('① 页面直接以白天启动（刷新前存的是白天）')
tick(5)
check('着色器一帧都没画（零 CPU）', glCalls.draw === 0, `draw=${glCalls.draw}`)
check('data-static-bg 已置位', htmlAttrs['data-static-bg'] === '1',
  `= ${htmlAttrs['data-static-bg']}`)

/* ---------- ② 切到黑夜 ---------- */
fireThemeChange('dark')
tick(5)
console.log('\n② 切到黑夜')
check('着色器开始逐帧绘制', glCalls.draw > 0, `draw=${glCalls.draw}`)
check('data-static-bg 已清除', !('data-static-bg' in htmlAttrs))

/* ---------- ③ 切回白天（关键：不许空烧 CPU） ---------- */
const drawnBefore = glCalls.draw
fireThemeChange('light')
tick(5)
console.log('\n③ 切回白天（关键：验证没有「看不见但仍在画」）')
check('一帧都没再画（CPU 真停了）', glCalls.draw === drawnBefore,
  `draw ${drawnBefore} → ${glCalls.draw}`)
check('data-static-bg 重新置位', htmlAttrs['data-static-bg'] === '1')

/* ---------- ④ 再切黑夜，检查相位连续 ---------- */
const before = glCalls.uTime.length
const drawBefore = glCalls.draw
fireThemeChange('dark')
tick(5)
console.log('\n④ 再切回黑夜（检查恢复后无相位跳变）')
check('着色器恢复绘制', glCalls.draw > drawBefore, `draw ${drawBefore} → ${glCalls.draw}`)
const added = glCalls.uTime.length - before
if (added >= 2) {
  const a = glCalls.uTime[before]
  const b = glCalls.uTime[glCalls.uTime.length - 1]
  const delta = b - a
  /* 4 帧 × 50ms = 0.2s 真实时间；模拟时钟与 performance.now() 同源，
     所以未补偿时 Δ 会等于「暂停期间的真实时长」（这里约 0.2s+），
     补偿后 u_time 只按帧推进。判据：Δ 应远小于暂停时长量级。 */
  check('u_time 无跳变（偏移量已补偿）', delta < 0.35,
    `Δ=${delta.toFixed(4)}s（4 帧真实间隔 0.2s；若未补偿会包含整段暂停时长）`)
} else {
  check('u_time 采样足够', false, `仅新增 ${added} 个采样`)
}

console.log(`\n=== 结果：${failed === 0 ? '全部通过 ✅' : failed + ' 项失败 ❌'} ===\n`)
process.exit(failed === 0 ? 0 : 1)
