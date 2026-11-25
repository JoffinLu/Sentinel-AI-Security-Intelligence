import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      // 开发环境将 API 请求代理到后端，避免跨域
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/metrics': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        // 拆分大体积依赖，优化首屏加载
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          antd: ['antd'],
          echarts: ['echarts', 'echarts-for-react'],
          // three 仅首屏光柱用到（约 470KB）。
          // 必须单独切chunk —— 否则会被合进主包，
          // 导致「进系统也要先下载 three」，首屏之外全在 wasted bandwidth。
          three: ['three'],
        },
      },
    },
  },
})
