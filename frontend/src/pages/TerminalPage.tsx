import { useEffect, useRef, useState } from 'react'
import '@xterm/xterm/css/xterm.css'
import { api, type Host } from '../api'
import { PageHead } from '../ui'
import { useChartPalette } from '../theme'

/**
 * Web 终端页：xterm.js + WebSocket。
 * 演示主机 → 模拟 shell（只读命令）；真实主机 → asyncssh PTY 透传。
 * 终端画布任何主题下都保持深色（--term-bg/--term-fg），更像真终端。
 */
export default function TerminalPage() {
  const [hosts, setHosts] = useState<Host[]>([])
  const [sel, setSel] = useState<number | null>(null)
  const [connected, setConnected] = useState(false)
  const [termReady, setTermReady] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<any>(null)
  const P = useChartPalette()

  useEffect(() => {
    api<{ hosts: Host[] }>('/fleet').then(d => {
      setHosts(d.hosts)
      setSel(s => s ?? d.hosts[0]?.id ?? null)
    })
  }, [])

  useEffect(() => {
    if (!boxRef.current) return
    let disposed = false
    Promise.all([import('@xterm/xterm'), import('@xterm/addon-fit')]).then(([{ Terminal }, { FitAddon }]) => {
      if (disposed || !boxRef.current) return
      const term = new Terminal({
        fontSize: 13, fontFamily: '"Cascadia Code", Consolas, monospace', cursorBlink: true,
        theme: {
          background: '#0e1420', foreground: '#d5dce8', cursor: '#22d3ee',
          selectionBackground: 'rgba(34,211,238,.25)',
          green: '#34d399', yellow: '#fbbf24', red: '#f87171', blue: '#38bdf8', magenta: '#a78bfa',
        },
      })
      const fit = new FitAddon()
      term.loadAddon(fit)
      term.open(boxRef.current)
      fit.fit()
      termRef.current = term
      setTermReady(true)
      window.addEventListener('resize', () => fit.fit())
    })
    return () => { disposed = true }
  }, [])

  // 主题切换时同步终端前景/光标色（背景保持深色）
  useEffect(() => {
    const term = termRef.current
    if (!term || !P['--term-fg']) return
    term.options.theme = {
      ...term.options.theme,
      background: P['--term-bg'] ?? '#0e1420',
      foreground: P['--term-fg'],
      cursor: P['--accent'],
    }
  }, [P['--term-bg'], P['--term-fg']])

  useEffect(() => {
    const term = termRef.current
    if (!term || sel == null) return
    term.reset()
    setConnected(false)

    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws/terminal/${sel}`)
    ws.onopen = () => { setConnected(true); term.focus() }
    ws.onmessage = e => term.write(e.data)
    ws.onclose = () => {
      setConnected(false)
      term.write('\r\n\x1b[38;5;245m[连接已关闭]\x1b[0m\r\n')
    }
    const dataSub = term.onData((d: string) => { if (ws.readyState === WebSocket.OPEN) ws.send(d) })
    return () => { dataSub.dispose(); ws.close() }
  }, [sel, termReady])

  const host = hosts.find(h => h.id === sel)

  return (
    <div className="fade-in">
      <PageHead title="远程终端" sub="演示主机为模拟 shell · 真实主机走 SSH PTY（asyncssh）">
        {hosts.map(h => (
          <button key={h.id} onClick={() => setSel(h.id)}
            className={`pill ${sel === h.id ? '' : 'text-[var(--text-mute)]'}`}
            style={sel === h.id
              ? { background: 'var(--accent-dim)', color: 'var(--accent)' }
              : { background: 'var(--neutral-bg)' }}>
            {h.name}
          </button>
        ))}
      </PageHead>
      <div className="card p-2 transition-colors" style={{ borderColor: connected ? 'var(--ok-border)' : undefined }}>
        <div className="flex items-center gap-2 px-2.5 py-1.5 text-[11.5px] text-[var(--text-faint)] flex-wrap">
          <span className="pulse-dot" style={{ background: connected ? 'var(--ok)' : 'var(--warn)' }} />
          {host ? host.name : '选择主机'}
          {host && (
            <span className="pill" style={host.mock
              ? { background: 'var(--neutral-bg)', color: 'var(--text-mute)', fontSize: 10 }
              : { background: 'var(--accent-dim)', color: 'var(--accent)', fontSize: 10 }}>
              {host.mock ? '模拟 shell' : 'SSH PTY'}
            </span>
          )}
          <span className="ml-auto mono">{connected ? '● 已连接' : '连接中…'}</span>
        </div>
        <div ref={boxRef} style={{ height: 'calc(100vh - 250px)', background: 'var(--term-bg)', borderRadius: 10, padding: 8 }} />
      </div>
      {host?.mock && (
        <p className="text-[11.5px] text-[var(--text-faint)] mt-3 leading-relaxed">
          演示终端支持：help · ls · cd · cat · ps · top · df · free · uptime · last · systemctl status …
          全部只读，输出为模拟数据，用于完整展示终端体验。
        </p>
      )}
    </div>
  )
}
