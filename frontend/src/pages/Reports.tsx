import { useEffect, useState } from 'react'
import { api, fmtTime, subscribe } from '../api'
import { Ic } from '../icons'
import { ConfirmDialog, PageHead } from '../ui'
import { useT } from '../i18n'
import { HealthRing } from '../charts'

type Report = { id: number; ts: number; kind: string; title: string; score: number; data: { overall: number; hosts: number; findings: number } }

export default function Reports() {
  const { t } = useT()
  const [reports, setReports] = useState<Report[]>([])
  const [viewing, setViewing] = useState<Report | null>(null)
  const [pendingDel, setPendingDel] = useState<Report | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const [errMsg, setErrMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const load = () => api<Report[]>('/reports').then(setReports)
  useEffect(() => { load(); return subscribe(() => load()) }, [])

  const gen = async () => {
    setBusy(true)
    try { await api('/reports/generate', { method: 'POST' }); load() } finally { setBusy(false) }
  }
  const del = async () => {
    if (!pendingDel) return
    try {
      await api(`/reports/${pendingDel.id}`, { method: 'DELETE' })
      if (viewing?.id === pendingDel.id) setViewing(null)
      setPendingDel(null)
      setErrMsg('')
      load()
    } catch (e: any) {
      setErrMsg(t('rep.delFail', { msg: e.message }))
    }
  }
  const clearAll = async () => {
    try {
      await api('/reports', { method: 'DELETE' })
      setConfirmClear(false)
      setViewing(null)
      setErrMsg('')
      load()
    } catch (e: any) {
      setErrMsg(t('rep.clearFail', { msg: e.message }))
    }
  }

  return (
    <div className="fade-in">
      <PageHead title={t('nav.reports')} sub={t('rep.sub')}>
        {!!reports.length && (
          <button className="btn btn-ghost" style={{ color: 'var(--crit)' }} onClick={() => setConfirmClear(true)}>
            <Ic name="trash" size={13} /> {t('rep.clearAll')}
          </button>
        )}
        <button className="btn btn-primary" disabled={busy} onClick={gen}>
          {busy ? <><span className="pulse-dot" style={{ background: 'var(--accent)' }} /> {t('rep.generating')}</> : <><Ic name="file" size={13} /> {t('rep.generate')}</>}
        </button>
      </PageHead>
      {errMsg && (
        <div className="card p-3 mb-4 text-[12.5px] flex items-center gap-2.5 fade-in" style={{ borderColor: 'var(--crit-border)', color: 'var(--crit)' }}>
          <span>✕ {errMsg}</span>
          <button className="btn btn-ghost ml-auto shrink-0" onClick={() => { setErrMsg(''); load() }}>{t('btn.retry')}</button>
        </div>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <div className="lg:col-span-4 space-y-2.5">
          {reports.map(r => (
            <div key={r.id} onClick={() => setViewing(r)}
              className={`card card-hover p-4 cursor-pointer flex items-center gap-3.5 group ${viewing?.id === r.id ? 'ring-1 ring-[var(--accent)]' : ''}`}
              style={viewing?.id === r.id ? { borderColor: 'var(--accent-border)' } : undefined}>
              <HealthRing score={r.data?.overall ?? r.score} size={58} thickness={6} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-[13.5px] font-semibold text-[var(--text-hi)]">{t('rep.num', { n: r.id })}</span>
                  <span className="pill" style={r.kind === 'auto'
                    ? { background: 'var(--violet-bg)', color: 'var(--violet)', fontSize: 10 }
                    : r.kind === 'diag'
                      ? { background: 'var(--accent-dim)', color: 'var(--accent)', fontSize: 10 }
                      : { background: 'var(--neutral-bg)', color: 'var(--text-mute)', fontSize: 10 }}>
                    {r.kind === 'auto' ? t('rep.auto') : r.kind === 'diag' ? t('rep.diag') : t('rep.manual')}
                  </span>
                </div>
                <div className="text-[11px] text-[var(--text-faint)] mt-1 num">
                  {fmtTime(r.ts)} · {t('rep.hosts', { n: r.data?.hosts ?? '—' })} · {t('rep.findings', { n: r.data?.findings ?? '—' })}
                </div>
              </div>
              <button title={t('rep.delTip')} onClick={e => { e.stopPropagation(); setPendingDel(r) }}
                className="shrink-0 w-7 h-7 rounded-lg text-[13px] opacity-0 group-hover:opacity-100 transition-opacity"
                style={{ color: 'var(--text-faint)', background: 'var(--neutral-bg)' }}
                onMouseEnter={e => (e.currentTarget.style.color = 'var(--crit)')}
                onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-faint)')}>✕</button>
            </div>
          ))}
          {!reports.length && (
            <div className="card p-8 text-center">
              <div className="flex justify-center mb-3 text-[var(--text-faint)]"><Ic name="file" size={30} sw={1.5} /></div>
              <div className="text-[var(--text-faint)] text-[13px]">{t('rep.empty')}</div>
            </div>
          )}
        </div>
        <div className="lg:col-span-8">
          {viewing ? (
            <div className="card p-4">
              <div className="flex items-center justify-between mb-3.5">
                <span className="text-[13px] text-[var(--text-mute)]">
                  {t('rep.num', { n: viewing.id })} · {t('rep.overallScore')} <b className="num text-[var(--text-hi)]">{viewing.data?.overall}</b>
                </span>
                <div className="flex gap-2.5">
                  <button className="btn btn-ghost" title={t('rep.printTip')}
                    onClick={() => (document.querySelector('iframe[title="report"]') as HTMLIFrameElement | null)?.contentWindow?.print()}>
                    🖨 {t('rep.printPdf')}
                  </button>
                  <a href={`/api/reports/${viewing.id}/html`} target="_blank" className="btn btn-ghost">↗ {t('rep.openNew')}</a>
                </div>
              </div>
              <iframe src={`/api/reports/${viewing.id}/html`} className="w-full h-[68vh] rounded-xl border-0" title="report"
                style={{ background: 'var(--bg-inset)' }} />
            </div>
          ) : <div className="card p-12 text-center text-[var(--text-faint)]">{t('rep.emptyPreview')}</div>}
        </div>
      </div>
      <ConfirmDialog open={!!pendingDel} title={t('rep.delConfirmTitle', { n: pendingDel?.id ?? '' })}
        body={t('rep.delConfirmBody')}
        onConfirm={del} onCancel={() => setPendingDel(null)} />
      <ConfirmDialog open={confirmClear} title={t('rep.clearConfirmTitle')}
        body={t('rep.clearConfirmBody', { n: reports.length })}
        confirmText={t('rep.clearConfirmBtn')} onConfirm={clearAll} onCancel={() => setConfirmClear(false)} />
    </div>
  )
}
