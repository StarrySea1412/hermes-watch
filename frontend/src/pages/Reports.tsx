import { useEffect, useState } from 'react'
import { api, fmtTime, subscribe } from '../api'
import { Ic } from '../icons'
import { ConfirmDialog, PageHead } from '../ui'
import { HealthRing } from '../charts'

type Report = { id: number; ts: number; kind: string; title: string; score: number; data: { overall: number; hosts: number; findings: number } }

export default function Reports() {
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
      setErrMsg(`删除失败：${e.message}（后端可能正在重启，请重试）`)
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
      setErrMsg(`清空失败：${e.message}（后端可能正在重启，请重试）`)
    }
  }

  return (
    <div className="fade-in">
      <PageHead title="报告中心" sub="健康报告全程本地生成与存储，无云端依赖 · 支持 cron 定时生成">
        {!!reports.length && (
          <button className="btn btn-ghost" style={{ color: 'var(--crit)' }} onClick={() => setConfirmClear(true)}>
            <Ic name="trash" size={13} /> 一键清空
          </button>
        )}
        <button className="btn btn-primary" disabled={busy} onClick={gen}>
          {busy ? <><span className="pulse-dot" style={{ background: 'var(--accent)' }} /> 生成中…</> : <><Ic name="file" size={13} /> 生成报告</>}
        </button>
      </PageHead>
      {errMsg && (
        <div className="card p-3 mb-4 text-[12.5px] flex items-center gap-2.5 fade-in" style={{ borderColor: 'var(--crit-border)', color: 'var(--crit)' }}>
          <span>✕ {errMsg}</span>
          <button className="btn btn-ghost ml-auto shrink-0" onClick={() => { setErrMsg(''); load() }}>重试</button>
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
                  <span className="text-[13.5px] font-semibold text-[var(--text-hi)]">报告 #{r.id}</span>
                  <span className="pill" style={r.kind === 'auto'
                    ? { background: 'var(--violet-bg)', color: 'var(--violet)', fontSize: 10 }
                    : { background: 'var(--neutral-bg)', color: 'var(--text-mute)', fontSize: 10 }}>
                    {r.kind === 'auto' ? '定时' : '手动'}
                  </span>
                </div>
                <div className="text-[11px] text-[var(--text-faint)] mt-1 num">
                  {fmtTime(r.ts)} · {r.data?.hosts ?? '—'} 台主机 · {r.data?.findings ?? '—'} 发现
                </div>
              </div>
              <button title="删除报告" onClick={e => { e.stopPropagation(); setPendingDel(r) }}
                className="shrink-0 w-7 h-7 rounded-lg text-[13px] opacity-0 group-hover:opacity-100 transition-opacity"
                style={{ color: 'var(--text-faint)', background: 'var(--neutral-bg)' }}
                onMouseEnter={e => (e.currentTarget.style.color = 'var(--crit)')}
                onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-faint)')}>✕</button>
            </div>
          ))}
          {!reports.length && (
            <div className="card p-8 text-center">
              <div className="flex justify-center mb-3 text-[var(--text-faint)]"><Ic name="file" size={30} sw={1.5} /></div>
              <div className="text-[var(--text-faint)] text-[13px]">还没有报告，点右上角生成</div>
            </div>
          )}
        </div>
        <div className="lg:col-span-8">
          {viewing ? (
            <div className="card p-4">
              <div className="flex items-center justify-between mb-3.5">
                <span className="text-[13px] text-[var(--text-mute)]">
                  报告 #{viewing.id} · 整体健康分 <b className="num text-[var(--text-hi)]">{viewing.data?.overall}</b>
                </span>
                <a href={`/api/reports/${viewing.id}/html`} target="_blank" className="btn btn-ghost">↗ 新窗口打开</a>
              </div>
              <iframe src={`/api/reports/${viewing.id}/html`} className="w-full h-[68vh] rounded-xl border-0" title="report"
                style={{ background: 'var(--bg-inset)' }} />
            </div>
          ) : <div className="card p-12 text-center text-[var(--text-faint)]">左侧选择一份报告预览</div>}
        </div>
      </div>
      <ConfirmDialog open={!!pendingDel} title={`删除报告 #${pendingDel?.id}？`}
        body="报告及其预览将被永久移除，该操作不可恢复。"
        onConfirm={del} onCancel={() => setPendingDel(null)} />
      <ConfirmDialog open={confirmClear} title="一键清空全部报告？"
        body={`将永久删除全部 ${reports.length} 份报告，该操作不可恢复。`}
        confirmText="全部清空" onConfirm={clearAll} onCancel={() => setConfirmClear(false)} />
    </div>
  )
}
