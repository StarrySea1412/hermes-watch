import { useEffect, useRef, useState } from 'react'
import { api, fmtTime, subscribe } from '../api'
import { ConfirmDialog, PageHead } from '../ui'
import { useT } from '../i18n'
import { Ic } from '../icons'

// 拨测（URL / TCP 服务监控）：对标 Uptime Kuma 的服务拨测页，与主机巡检（Fleet）解耦。
// 状态机（Gatus 式双阈值防抖）：up 中连续 fail_threshold 次失败 → down；down 中连续
// success_threshold 次成功 → up。防抖窗口内显示「观察中 / 恢复中 x/N」徽标。

type Probe = {
  id: number; name: string; kind: 'url' | 'tcp'; target: string
  up: 0 | 1; fail_streak: number; succ_streak: number
  fail_threshold: number; success_threshold: number; timeout_s: number
  keyword: string; max_latency_ms: number; cert_days_min: number; interval_s: number
  last_ts: number | null; last_latency: number | null; last_error: string | null
  last_flip_ts: number | null; created_at: number | null
}
type ProbeLog = { id: number; probe_id: number; ts: number; up: 0 | 1; latency: number | null; error: string | null }

const EMPTY_LOGS: ProbeLog[] = []

/** 单条拨测卡：状态 pill + 延迟 + 48 桶心跳条带（视觉对齐 HostDetail 巡检心跳） */
function ProbeCard({ p, logs, running, onRun, onAskDelete }: {
  p: Probe; logs: ProbeLog[]; running: boolean
  onRun: (p: Probe) => void; onAskDelete: (p: Probe) => void
}) {
  const { t } = useT()
  const ok = p.up === 1
  const color = ok ? 'var(--ok)' : 'var(--crit)'
  // 心跳条带：最近 48 次拨测（log 为 ts 倒序 → 反转成旧→新），不足 48 次左侧留空位
  const recent = logs.slice(0, 48).reverse()
  const bars: (ProbeLog | null)[] = [...Array.from({ length: 48 - recent.length }, () => null), ...recent]
  const barTitle = (l: ProbeLog) =>
    `${fmtTime(l.ts)} · ${l.up ? t('pr.hb.up') : t('pr.hb.down')}` +
    (l.latency != null ? ` · ${Math.round(l.latency)}ms` : '') +
    (l.error ? ` · ${l.error}` : '')

  // 条件徽标（参考 kind 徽标的 pill 样式，用中性色调避免抢过类型徽标）：
  // URL 条件三件套（关键词 / 延迟上限 / 证书天数）+ 每目标独立周期（仅 >0 时标，0=用全局不标）
  const isUrl = p.kind === 'url'
  const kw = (p.keyword || '').trim()
  const conds: { key: string; text: string; title?: string }[] = []
  if (isUrl && kw) conds.push({ key: 'kw', text: t('pr.badgeKeyword', { n: kw.length > 18 ? kw.slice(0, 18) + '…' : kw }), title: kw })
  if (isUrl && (p.max_latency_ms || 0) > 0) conds.push({ key: 'ms', text: t('pr.badgeLatency', { n: p.max_latency_ms }) })
  if (isUrl && (p.cert_days_min || 0) > 0) conds.push({ key: 'cert', text: t('pr.badgeCert', { n: p.cert_days_min }) })
  if ((p.interval_s || 0) > 0) conds.push({ key: 'iv', text: t('pr.badgeInterval', { n: p.interval_s }) })
  const watching = ok ? p.fail_streak > 0 : p.succ_streak > 0

  return (
    <div className="card card-hover p-4 flex flex-col h-full">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-semibold text-[14.5px] text-[var(--text-hi)] flex items-center gap-2">
            {p.name}
            <span className="pill" style={{ background: 'var(--accent-dim)', color: 'var(--accent)', fontSize: 10, padding: '1px 7px' }}>
              {p.kind === 'tcp' ? 'TCP' : 'URL'}
            </span>
          </div>
          <div className="text-[11.5px] text-[var(--text-faint)] mt-0.5 mono truncate" title={p.target}>{p.target}</div>
        </div>
        <span className="pill shrink-0" style={{ background: ok ? 'var(--ok-bg)' : 'var(--crit-bg)', color, border: `1px solid ${ok ? 'var(--ok-border)' : 'var(--crit-border)'}` }}>
          <span className="pulse-dot" style={{ background: color }} />
          {ok ? t('pr.status.up') : t('pr.status.down')}
        </span>
      </div>

      {/* 条件徽标 + 防抖窗口徽标（up 中在攒失败 / down 中在攒成功）同排展示 */}
      {(conds.length > 0 || watching) && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {conds.map(b => (
            <span key={b.key} className="pill" title={b.title}
              style={{ fontSize: 10, padding: '1px 7px', background: 'var(--neutral-bg)', color: 'var(--text-mute)' }}>
              {b.text}
            </span>
          ))}
          {watching && (
            <span className="pill" style={{ fontSize: 10.5, background: ok ? 'var(--warn-bg)' : 'var(--ok-bg)', color: ok ? 'var(--warn)' : 'var(--ok)', border: `1px solid ${ok ? 'var(--warn-border)' : 'var(--ok-border)'}` }}>
              {ok ? t('pr.watch', { n: p.fail_streak, m: p.fail_threshold }) : t('pr.recover', { n: p.succ_streak, m: p.success_threshold })}
            </span>
          )}
        </div>
      )}

      <div className="mt-3 flex items-baseline gap-2">
        <span className="text-[11px] text-[var(--text-faint)]">{t('pr.latency')}</span>
        <span className="text-[17px] font-bold num leading-none" style={{ color: ok ? 'var(--text-hi)' : 'var(--crit)' }}>
          {p.last_latency != null ? Math.round(p.last_latency) : '—'}<span className="text-[11px] text-[var(--text-faint)] font-normal"> ms</span>
        </span>
      </div>
      <div className="mt-2.5">
        <div className="flex items-center gap-2 mb-1.5">
          <span className="text-[11px] text-[var(--text-faint)]">{t('pr.heartbeat')}</span>
          <div className="flex items-center gap-2.5 ml-auto text-[10px] text-[var(--text-faint)]">
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm" style={{ background: 'var(--ok)' }} />{t('pr.hb.up')}</span>
            <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm" style={{ background: 'var(--crit)' }} />{t('pr.hb.down')}</span>
          </div>
        </div>
        <div className="flex gap-[2px] h-4">
          {bars.map((l, i) => (
            <div key={i} className="flex-1 rounded-[2px] transition-colors" title={l ? barTitle(l) : t('pr.hb.none')}
              style={{ background: l ? (l.up ? 'var(--ok)' : 'var(--crit)') : 'var(--border)', opacity: l ? 0.9 : 0.5 }} />
          ))}
        </div>
      </div>

      {!ok && p.last_error && (
        <div className="text-[11px] mt-2 text-[var(--crit)] mono truncate" title={p.last_error}>{t('pr.errLast', { err: p.last_error })}</div>
      )}

      <div className="mt-auto pt-2.5 flex items-center gap-2 text-[11px] text-[var(--text-faint)] flex-wrap">
        <span>{p.last_ts ? t('pr.lastCheck', { time: fmtTime(p.last_ts).slice(-8) }) : t('pr.waitingFirst')}</span>
        <span>·</span>
        {(p.last_flip_ts || p.created_at)
          ? <span className={ok ? '' : 'text-[var(--crit)]'}>{t(ok ? 'pr.upSince' : 'pr.downSince', { time: fmtTime(p.last_flip_ts ?? p.created_at ?? 0).slice(-8) })}</span>
          : <span>{t('pr.waitingFirst')}</span>}
      </div>

      <div className="mt-2.5 flex items-center gap-2 border-t pt-2.5" style={{ borderColor: 'var(--border)' }}>
        <button className="btn btn-ghost" style={{ padding: '4px 10px', fontSize: 12 }} disabled={running} onClick={() => onRun(p)}>
          <Ic name="play" size={12} /> {running ? t('pr.running') : t('pr.runNow')}
        </button>
        <button className="btn btn-ghost ml-auto" style={{ padding: '4px 10px', fontSize: 12, color: 'var(--text-faint)' }}
          onClick={() => onAskDelete(p)}>
          <Ic name="trash" size={12} /> {t('btn.delete')}
        </button>
      </div>
    </div>
  )
}

