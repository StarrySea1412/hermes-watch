import { useEffect, useState } from 'react'
import { api, type Host } from '../api'
import { Ic } from '../icons'
import { PageHead } from '../ui'

/**
 * 接入中心：两种接入方式
 * 1. 出站 Agent（推荐，beszel 式）：目标机跑一个 sh 脚本，主动 push，无需入站 SSH
 * 2. SSH 拉取：面板填凭据，服务端每轮巡检只读探测
 */
export default function Enroll() {
  const [hosts, setHosts] = useState<Host[]>([])
  const [sel, setSel] = useState<number | null>(null)
  const [token, setToken] = useState('')
  const [copied, setCopied] = useState('')
  const [url, setUrl] = useState(`${location.protocol}//${location.host}`)
  void setUrl

  useEffect(() => {
    api<{ hosts: Host[] }>('/fleet').then(d => {
      setHosts(d.hosts)
      setSel(s => s ?? d.hosts[0]?.id ?? null)
    })
  }, [])

  const host = hosts.find(h => h.id === sel)

  const genToken = async () => {
    if (!sel) return
    const r = await api<{ token: string }>(`/hosts/${sel}/agent-token`, { method: 'POST' })
    setToken(r.token)
  }

  const copy = async (text: string, key: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(key); setTimeout(() => setCopied(''), 1500) } catch { /* noop */ }
  }

  const installCmd = `HW_URL=${url} HW_TOKEN=${token || '<先生成 Token>'} ./hermes-watch-agent.sh`
  const cronCmd = `(HW_URL=${url} HW_TOKEN=${token || '<Token>'} setsid ./hermes-watch-agent.sh >/var/log/hw-agent.log 2>&1 &)`

  return (
    <div className="fade-in max-w-5xl">
      <PageHead title="接入中心" sub="出站 Agent 主动上报（推荐，无需入站 SSH）· SSH 拉取巡检" />

      <div className="card p-5 mb-4">
        <div className="flex items-center gap-2.5 mb-4">
          <span className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'var(--ok-bg)', border: '1px solid var(--ok-border)', color: 'var(--ok)' }}>
            <Ic name="zap" size={15} />
          </span>
          <span className="pill" style={{ background: 'var(--ok-bg)', color: 'var(--ok)' }}>方式 A · 推荐</span>
          <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">出站 Agent（beszel 式）</h3>
        </div>
        <p className="text-[12.5px] text-[var(--text-mute)] leading-relaxed mb-4">
          目标机器只跑一个 <span className="mono text-[var(--accent)]">sh + curl</span> 脚本，每 60 秒把指标
          <b>主动推送</b>到本平台 — 机器在内网/防火墙后也能接入，无需开入站端口。
          脚本纯只读（top/free/df//proc），不含任何写操作。
        </p>

        <div className="flex items-center gap-2 mb-2">
          <span className="w-5 h-5 rounded-full flex items-center justify-center text-[10.5px] font-bold num shrink-0"
            style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>1</span>
          <span className="text-[12px] text-[var(--text-mute)] font-medium">选择主机并生成接入 Token</span>
        </div>
        <div className="flex flex-wrap gap-2 mb-2">
          {hosts.map(h => (
            <button key={h.id} onClick={() => { setSel(h.id); setToken('') }}
              className={`pill ${sel === h.id ? '' : 'text-[var(--text-mute)]'}`}
              style={sel === h.id ? { background: 'var(--accent-dim)', color: 'var(--accent)' } : { background: 'var(--neutral-bg)' }}>
              {h.name}{h.mock && ' ·演示'}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2.5 mb-5">
          <button className="btn btn-primary shrink-0" disabled={!sel} onClick={genToken}>生成 Token</button>
          {token && (
            <>
              <code className="inset px-3 py-2 mono text-[12px] max-w-full break-all" style={{ color: 'var(--ok)' }}>{token}</code>
              <button className="btn btn-ghost" onClick={() => copy(token, 'tok')}>{copied === 'tok' ? '✓ 已复制' : '复制'}</button>
            </>
          )}
          {host && <span className="text-[11.5px] text-[var(--text-faint)]">→ 将绑定到 {host.name}</span>}
        </div>

        <div className="flex items-center gap-2 mb-2">
          <span className="w-5 h-5 rounded-full flex items-center justify-center text-[10.5px] font-bold num shrink-0"
            style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>2</span>
          <span className="text-[12px] text-[var(--text-mute)] font-medium">下载 agent 脚本到目标机</span>
        </div>
        <div className="inset flex items-center gap-2.5 px-3 py-2.5 mb-3">
          <code className="mono text-[12px] text-[var(--text)] flex-1 truncate">
            curl -fsSL {url}/api/agent/script -o hermes-watch-agent.sh && chmod +x hermes-watch-agent.sh
          </code>
          <button className="btn btn-ghost shrink-0" onClick={() =>
            copy(`curl -fsSL ${url}/api/agent/script -o hermes-watch-agent.sh && chmod +x hermes-watch-agent.sh`, 'dl')}>
            {copied === 'dl' ? '✓' : '复制'}
          </button>
        </div>

        <div className="flex items-center gap-2 mb-2">
          <span className="w-5 h-5 rounded-full flex items-center justify-center text-[10.5px] font-bold num shrink-0"
            style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>3</span>
          <span className="text-[12px] text-[var(--text-mute)] font-medium">带上 Token 运行（前台试跑）</span>
        </div>
        <div className="inset flex items-center gap-2.5 px-3 py-2.5 mb-2.5">
          <code className="mono text-[12px] flex-1 truncate" style={{ color: 'var(--code-warn-text)' }}>{installCmd}</code>
          <button className="btn btn-ghost shrink-0" onClick={() => copy(installCmd, 'run')}>{copied === 'run' ? '✓' : '复制'}</button>
        </div>
        <div className="text-[12px] text-[var(--text-faint)] mb-2">生产环境后台常驻：</div>
        <div className="inset flex items-center gap-2.5 px-3 py-2.5">
          <code className="mono text-[11.5px] flex-1 truncate" style={{ color: 'var(--code-warn-text)' }}>{cronCmd}</code>
          <button className="btn btn-ghost shrink-0" onClick={() => copy(cronCmd, 'bg')}>{copied === 'bg' ? '✓' : '复制'}</button>
        </div>
      </div>

      <div className="card p-5 mb-4">
        <div className="flex items-center gap-2.5 mb-3">
          <span className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'var(--accent-dim)', border: '1px solid var(--accent-border)', color: 'var(--accent)' }}>
            <Ic name="download" size={15} />
          </span>
          <span className="pill" style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>方式 B</span>
          <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">SSH 拉取巡检</h3>
        </div>
        <p className="text-[12.5px] text-[var(--text-mute)] leading-relaxed">
          在 <a href="/settings" className="text-[var(--accent)] hover:underline">设置页</a> 添加主机（IP + 凭据），
          巡检循环每轮通过 SSH 只读探测（top / free / df / ps / systemctl / last / openssl）。
          适合你能直连的机器；出站 Agent 适合网络不可达但机器能出网的场景。
        </p>
      </div>

      <div className="card p-5">
        <div className="flex items-center gap-2.5 mb-3">
          <span className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'var(--violet-bg)', border: '1px solid var(--violet-border)', color: 'var(--violet)' }}>
            <Ic name="globe" size={15} />
          </span>
          <span className="pill" style={{ background: 'var(--violet-bg)', color: 'var(--violet)' }}>Bonus</span>
          <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">把巡检数据接入任意 LLM（本地 MCP）</h3>
        </div>
        <p className="text-[12.5px] text-[var(--text-mute)] leading-relaxed mb-3">
          平台内置只读 MCP 端点，Claude Desktop / Cursor 等 MCP 客户端可直接查询 fleet 状态、发现、诊断与事件流
          — 数据全程本地，不经第三方。
        </p>
        <div className="inset flex items-center gap-2.5 px-3 py-2.5">
          <code className="mono text-[12px] flex-1 truncate" style={{ color: 'var(--code-warn-text)' }}>
            {`{"mcpServers": {"hermes-watch": {"type": "http", "url": "${url}/api/mcp"}}}`}
          </code>
          <button className="btn btn-ghost shrink-0" onClick={() =>
            copy(JSON.stringify({ mcpServers: { 'hermes-watch': { type: 'http', url: `${url}/api/mcp` } } }), 'mcp')}>
            {copied === 'mcp' ? '✓' : '复制'}
          </button>
        </div>
      </div>
    </div>
  )
}
