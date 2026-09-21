import { lazy, Suspense, createContext, useContext, useEffect, useState } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { api } from './api'
import { useT } from './i18n'
import { Layout, RoleCtx, type Role } from './ui'
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
const Probes = lazy(() => import('./pages/Probes'))

function PageFallback() {
  const { t } = useT()
  // 玻璃骨架屏：路由 chunk 加载间隙也保持液态玻璃语言（卡体 .card 自带折射/眩光/颗粒）
  return (
    <div className="h-full flex items-center justify-center p-6">
      <div className="card p-6 w-full max-w-[380px] rise-in">
        <div className="flex items-center gap-2.5 mb-4">
          <span className="pulse-dot" style={{ background: 'var(--accent)' }} />
          <span className="text-[12.5px] text-[var(--text-mute)]">{t('ui.loading')}</span>
        </div>
        <div className="skel h-3 rounded-full mb-2.5" style={{ width: '82%' }} />
        <div className="skel h-3 rounded-full mb-2.5" style={{ width: '64%', animationDelay: '.15s' }} />
        <div className="skel h-3 rounded-full" style={{ width: '46%', animationDelay: '.3s' }} />
      </div>
    </div>
  )
}

type GateState = 'loading' | 'ok' | 'locked'

// 演示站只读模式（后端 HW_DEMO_MODE=on，/auth/status 回带）：全站写操作已由后端 423 拦截，
// 前端同步降级为 observer 视图——写入口直接不渲染，只留「看」的全部能力
export const DemoCtx = createContext(false)
export const useDemoMode = () => useContext(DemoCtx)

function Gate() {
  const [state, setState] = useState<GateState>('loading')
  const [role, setRole] = useState<Role>('admin')
  const [demo, setDemo] = useState(false)
  useEffect(() => {
    api<{ enabled: boolean; authenticated: boolean; role: string; demo_mode?: boolean }>('/auth/status')
      .then(s => {
        // 访问控制未开启 = 单人本机模式，按 admin 全量展示；登录跳转走整页刷新，
        // 所以角色只在 Gate 挂载时取一次即可。演示站一律按只读视图渲染
        setRole(!s.enabled || s.role === 'admin' ? 'admin' : s.role === 'operator' ? 'operator' : 'observer')
        setDemo(!!s.demo_mode)
        setState(s.enabled && !s.authenticated ? 'locked' : 'ok')
      })
      .catch(() => setState('ok')) // 后端不可达时仍渲染界面，由各页面的报错兜底
  }, [])
  if (state === 'loading') return null
  if (state === 'locked') return <Login />
  return (
    <ErrorBoundary>
      <Suspense fallback={<PageFallback />}>
        <RoleCtx.Provider value={demo ? 'observer' : role}>
          <DemoCtx.Provider value={demo}>
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
            <Route path="/probes" element={<Probes />} />
            <Route path="/settings" element={<Settings />} />
          </Route>
        </Routes>
          </DemoCtx.Provider>
        </RoleCtx.Provider>
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