export default function Probes() {
  const { t } = useT()
  const [probes, setProbes] = useState<Probe[]>([])
  const [logs, setLogs] = useState<Record<number, ProbeLog[]>>({})
  const [loadErr, setLoadErr] = useState('')
  const [msg, setMsg] = useState('')
  const [form, setForm] = useState({ name: '', kind: 'url' as 'url' | 'tcp', target: '' })
  const [advOpen, setAdvOpen] = useState(false)
  const [adv, setAdv] = useState({
    fail_threshold: '3', success_threshold: '2', timeout_s: '10',
    keyword: '', max_latency_ms: '', cert_days_min: '', interval_s: '',
  })
  const [adding, setAdding] = useState(false)
  const [runningId, setRunningId] = useState<number | null>(null)
  const [delTarget, setDelTarget] = useState<Probe | null>(null)
  const [iv, setIv] = useState('30')
  const [ivSaved, setIvSaved] = useState(false)
  const msgTimer = useRef<number | null>(null)
  const lastLoad = useRef(0)
  const pending = useRef<number | null>(null)

  const load = () => {
    lastLoad.current = Date.now()
    api<Probe[]>('/probes')
      .then(ps => {
        setProbes(ps); setLoadErr('')
        // 心跳日志逐条拉取；单条失败静默（列表主体不受影响）
        Promise.all(ps.map(p => api<ProbeLog[]>(`/probes/${p.id}/log?limit=120`).catch(() => [] as ProbeLog[])))
          .then(rows => {
            const m: Record<number, ProbeLog[]> = {}
            for (const r of rows) if (r[0]) m[r[0].probe_id] = r
            setLogs(m)
          })
      })
      .catch(e => { if (!probes.length) setLoadErr(e.message) })
  }

  // SSE 节流刷新：probe 事件密集时（一轮拨测多条翻转）2s 内只全量刷一次
  const scheduleRefresh = () => {
    const gap = Date.now() - lastLoad.current
    if (gap > 2000) { load(); return }
    if (pending.current != null) return
    pending.current = window.setTimeout(() => { pending.current = null; load() }, 2000 - gap)
  }

  // 行内提示（✓/✕ 前缀决定颜色），6s 自动消退
  const flash = (m: string) => {
    setMsg(m)
    if (msgTimer.current) clearTimeout(msgTimer.current)
    msgTimer.current = window.setTimeout(() => setMsg(''), 6000)
  }

  useEffect(() => {
    load()
    api<Record<string, string>>('/settings').then(s => { if (s.probe_interval) setIv(s.probe_interval) }).catch(() => { /* noop */ })
    const unsub = subscribe(e => { if (e.kind === 'probe') scheduleRefresh() })
    return () => {
      unsub()
      if (pending.current != null) clearTimeout(pending.current)
      if (msgTimer.current) clearTimeout(msgTimer.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const add = () => {
    setAdding(true)
    const payload: Record<string, unknown> = { name: form.name, kind: form.kind, target: form.target }
    if (adv.fail_threshold !== '') payload.fail_threshold = Number(adv.fail_threshold)
    if (adv.success_threshold !== '') payload.success_threshold = Number(adv.success_threshold)
    if (adv.timeout_s !== '') payload.timeout_s = Number(adv.timeout_s)
    // URL 条件三件套仅随 url 拨测提交（后端对 TCP 拒收）；独立周期两类通用，0=用全局
    if (form.kind === 'url' && adv.keyword.trim()) payload.keyword = adv.keyword.trim()
    if (form.kind === 'url' && adv.max_latency_ms !== '') payload.max_latency_ms = Number(adv.max_latency_ms)
    if (form.kind === 'url' && adv.cert_days_min !== '') payload.cert_days_min = Number(adv.cert_days_min)
    if (adv.interval_s !== '') payload.interval_s = Number(adv.interval_s)
    api<{ id: number }>('/probes', { method: 'POST', body: JSON.stringify(payload) })
      .then(() => {
        flash(t('pr.added', { n: form.name }))
        setForm({ name: '', kind: form.kind, target: '' })
        load()
      })
      .catch(e => flash(`✕ ${e.message}`))
      .finally(() => setAdding(false))
  }

  const runNow = (p: Probe) => {
    setRunningId(p.id)
    api<{ ok: boolean; latency: number | null; error: string }>(`/probes/${p.id}/run`, { method: 'POST' })
      .then(r => {
        flash(r.ok ? t('pr.runOk', { name: p.name, ms: r.latency ?? 0 }) : t('pr.runFail', { name: p.name, err: r.error || '—' }))
        load()
      })
      .catch(e => flash(`✕ ${e.message}`))
      .finally(() => setRunningId(null))
  }

  const doDelete = () => {
    if (!delTarget) return
    const p = delTarget
    setDelTarget(null)
    api(`/probes/${p.id}`, { method: 'DELETE' })
      .then(() => { flash(t('pr.deleted', { n: p.name })); load() })
      .catch(e => flash(`✕ ${e.message}`))
  }

  // 拨测周期：行内即改即存（后端下限 15s），交互对齐设置页巡检周期
  const saveIv = () => {
    const n = Math.max(15, Math.round(Number(iv) || 30))
    setIv(String(n))
    api('/settings', { method: 'POST', body: JSON.stringify({ probe_interval: n }) })
      .then(() => { setIvSaved(true); setTimeout(() => setIvSaved(false), 2000) })
      .catch(e => flash(`✕ ${e.message}`))
  }

  return (
    <div className="fade-in">
      <PageHead title={t('pr.title')} sub={t('pr.subtitle', { n: Number(iv) || 30 })}>
        <span className="flex items-center gap-1.5 text-[11.5px] text-[var(--text-faint)]">
          {t('pr.interval')}
          <input className="input num inline-block text-center" inputMode="numeric" style={{ width: 56, padding: '4px 6px' }}
            value={iv} onChange={e => setIv(e.target.value)} onBlur={saveIv} />
          {t('st.thresh.seconds')}
          {ivSaved && <span style={{ color: 'var(--ok)' }}>{t('st.thresh.saved')}</span>}
        </span>
      </PageHead>

      {loadErr && !probes.length && (
        <div className="card p-10 text-center mb-4">
          <div className="flex justify-center mb-3 text-[var(--crit)]"><Ic name="alert" size={30} sw={1.5} /></div>
          <div className="text-[13px] text-[var(--text-mute)] mb-1">{t('pr.loadFailed')}</div>
          <div className="text-[11.5px] text-[var(--text-faint)] mono mb-4">{loadErr}</div>
          <button className="btn btn-primary" onClick={load}><Ic name="refresh" size={13} /> {t('btn.retry')}</button>
        </div>
      )}

      {msg && (
        <div className="card px-4 py-2.5 mb-4 flex items-center gap-2.5 fade-in"
          style={{ borderColor: msg.startsWith('✓') ? 'var(--ok-border)' : 'var(--crit-border)' }}>
          <span className="pulse-dot" style={{ background: msg.startsWith('✓') ? 'var(--ok)' : 'var(--crit)' }} />
          <span className="text-[13px] text-[var(--text)]">{msg}</span>
        </div>
      )}

      {/* 添加拨测 */}
      <div className="card p-5 mb-5">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-1">
          <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)] flex items-center gap-2">
            <Ic name="radio" size={15} style={{ color: 'var(--accent)' }} /> {t('pr.addTitle')}
          </h3>
          <span className="text-[10.5px] text-[var(--text-faint)]">{t('pr.addHint')}</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto_2fr_auto] gap-2.5 items-end">
          <div>
            <div className="text-[11px] text-[var(--text-faint)] mb-1.5">{t('pr.name')}</div>
            <input className="input" placeholder={t('pr.namePh')} value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value })} />
          </div>
          <div>
            <div className="text-[11px] text-[var(--text-faint)] mb-1.5">{t('pr.kind')}</div>
            <div className="flex gap-1.5">
              {(['url', 'tcp'] as const).map(k => (
                <button key={k} onClick={() => setForm({ ...form, kind: k })}
                  className={`pill ${form.kind === k ? '' : 'text-[var(--text-mute)]'}`}
                  style={form.kind === k ? { background: 'var(--accent-dim)', color: 'var(--accent)' } : { background: 'var(--neutral-bg)' }}>
                  {k === 'url' ? 'URL' : 'TCP'}
                </button>
              ))}
            </div>
          </div>
          <div>
            <div className="text-[11px] text-[var(--text-faint)] mb-1.5">{t('pr.target')}</div>
            <input className="input mono" value={form.target}
              placeholder={form.kind === 'url' ? t('pr.phTargetUrl') : t('pr.phTargetTcp')}
              onChange={e => setForm({ ...form, target: e.target.value })} />
          </div>
          <button className="btn btn-primary justify-center" disabled={adding || !form.name.trim() || !form.target.trim()} onClick={add}>
            <Ic name="plus" size={13} /> {t('pr.add')}
          </button>
        </div>
        {/* 高级折叠：双阈值 + 超时（acc-body 手风琴，高度自适应） */}
        <div className="mt-3">
          <button className="text-[11.5px] text-[var(--text-faint)] hover:text-[var(--accent)] flex items-center gap-1"
            onClick={() => setAdvOpen(v => !v)}>
            <Ic name="chevron-down" size={12} style={{ transform: advOpen ? 'rotate(180deg)' : undefined, transition: 'transform .2s' }} />
            {t('pr.adv')}
          </button>
          <div className={`acc-body ${advOpen ? 'open' : ''}`}>
            <div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 pt-3">
                <div>
                  <div className="flex justify-between text-[11px] text-[var(--text-faint)] mb-1">
                    <span>{t('pr.failTh')}</span><span>{t('st.thresh.def', { n: 3 })}</span>
                  </div>
                  <input className="input num" inputMode="numeric" placeholder="3" value={adv.fail_threshold}
                    onChange={e => setAdv({ ...adv, fail_threshold: e.target.value })} />
                </div>
                <div>
                  <div className="flex justify-between text-[11px] text-[var(--text-faint)] mb-1">
                    <span>{t('pr.succTh')}</span><span>{t('st.thresh.def', { n: 2 })}</span>
                  </div>
                  <input className="input num" inputMode="numeric" placeholder="2" value={adv.success_threshold}
                    onChange={e => setAdv({ ...adv, success_threshold: e.target.value })} />
                </div>
                <div>
                  <div className="flex justify-between text-[11px] text-[var(--text-faint)] mb-1">
                    <span>{t('pr.timeout')}</span><span>{t('st.thresh.def', { n: 10 })}</span>
                  </div>
                  <input className="input num" inputMode="numeric" placeholder="10" value={adv.timeout_s}
                    onChange={e => setAdv({ ...adv, timeout_s: e.target.value })} />
                </div>
              </div>
              {/* 条件引擎输入：URL 条件三件套（TCP 选中时置灰禁用，后端对 TCP 拒收）+ 两类通用的独立周期；留空 = 不启用 */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5 pt-2.5">
                <div style={form.kind === 'tcp' ? { opacity: 0.45 } : undefined}
                  title={form.kind === 'tcp' ? t('pr.condUrlOnly') : undefined}>
                  <div className="text-[11px] text-[var(--text-faint)] mb-1">{t('pr.condKeyword')}</div>
                  <input className="input" placeholder={t('pr.condKeywordPh')} value={adv.keyword}
                    disabled={form.kind === 'tcp'}
                    onChange={e => setAdv({ ...adv, keyword: e.target.value })} />
                </div>
                <div style={form.kind === 'tcp' ? { opacity: 0.45 } : undefined}
                  title={form.kind === 'tcp' ? t('pr.condUrlOnly') : undefined}>
                  <div className="flex justify-between text-[11px] text-[var(--text-faint)] mb-1">
                    <span>{t('pr.condLatency')}</span><span>{t('pr.condOff')}</span>
                  </div>
                  <input className="input num" inputMode="numeric" placeholder="0" value={adv.max_latency_ms}
                    disabled={form.kind === 'tcp'}
                    onChange={e => setAdv({ ...adv, max_latency_ms: e.target.value })} />
                </div>
                <div style={form.kind === 'tcp' ? { opacity: 0.45 } : undefined}
                  title={form.kind === 'tcp' ? t('pr.condUrlOnly') : undefined}>
                  <div className="flex justify-between text-[11px] text-[var(--text-faint)] mb-1">
                    <span>{t('pr.condCert')}</span><span>{t('pr.condOff')}</span>
                  </div>
                  <input className="input num" inputMode="numeric" placeholder="0" value={adv.cert_days_min}
                    disabled={form.kind === 'tcp'}
                    onChange={e => setAdv({ ...adv, cert_days_min: e.target.value })} />
                </div>
                <div>
                  <div className="flex justify-between text-[11px] text-[var(--text-faint)] mb-1">
                    <span>{t('pr.condInterval')}</span><span>{t('pr.condGlobal')}</span>
                  </div>
                  <input className="input num" inputMode="numeric" placeholder="0" value={adv.interval_s}
                    onChange={e => setAdv({ ...adv, interval_s: e.target.value })} />
                </div>
              </div>
              <div className="text-[11px] text-[var(--text-faint)] mt-2">{t('pr.advHint', { n: Number(adv.fail_threshold) || 3, m: Number(adv.success_threshold) || 2 })}</div>
            </div>
          </div>
        </div>
      </div>

      {/* 空状态：说明拨测能干什么 */}
      {!probes.length && !loadErr && (
        <div className="card p-10 text-center">
          <div className="flex justify-center mb-3 text-[var(--text-faint)]"><Ic name="radio" size={30} sw={1.5} /></div>
          <div className="text-[13.5px] font-semibold text-[var(--text-hi)] mb-1.5">{t('pr.emptyTitle')}</div>
          <div className="text-[12.5px] text-[var(--text-mute)] max-w-md mx-auto leading-relaxed">{t('pr.emptyDesc')}</div>
        </div>
      )}

      {/* 拨测卡片 */}
      {probes.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {probes.map((p, i) => (
            <div key={p.id} className="rise-in h-full" style={{ animationDelay: `${Math.min(i, 6) * 45}ms` }}>
              <ProbeCard p={p} logs={logs[p.id] ?? EMPTY_LOGS} running={runningId === p.id}
                onRun={runNow} onAskDelete={setDelTarget} />
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog open={!!delTarget}
        title={delTarget ? t('pr.delTitle', { n: delTarget.name }) : ''}
        body={delTarget ? t('pr.delBody', { target: delTarget.target }) : ''}
        onConfirm={doDelete} onCancel={() => setDelTarget(null)} />
    </div>
  )
}
