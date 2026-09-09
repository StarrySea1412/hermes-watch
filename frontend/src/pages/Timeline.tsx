import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, fmtTime, subscribe, type Event } from '../api'
import { Ic, type IconName } from '../icons'
import { PageHead } from '../ui'
import { useT } from '../i18n'

const KIND: Record<string, { icon: IconName; color: string; bg: string; border: string; label: string }> = {
  finding: { icon: 'alert', color: 'var(--warn)', bg: 'var(--warn-bg)', border: 'var(--warn-border)', label: 'tl.kind.finding' },
  analysis: { icon: 'activity', color: 'var(--accent)', bg: 'var(--accent-dim)', border: 'var(--accent-border)', label: 'tl.kind.analysis' },
  proposal: { icon: 'pen', color: 'var(--violet)', bg: 'var(--violet-bg)', border: 'var(--violet-border)', label: 'tl.kind.proposal' },
  report: { icon: 'file', color: 'var(--ok)', bg: 'var(--ok-bg)', border: 'var(--ok-border)', label: 'tl.kind.report' },
  error: { icon: 'x-circle', color: 'var(--crit)', bg: 'var(--crit-bg)', border: 'var(--crit-border)', label: 'tl.kind.error' },
  ok: { icon: 'check-circle', color: 'var(--ok)', bg: 'var(--ok-bg)', border: 'var(--ok-border)', label: 'tl.kind.ok' },
  host: { icon: 'plus', color: 'var(--text-mute)', bg: 'var(--neutral-bg)', border: 'var(--border)', label: 'tl.kind.host' },
  settings: { icon: 'sliders', color: 'var(--text-mute)', bg: 'var(--neutral-bg)', border: 'var(--border)', label: 'nav.settings' },
}
const KIND_FALLBACK = { icon: 'clock' as IconName, color: 'var(--text-mute)', bg: 'var(--neutral-bg)', border: 'var(--border)', label: 'tl.kind.event' }

/** 从事件 data 载荷推导可跳转动作（有就渲染「查看 →」链接） */
function actionOf(e: Event): { label: string; to: string } | null {
  const d = e.data || {}
  if (d.report_id) return { label: 'tl.viewReport', to: '/reports' }
  if (d.finding_id || d.proposal_id || d.run_id) return { label: 'tl.viewDiag', to: '/diagnostics' }
  return null
}

// 天分组 id 与语言无关（仅作 Map key / React key），展示标题由 dayKey 走 i18n
function dayId(ts: number): string {
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}

function dayKey(ts: number): { key: string; vars?: Record<string, string | number> } {
  const d = new Date(ts * 1000)
  const now = new Date()
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString()
  if (same(d, now)) return { key: 'tl.day.today' }
  if (same(d, new Date(now.getTime() - 86400_000))) return { key: 'tl.day.yesterday' }
  return { key: 'tl.day.date', vars: { m: d.getMonth() + 1, d: d.getDate() } }
}

