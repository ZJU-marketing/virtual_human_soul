import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5273,
    proxy: {
      // 前端开发代理 → FastAPI (避免 CORS/SSE 跨域问题)
      '/api': { target: 'http://127.0.0.1:8300', changeOrigin: true },
    },
  },
})
