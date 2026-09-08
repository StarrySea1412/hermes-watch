import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ThemeProvider } from 'next-themes'
import './index.css'
import App from './App.tsx'

// PWA 可安装性（仅生产构建注册；SW 只透传不缓存，面板数据必须强实时）
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* 注册失败不影响使用 */ })
  })
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* defaultTheme="dark" 保持深海夜色为默认；enableSystem 让"跟随系统"可选中 */}
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem disableTransitionOnChange>
      <App />
    </ThemeProvider>
  </StrictMode>,
)