export default function Timeline() {
  const { t } = useT()
  const [events, setEvents] = useState<Event[]>([])
  const [kind, setKind] = useState('all')
  const [range, setRange] = useState(0)   // 秒；0 = 不限
  const [limit, setLimit] = useState(80)
  const nav = useNavigate()

  const load = () => api<Event[]>(`/events?limit=${limit}`).then(setEvents)
  useEffect(() => { load(); return subscribe(() => load()) }, [limit])

  // 先按时间范围过滤，类型计数基于过滤后的集合（联动）
  const timeFiltered = useMemo(() => {
    if (!range) return events
    const cutoff = Date.now() / 1000 - range
    return events.filter(e => e.ts >= cutoff)
  }, [events, range])

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const e of timeFiltered) c[e.kind] = (c[e.kind] || 0) + 1
    return c
  }, [timeFiltered])

  const shown = useMemo(() => timeFiltered.filter(e => kind === 'all' || e.kind === kind), [timeFiltered, kind])

  // 按天分组（保持时间倒序），组内再渲染卡片
  const groups = useMemo(() => {
    const m = new Map<string, Event[]>()
    for (const e of shown) {
      const k = dayId(e.ts)
      if (!m.has(k)) m.set(k, [])
      m.get(k)!.push(e)
    }
    return Array.from(m.entries())
  }, [shown])

  const dayText = (ts: number) => {
    const k = dayKey(ts)
    return t(k.key, k.vars)
  }

  const filterPill = (k: string, label: string) => (
    <button key={k} onClick={() => setKind(k)}
      className={`pill ${kind === k ? '' : 'text-[var(--text-mute)]'}`}
      style={kind === k
        ? { background: 'var(--accent-dim)', color: 'var(--accent)' }
        : { background: 'var(--neutral-bg)' }}>
      {label} <b className="num opacity-70">{k === 'all' ? timeFiltered.length : counts[k] || 0}</b>
    </button>
  )

  const RANGES: [number, string][] = [[3600, 'tl.range.1h'], [6 * 3600, 'tl.range.6h'], [86400, 'tl.range.24h'], [3 * 86400, 'tl.range.3d'], [0, 'tl.range.all']]

  return (
    <div className="fade-in max-w-4xl">
      <PageHead title={t('nav.timeline')} sub={t('tl.subtitle')} />

      {/* 概要条 */}
      <div className="card p-4 mb-4 space-y-3">
        <div className="flex items-center gap-x-6 gap-y-2 flex-wrap">
          <div className="text-[13px] text-[var(--text-mute)]">
            <b className="num text-[var(--text-hi)] text-[16px]">{timeFiltered.length}</b> {t('tl.eventsSuffix')}
            <span className="text-[11px] text-[var(--text-faint)] ml-2">
              {timeFiltered.length ? t('tl.coveredTo', { day: dayText(timeFiltered[timeFiltered.length - 1].ts) }) : ''}
            </span>
          </div>
          <div className="flex items-center gap-2 flex-wrap ml-auto">
            {filterPill('all', t('tl.kind.all'))}
            {filterPill('finding', t('tl.kind.finding'))}
            {filterPill('analysis', t('tl.kind.analysis'))}
            {filterPill('proposal', t('tl.kind.proposal'))}
            {filterPill('report', t('tl.kind.report'))}
            {filterPill('error', t('tl.kind.error'))}
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap pt-3 border-t" style={{ borderColor: 'var(--border)' }}>
          <span className="text-[11px] text-[var(--text-faint)] flex items-center gap-1">{t('tl.timeLabel')}</span>
          {RANGES.map(([sec, label]) => (
            <button key={sec} onClick={() => setRange(sec)}
              className={`pill ${range === sec ? '' : 'text-[var(--text-mute)]'}`}
              style={range === sec
                ? { background: 'var(--violet-bg)', color: 'var(--violet)' }
                : { background: 'var(--neutral-bg)' }}>
              {t(label)}
            </button>
          ))}
        </div>
      </div>

      {/* 按天分组的时间线 */}
      {groups.map(([day, evs]) => (
        <div key={day} className="mb-7">
          <div className="sticky top-0 z-10 py-1.5 -mx-1 px-1 mb-1"
            style={{ background: 'linear-gradient(to top, var(--bg-base) 70%, transparent)' }}>
            <span className="pill" style={{ background: 'var(--neutral-strong-bg)', color: 'var(--text-mute)' }}>
              {dayText(evs[0].ts)} <b className="num">{evs.length}</b>
            </span>
          </div>
          <div className="relative ml-3 border-l border-[var(--border-strong)] space-y-2.5">
            {evs.map(e => {
              const k = KIND[e.kind] ?? KIND_FALLBACK
              const action = actionOf(e)
              const count = (e.data?.count ?? 1) > 1 ? e.data.count : null
              return (
                <div key={e.id} className="relative pl-10 fade-in">
                  {/* 节点：实底面板色 + 状态色实边框 + 柔和光晕，不再透出背后的轴线 */}
                  <span className="absolute -left-[18px] top-3 w-9 h-9 rounded-full flex items-center justify-center z-10"
                    style={{
                      background: 'var(--bg-panel)',
                      border: `2px solid ${k.color}`,
                      color: k.color,
                      boxShadow: `0 0 0 4px ${k.bg}, 0 2px 10px rgba(0,0,0,.22)`,
                    }}>
                    <Ic name={k.icon} size={15} sw={2.2} />
                  </span>
                  <div className="card p-3.5 hover:border-[var(--border-strong)] transition-colors" style={{ borderLeft: `3px solid ${k.color}` }}>
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="pill text-[10.5px]" style={{ background: k.bg, color: k.color }}>{t(k.label)}</span>
                      {e.host_name && (
                        <span className="pill text-[10.5px]" style={{ background: 'var(--neutral-bg)', color: 'var(--text-mute)' }}>
                          {e.host_name}
                        </span>
                      )}
                      {!!count && (
                        <span className="pill text-[10px]" style={{ background: 'var(--warn-bg)', color: 'var(--warn)' }}>
                          {t('tl.ongoing', { n: count })}
                        </span>
                      )}
                      <span className="ml-auto text-[10.5px] text-[var(--text-faint)] num">{fmtTime(e.ts)}</span>
                    </div>
                    <div className="text-[13px] text-[var(--text)] mt-1.5 leading-relaxed">{e.message}</div>
                    {action && (
                      <button onClick={() => nav(action.to)}
                        className="text-[11.5px] mt-2 hover:underline" style={{ color: 'var(--accent)' }}>
                        {t(action.label)} →
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      ))}

      {!shown.length && (
        <div className="card p-10 text-center">
          <div className="flex justify-center mb-3 text-[var(--text-faint)]"><Ic name="clock" size={30} sw={1.5} /></div>
          <div className="text-[var(--text-mute)] text-[13px]">{events.length ? t('tl.emptyFiltered') : t('tl.empty')}</div>
        </div>
      )}

      {events.length >= limit && (
        <div className="text-center mt-2">
          <button className="btn btn-ghost" onClick={() => setLimit(l => l + 120)}>{t('tl.loadOlder')}</button>
        </div>
      )}
    </div>
  )
}
