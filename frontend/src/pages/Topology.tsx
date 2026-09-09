import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import * as echarts from 'echarts'
import { api, subscribe, type HostCard } from '../api'
import { Ic } from '../icons'
import { PageHead } from '../ui'
import { useT } from '../i18n'
import { alpha, useChartPalette } from '../theme'

/**
 * Fleet 拓扑 —— 「雷达观测站」风格：
 * Hub 居中发光，主机按健康状态分层（严重内圈 → 警告中圈 → 健康外圈），
 * 节点为径向渐变圆 + 状态色粗环 + 内嵌图标 + 外发光，卡片顶部常驻状态统计条。
 */
const RING: Record<HostCard['status'], number> = { crit: 150, warn: 240, ok: 330, offline: 330 }

export default function Topology() {
  const { t, lang } = useT()
  const [hosts, setHosts] = useState<HostCard[]>([])
  const nav = useNavigate()
  useEffect(() => {
    api<{ hosts: HostCard[] }>('/fleet').then(d => setHosts(d.hosts))
    return subscribe(() => api<{ hosts: HostCard[] }>('/fleet').then(d => setHosts(d.hosts)))
  }, [])

  const P = useChartPalette()
  // callback ref：画布容器随 hosts 数据条件渲染，用 state 触发初始化 effect 重跑
  const [box, setBox] = useState<HTMLDivElement | null>(null)
  const chartRef = useRef<echarts.ECharts | undefined>(undefined)
  const navRef = useRef(nav); navRef.current = nav
  const hostsRef = useRef(hosts); hostsRef.current = hosts

  // 初始化只做一次（容器挂载 + palette 就绪后），数据/主题变化走 setOption —— 保住用户的缩放/平移状态
  useEffect(() => {
    if (!box || chartRef.current || !P['--bg-panel']) return
    // 兜底清理同容器上的残留实例（HMR/StrictMode 场景），避免 "already initialized" 报错
    echarts.getInstanceByDom(box)?.dispose()
    const chart = echarts.init(box)
    chartRef.current = chart
    chart.on('click', (p: any) => {
      const id = String(p?.data?.id ?? '')
      if (p?.dataType === 'node' && id && !id.startsWith('hub') && !id.startsWith('deco'))
        navRef.current(`/host/${id}`)
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); chart.dispose(); chartRef.current = undefined }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [box, P['--bg-panel']])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !hosts.length || !P['--bg-panel']) return

    const colorOf = (h: HostCard) =>
      h.status === 'ok' ? P['--ok'] : h.status === 'warn' ? P['--warn']
        : h.status === 'offline' ? P['--text-faint'] : P['--crit']
    const fillOf = (h: HostCard) =>
      h.status === 'ok' ? P['--node-ok'] : h.status === 'warn' ? P['--node-warn']
        : h.status === 'offline' ? P['--text-faint'] : P['--node-crit']
    // 径向渐变：中心近乎透明 → 状态色薄雾 → 底色实体，比平涂立体
    const gradientOf = (h: HostCard) => ({
      type: 'radial', x: 0.5, y: 0.5, r: 0.5,
      colorStops: [
        { offset: 0, color: alpha(colorOf(h), 0.04) },
        { offset: 0.62, color: alpha(colorOf(h), 0.1) },
        { offset: 1, color: fillOf(h) },
      ],
    })

    const nodes: any[] = [
      // Hub 双层光环（纯装饰，不响应交互）
      { id: 'deco-1', name: '', x: 0, y: 0, symbolSize: 118, fixed: true, silent: true,
        itemStyle: { color: 'transparent', borderColor: P['--accent'], borderWidth: 1.5, opacity: 0.32 } },
      { id: 'deco-2', name: '', x: 0, y: 0, symbolSize: 160, fixed: true, silent: true,
        itemStyle: { color: 'transparent', borderColor: P['--accent'], borderWidth: 1, opacity: 0.14 } },
      { id: 'hub', name: 'Hermes Watch', symbolSize: 96, category: 0, fixed: true, x: 0, y: 0,
        itemStyle: {
          color: {
            type: 'radial', x: 0.5, y: 0.5, r: 0.5,
            colorStops: [
              { offset: 0, color: alpha(P['--accent'], 0.16) },
              { offset: 0.7, color: P['--bg-inset'] },
              { offset: 1, color: P['--bg-panel'] },
            ],
          },
          borderColor: P['--accent'], borderWidth: 2.5,
          shadowBlur: 32, shadowColor: alpha(P['--accent'], 0.6),
        },
        label: { show: true, position: 'inside',
          formatter: `{icon|🐚}\n{name|Hermes Watch}`,
          rich: { icon: { fontSize: 22, align: 'center', lineHeight: 28 },
                  name: { color: P['--accent'], fontSize: 11, fontWeight: 700, align: 'center', lineHeight: 15 } } },
      },
    ]
    const links: any[] = []

    // 分层布环：每个状态环内均匀分布 + 微抖动打破呆板对称；主机少时整体收拢
    const byStatus: Record<string, HostCard[]> = { crit: [], warn: [], ok: [], offline: [] }
    hosts.forEach(h => (byStatus[h.status] ?? byStatus.ok).push(h))
    const shrink = hosts.length <= 3 ? 0.78 : 1
    const ringStart: Record<string, number> = { crit: 0.4, warn: 0.9, ok: 0.05, offline: 0.05 }
    for (const st of ['crit', 'warn', 'ok', 'offline'] as const) {
      const arr = byStatus[st]
      arr.forEach((h, i) => {
        const angle = ringStart[st] + (i / arr.length) * Math.PI * 2
        const r = RING[st] * shrink
        const x = Math.cos(angle) * r + Math.sin(h.id * 9.7) * 12
        const y = Math.sin(angle) * r + Math.cos(h.id * 5.3) * 9
        const c = colorOf(h)
        nodes.push({
          id: String(h.id), name: h.name, x, y, fixed: true,
          symbolSize: 56 + Math.min(h.open_findings, 6) * 6,
          itemStyle: {
            color: gradientOf(h),
            borderColor: c, borderWidth: 2.5,
            shadowBlur: st === 'ok' ? 16 : 26, shadowColor: alpha(c, 0.55),
          },
          label: { show: true, position: 'bottom', distance: 7,
            formatter: `{name|${h.name}}\n{meta|${h.group}${h.latest ? ` · CPU ${h.latest.cpu.toFixed(0)}% · ${t('topo.disk')} ${h.latest.disk.toFixed(0)}%` : ''}}\n{score|● ${h.score}}`,
            rich: {
              name: { color: P['--text-hi'], fontSize: 12.5, fontWeight: 600, align: 'center', lineHeight: 19 },
              meta: { color: P['--text-faint'], fontSize: 10, align: 'center', lineHeight: 15 },
              score: { color: c, fontSize: 11, fontWeight: 700, align: 'center', lineHeight: 16 },
            } },
        })
        links.push({
          source: 'hub', target: String(h.id),
          lineStyle: { color: c, width: st === 'ok' ? 1.4 : st === 'warn' ? 2.2 : 2.6,
                       curveness: 0.12, type: st === 'crit' ? [7, 7] : st === 'offline' ? [3, 6] : 'solid',
                       opacity: st === 'ok' ? 0.75 : st === 'offline' ? 0.5 : 1 },
        })
      })
    }

    const bar = (label: string, v: number | undefined, color: string) => `<div style="display:flex;align-items:center;gap:8px;margin-top:4px">
        <span style="width:34px;color:${P['--text-faint']}">${label}</span>
        <div style="flex:1;height:5px;border-radius:3px;background:${P['--border']};overflow:hidden">
          <div style="width:${Math.min(100, v ?? 0)}%;height:100%;background:${color}"></div></div>
        <b class="num" style="width:38px;text-align:right">${(v ?? 0).toFixed(0)}%</b></div>`

    chart.setOption({
      animationDuration: 500,
      tooltip: {
        backgroundColor: P['--bg-panel'], borderColor: P['--border-strong'],
        textStyle: { color: P['--text'], fontSize: 12 },
        extraCssText: 'box-shadow: 0 8px 24px rgba(0,0,0,.25); border-radius: 10px; padding: 10px 12px;',
        formatter: (p: any) => {
          if (p?.dataType !== 'node') return ''
          const id = String(p?.data?.id ?? '')
          if (id === 'hub' || id.startsWith('deco')) return t('topo.hubTooltip')
          const h = hostsRef.current.find(x => String(x.id) === id)
          if (!h) return p.name ?? ''
          const l = h.latest ?? ({} as any)
          return `<div style="min-width:212px"><b>${h.name}</b> <span style="color:${P['--text-faint']}">· ${h.group}</span>
            <div style="margin-top:3px">${t('topo.healthScore')} <b style="color:${colorOf(h)}">${h.score}</b> · ${t('topo.openFindingsN', { n: h.open_findings })}</div>
            ${bar('CPU', l.cpu, P['--m-cpu'])}${bar(t('topo.mem'), l.mem, P['--m-mem'])}${bar(t('topo.disk'), l.disk, P['--m-disk'])}
            <div style="margin-top:6px;color:${P['--text-faint']};font-size:11px"><i>${t('topo.clickNodeHint')}</i></div></div>`
        },
      },
      series: [{
        type: 'graph', layout: 'none', roam: true, scaleLimit: { min: 0.4, max: 3 },
        data: nodes, links,
        categories: [{ name: 'Hub' }, { name: 'hosts' }],
        emphasis: { scale: 1.12, focus: 'adjacency', itemStyle: { shadowBlur: 34 } },
      }],
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hosts, P, lang])

  const stats = useMemo(() => ({
    ok: hosts.filter(h => h.status === 'ok').length,
    warn: hosts.filter(h => h.status === 'warn').length,
    crit: hosts.filter(h => h.status === 'crit').length,
    offline: hosts.filter(h => h.status === 'offline').length,
    findings: hosts.reduce((s, h) => s + h.open_findings, 0),
  }), [hosts])

  const chip = (color: string, bg: string, dotColor: string, label: string) => (
    <span className="pill" style={{ background: bg, color, fontSize: 11 }}>
      <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ background: dotColor }} />
      {label}
    </span>
  )

  return (
    <div className="fade-in flex flex-col" style={{ height: 'calc(100vh - 120px)' }}>
      <PageHead title={t('nav.topology')} sub={t('topo.sub')} />
      <div className="card flex-1 min-h-0 flex flex-col overflow-hidden">
        <div className="shrink-0 flex items-center gap-2 flex-wrap px-3.5 py-2.5 border-b" style={{ borderColor: 'var(--border)' }}>
          <span className="text-[12.5px] font-semibold text-[var(--text-hi)]">{t('topo.panelTitle')}</span>
          {chip('var(--ok)', 'var(--ok-bg)', 'var(--ok)', t('topo.nHealthy', { n: stats.ok }))}
          {stats.warn > 0 && chip('var(--warn)', 'var(--warn-bg)', 'var(--warn)', t('topo.nWarn', { n: stats.warn }))}
          {stats.crit > 0 && chip('var(--crit)', 'var(--crit-bg)', 'var(--crit)', t('topo.nCrit', { n: stats.crit }))}
          {stats.offline > 0 && chip('var(--text-mute)', 'var(--neutral-bg)', 'var(--text-faint)', t('topo.nOffline', { n: stats.offline }))}
          {stats.findings > 0 && chip('var(--text-mute)', 'var(--neutral-bg)', 'var(--text-faint)', t('topo.nFindings', { n: stats.findings }))}
          <span className="ml-auto text-[10.5px] text-[var(--text-faint)]">{t('topo.controlsHint')}</span>
        </div>
        <div className="relative flex-1 min-h-0 topo-bg">
          {hosts.length === 0 ? (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="text-center">
                <div className="flex justify-center mb-2 text-[var(--text-faint)]"><Ic name="radio" size={30} sw={1.5} /></div>
                <div className="text-[13px] text-[var(--text-faint)]">
                  {t('topo.emptyBefore')}<a href="/settings" className="text-[var(--accent)] hover:underline">{t('topo.settingsLink')}</a>{t('topo.emptyMiddle')}<a href="/enroll" className="text-[var(--accent)] hover:underline">{t('topo.enrollLink')}</a>{t('topo.emptyAfter')}
                </div>
              </div>
            </div>
          ) : <div ref={setBox} className="absolute inset-0" />}
        </div>
      </div>
    </div>
  )
}
