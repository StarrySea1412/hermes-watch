import { lazy, Suspense, useEffect, useState } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { api } from './api'
import { useT } from './i18n'
import { Layout } from './ui'
import { ErrorBoundary } from './ErrorBoundary'
import Login from './pages/Login'

// 路由级代码分割：每页独立 chunk，echarts 只跟随用到图表的页面异步加载
const Fleet = lazy(() => import('./pages/Fleet'))
const Topology = lazy(() => import('./pages/Topology'))
const Chat = lazy(() => import('./pages/Chat'))
const HostDetail = lazy(() => import('./pages/HostDetail'))
const Timeline = lazy(() => import('./pages/Timeline'))
const Diagnostics = lazy(() => import('./pages/Diagnostics'))
const Reports = lazy(() => import('./pages/Reports'))
const TerminalPage = lazy(() => import('./pages/TerminalPage'))
const Enroll = lazy(() => import('./pages/Enroll'))
const Settings = lazy(() => import('./pages/Settings'))

function PageFallback() {
  const { t } = useT()
  return (
    <div className="h-full flex items-center justify-center text-[var(--text-faint)] text-[13px]">
      <span className="pulse-dot" style={{ background: 'var(--accent)' }} /> {t('ui.loading')}
    </div>
  )
}

type GateState = 'loading' | 'ok' | 'locked'

function Gate() {
  const [state, setState] = useState<GateState>('loading')
  useEffect(() => {
    api<{ enabled: boolean; authenticated: boolean }>('/auth/status')
      .then(s => setState(s.enabled && !s.authenticated ? 'locked' : 'ok'))
      .catch(() => setState('ok')) // 后端不可达时仍渲染界面，由各页面的报错兜底
  }, [])
  if (state === 'loading') return null
  if (state === 'locked') return <Login />
  return (
    <ErrorBoundary>
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route element={<Layout />}>
            <Route path="/" element={<Fleet />} />
            <Route path="/topology" element={<Topology />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/host/:id" element={<HostDetail />} />
            <Route path="/timeline" element={<Timeline />} />
            <Route path="/diagnostics" element={<Diagnostics />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/terminal" element={<TerminalPage />} />
            <Route path="/enroll" element={<Enroll />} />
            <Route path="/settings" element={<Settings />} />
          </Route>
        </Routes>
      </Suspense>
    </ErrorBoundary>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Gate />
    </BrowserRouter>
  )
}
