import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useTheme } from 'next-themes'
import { subscribe } from './api'
import { getLang, setLang, useT, type Lang } from './i18n'
import { Ic, type IconName } from './icons'

/* 图表组件（useEChart/HealthRing/Spark/LineChart）在 ./charts.tsx —— 独立模块
   让 echarts 只进异步 chunk，主包不背 1MB。页面从 '../charts' 导入。 */

/* ============ 语言切换按钮（与主题切换同款交互） ============ */
export function LangToggle() {
  const { t } = useT()
  const next = () => {
    const cur = getLang()
    setLang(cur === 'zh' ? 'en' : 'zh')
  }
  const lang: Lang = getLang()
  return (
    <button className="btn btn-ghost theme-toggle" onClick={next} title={t('lang.title')}
      style={{ width: 38, padding: '7px 0', justifyContent: 'center', fontSize: 11, fontWeight: 700 }}>
      {lang === 'zh' ? 'EN' : '中'}
    </button>
  )
}

/* ============ 主题切换按钮 ============ */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])
  const next = () => setTheme(resolved => {
    // 循环：dark → light → system → dark
    const order = ['dark', 'light', 'system']
    return order[(order.indexOf(resolved ?? 'dark') + 1) % order.length]
  })
  // 挂载前渲染占位，避免 hydration 式闪烁（本项目纯 CSR，仅防布局跳动）
  if (!mounted) return <button className="btn btn-ghost theme-toggle" style={{ width: 38, padding: '7px 0', justifyContent: 'center' }}><Ic name="sun" size={15} /></button>
  const icon: IconName = theme === 'dark' ? 'moon' : theme === 'light' ? 'sun' : 'monitor'
  const label = theme === 'dark' ? '夜间' : theme === 'light' ? '日间' : '跟随系统'
  return (
    <button className="btn btn-ghost theme-toggle" onClick={next} title={`主题：${label}（点击切换 日间/夜间/跟随系统）`}
      style={{ width: 38, padding: '7px 0', justifyContent: 'center' }}>
      <Ic name={icon} size={15} />
    </button>
  )
}

/* ============ 布局 ============ */
const NAV: { to: string; icon: IconName; key: string; end?: boolean }[] = [
  { to: '/', icon: 'grid', key: 'nav.fleet', end: true },
  { to: '/topology', icon: 'radar', key: 'nav.topology' },
  { to: '/diagnostics', icon: 'activity', key: 'nav.diagnostics' },
  { to: '/terminal', icon: 'terminal', key: 'nav.terminal' },
  { to: '/chat', icon: 'sparkles', key: 'nav.chat' },
  { to: '/timeline', icon: 'clock', key: 'nav.timeline' },
  { to: '/reports', icon: 'file', key: 'nav.reports' },
  { to: '/enroll', icon: 'zap', key: 'nav.enroll' },
  { to: '/settings', icon: 'sliders', key: 'nav.settings' },
]

