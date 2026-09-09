import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, fmtNet, fmtTime, SEV, type Finding } from '../api'
import { PageHead } from '../ui'
import { HealthRing, LineChart } from '../charts'
import { useChartPalette } from '../theme'

type Detail = {
  host: { id: number; name: string; hostname: string; group: string; username: string; mock: boolean }
  metrics: { ts: number; cpu: number; mem: number; disk: number; net_in: number; net_out: number; load1: number }[]
  findings: Finding[]
  extras: { top_proc: string; failed_services: string[]; logins: { user: string; tty: string; ip: string; when: string }[]; cert_days_left: number | null }
}

  const TABS = ['进程', '服务与登录', '证书', 'IO 与温度'] as const

export default function HostDetail() {
  const { id } = useParams()
  const [d, setD] = useState<Detail | null>(null)
  const [err, setErr] = useState('')
  const [range, setRange] = useState(240)
  const [tab, setTab] = useState<(typeof TABS)[number]>('进程')
  const P = useChartPalette()

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
  const HB_LABEL = { ok: '正常', warn: '警告', crit: '严重', none: '无数据' } as const

  if (err && !d) return (
    <div className="fade-in">
      <PageHead title="主机详情" />
      <div className="card p-10 text-center">
        <div className="text-3xl mb-3">📡</div>
        <div className="text-[13px] text-[var(--text-mute)] mb-1">主机数据加载失败</div>
        <div className="text-[11.5px] text-[var(--text-faint)] mono mb-4">{err}</div>
        <button className="btn btn-primary" onClick={reload}>↻ 重试</button>
        <div className="text-[11px] text-[var(--text-faint)] mt-4">后端可能正在重启，或主机不可达；稍候重试即可</div>
      </div>
    </div>
  )
  if (!d) return <div className="text-[var(--text-faint)]">加载中…</div>
  // extras 各键可缺省（旧数据/单探针失败），全部给安全默认值 —— 每个字段的 .map/.length 都不能裸调
  const ex = (d.extras ?? {}) as Detail['extras']
  const logins = Array.isArray(ex.logins) ? ex.logins : []
  const failedServices = Array.isArray(ex.failed_services) ? ex.failed_services : []
  const certDays = typeof ex.cert_days_left === 'number' ? ex.cert_days_left : null
  const ioRead = typeof (ex as any).disk_io_read === 'number' ? (ex as any).disk_io_read : 0
  const ioWrite = typeof (ex as any).disk_io_write === 'number' ? (ex as any).disk_io_write : 0
  const tempC = typeof (ex as any).temp_c === 'number' ? (ex as any).temp_c : 0
  const latest = d.metrics.at(-1)
  const score = 100 - d.findings.reduce((s, f) => s + (f.severity === 'crit' ? 25 : 8), 0)
  const chartData = d.metrics.map(m => [m.ts * 1000, m.cpu, m.mem, m.disk])
  const marks = d.findings.map(f => ({ ts: f.ts, label: f.title }))
  const isPrivate = (ip: string) => ip.startsWith('10.') || ip.startsWith('192.168.') || ip.startsWith('172.')

  return (
    <div className="fade-in">
      <PageHead title={d.host.name} sub={`${d.host.hostname} · SSH ${d.host.username}@${d.host.hostname} · ${d.host.mock ? '演示主机' : '真实主机'} · 发现时间在图表上以红色虚线标注`}>
        {[[60, '1 小时'], [240, '4 小时'], [1440, '24 小时']].map(([r, l]) => (
          <button key={r as number} onClick={() => setRange(r as number)}
            className={`pill ${range === r ? '' : 'text-[var(--text-mute)]'}`}
            style={range === r ? { background: 'var(--accent-dim)', color: 'var(--accent)' } : { background: 'var(--neutral-bg)' }}>
            {l}
          </button>
        ))}
      </PageHead>

      <div className="flex flex-col xl:flex-row gap-4 items-stretch mb-5">
        <div className="card p-4 flex items-center gap-4 w-full xl:w-72 xl:shrink-0">
          <HealthRing score={score} size={80} />
          <div className="text-sm">
            <div className="text-[var(--text-hi)] font-semibold">{d.findings.length} 条发现</div>
            <div className="text-[11.5px] text-[var(--text-faint)] mt-1.5 space-y-0.5">
              <div className="num">CPU {latest?.cpu.toFixed(0)}% · 内存 {latest?.mem.toFixed(0)}%</div>
              <div className="num">磁盘 {latest?.disk.toFixed(0)}% · 负载 {latest?.load1?.toFixed(2)}</div>
              <div className="num">网入 {fmtNet(latest?.net_in ?? 0)}</div>
            </div>
          </div>
        </div>
      <div className="card p-3 flex-1">
        <LineChart data={chartData} marks={marks} height={230} series={[
          { name: 'CPU', key: 1, color: P['--m-cpu'] },
          { name: '内存', key: 2, color: P['--m-mem'] },
          { name: '磁盘', key: 3, color: P['--m-disk'] },
        ]} />
        <div className="mt-2.5 px-1">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-[11px] text-[var(--text-faint)]">巡检心跳</span>
            <div className="flex items-center gap-2.5 ml-auto text-[10px] text-[var(--text-faint)]">
              {(['ok', 'warn', 'crit'] as const).map(k => (
                <span key={k} className="flex items-center gap-1">
                  <span className="w-2 h-2 rounded-sm" style={{ background: HB_COLOR[k] }} />{HB_LABEL[k]}
                </span>
              ))}
            </div>
          </div>
          <div className="flex gap-[2px] h-4">
            {heartbeat.map((b, i) => (
              <div key={i} className="flex-1 rounded-[2px] transition-colors" title={`${fmtTime(b.from)} · ${HB_LABEL[b.state]}`}
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
                  {SEV[f.severity].label}
                </span>
                <span className="font-semibold text-[14px]">{f.title}</span>
                <span className="text-[11px] text-[var(--text-faint)] num ml-auto">{fmtTime(f.ts)} · {f.status === 'analyzed' ? '已诊断' : '待诊断'}</span>
              </div>
              {f.card && (
                <div className="mt-3 text-[13px] inset p-3">
                  <span className="text-[var(--accent)] font-semibold">根因：</span>{f.card.root_cause}
                  {f.card.proposal_id && <div className="text-[11px] text-[var(--text-faint)] mt-1.5">已生成修复提案 → 诊断中心审批</div>}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="card p-4">
        <div className="flex gap-2 mb-3.5">
          {TABS.map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`pill ${tab === t ? '' : 'text-[var(--text-mute)]'}`}
              style={tab === t ? { background: 'var(--accent-dim)', color: 'var(--accent)' } : { background: 'var(--neutral-bg)' }}>
              {t}
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
            <div>失败服务：{failedServices.length
              ? failedServices.map(s => <span key={s} className="pill mr-1.5" style={{ background: 'var(--crit-bg)', color: 'var(--crit)' }}>{s}</span>)
              : <span className="text-[var(--ok)]">无</span>}</div>
            <div className="text-[var(--text-mute)] text-[12px]">最近登录</div>
            {logins.length ? (
              <div className="overflow-x-auto">
                <table className="text-[12px] w-full">
                <tbody>
                  {logins.map((l, i) => (
                    <tr key={i} style={{ color: isPrivate(l.ip) ? 'var(--text)' : 'var(--crit)' }}>
                      <td className="py-1.5 pr-8 font-medium">{l.user}</td>
                      <td className="pr-8 text-[var(--text-faint)]">{l.tty}</td>
                      <td className="pr-8 mono">{l.ip}{!isPrivate(l.ip) && ' ⚠ 外部来源'}</td>
                      <td className="text-[var(--text-faint)] num">{l.when}</td>
                    </tr>
                  ))}
                </tbody>
                </table>
              </div>
            ) : <div className="text-[var(--text-faint)] text-[12px]">暂无登录记录（探针未取到 last 输出）</div>}
          </div>
        )}
        {tab === '证书' && (
          <div className="text-[13px]">
            证书剩余有效期：
            <span className="font-bold ml-1.5 num" style={{ color: certDays !== null && certDays < 14 ? 'var(--warn)' : 'var(--ok)' }}>
              {certDays ?? '—'} 天
            </span>
            <div className="text-[11px] text-[var(--text-faint)] mt-1.5">检测 /etc/letsencrypt 下的证书，14 天内到期将产生警告发现</div>
          </div>
        )}
        {tab === 'IO 与温度' && (
          <div className="text-[13px] space-y-2.5">
            <div>磁盘 IO（agent 采样窗口均值）：
              读 <b className="num ml-1">{(ioRead / 1024).toFixed(2)} MB/s</b>
              <span className="text-[var(--text-faint)] mx-2">·</span>
              写 <b className="num ml-1" style={{ color: ioWrite >= 80 * 1024 ? 'var(--warn)' : 'inherit' }}>
                {(ioWrite / 1024).toFixed(2)} MB/s</b>
              {ioRead === 0 && ioWrite === 0 && <span className="text-[var(--text-faint)] ml-2">（无上报数据——出站 agent 主机才采集）</span>}
            </div>
            <div>温度：
              {tempC > 0
                ? <b className="num ml-1" style={{ color: tempC >= 80 ? 'var(--crit)' : tempC >= 70 ? 'var(--warn)' : 'var(--ok)' }}>{tempC.toFixed(1)}°C</b>
                : <span className="text-[var(--text-faint)] ml-1">未检测到温度传感器</span>}
            </div>
            <div className="text-[11px] text-[var(--text-faint)]">写入持续超 80 MB/s 或温度超 80°C 时规则引擎产生发现；阈值可在设置页调整</div>
          </div>
        )}
        </div>
      </div>
    </div>
  )
}
