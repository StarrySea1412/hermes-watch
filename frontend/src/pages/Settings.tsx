import { useEffect, useState } from 'react'
import { api, subscribe, type Host } from '../api'
import { PageHead } from '../ui'

const THRESHOLD_FIELDS: { key: string; label: string; hint: string }[] = [
  { key: 'disk_warn', label: '磁盘警告 %', hint: '默认 85' },
  { key: 'disk_crit', label: '磁盘严重 %', hint: '默认 95' },
  { key: 'mem_warn', label: '内存警告 %', hint: '默认 85' },
  { key: 'mem_crit', label: '内存严重 %', hint: '默认 92' },
  { key: 'cpu_warn', label: 'CPU 警告 %', hint: '默认 85' },
  { key: 'load_warn', label: 'load1 警告', hint: '默认 8.0' },
  { key: 'cert_days', label: '证书剩余天数', hint: '默认 14' },
]

const THRESH_KEYS = THRESHOLD_FIELDS.map(f => f.key)

export default function Settings() {
  const [hosts, setHosts] = useState<Host[]>([])
  const [settings, setSettings] = useState<Record<string, string>>({})
  const [form, setForm] = useState({ name: '', hostname: '', username: 'root', secret: '', group_name: 'default' })
  const [msg, setMsg] = useState('')
  const [provider, setProvider] = useState<any>({})
  const [webhook, setWebhook] = useState('')
  const [webhookSaved, setWebhookSaved] = useState('')
  const [th, setTh] = useState<Record<string, string>>({})
  const [thSaved, setThSaved] = useState(false)
  const [authPw, setAuthPw] = useState('')
  const [authOld, setAuthOld] = useState('')
  const [authMsg, setAuthMsg] = useState('')
  const [models, setModels] = useState<string[]>([])
  const [fetching, setFetching] = useState(false)
  const [modelsMsg, setModelsMsg] = useState('')
  const [testing, setTesting] = useState(false)
  const [testRes, setTestRes] = useState<any>(null)

  const load = () => Promise.all([
    api<{ hosts: Host[] }>('/fleet').then(d => setHosts(d.hosts)),
    api<Record<string, string>>('/settings').then(s => {
      setSettings(s)
      try { setProvider(JSON.parse(s.ai_provider || '{}')) } catch { /* ignore */ }
      setWebhookSaved(s.webhook_url || '')
      setWebhook(s.webhook_url || '')
      const t: Record<string, string> = {}
      for (const k of THRESH_KEYS) t[k] = s[k] ?? ''
      setTh(t)
    }),
  ])
  useEffect(() => { load(); return subscribe(() => load()) }, [])

  const saveSetting = async (k: string, v: string) => { await api('/settings', { method: 'POST', body: JSON.stringify({ [k]: v }) }); load() }
  const add = async () => {
    try {
      await api('/hosts', { method: 'POST', body: JSON.stringify(form) })
      setMsg(`✓ 已添加 ${form.name}，下一轮巡检（≤60s）开始采集`)
      setForm({ name: '', hostname: '', username: 'root', secret: '', group_name: 'default' })
      load()
    } catch (e: any) { setMsg(`✕ ${e.message}`) }
  }
  const del = async (h: Host) => { await api(`/hosts/${h.id}`, { method: 'DELETE' }); load() }
  const saveProvider = (patch: any) => {
    const next = { ...provider, ...patch }
    setProvider(next)
    saveSetting('ai_provider', JSON.stringify(next))
  }
  const saveThresholds = async () => {
    const payload: Record<string, string> = {}
    for (const k of THRESH_KEYS) if (th[k] !== '') payload[k] = th[k]
    await api('/settings', { method: 'POST', body: JSON.stringify(payload) })
    setThSaved(true); setTimeout(() => setThSaved(false), 2000)
    load()
  }
  const aiOn = settings.ai_outbound === 'on'
  const execOn = settings.propose_exec === 'on'
  const authOn = settings.panel_auth === 'on'
  const autoReportOn = (settings.auto_report_min ?? '0') !== '0'

  const fetchModels = async () => {
    setFetching(true); setModelsMsg(''); setModels([])
    saveProvider({})
    try {
      const r = await api<any>('/llm/models', { method: 'POST', body: JSON.stringify({ base_url: provider.base_url ?? '', api_key: provider.api_key ?? '' }) })
      setModels(r.models ?? [])
      setModelsMsg(r.ok ? `✓ 拉取到 ${r.models.length} 个模型` : `✕ ${r.error}`)
    } catch (e: any) { setModelsMsg(`✕ ${e.message}`) }
    setFetching(false)
  }
  const testLlm = async () => {
    setTesting(true); setTestRes(null)
    saveProvider({})
    try {
      const r = await api<any>('/llm/test', { method: 'POST', body: JSON.stringify({ base_url: provider.base_url ?? '', api_key: provider.api_key ?? '', model: provider.model ?? '' }) })
      setTestRes(r)
    } catch (e: any) { setTestRes({ ok: false, error: e.message }) }
    setTesting(false)
  }

  return (
    <div className="fade-in max-w-5xl">
      <PageHead title="设置" sub="主机清单 · 告警阈值 · AI 安全开关（默认关）· 通知通道" />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* 告警阈值 */}
        <div className="card p-5">
          <div className="flex items-center justify-between mb-1">
            <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">告警阈值</h3>
            <span className="text-[10.5px] text-[var(--text-faint)]">留空 = 使用默认值</span>
          </div>
          <p className="text-[12px] text-[var(--text-faint)] mb-3.5">规则引擎先于一切 AI 生效，阈值改完立即作用于下一轮巡检</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            {THRESHOLD_FIELDS.map(f => (
              <div key={f.key}>
                <div className="flex justify-between text-[11px] text-[var(--text-faint)] mb-1">
                  <span>{f.label}</span><span>{f.hint}</span>
                </div>
                <input className="input num" inputMode="decimal" placeholder={f.hint.replace('默认 ', '')}
                  value={th[f.key] ?? ''} onChange={e => setTh({ ...th, [f.key]: e.target.value })} />
              </div>
            ))}
          </div>
          <div className="flex items-center gap-3 mt-3.5">
            <button className="btn btn-primary" onClick={saveThresholds}>{thSaved ? '✓ 已保存' : '保存阈值'}</button>
            <span className="text-[11px] text-[var(--text-faint)]">巡检周期 <input className="input num inline-block text-center"
              style={{ width: 64, padding: '4px 6px' }} value={settings.poll_seconds ?? '60'}
              onChange={e => setSettings({ ...settings, poll_seconds: e.target.value })}
              onBlur={() => saveSetting('poll_seconds', settings.poll_seconds ?? '60')} /> 秒</span>
          </div>
        </div>

        {/* 自动报告 + AI 开关 + 提案执行 */}
        <div className="space-y-4">
          <div className="card p-5">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">提案执行</h3>
                <p className="text-[12px] text-[var(--text-faint)] mt-1.5 leading-relaxed">
                  已批准的修复提案可由人工显式触发执行。命令逐段过白名单，mock 主机在模拟环境生效，
                  每次尝试（含被拦截/超时）均写入审计。默认关闭。
                </p>
              </div>
              <button onClick={() => saveSetting('propose_exec', execOn ? 'off' : 'on')}
                className="btn shrink-0" style={execOn
                  ? { background: 'var(--ok-bg)', color: 'var(--ok)', borderColor: 'var(--ok-border)' }
                  : { background: 'var(--neutral-bg)', color: 'var(--text-mute)', borderColor: 'var(--border)' }}>
                {execOn ? '● 允许执行' : '○ 已关闭'}
              </button>
            </div>
          </div>

          <div className="card p-5">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">访问控制</h3>
                <p className="text-[12px] text-[var(--text-faint)] mt-1.5 leading-relaxed">
                  为面板加一道口令（PBKDF2 存储 + 签名 Cookie 会话）。出站 Agent 与本地 MCP 不受影响，各自走自己的通道。
                </p>
              </div>
              <span className="pill shrink-0" style={authOn
                ? { background: 'var(--ok-bg)', color: 'var(--ok)' }
                : { background: 'var(--neutral-bg)', color: 'var(--text-mute)' }}>
                {authOn ? '● 已开启' : '○ 已关闭'}
              </span>
            </div>
            {!authOn && (
              <div className="flex flex-col sm:flex-row gap-2.5 mt-3.5">
                <input className="input" type="password" placeholder="设置面板口令（≥4 位）" value={authPw}
                  onChange={e => setAuthPw(e.target.value)} />
                <button className="btn btn-primary shrink-0" disabled={!authPw}
                  onClick={() => api('/auth/enable', { method: 'POST', body: JSON.stringify({ password: authPw }) })
                    .then(() => { setAuthPw(''); setAuthMsg('✓ 已开启，下次访问需登录'); load() })
                    .catch(e => setAuthMsg(`✕ ${e.message}`))}>启用</button>
              </div>
            )}
            {authOn && (
              <div className="mt-3.5 space-y-2.5">
                <div className="flex flex-col sm:flex-row gap-2.5">
                  <input className="input" type="password" placeholder="当前口令" value={authOld}
                    onChange={e => setAuthOld(e.target.value)} />
                  <input className="input" type="password" placeholder="新口令（≥4 位）" value={authPw}
                    onChange={e => setAuthPw(e.target.value)} />
                  <button className="btn shrink-0" disabled={!authOld || !authPw}
                    onClick={() => api('/auth/change', { method: 'POST', body: JSON.stringify({ old: authOld, new: authPw }) })
                      .then(() => { setAuthPw(''); setAuthOld(''); setAuthMsg('✓ 口令已更换') })
                      .catch(e => setAuthMsg(`✕ ${e.message}`))}>修改口令</button>
                </div>
                <div className="flex flex-col sm:flex-row gap-2.5">
                  <button className="btn btn-ghost shrink-0" onClick={() => api('/auth/logout', { method: 'POST' }).then(() => { location.href = '/login' })}>退出登录</button>
                  <input className="input" type="password" placeholder="输入当前口令以关闭访问控制" value={authOld}
                    onChange={e => setAuthOld(e.target.value)} />
                  <button className="btn shrink-0" disabled={!authOld}
                    onClick={() => api('/auth/disable', { method: 'POST', body: JSON.stringify({ password: authOld }) })
                      .then(() => { setAuthOld(''); setAuthPw(''); setAuthMsg(''); load() })
                      .catch(e => setAuthMsg(`✕ ${e.message}`))}>关闭访问控制</button>
                </div>
              </div>
            )}
            {authMsg && <div className="text-[12px] mt-2.5 text-[var(--text-mute)]">{authMsg}</div>}
          </div>

          <div className="card p-5">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">定时健康报告</h3>
                <p className="text-[12px] text-[var(--text-faint)] mt-1.5 leading-relaxed">
                  每 N 分钟自动生成一份 Fleet 健康报告，进报告中心与事件流。0 = 关闭。
                </p>
              </div>
              <button onClick={() => saveSetting('auto_report_min', autoReportOn ? '0' : '30')}
                className="btn shrink-0" style={autoReportOn
                  ? { background: 'var(--ok-bg)', color: 'var(--ok)', borderColor: 'var(--ok-border)' }
                  : { background: 'var(--neutral-bg)', color: 'var(--text-mute)', borderColor: 'var(--border)' }}>
                {autoReportOn ? `● 每 ${settings.auto_report_min} 分钟` : '○ 已关闭'}
              </button>
            </div>
          </div>

        </div>
      </div>

      {/* AI 外发：全宽大卡片 */}
      <div className="card p-5 mt-4">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2.5">
              <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">AI 外发总开关</h3>
              <span className="pill" style={aiOn
                ? { background: 'var(--ok-bg)', color: 'var(--ok)' }
                : { background: 'var(--neutral-bg)', color: 'var(--text-mute)' }}>
                {aiOn ? '● 已开启' : '○ 已关闭'}
              </span>
            </div>
            <p className="text-[12px] text-[var(--text-faint)] mt-1.5 leading-relaxed max-w-3xl">
              开启后诊断摘要发送到 LLM 生成叙事，AI 只补充视角、不覆盖规则引擎结论。
              关闭时全部结论由本地规则引擎产生，<b className="text-[var(--text-mute)]">数据不出本机</b>。
              兼容任意 OpenAI 协议端点（Ollama / vLLM / one-api / 云厂商），API Key 可空（本地端点无需鉴权）。
            </p>
          </div>
          <button onClick={() => saveSetting('ai_outbound', aiOn ? 'off' : 'on')}
            className="btn shrink-0" style={aiOn
              ? { background: 'var(--ok-bg)', color: 'var(--ok)', borderColor: 'var(--ok-border)' }
              : { background: 'var(--neutral-bg)', color: 'var(--text-mute)', borderColor: 'var(--border)' }}>
            {aiOn ? '● 已开启' : '○ 已关闭'}
          </button>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-4">
          <div>
            <div className="text-[11px] text-[var(--text-faint)] mb-1.5">Base URL</div>
            <input className="input mono" placeholder="http://localhost:11434/v1" value={provider.base_url ?? ''}
              onChange={e => setProvider({ ...provider, base_url: e.target.value })}
              onBlur={() => saveProvider({})} />
          </div>
          <div>
            <div className="text-[11px] text-[var(--text-faint)] mb-1.5">模型</div>
            <input className="input mono" placeholder="qwen2.5:7b / deepseek-chat" list="llm-models" value={provider.model ?? ''}
              onChange={e => setProvider({ ...provider, model: e.target.value })}
              onBlur={() => saveProvider({})} />
            <datalist id="llm-models">
              {models.map(m => <option key={m} value={m} />)}
            </datalist>
          </div>
          <div>
            <div className="text-[11px] text-[var(--text-faint)] mb-1.5">API Key（可空）</div>
            <input className="input mono" placeholder="sk-…" type="password" value={provider.api_key ?? ''}
              onChange={e => setProvider({ ...provider, api_key: e.target.value })}
              onBlur={() => saveProvider({})} />
          </div>
        </div>
        <div className="flex items-center gap-2.5 mt-3.5 flex-wrap">
          <button className="btn" disabled={fetching || !provider.base_url} onClick={fetchModels}>
            {fetching ? '拉取中…' : models.length ? '↻ 重新拉取模型' : '⌄ 获取模型列表'}
          </button>
          <button className="btn" disabled={testing || !provider.base_url} onClick={testLlm}>
            {testing ? '测试中…' : '⚡ 连通测活'}
          </button>
          {models.length > 0 && (
            <select className="input mono" style={{ width: 'auto', padding: '6px 10px' }} value=""
              onChange={e => { if (e.target.value) saveProvider({ model: e.target.value }) }}>
              <option value="">从 {models.length} 个模型中选择…</option>
              {models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          )}
          {modelsMsg && <span className="text-[12px]" style={{ color: modelsMsg.startsWith('✓') ? 'var(--ok)' : 'var(--crit)' }}>{modelsMsg}</span>}
        </div>
        {testRes && (
          <div className="inset px-3 py-2.5 mt-3 text-[12px] mono" style={testRes.ok
            ? { color: 'var(--ok)' } : { color: 'var(--crit)' }}>
            {testRes.ok
              ? `✓ 端点连通 · ${testRes.latency_ms}ms · HTTP ${testRes.status}${testRes.model ? ` · 模型 ${testRes.model}` : ''}${testRes.reply ? ` · 回复「${testRes.reply}」` : ''}`
              : `✕ 测活失败：${testRes.error}${testRes.latency_ms ? ` · ${testRes.latency_ms}ms` : ''}`}
          </div>
        )}
        <p className="text-[11px] text-[var(--text-faint)] mt-3 leading-relaxed">
          测活与诊断叙事走同一条 chat/completions 路径，通过即代表 AI 叙事可用。
          本地试运行：py backend/mock_llm.py → Base URL 填 http://127.0.0.1:18777/v1
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
        <div className="card p-5">
          <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)] mb-1.5">通知 Webhook</h3>
          <p className="text-[12px] text-[var(--text-faint)] mb-3">产生 crit 发现或报告生成时 POST JSON（企业微信/钉钉/Telegram bot 网关均可）</p>
          <div className="flex flex-col sm:flex-row gap-2.5">
            <input className="input mono" placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…" value={webhook}
              onChange={e => setWebhook(e.target.value)} />
            <button className="btn btn-primary shrink-0" onClick={() => saveSetting('webhook_url', webhook).then(() => setWebhookSaved(webhook))}>
              {webhookSaved === webhook && webhookSaved ? '✓ 已保存' : '保存'}
            </button>
          </div>
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2.5 mb-1.5">
            <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">本地 MCP 端点</h3>
            <span className="pill" style={{ background: 'var(--violet-bg)', color: 'var(--violet)', fontSize: 10 }}>只读</span>
          </div>
          <p className="text-[12px] text-[var(--text-faint)] mb-3">Claude Desktop / Cursor 等 MCP 客户端直查巡检数据，5 个只读工具</p>
          <div className="inset px-3 py-2.5">
            <code className="mono text-[12px]" style={{ color: 'var(--code-warn-text)' }}>
              {location.protocol}//{location.host}/api/mcp
            </code>
          </div>
          <div className="text-[11px] text-[var(--text-faint)] mt-2.5">工具：fleet_status · list_findings · get_finding · host_history · recent_events</div>
        </div>
      </div>

      <div className="card p-5 mt-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">主机清单</h3>
          <a href="/enroll" className="text-[12px] text-[var(--accent)] hover:underline">⌁ 出站 Agent 接入 →</a>
        </div>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-[13px]">
          <thead>
            <tr className="text-[11px] text-[var(--text-faint)] text-left border-b border-[var(--border)]">
              <th className="py-2.5 font-medium">名称</th><th className="font-medium">地址</th><th className="font-medium">分组</th><th className="font-medium">类型</th><th></th>
            </tr>
          </thead>
          <tbody>
            {hosts.map(h => (
              <tr key={h.id} className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--bg-hover)] transition-colors">
                <td className="py-2.5 font-medium text-[var(--text-hi)]">{h.name}</td>
                <td className="text-[var(--text-mute)] mono text-[12px]">{h.hostname}</td>
                <td className="text-[var(--text-mute)]">{h.group}</td>
                <td>
                  <span className="pill" style={h.mock
                    ? { background: 'var(--neutral-bg)', color: 'var(--text-mute)' }
                    : { background: 'var(--accent-dim)', color: 'var(--accent)' }}>
                    {h.mock ? '演示' : 'SSH'}
                  </span>
                </td>
                <td className="text-right">
                  {!h.mock && <button onClick={() => del(h)} className="text-[12px] text-[var(--text-faint)] hover:text-[var(--crit)]">删除</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
        <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5 items-end">
          <input className="input" placeholder="名称" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
          <input className="input col-span-2 mono" placeholder="IP / 主机名" value={form.hostname} onChange={e => setForm({ ...form, hostname: e.target.value })} />
          <input className="input" placeholder="用户" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} />
          <input className="input" placeholder="密码（可空=密钥）" type="password" value={form.secret} onChange={e => setForm({ ...form, secret: e.target.value })} />
          <button className="btn btn-primary justify-center" onClick={add}>＋ 添加主机</button>
        </div>
        {msg && <div className="text-[12px] mt-2.5 text-[var(--text-mute)]">{msg}</div>}
        <p className="text-[11px] text-[var(--text-faint)] mt-3 leading-relaxed">
          真实主机走 SSH 只读探测（top / free / df / systemctl / last / openssl），需要网络可达且凭据正确。
          机器在内网、无法 SSH？用 <a href="/enroll" className="text-[var(--accent)] hover:underline">出站 Agent</a> 主动上报。
          演示主机数据由内置模拟器生成，用于故事复现。
        </p>
      </div>
    </div>
  )
}
