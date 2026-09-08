import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, fmtTime, subscribe, type Event, type HostCard } from '../api'
import { PageHead, Stat } from '../ui'
import { Ic } from '../icons'
import { HealthRing, Spark } from '../charts'
import { alpha, useChartPalette } from '../theme'

// 分组着色从当前主题的语义 token 取值，日/夜各自有对比度合适的色板
const GROUP_ORDER = ['--accent', '--violet', '--ok', '--warn', '--crit', '--info']

export default function Fleet() {
  const P = useChartPalette()
  const [hosts, setHosts] = useState<HostCard[]>([])
  const [events, setEvents] = useState<Event[]>([])
  const [group, setGroup] = useState<string>('全部')
  const [sort, setSort] = useState<'score' | 'name' | 'findings'>('score')
  const nav = useNavigate()

  const load = () => api<{ hosts: HostCard[] }>('/fleet').then(d => setHosts(d.hosts))
  useEffect(() => {
    load()
    api<Event[]>('/events?limit=14').then(setEvents)
    return subscribe(() => {
      load()
      api<Event[]>('/events?limit=14').then(setEvents)
    })
  }, [])

  const groups = useMemo(() => ['全部', ...Array.from(new Set(hosts.map(h => h.group)))], [hosts])
  const shown = useMemo(() => {
    let arr = hosts.filter(h => group === '全部' || h.group === group)
    if (sort === 'score') arr = [...arr].sort((a, b) => a.score - b.score)
    if (sort === 'name') arr = [...arr].sort((a, b) => a.name.localeCompare(b.name))
    if (sort === 'findings') arr = [...arr].sort((a, b) => b.open_findings - a.open_findings)
    return arr
  }, [hosts, group, sort])

  const avg = hosts.length ? Math.round(hosts.reduce((s, h) => s + h.score, 0) / hosts.length) : 100
  const findings = hosts.reduce((s, h) => s + h.open_findings, 0)
  const critHosts = hosts.filter(h => h.status === 'crit').length
  const demoCount = hosts.filter(h => h.mock).length
  const groupColor = (g: string) => P[GROUP_ORDER[Math.max(0, groups.indexOf(g)) % GROUP_ORDER.length]] ?? P['--accent'] ?? '#22d3ee'

  const removeDemo = () => {
    if (!confirm(`移除 ${demoCount} 台演示主机及其全部数据？（真实主机不受影响，之后可在 Fleet 页恢复演示）`)) return
    api('/demo', { method: 'DELETE' }).then(load)
  }

  return (
    <div className="fade-in">
      <PageHead title="Fleet 总览" sub={`${hosts.length} 台主机 · 自动巡检每 60 秒 · 数据全程本地`}>
        <button className="btn btn-primary" onClick={() => api('/reports/generate', { method: 'POST' }).then(() => nav('/reports'))}>
          生成健康报告
        </button>
      </PageHead>

      {demoCount > 0 && (
        <div className="card p-4 mb-4 flex items-center gap-4" style={{ background: 'var(--violet-bg)', borderColor: 'var(--violet-border)' }}>
          <span className="shrink-0" style={{ color: 'var(--violet)' }}><Ic name="flask" size={22} sw={1.8} /></span>
          <div className="flex-1">
            <div className="text-[13px] font-semibold text-[var(--text-hi)]">
              演示引导模式 —— {demoCount} 台内置演示机，用于走通「告警 → 诊断 → 提案 → 执行」全流程
            </div>
            <div className="text-[11.5px] text-[var(--text-mute)] mt-1 leading-relaxed">
              真实接入两条路：<b>设置页 SSH 拉取</b>（填 IP + 密码；本机密钥免密主机密码留空即可）或 <b>接入中心出站 Agent</b>（Go 单二进制 / sh 脚本，免开入站端口）
            </div>
          </div>
          <a href="/settings" className="btn shrink-0" style={{ borderColor: 'var(--violet-border)' }}>接入真实服务器 →</a>
          <button className="btn btn-ghost shrink-0" onClick={removeDemo}>移除演示主机</button>
        </div>
      )}
      {demoCount === 0 && hosts.length > 0 && (
        <div className="flex items-center gap-2.5 mb-4 text-[12px] text-[var(--text-faint)]">
          <span className="flex items-center gap-1.5"><Ic name="radio" size={13} /> 真实接入模式 —— 全部为主机实采数据</span>
          <button className="pill" style={{ background: 'var(--neutral-bg)', color: 'var(--text-mute)' }}
            onClick={() => api('/demo/seed', { method: 'POST' }).then(load).catch(e => alert(e.message))}>
            ＋ 恢复演示引导
          </button>
        </div>
      )}

      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4 mb-6">
        <Stat label="Fleet 整体健康分" value={avg} hint={`${critHosts} 台严重 · ${findings} 条待处理`} accent={avg >= 90 ? 'var(--ok)' : avg >= 70 ? 'var(--warn)' : 'var(--crit)'} />
        <Stat label="健康主机" value={<span>{hosts.filter(h => h.status === 'ok').length}<span className="text-[14px] text-[var(--text-faint)]"> / {hosts.length}</span></span>} accent="var(--ok)" />
        <Stat label="待处理发现" value={findings} accent={findings ? 'var(--warn)' : 'var(--text-mute)'} hint="点击主机卡片查看诊断" />
        <Stat label="巡检状态" value={<span className="flex items-center gap-2 text-[18px]"><span className="pulse-dot" style={{ background: 'var(--ok)' }} />运行中</span>} hint="60s 周期 · SSE 实时推送" />
      </div>

      <div className="flex items-center gap-2 mb-4 flex-wrap">
        {groups.map(g => (
          <button key={g} onClick={() => setGroup(g)}
            className={`pill ${group === g ? '' : 'text-[var(--text-mute)]'}`}
            style={group === g
              ? { background: g === '全部' ? 'var(--accent-dim)' : alpha(groupColor(g), 0.12), color: g === '全部' ? 'var(--accent)' : groupColor(g), border: `1px solid ${g === '全部' ? 'var(--accent-border)' : alpha(groupColor(g), 0.27)}` }
              : { background: 'var(--neutral-bg)', border: '1px solid transparent' }}>
            {g !== '全部' && <span className="w-1.5 h-1.5 rounded-full" style={{ background: groupColor(g) }} />}
            {g}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-1.5 text-[12px] text-[var(--text-faint)]">
          排序
          {(['score', 'findings', 'name'] as const).map(s => (
            <button key={s} onClick={() => setSort(s)}
              className={`pill ${sort === s ? 'text-[var(--accent)]' : ''}`}
              style={sort === s ? { background: 'var(--accent-dim)' } : { background: 'var(--neutral-bg)', color: 'var(--text-mute)' }}>
              {{ score: '健康分', findings: '发现数', name: '名称' }[s]}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        {shown.map((h, i) => {
          const color = h.status === 'ok' ? (P['--ok'] ?? '#34d399') : h.status === 'warn' ? (P['--warn'] ?? '#fbbf24') : (P['--crit'] ?? '#f87171')
          return (
            <div key={h.id} className="card card-hover p-4 cursor-pointer rise-in"
              style={{ animationDelay: `${Math.min(i, 8) * 45}ms` }}
              onClick={() => nav(`/host/${h.id}`)}>
              <div className="flex items-start justify-between">
                <div>
                  <div className="font-semibold text-[14.5px] text-[var(--text-hi)] flex items-center gap-2">
                    {h.name}
                    <span className="pill" style={{ background: alpha(groupColor(h.group), 0.09), color: groupColor(h.group), fontSize: 10, padding: '1px 7px' }}>
                      {h.group}
                    </span>
                  </div>
                  <div className="text-[11.5px] text-[var(--text-faint)] mt-0.5 mono">{h.hostname}</div>
                </div>
                <span className="pill" style={{ background: alpha(color, 0.09), color, border: `1px solid ${alpha(color, 0.2)}` }}>
                  <span className="w-1.5 h-1.5 rounded-full" style={{ background: color }} />
                  {h.status === 'ok' ? '健康' : h.status === 'warn' ? '警告' : '严重'}
                </span>
              </div>
              <div className="flex items-center gap-3 mt-2">
                <HealthRing score={h.score} size={70} thickness={6} />
                <div className="flex-1 grid grid-cols-3 gap-1 text-center text-[11px] text-[var(--text-faint)]">
                  {[['CPU', h.latest?.cpu, 'var(--m-cpu)'], ['内存', h.latest?.mem, 'var(--m-mem)'], ['磁盘', h.latest?.disk, 'var(--m-disk)']].map(([k, v, c]) => (
                    <div key={k as string}>
                      <div className="text-[13px] font-semibold num" style={{ color: v as number > 85 ? 'var(--crit)' : 'var(--text)' }}>
                        {v == null ? '—' : (v as number).toFixed(0)}%
                      </div>
                      <div className="mt-0.5">{k}</div>
                      <div className="mt-1 h-1 rounded-full overflow-hidden" style={{ background: 'var(--border)' }}>
                        <div className="h-full rounded-full transition-all" style={{ width: `${Math.min(100, v as number ?? 0)}%`, background: c as string }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <div className="mt-2"><Spark data={h.spark} metric="disk" color={color} /></div>
              <div className="text-[11px] text-[var(--text-faint)] mt-1.5 flex justify-between">
                <span>发现 <span className={h.open_findings ? 'text-[var(--warn)] font-semibold' : ''}>{h.open_findings}</span></span>
                <span>{fmtTime(h.latest?.ts ?? 0).slice(-8)} 采集</span>
              </div>
            </div>
          )
        })}
      </div>

      <div className="card p-4 mt-6">
        <div className="text-[12.5px] font-semibold text-[var(--text-mute)] mb-2.5 flex items-center gap-2">
          <span className="pulse-dot" style={{ background: 'var(--accent)' }} /> 最近事件
        </div>
        <div className="space-y-1.5">
          {events.slice(0, 6).map(e => {
            const dot = e.kind === 'finding' ? 'var(--warn)' : e.kind === 'error' ? 'var(--crit)'
              : e.kind === 'proposal' ? 'var(--violet)' : e.kind === 'report' ? 'var(--ok)' : 'var(--accent)'
            return (
              <div key={e.id} className="flex items-center gap-3 text-[12.5px] fade-in">
                <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: dot }} />
                <span className="text-[var(--text-faint)] num w-14 shrink-0">{fmtTime(e.ts).slice(-8)}</span>
                {e.host_name && <span className="text-[var(--accent)] w-16 shrink-0 truncate">{e.host_name}</span>}
                <span className="text-[var(--text)] truncate">{e.message}</span>
              </div>
            )
          })}
          {!events.length && <div className="text-[var(--text-faint)] text-[12px] py-2">等待第一次巡检…</div>}
        </div>
      </div>
    </div>
  )
}
