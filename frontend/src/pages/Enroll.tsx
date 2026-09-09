import { useEffect, useState } from 'react'
import { api, type Host } from '../api'
import { Ic } from '../icons'
import { PageHead } from '../ui'
import { useT } from '../i18n'

/**
 * 接入中心：两种接入方式
 * 1. 出站 Agent（推荐，beszel 式）：目标机跑一个 sh 脚本，主动 push，无需入站 SSH
 * 2. SSH 拉取：面板填凭据，服务端每轮巡检只读探测
 */
export default function Enroll() {
  const { t } = useT()
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

  const installCmd = `HW_URL=${url} HW_TOKEN=${token || t('enroll.needToken')} ./hermes-watch-agent.sh`
  const cronCmd = `(HW_URL=${url} HW_TOKEN=${token || '<Token>'} setsid ./hermes-watch-agent.sh >/var/log/hw-agent.log 2>&1 &)`

  return (
    <div className="fade-in max-w-5xl">
      <PageHead title={t('nav.enroll')} sub={t('enroll.sub')} />

      <div className="card p-5 mb-4">
        <div className="flex items-center gap-2.5 mb-4">
          <span className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'var(--ok-bg)', border: '1px solid var(--ok-border)', color: 'var(--ok)' }}>
            <Ic name="zap" size={15} />
          </span>
          <span className="pill" style={{ background: 'var(--ok-bg)', color: 'var(--ok)' }}>{t('enroll.methodA')}</span>
          <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{t('enroll.aTitle')}</h3>
        </div>
        <p className="text-[12.5px] text-[var(--text-mute)] leading-relaxed mb-4">
          {t('enroll.aDesc1')} <span className="mono text-[var(--accent)]">sh + curl</span> {t('enroll.aDesc2')}
          <b>{t('enroll.aDesc3')}</b>{t('enroll.aDesc4')} {t('enroll.aDesc5')}
        </p>

        <div className="flex items-center gap-2 mb-2">
          <span className="w-5 h-5 rounded-full flex items-center justify-center text-[10.5px] font-bold num shrink-0"
            style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>1</span>
          <span className="text-[12px] text-[var(--text-mute)] font-medium">{t('enroll.step1')}</span>
        </div>
        <div className="flex flex-wrap gap-2 mb-2">
          {hosts.map(h => (
            <button key={h.id} onClick={() => { setSel(h.id); setToken('') }}
              className={`pill ${sel === h.id ? '' : 'text-[var(--text-mute)]'}`}
              style={sel === h.id ? { background: 'var(--accent-dim)', color: 'var(--accent)' } : { background: 'var(--neutral-bg)' }}>
              {h.name}{h.mock && t('enroll.demo')}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2.5 mb-5">
          <button className="btn btn-primary shrink-0" disabled={!sel} onClick={genToken}>{t('enroll.genToken')}</button>
          {token && (
            <>
              <code className="inset px-3 py-2 mono text-[12px] max-w-full break-all" style={{ color: 'var(--ok)' }}>{token}</code>
              <button className="btn btn-ghost" onClick={() => copy(token, 'tok')}>{copied === 'tok' ? t('enroll.copied') : t('btn.copy')}</button>
            </>
          )}
          {host && <span className="text-[11.5px] text-[var(--text-faint)]">{t('enroll.bindTo', { name: host.name })}</span>}
        </div>

        <div className="flex items-center gap-2 mb-2">
          <span className="w-5 h-5 rounded-full flex items-center justify-center text-[10.5px] font-bold num shrink-0"
            style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>2</span>
          <span className="text-[12px] text-[var(--text-mute)] font-medium">{t('enroll.step2')}</span>
        </div>
        <div className="inset flex items-center gap-2.5 px-3 py-2.5 mb-3">
          <code className="mono text-[12px] text-[var(--text)] flex-1 truncate">
            curl -fsSL {url}/api/agent/script -o hermes-watch-agent.sh && chmod +x hermes-watch-agent.sh
          </code>
          <button className="btn btn-ghost shrink-0" onClick={() =>
            copy(`curl -fsSL ${url}/api/agent/script -o hermes-watch-agent.sh && chmod +x hermes-watch-agent.sh`, 'dl')}>
            {copied === 'dl' ? '✓' : t('btn.copy')}
          </button>
        </div>

        <div className="flex items-center gap-2 mb-2">
          <span className="w-5 h-5 rounded-full flex items-center justify-center text-[10.5px] font-bold num shrink-0"
            style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>3</span>
          <span className="text-[12px] text-[var(--text-mute)] font-medium">{t('enroll.step3')}</span>
        </div>
        <div className="inset flex items-center gap-2.5 px-3 py-2.5 mb-2.5">
          <code className="mono text-[12px] flex-1 truncate" style={{ color: 'var(--code-warn-text)' }}>{installCmd}</code>
          <button className="btn btn-ghost shrink-0" onClick={() => copy(installCmd, 'run')}>{copied === 'run' ? '✓' : t('btn.copy')}</button>
        </div>
        <div className="text-[12px] text-[var(--text-faint)] mb-2">{t('enroll.bgHint')}</div>
        <div className="inset flex items-center gap-2.5 px-3 py-2.5">
          <code className="mono text-[11.5px] flex-1 truncate" style={{ color: 'var(--code-warn-text)' }}>{cronCmd}</code>
          <button className="btn btn-ghost shrink-0" onClick={() => copy(cronCmd, 'bg')}>{copied === 'bg' ? '✓' : t('btn.copy')}</button>
        </div>
      </div>

      <div className="card p-5 mb-4">
        <div className="flex items-center gap-2.5 mb-3">
          <span className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'var(--accent-dim)', border: '1px solid var(--accent-border)', color: 'var(--accent)' }}>
            <Ic name="download" size={15} />
          </span>
          <span className="pill" style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>{t('enroll.methodB')}</span>
          <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{t('enroll.bTitle')}</h3>
        </div>
        <p className="text-[12.5px] text-[var(--text-mute)] leading-relaxed">
          {t('enroll.bDesc1')} <a href="/settings" className="text-[var(--accent)] hover:underline">{t('enroll.settingsLink')}</a> {t('enroll.bDesc2')} {t('enroll.bDesc3')} {t('enroll.bDesc4')}
        </p>
      </div>

      <div className="card p-5">
        <div className="flex items-center gap-2.5 mb-3">
          <span className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: 'var(--violet-bg)', border: '1px solid var(--violet-border)', color: 'var(--violet)' }}>
            <Ic name="globe" size={15} />
          </span>
          <span className="pill" style={{ background: 'var(--violet-bg)', color: 'var(--violet)' }}>Bonus</span>
          <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{t('enroll.mcpTitle')}</h3>
        </div>
        <p className="text-[12.5px] text-[var(--text-mute)] leading-relaxed mb-3">
          {t('enroll.mcpDesc')}
        </p>
        <div className="inset flex items-center gap-2.5 px-3 py-2.5">
          <code className="mono text-[12px] flex-1 truncate" style={{ color: 'var(--code-warn-text)' }}>
            {`{"mcpServers": {"hermes-watch": {"type": "http", "url": "${url}/api/mcp"}}}`}
          </code>
          <button className="btn btn-ghost shrink-0" onClick={() =>
            copy(JSON.stringify({ mcpServers: { 'hermes-watch': { type: 'http', url: `${url}/api/mcp` } } }), 'mcp')}>
            {copied === 'mcp' ? '✓' : t('btn.copy')}
          </button>
        </div>
      </div>
    </div>
  )
}