function SidebarInner({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useT()
  return (
    <>
      <div className="px-2 pt-2 pb-5">
        <div className="flex items-center gap-2.5">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center text-lg shrink-0"
            style={{ background: 'linear-gradient(135deg, var(--accent-dim), var(--violet-bg))', border: '1px solid var(--accent-border)' }}>
            🐚
          </div>
          <div className="min-w-0">
            <div className="font-bold text-[15px] text-[var(--text-hi)] tracking-wide">Hermes Watch</div>
            <div className="text-[10.5px] text-[var(--text-faint)]">{t('app.tagline')}</div>
          </div>
        </div>
      </div>
      {NAV.map(n => (
        <NavLink key={n.to} to={n.to} end={n.end as any} onClick={onNavigate}
          className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
          <span className="nav-icon"><Ic name={n.icon} size={15.5} /></span>{t(n.key)}
        </NavLink>
      ))}
      <div className="mt-auto px-2 pb-1 text-[10.5px] text-[var(--text-faint)] leading-relaxed">
        <div className="flex items-center gap-1.5 mb-1">
          <span className="pulse-dot" style={{ background: 'var(--ok)' }} /> {t('app.loopRunning')}
        </div>
        MVP 0.3 · 规则引擎先行<br />AI 深挖 · 只读提议制
      </div>
    </>
  )
}

export function Layout() {
  const [flash, setFlash] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const { pathname } = useLocation()
  const { t, lang } = useT()

  // 浏览器标签页标题随路由同步（语言切换时 useT 触发重渲染，一并刷新）
  useEffect(() => {
    const nav = NAV.find(n => n.to === pathname)
    const name = nav ? t(nav.key) : (pathname.startsWith('/host/') ? (lang === 'en' ? 'Host Detail' : '主机详情') : 'Hermes Watch')
    document.title = `${name} · Hermes Watch`
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname, lang])

  useEffect(() => {
    return subscribe(e => {
      if (e.kind === 'finding' || e.kind === 'error') {
        setFlash(e.message)
        setTimeout(() => setFlash(''), 6000)
      }
    })
  }, [])
  return (
    <div className="flex h-screen overflow-hidden">
      {/* 桌面侧栏（≥lg） */}
      <aside className="hidden lg:flex w-60 shrink-0 border-r border-[var(--border)] flex-col p-4 gap-1 bg-[var(--bg-panel)]/60">
        <SidebarInner />
      </aside>
      {/* 移动端抽屉侧栏（<lg），点导航或遮罩关闭 */}
      {menuOpen && <div className="lg:hidden fixed inset-0 z-40 bg-black/55" onClick={() => setMenuOpen(false)} />}
      <aside className={`lg:hidden fixed inset-y-0 left-0 z-50 w-64 max-w-[82vw] border-r border-[var(--border)] flex flex-col p-4 gap-1 bg-[var(--bg-panel)] transition-transform duration-200 ${menuOpen ? 'translate-x-0' : '-translate-x-full'}`}>
        <SidebarInner onNavigate={() => setMenuOpen(false)} />
      </aside>
      <div className="flex-1 flex flex-col min-w-0">
        {/* 移动端顶栏（<lg） */}
        <header className="lg:hidden shrink-0 h-14 flex items-center gap-3 px-4 border-b border-[var(--border)] bg-[var(--bg-panel)]/60">
          <button className="btn btn-ghost" style={{ padding: '6px 10px' }}
            onClick={() => setMenuOpen(true)} aria-label="打开导航菜单"><Ic name="menu" size={17} /></button>
          <div className="font-bold text-[14px] text-[var(--text-hi)] tracking-wide flex items-center gap-2 min-w-0">
            <span>🐚</span><span className="truncate">Hermes Watch</span>
          </div>
          <span className="ml-auto flex items-center gap-1.5 text-[10.5px] text-[var(--text-faint)]">
            <span className="pulse-dot" style={{ background: 'var(--ok)' }} />运行中
          </span>
        </header>
        <main className="flex-1 overflow-y-auto p-4 sm:p-6 lg:p-7 relative min-w-0">
          {flash && (
            <div className="absolute top-5 right-4 sm:right-7 left-4 sm:left-auto z-50 fade-in flex sm:justify-end">
              <div className="card px-4 py-2.5 flex items-center gap-2.5" style={{ borderColor: 'var(--crit-bg)' }}>
                <span className="pulse-dot" style={{ background: 'var(--crit)' }} />
                <span className="text-[13px] text-[var(--text)]">{flash}</span>
              </div>
            </div>
          )}
          <Outlet />
        </main>
      </div>
    </div>
  )
}

export function PageHead({ title, sub, children }: { title: string; sub?: string; children?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 flex-wrap mb-6">
      <div className="min-w-0">
        <h1 className="text-[20px] font-bold text-[var(--text-hi)] tracking-wide">{title}</h1>
        {sub && <p className="text-[12.5px] text-[var(--text-faint)] mt-1">{sub}</p>}
      </div>
      <div className="flex gap-2.5 items-center flex-wrap">
        <LangToggle />
        <ThemeToggle />
        {children}
      </div>
    </div>
  )
}

/** 迷你统计卡 */
export function Stat({ label, value, hint, accent }: { label: string; value: React.ReactNode; hint?: string; accent?: string }) {
  return (
    <div className="card card-hover p-4">
      <div className="text-[11.5px] text-[var(--text-mute)] font-medium">{label}</div>
      <div className="text-[26px] font-bold num mt-1 leading-none" style={{ color: accent ?? 'var(--text-hi)' }}>{value}</div>
      {hint && <div className="text-[11px] text-[var(--text-faint)] mt-1.5">{hint}</div>}
    </div>
  )
}

/** 主题化确认对话框（替代原生 confirm）：Esc/点遮罩取消 */
export function ConfirmDialog({ open, title, body, confirmText = '删除', cancelText = '取消',
                               onConfirm, onCancel }:
                              { open: boolean; title: string; body?: string; confirmText?: string; cancelText?: string;
                                onConfirm: () => void; onCancel: () => void }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onCancel])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4 fade-in"
      style={{ background: 'rgba(4,10,22,.55)', backdropFilter: 'blur(2px)' }} onClick={onCancel}>
      <div className="card p-5 w-full max-w-[360px] pop-in" role="dialog" aria-modal="true" onClick={e => e.stopPropagation()}>
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'var(--crit-bg)', border: '1px solid var(--crit-border)', color: 'var(--crit)' }}>
            <Ic name="trash" size={16} />
          </div>
          <div className="min-w-0">
            <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{title}</h3>
            {body && <p className="text-[12.5px] text-[var(--text-mute)] mt-1.5 leading-relaxed">{body}</p>}
          </div>
        </div>
        <div className="flex justify-end gap-2.5 mt-4">
          <button className="btn btn-ghost" onClick={onCancel}>{cancelText}</button>
          <button autoFocus className="btn justify-center" onClick={onConfirm}
            style={{ background: 'var(--crit-bg)', color: 'var(--crit)', borderColor: 'var(--crit-border)' }}>
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  )
}

/** 供页面里读取当前主题色板（palette 键即 CSS 变量名） */
export { useResolvedTheme, useChartPalette } from './theme'
