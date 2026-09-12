import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, fmtNet, fmtTime, type Finding } from '../api'
import { useT } from '../i18n'
import { PageHead } from '../ui'
import { Ic } from '../icons'
import { HealthRing, LineChart } from '../charts'
import { useChartPalette } from '../theme'

type Detail = {
  host: { id: number; name: string; hostname: string; group: string; username: string; mock: boolean }
  metrics: { ts: number; cpu: number; mem: number; disk: number; net_in: number; net_out: number; load1: number; swap?: number }[]
  findings: Finding[]
  extras: { top_proc: string; failed_services: string[]; logins: { user: string; tty: string; ip: string; when: string }[]; cert_days_left: number | null; docker_containers?: { name: string; state: string; status: string; image: string }[] }
}

  const TABS = ['进程', '服务与登录', '证书', 'IO 与温度', '端口与暴露'] as const
// TABS 原值用作 state/比较值保持不动，展示文案另走 i18n
const TAB_LABEL: Record<(typeof TABS)[number], string> = {
  '进程': 'hd.tab.proc',
  '服务与登录': 'hd.tab.svc',
  '证书': 'hd.tab.cert',
  'IO 与温度': 'hd.tab.io',
  '端口与暴露': 'hd.tab.port',
}

export default function HostDetail() {
  const { t } = useT()
  const { id } = useParams()
  const [d, setD] = useState<Detail | null>(null)
  const [err, setErr] = useState('')
  const [range, setRange] = useState(240)
  const [tab, setTab] = useState<(typeof TABS)[number]>('进程')
  const [portInfo, setPortInfo] = useState<{ ports: { port: number; addr: string; proc?: string }[]; baseline: number[]; new_ports: number[] } | null>(null)
  const P = useChartPalette()

  useEffect(() => {
    if (tab === '端口与暴露' && !portInfo) {
      api<{ ports: any[]; baseline: number[]; new_ports: number[] }>(`/hosts/${id}/ports`)
        .then(setPortInfo).catch(() => { /* noop */ })
    }
  }, [tab, id, portInfo])

  useEffect(() => {
    let alive = true
    // SSE 刷新失败不打断已有数据；首屏失败给错误态 + 重试，不再永远卡「加载中」
    api<Detail>(`/hosts/${id}?range_min=${range}`)
      .then(x => { if (alive) { setD(x); setErr('') } })
      .catch(e => { if (alive && !d) setErr(e.message) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, range])

  const reload = () => { setErr(''); api<Detail>(`/hosts/${id}?range_min=${range}`).then(setD).catch(e => setErr(e.message)) }

  // 巡检心跳：把时间窗切成 48 桶，每桶取桶内最差状态（UK ping chart 式的上下条带）
  // 注意：hook 必须在下面所有提前 return 之前调用，否则 d 加载完成后 hooks 数量变化会崩
  const emptyMetrics: Detail['metrics'] = []
  const heartbeat = useMemo(() => {
    const now = Date.now() / 1000
    const span = range * 60
    const start = now - span
    const bw = span / 48
    const buckets = Array.from({ length: 48 }, (_, i) => ({ state: 'none' as 'ok' | 'warn' | 'crit' | 'none', from: start + i * bw }))
    for (const m of d?.metrics ?? emptyMetrics) {
      const idx = Math.min(47, Math.max(0, Math.floor((m.ts - start) / bw)))
      const st: 'ok' | 'warn' | 'crit' = (m.disk >= 95 || m.mem >= 92) ? 'crit'
        : (m.disk >= 85 || m.mem >= 85 || m.cpu >= 85) ? 'warn' : 'ok'
      const b = buckets[idx]
      if (st === 'crit' || (st === 'warn' && b.state !== 'crit') || (st === 'ok' && b.state === 'none')) b.state = st
    }
    return buckets
  }, [d, range])
  const HB_COLOR = { ok: 'var(--ok)', warn: 'var(--warn)', crit: 'var(--crit)', none: 'var(--border)' } as const
  const HB_LABEL = { ok: 'hd.hb.ok', warn: 'sev.warn', crit: 'sev.crit', none: 'hd.hb.none' } as const

  if (err && !d) return (
    <div className="fade-in">
      <PageHead title={t('hd.title')} />
      <div className="card p-10 text-center">
        <div className="text-3xl mb-3">📡</div>
        <div className="text-[13px] text-[var(--text-mute)] mb-1">{t('hd.loadFailed')}</div>
        <div className="text-[11.5px] text-[var(--text-faint)] mono mb-4">{err}</div>
        <button className="btn btn-primary" onClick={reload}><Ic name="refresh" size={13} /> {t('btn.retry')}</button>
        <div className="text-[11px] text-[var(--text-faint)] mt-4">{t('hd.loadFailedHint')}</div>
      </div>
    </div>
  )
  if (!d) return <div className="text-[var(--text-faint)]">{t('hd.loading')}</div>
  // extras 各键可缺省（旧数据/单探针失败），全部给安全默认值 —— 每个字段的 .map/.length 都不能裸调
  const ex = (d.extras ?? {}) as Detail['extras']
  const logins = Array.isArray(ex.logins) ? ex.logins : []
  const failedServices = Array.isArray(ex.failed_services) ? ex.failed_services : []
  const containers = Array.isArray(ex.docker_containers) ? ex.docker_containers : []
  const certDays = typeof ex.cert_days_left === 'number' ? ex.cert_days_left : null
  const ioRead = typeof (ex as any).disk_io_read === 'number' ? (ex as any).disk_io_read : 0
  const ioWrite = typeof (ex as any).disk_io_write === 'number' ? (ex as any).disk_io_write : 0
  const tempC = typeof (ex as any).temp_c === 'number' ? (ex as any).temp_c : 0
  const latest = d.metrics.at(-1)
  const score = 100 - d.findings.reduce((s, f) => s + (f.severity === 'crit' ? 25 : 8), 0)
  const chartData = d.metrics.map(m => [m.ts * 1000, m.cpu, m.mem, m.disk])
  const marks = d.findings.map(f => ({ ts: f.ts, label: f.title }))
  const isPrivate = (ip: string) => ip.startsWith('10.') || ip.startsWith('192.168.') || ip.startsWith('172.')
  // 容器状态 → 展示色:running 健康,exited/dead 异常,created/paused/restarting 等过渡态给警告色
  const dockerColor = (state: string) =>
    state === 'running' ? 'var(--ok)' : (state === 'exited' || state === 'dead') ? 'var(--crit)' : 'var(--warn)'

  return (
    <div className="fade-in">
      <PageHead title={d.host.name} sub={`${d.host.hostname} · SSH ${d.host.username}@${d.host.hostname} · ${d.host.mock ? t('hd.mockHost') : t('hd.realHost')} · ${t('hd.findingsMarked')}`}>
        {[[60, 'hd.range.1h'], [240, 'hd.range.4h'], [1440, 'hd.range.24h'], [10080, 'hd.range.7d'], [43200, 'hd.range.30d'], [129600, 'hd.range.90d']].map(([r, l]) => (
          <button key={r as number} onClick={() => setRange(r as number)}
            className={`pill ${range === r ? '' : 'text-[var(--text-mute)]'}`}
            style={range === r ? { background: 'var(--accent-dim)', color: 'var(--accent)' } : { background: 'var(--neutral-bg)' }}>
            {t(l as string)}
          </button>
        ))}
      </PageHead>

      <div className="flex flex-col xl:flex-row gap-4 items-stretch mb-5">
        <div className="card p-4 flex items-center gap-4 w-full xl:w-72 xl:shrink-0">
          <HealthRing score={score} size={80} />
          <div className="text-sm">
            <div className="text-[var(--text-hi)] font-semibold">{t('hd.findingCount', { n: d.findings.length })}</div>
            <div className="text-[11.5px] text-[var(--text-faint)] mt-1.5 space-y-0.5">
              <div className="num">{t('hd.metric.cpuMem', { c: latest?.cpu.toFixed(0) ?? '', m: latest?.mem.toFixed(0) ?? '' })}</div>
              <div className="num">{t('hd.metric.diskLoad', { d: latest?.disk.toFixed(0) ?? '', l: latest?.load1?.toFixed(2) ?? '' })}</div>
              {!!latest?.swap && latest.swap > 0 && (
                <div className="num">Swap {latest.swap.toFixed(0)}%</div>
              )}
              <div className="num">{t('hd.metric.netIn', { v: fmtNet(latest?.net_in ?? 0) })}</div>
            </div>
          </div>
        </div>
      <div className="card p-3 flex-1">
        <LineChart data={chartData} marks={marks} height={230} series={[
          { name: 'CPU', key: 1, color: P['--m-cpu'] },
          { name: t('hd.series.mem'), key: 2, color: P['--m-mem'] },
          { name: t('hd.series.disk'), key: 3, color: P['--m-disk'] },
        ]} />
        <div className="mt-2.5 px-1">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-[11px] text-[var(--text-faint)]">{t('hd.heartbeat')}</span>
            <div className="flex items-center gap-2.5 ml-auto text-[10px] text-[var(--text-faint)]">
              {(['ok', 'warn', 'crit'] as const).map(k => (
                <span key={k} className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-sm" style={{ background: HB_COLOR[k] }} />{t(HB_LABEL[k])}
                </span>
              ))}
            </div>
          </div>
          <div className="flex gap-[2px] h-4">
            {heartbeat.map((b, i) => (
              <div key={i} className="flex-1 rounded-[2px] transition-colors" title={`${fmtTime(b.from)} · ${t(HB_LABEL[b.state])}`}
                style={{ background: HB_COLOR[b.state], opacity: b.state === 'none' ? 0.5 : 0.9 }} />
            ))}
          </div>
        </div>
      </div>
      </div>

      {d.findings.length > 0 && (
        <div className="space-y-3 mb-5">
          {d.findings.map((f, i) => (
            <div key={f.id} className="card p-4 fade-in rise-in"
              style={{
                animationDelay: `${Math.min(i, 6) * 50}ms`,
                borderLeft: `3px solid ${f.severity === 'crit' ? 'var(--crit)' : 'var(--warn)'}`,
              }}>
              <div className="flex items-center gap-3 flex-wrap">
                <span className="pill" style={{ background: f.severity === 'crit' ? 'var(--crit-bg)' : 'var(--warn-bg)', color: f.severity === 'crit' ? 'var(--crit)' : 'var(--warn)' }}>
                  {t(`sev.${f.severity}`)}
                </span>
                <span className="font-semibold text-[14px]">{f.title}</span>
                <span className="text-[11px] text-[var(--text-faint)] num ml-auto">{fmtTime(f.ts)} · {f.status === 'analyzed' ? t('status.analyzed') : t('status.open')}</span>
              </div>
              {f.card && (
                <div className="mt-3 text-[13px] inset p-3">
                  <span className="text-[var(--accent)] font-semibold">{t('hd.rootCause')}</span>{f.card.root_cause}
                  {f.card.proposal_id && <div className="text-[11px] text-[var(--text-faint)] mt-1.5">{t('hd.proposalCreated')}</div>}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="card p-4">
        <div className="flex gap-2 mb-3.5">
          {TABS.map(tabName => (
            <button key={tabName} onClick={() => setTab(tabName)}
              className={`pill ${tab === tabName ? '' : 'text-[var(--text-mute)]'}`}
              style={tab === tabName ? { background: 'var(--accent-dim)', color: 'var(--accent)' } : { background: 'var(--neutral-bg)' }}>
              {t(TAB_LABEL[tabName])}
            </button>
          ))}
        </div>
        <div key={tab} className="fade-in">
        {tab === '进程' && (
          <pre className="text-[12px] whitespace-pre-wrap mono leading-relaxed m-0 px-3 py-3 rounded-lg overflow-x-auto"
            style={{ background: 'var(--term-bg)', border: '1px solid #223049', color: '#8fa3bd' }}>{d.extras?.top_proc || '—'}</pre>
        )}
        {tab === '服务与登录' && (
          <div className="text-[13px] space-y-3">
            <div>{t('hd.failedServices')}{failedServices.length
              ? failedServices.map(s => <span key={s} className="pill mr-1.5" style={{ background: 'var(--crit-bg)', color: 'var(--crit)' }}>{s}</span>)
              : <span className="text-[var(--ok)]">{t('hd.none')}</span>}</div>
            {/* 容器：docker ps -a 清单只读展示；非 running 容器的告警/恢复由规则引擎负责,这里仅陈列 */}
            <div>
              <div className="flex items-center gap-2 text-[var(--text-mute)] text-[12px]">
                {t('hd.docker.title')}
                <span className="pill num" style={{ background: 'var(--neutral-bg)', color: 'var(--text-faint)', fontSize: 10, padding: '1px 7px' }}>{containers.length}</span>
              </div>
              {containers.length ? (
                <div className="mt-1 text-[12px]">
                  {containers.map(c => (
                    <div key={c.name} className="flex items-center gap-2.5 py-1.5 border-b border-[var(--border)] last:border-0">
                      <span className="w-2 h-2 rounded-full shrink-0" style={{ background: dockerColor(c.state) }} title={c.state} />
                      <span className="font-semibold shrink-0">{c.name}</span>
                      <span className="mono text-[11.5px] text-[var(--text-mute)] flex-1 min-w-0 truncate" title={c.image}>{c.image}</span>
                      <span className="text-[11.5px] text-[var(--text-faint)] shrink-0">{c.status}</span>
                    </div>
                  ))}
                </div>
              ) : <div className="text-[var(--text-faint)] text-[12px]">{t('hd.docker.none')}</div>}
            </div>
            <div className="text-[var(--text-mute)] text-[12px]">{t('hd.recentLogins')}</div>
            {logins.length ? (
              <div className="overflow-x-auto">
                <table className="text-[12px] w-full">
                <tbody>
                  {logins.map((l, i) => (
                    <tr key={i} style={{ color: isPrivate(l.ip) ? 'var(--text)' : 'var(--crit)' }}>
                      <td className="py-1.5 pr-8 font-medium">{l.user}</td>
                      <td className="pr-8 text-[var(--text-faint)]">{l.tty}</td>
                      <td className="pr-8 mono">{l.ip}{!isPrivate(l.ip) && t('hd.externalSource')}</td>
                      <td className="text-[var(--text-faint)] num">{l.when}</td>
                    </tr>
                  ))}
                </tbody>
                </table>
              </div>
            ) : <div className="text-[var(--text-faint)] text-[12px]">{t('hd.noLogins')}</div>}
          </div>
        )}
        {tab === '证书' && (
          <div className="text-[13px]">
            {t('hd.certValid')}
            <span className="font-bold ml-1.5 num" style={{ color: certDays !== null && certDays < 14 ? 'var(--warn)' : 'var(--ok)' }}>
              {t('hd.certDays', { n: certDays ?? '—' })}
            </span>
            <div className="text-[11px] text-[var(--text-faint)] mt-1.5">{t('hd.certHint')}</div>
          </div>
        )}
        {tab === 'IO 与温度' && (
          <div className="text-[13px] space-y-2.5">
            <div>{t('hd.diskIO')}
              {t('hd.read')} <b className="num ml-1">{(ioRead / 1024).toFixed(2)} MB/s</b>
              <span className="text-[var(--text-faint)] mx-2">·</span>
              {t('hd.write')} <b className="num ml-1" style={{ color: ioWrite >= 80 * 1024 ? 'var(--warn)' : 'inherit' }}>
                {(ioWrite / 1024).toFixed(2)} MB/s</b>
              {ioRead === 0 && ioWrite === 0 && <span className="text-[var(--text-faint)] ml-2">{t('hd.ioNoData')}</span>}
            </div>
            <div>{t('hd.temp')}
              {tempC > 0
                ? <b className="num ml-1" style={{ color: tempC >= 80 ? 'var(--crit)' : tempC >= 70 ? 'var(--warn)' : 'var(--ok)' }}>{tempC.toFixed(1)}°C</b>
                : <span className="text-[var(--text-faint)] ml-1">{t('hd.noTempSensor')}</span>}
            </div>
            <div className="text-[11px] text-[var(--text-faint)]">{t('hd.ioHint')}</div>
          </div>
        )}
        {tab === '端口与暴露' && (
          <div className="text-[13px]">
            {!portInfo && <div className="text-[var(--text-faint)]">{t('hd.portLoading')}</div>}
            {portInfo && (
              <>
                <div className="flex items-center gap-2.5 mb-2.5 flex-wrap">
                  <span>{t('hd.listening')} <b className="num">{portInfo.ports.length}</b> {t('hd.portsUnit')}</span>
                  <span className="text-[var(--text-faint)]">·</span>
                  <span>{t('hd.baseline')} <b className="num">{portInfo.baseline.length}</b> {t('hd.baselineUnit')}</span>
                  {portInfo.new_ports.length > 0 && (
                    <span className="pill" style={{ background: 'var(--warn-bg)', color: 'var(--warn)' }}>
                      {t('hd.outOfBaselineN', { n: portInfo.new_ports.join(', ') })}
                    </span>
                  )}
                  <button className="btn btn-ghost ml-auto shrink-0" style={{ fontSize: 11 }}
                    title={t('hd.resetBaselineTitle')}
                    onClick={() => api(`/hosts/${id}/ports/baseline`, { method: 'DELETE' })
                      .then(() => setPortInfo(null))}>{t('hd.resetBaseline')}</button>
                </div>
                <div className="overflow-x-auto">
                  <table className="text-[12px] w-full">
                    <thead>
                      <tr className="text-[11px] text-[var(--text-faint)] text-left border-b border-[var(--border)]">
                        <th className="py-2 font-medium">{t('hd.col.port')}</th><th className="font-medium">{t('hd.col.addr')}</th>
                        <th className="font-medium">{t('hd.col.proc')}</th><th className="font-medium">{t('hd.baseline')}</th><th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {portInfo.ports.map(p => {
                        const inBaseline = portInfo.baseline.includes(p.port)
                        return (
                          <tr key={`${p.addr}:${p.port}`} className="border-b border-[var(--border)] last:border-0">
                            <td className="py-1.5 font-bold num">{p.port}</td>
                            <td className="mono text-[var(--text-mute)]">{p.addr}</td>
                            <td className="text-[var(--text-mute)]">{p.proc || '—'}</td>
                            <td>
                              {inBaseline
                                ? <span className="pill" style={{ background: 'var(--ok-bg)', color: 'var(--ok)', fontSize: 10 }}>{t('hd.inBaseline')}</span>
                                : <span className="pill" style={{ background: 'var(--warn-bg)', color: 'var(--warn)', fontSize: 10 }}>{t('hd.notInBaseline')}</span>}
                            </td>
                            <td className="text-right">
                              {!inBaseline && (
                                <button className="text-[11.5px] text-[var(--text-faint)] hover:text-[var(--accent)]"
                                  title={t('hd.addToBaselineTitle')}
                                  onClick={() => api(`/hosts/${id}/ports/baseline`, { method: 'POST', body: JSON.stringify({ ports: [p.port] }) })
                                    .then(() => setPortInfo(null))}>{t('hd.addToBaseline')}</button>
                              )}
                            </td>
                          </tr>
                        )
                      })}
                      {!portInfo.ports.length && (
                        <tr><td colSpan={5} className="py-3 text-[var(--text-faint)]">{t('hd.noPorts')}</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <div className="text-[11px] text-[var(--text-faint)] mt-2.5 leading-relaxed">
                  {t('hd.portsHint')}
                </div>
              </>
            )}
          </div>
        )}
        </div>
      </div>
    </div>
  )
}
