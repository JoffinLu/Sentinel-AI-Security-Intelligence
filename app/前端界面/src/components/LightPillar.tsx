/**
 * LightPillar — 光柱背景特效
 *
 * 基于 Uiverse 的 raymarch 着色器（MIT 思路重写，适配本项目）。
 * 用一束「光柱」做首屏主视觉，缓慢旋转呼吸。
 *
 * ---------- 与原版的差异（都是为本项目做的调整） ----------
 *
 * ① quality 默认 'medium' 而非 'high'。
 *    原版 high 是 80 次迭代 + 4 层波形 + pixelRatio 2，
 *    而本机**16 核CPU、无 GPU**（见项目记忆）——
 *    着色器全靠 CPU 跑，80 次迭代会直接把首屏拖到个位数帧率。
 *    medium 是 40 次迭代 + pixelRatio 0.65，实测可接受。
 *    真机有 GPU 时可传 quality="high"。
 *
 * ② 页面隐藏时暂停渲染。
 *    原版 rAF 一直在跑，切到后台标签页也照跑，白烧 CPU。
 *    这里监听 visibilitychange，离开时停rAF。
 *
 * ③ prefers-reduced-motion 时不启动动画。
 *    尊重系统级无障碍设置。
 *
 * ④ 无 WebGL 时降级为纯 CSS 渐变，不显示「WebGL not supported」英文。
 *    首屏没有后备方案会直接穿帮成一块黑。
 *
 * ⑤ 页面不可见时（首屏之外）由外部控制不渲染 —— 见 LandingPage 的用法。
 */

import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'

export interface LightPillarProps {
  /** 柱顶颜色 */
  topColor?: string
  /** 柱底颜色 */
  bottomColor?: string
  /** 整体强度 */
  intensity?: number
  /** 旋转速度（越大转得越快） */
  rotationSpeed?: number
  /**辉光量：越大越亮，过大会糊成一片 */
  glowAmount?: number
  /** 柱宽度 */
  pillarWidth?: number
  /** 柱高度 */
  pillarHeight?: number
  /** 噪点强度：用于打散色带smoothstep 的边缘 */
  noiseIntensity?: number
  /** 柱体自身旋转角度（度） */
  pillarRotation?: number
  /** 是否跟随鼠标 */
  interactive?: boolean
  /** 混合模式，'screen' 让光柱与下层叠加而不遮挡。
   *  用CSSProperties 的 MixBlendMode 类型约束，避免传非法值。*/
  mixBlendMode?: React.CSSProperties['mixBlendMode']
  /** 画质档位 */
  quality?: 'low' | 'medium' | 'high'
  /** 亮色模式（用于浅色背景） */
  lightMode?: boolean
  className?: string
}

