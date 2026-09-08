import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // next-themes 显式加入预构建：Vite 8 下新装依赖未预构建时，其裸导入会在首屏加载时序里拿不到
  optimizeDeps: {
    include: ['next-themes'],
  },
  resolve: {
    dedupe: ['react', 'react-dom'],
  },
  server: {
    port: 5273,
    proxy: {
      '/api': 'http://127.0.0.1:8800',
      '/ws': { target: 'ws://127.0.0.1:8800', ws: true },
    },
  },
})