export default function LightPillar({
  topColor = '#5227FF',
  bottomColor = '#FF9FFC',
  intensity = 1.0,
  rotationSpeed = 0.3,
  interactive = false,
  className = '',
  glowAmount = 0.005,
  pillarWidth = 3.0,
  pillarHeight = 0.4,
  noiseIntensity = 0.5,
  mixBlendMode = 'screen',
  pillarRotation = 0,
  quality = 'medium',
  lightMode = false,
}: LightPillarProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const rafRef = useRef<number | null>(null)
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null)
  const materialRef = useRef<THREE.ShaderMaterial | null>(null)
  const sceneRef = useRef<THREE.Scene | null>(null)
  const cameraRef = useRef<THREE.OrthographicCamera | null>(null)
  const geometryRef = useRef<THREE.PlaneGeometry | null>(null)
  const mouseRef = useRef(new THREE.Vector2(0, 0))
  const timeRef = useRef(0)
  const rotationSpeedRef = useRef(rotationSpeed)
  const [webGLSupported, setWebGLSupported] = useState(true)

  // 参数变化时同步给 uniform，避免重建 WebGL 上下文。
  // （重建上下文会闪一下，且在无 GPU 机器上代价很高）
  useEffect(() => {
    rotationSpeedRef.current = rotationSpeed
  }, [rotationSpeed])

  // 只处理「已创建后改 uniform」的场景
  useEffect(() => {
    const m = materialRef.current
    if (!m) return
    const toVec3 = (hex: string) => {
      const c = new THREE.Color(hex)
      return new THREE.Vector3(c.r, c.g, c.b)
    }
    m.uniforms.uTopColor.value = toVec3(topColor)
    m.uniforms.uBottomColor.value = toVec3(bottomColor)
    m.uniforms.uIntensity.value = intensity
    m.uniforms.uInteractive.value = interactive
    m.uniforms.uGlowAmount.value = glowAmount
    m.uniforms.uPillarWidth.value = pillarWidth
    m.uniforms.uPillarHeight.value = pillarHeight
    m.uniforms.uNoiseIntensity.value = noiseIntensity
    m.uniforms.uLightMode.value = lightMode ? 1 : 0
    const rad = (pillarRotation * Math.PI) / 180
    m.uniforms.uPillarRotCos.value = Math.cos(rad)
    m.uniforms.uPillarRotSin.value = Math.sin(rad)
  }, [
    topColor, bottomColor, intensity, interactive, glowAmount,
    pillarWidth, pillarHeight, noiseIntensity, lightMode, pillarRotation,
  ])

  // ---- WebGL 支持性探测（只探测一次）----
  useEffect(() => {
    const canvas = document.createElement('canvas')
    const gl =
      canvas.getContext('webgl') || canvas.getContext('experimental-webgl')
    if (!gl) setWebGLSupported(false)
  }, [])

  useEffect(() => {
    if (!containerRef.current || !webGLSupported) return

    const container = containerRef.current
    const width = container.clientWidth || 1
    const height = container.clientHeight || 1

    const scene = new THREE.Scene
    sceneRef.current = scene
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1)
    cameraRef.current = camera

    /* 画质档位
     * 迭代次数是本着色器唯一的性能开关 —— 主循环每像素跑 MAX_ITER 次，
     * 80 → 40 直接砍掉一半 CPU 占用。
     *
     * pixelRatio 同样影响**清晰度**而不只是性能：< 1 的值是「低分辨率渲染
     * 再放大」，光柱的边缘与色带会明显发虚（用户反馈「感觉很模糊」）。
     * medium 从 0.65提到 **0.9** —— 只放大 10%，肉眼几乎看不出模糊，
     * 相比 0.65 的像素量仅多 1.9倍，但比 high 的 2.0 仍省一半。 */
    const qualitySettings = {
      low: {
        iterations: 24,
        waveIterations: 1,
        pixelRatio: 0.7,
        precision: 'mediump',
        stepMultiplier: 1.5,
      },
      medium: {
        iterations: 40,
        waveIterations: 2,
        pixelRatio: 0.9,
        precision: 'mediump',
        stepMultiplier: 1.2,
      },
      high: {
        iterations: 80,
        waveIterations: 4,
        pixelRatio: Math.min(window.devicePixelRatio, 2),
        precision: 'highp',
        stepMultiplier: 1.0,
      },
    } as const

    const settings = qualitySettings[quality] ?? qualitySettings.medium

    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: false,
        alpha: true,
        powerPreference: 'low-power',
        precision: settings.precision,
        stencil: false,
        depth: false,
      })
    } catch {
      setWebGLSupported(false)
      return
    }

    renderer.setSize(width, height)
    renderer.setPixelRatio(settings.pixelRatio)
    container.appendChild(renderer.domElement)
    rendererRef.current = renderer

    const parseColor = (hex: string) => {
      const color = new THREE.Color(hex)
      return new THREE.Vector3(color.r, color.g, color.b)
    }

    const vertexShader = `
      varying vec2 vUv;
      void main() {
        vUv = uv;
        gl_Position = vec4(position, 1.0);
      }
    `

    // raymarch：沿视线推进，逐步逼近光柱曲面并累加发光
    const fragmentShader = `
      precision ${settings.precision} float;

      uniform float uTime;
      uniform vec2 uResolution;
      uniform vec2 uMouse;
      uniform vec3 uTopColor;
      uniform vec3 uBottomColor;
      uniform float uIntensity;
      uniform bool uInteractive;
      uniform float uGlowAmount;
      uniform float uPillarWidth;
      uniform float uPillarHeight;
      uniform float uNoiseIntensity;
      uniform float uLightMode;
      uniform float uRotCos;
      uniform float uRotSin;
      uniform float uPillarRotCos;
      uniform float uPillarRotSin;
      uniform float uWaveSin;
      uniform float uWaveCos;
      varying vec2 vUv;

      const float STEP_MULT = ${settings.stepMultiplier.toFixed(1)};
      const int MAX_ITER = ${settings.iterations};
      const int WAVE_ITER = ${settings.waveIterations};

      void main() {
        vec2 uv = (vUv * 2.0 - 1.0) * vec2(uResolution.x / uResolution.y, 1.0);
        uv = vec2(uPillarRotCos * uv.x - uPillarRotSin * uv.y, uPillarRotSin * uv.x + uPillarRotCos * uv.y);

        vec3 ro = vec3(0.0, 0.0, -10.0);
        vec3 rd = normalize(vec3(uv, 1.0));

        float rotC = uRotCos;
        float rotS = uRotSin;
        if (uInteractive && (uMouse.x != 0.0 || uMouse.y != 0.0)) {
          float a = uMouse.x * 6.283185;
          rotC = cos(a);
          rotS = sin(a);
        }

        vec3 col = vec3(0.0);
        float t = 0.1;

        for (int i = 0; i < MAX_ITER; i++) {
          vec3 p = ro + rd * t;
          p.xz = vec2(rotC * p.x - rotS * p.z, rotS * p.x + rotC * p.z);

          vec3 q = p;
          q.y = p.y * uPillarHeight + uTime;

          float freq = 1.0;
          float amp = 1.0;
          for (int j = 0; j < WAVE_ITER; j++) {
            q.xz = vec2(uWaveCos * q.x - uWaveSin * q.z, uWaveSin * q.x + uWaveCos * q.z);
            q += cos(q.zxy * freq - uTime * float(j) * 2.0) * amp;
            freq *= 2.0;
            amp *= 0.5;
          }

          float d = length(cos(q.xz)) - 0.2;
          float bound = length(p.xz) - uPillarWidth;
          float k = 4.0;
          float h = max(k - abs(d - bound), 0.0);
          d = max(d, bound) + h * h * 0.0625 / k;
          d = abs(d) * 0.15 + 0.01;

          float grad = clamp((15.0 - p.y) / 30.0, 0.0, 1.0);
          col += mix(uBottomColor, uTopColor, grad) / d;

          t += d * STEP_MULT;
          if (t > 50.0) break;
        }

        float widthNorm = uPillarWidth / 3.0;
        col = tanh(col * uGlowAmount / widthNorm);

        col -= fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453) / 15.0 * uNoiseIntensity;

        vec3 result = clamp(col * uIntensity, 0.0, 1.0);
        if (uLightMode > 0.5) {
          float energy = max(result.r, max(result.g, result.b));
          vec3 hue = result / max(energy, 0.001);
          float coverage = smoothstep(0.025, 0.95, energy);
          hue = pow(clamp(hue, 0.0, 1.0), vec3(1.25));
          result = mix(vec3(1.0), hue, coverage * 0.94);
        }
        gl_FragColor = vec4(result, 1.0);
      }
    `

    const pillarRotRad = (pillarRotation * Math.PI) / 180
    const waveSin = Math.sin(0.4)
    const waveCos = Math.cos(0.4)

    const material = new THREE.ShaderMaterial({
      vertexShader,
      fragmentShader,
      uniforms: {
        uTime: { value: 0 },
        uResolution: { value: new THREE.Vector2(width, height) },
        uMouse: { value: mouseRef.current },
        uTopColor: { value: parseColor(topColor) },
        uBottomColor: { value: parseColor(bottomColor) },
        uIntensity: { value: intensity },
        uInteractive: { value: interactive },
        uGlowAmount: { value: glowAmount },
        uPillarWidth: { value: pillarWidth },
        uPillarHeight: { value: pillarHeight },
        uNoiseIntensity: { value: noiseIntensity },
        uLightMode: { value: lightMode ? 1 : 0 },
        uRotCos: { value: 1.0 },
        uRotSin: { value: 0.0 },
        uPillarRotCos: { value: Math.cos(pillarRotRad) },
        uPillarRotSin: { value: Math.sin(pillarRotRad) },
        uWaveSin: { value: waveSin },
        uWaveCos: { value: waveCos },
      },
      transparent: true,
      depthWrite: false,
      depthTest: false,
    })
    materialRef.current = material

    const geometry = new THREE.PlaneGeometry(2, 2)
    geometryRef.current = geometry
    const mesh = new THREE.Mesh(geometry, material)
    scene.add(mesh)

    let mouseMoveTimeout: number | null = null
    const handleMouseMove = (event: MouseEvent) => {
      if (!interactive) return
      if (mouseMoveTimeout) return
      mouseMoveTimeout = window.setTimeout(() => {
        mouseMoveTimeout = null
      }, 16)
      const rect = container.getBoundingClientRect()
      const x = ((event.clientX - rect.left) / rect.width) * 2 - 1
      const y = -((event.clientY - rect.top) / rect.height) * 2 + 1
      mouseRef.current.set(x, y)
    }
    if (interactive) {
      container.addEventListener('mousemove', handleMouseMove, { passive: true })
    }

    /* 动画循环
     * 帧率节流到 30fps：这个着色器是纯 CPU 的光栅化，
     * 60fps 只会让每帧计算量翻倍而视觉上看不出差别
     * （光柱旋转极慢，30fps 完全够）。 */
    const targetFPS = 30
    const frameTime = 1000 / targetFPS
    let lastTime = performance.now()
    let running = true

    const animate = (currentTime: number) => {
      if (!materialRef.current || !rendererRef.current ||
          !sceneRef.current || !cameraRef.current) return

      const deltaTime = currentTime - lastTime
      if (deltaTime >= frameTime) {
        timeRef.current += 0.016 * rotationSpeedRef.current
        const t = timeRef.current
        materialRef.current.uniforms.uTime.value = t
        materialRef.current.uniforms.uRotCos.value = Math.cos(t * 0.3)
        materialRef.current.uniforms.uRotSin.value = Math.sin(t * 0.3)
        rendererRef.current.render(sceneRef.current, cameraRef.current)
        lastTime = currentTime - (deltaTime % frameTime)
      }
      rafRef.current = requestAnimationFrame(animate)
    }

    /* 页面隐藏时停渲染 —— 首屏光柱是常驻动画，
     * 用户切到别的标签页时继续跑纯属烧CPU。 */
    const handleVisibility = () => {
      if (document.hidden) {
        running = false
        if (rafRef.current) {
          cancelAnimationFrame(rafRef.current)
          rafRef.current = null
        }
      } else if (!running) {
        running = true
        lastTime = performance.now()
        rafRef.current = requestAnimationFrame(animate)
      }
    }

    /* 尊重系统的「减少动态效果」偏好。
     * 此时渲染一帧静态画面即可，完全不启动 rAF。 */
    const prefersReduced = window.matchMedia?.(
      '(prefers-reduced-motion: reduce)',
    ).matches

    if (prefersReduced) {
      // 静态帧：给一个固定的 uTime，让光柱呈自然形态
      timeRef.current = 2.0
      material.uniforms.uTime.value = 2.0
      renderer.render(scene, camera)
    } else {
      rafRef.current = requestAnimationFrame(animate)
      document.addEventListener('visibilitychange', handleVisibility)
    }

    let resizeTimeout: number | null = null
    const handleResize = () => {
      if (resizeTimeout) clearTimeout(resizeTimeout)
      resizeTimeout = window.setTimeout(() => {
        if (!rendererRef.current || !materialRef.current || !containerRef.current) return
        const nw = containerRef.current.clientWidth
        const nh = containerRef.current.clientHeight
        if (!nw || !nh) return
        rendererRef.current.setSize(nw, nh)
        materialRef.current.uniforms.uResolution.value.set(nw, nh)
      }, 150)
    }
    window.addEventListener('resize', handleResize, { passive: true })

    return () => {
      window.removeEventListener('resize', handleResize)
      document.removeEventListener('visibilitychange', handleVisibility)
      if (interactive) {
        container.removeEventListener('mousemove', handleMouseMove)
      }
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
      const r = rendererRef.current
      if (r) {
        r.dispose
        r.forceContextLoss
        if (container.contains(r.domElement)) {
          container.removeChild(r.domElement)
        }
      }
      materialRef.current?.dispose
      geometryRef.current?.dispose
      rendererRef.current = null
      materialRef.current = null
      sceneRef.current = null
      cameraRef.current = null
      geometryRef.current = null
    }
  }, [webGLSupported, quality])

  /* 无 WebGL 时的降级。
   * 原文案是英文「WebGL not supported」，直接放首屏会穿帮。
   * 改为纯 CSS 径向渐变 —— 保住暗绿氛围，不出现死板灰块。 */
  if (!webGLSupported) {
    return (
      <div className={`light-pillar-fallback ${className}`} aria-hidden>
        <span className="light-pillar-fallback-glow" />
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className={`light-pillar-container ${className}`}
      style={{ mixBlendMode }}
      aria-hidden
    />
  )
}