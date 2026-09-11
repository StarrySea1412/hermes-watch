import { useEffect, useState } from 'react'
import { api, fmtTime, subscribe, type Host } from '../api'
import { ConfirmDialog, PageHead } from '../ui'
import { Ic } from '../icons'
import { UserAdd } from './UserAdd'
import { SectionNav, SectionRail, SectionHead } from './SectionNav'
import { useT } from '../i18n'

type TFn = (key: string, vars?: Record<string, string | number>) => string

// 备份条目（GET /api/backups，后端按文件名倒序）
type Backup = { name: string; size: number; ts: number }

// 字节数 → 人类可读（备份文件通常 KB~MB 级，B 仅兜底）
const fmtSize = (n: number) =>
  n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`

const THRESHOLD_FIELDS: { key: string; label: string; hint: string; def: string }[] = [
  { key: 'disk_warn', label: 'st.thresh.diskWarn', hint: 'st.thresh.def', def: '85' },
  { key: 'disk_crit', label: 'st.thresh.diskCrit', hint: 'st.thresh.def', def: '95' },
  { key: 'mem_warn', label: 'st.thresh.memWarn', hint: 'st.thresh.def', def: '85' },
  { key: 'mem_crit', label: 'st.thresh.memCrit', hint: 'st.thresh.def', def: '92' },
  { key: 'cpu_warn', label: 'st.thresh.cpuWarn', hint: 'st.thresh.def', def: '85' },
  { key: 'load_warn', label: 'st.thresh.loadWarn', hint: 'st.thresh.def', def: '8.0' },
  { key: 'cert_days', label: 'st.thresh.certDays', hint: 'st.thresh.def', def: '14' },
  { key: 'io_warn', label: 'st.thresh.ioWarn', hint: 'st.thresh.def', def: '80000' },
  { key: 'temp_warn', label: 'st.thresh.tempWarn', hint: 'st.thresh.def', def: '80' },
  { key: 'swap_warn', label: 'st.thresh.swapWarn', hint: 'st.thresh.def', def: '70' },
]

const THRESH_KEYS = THRESHOLD_FIELDS.map(f => f.key)

const NOTIFY_LABELS: Record<string, string> = {
  wecom: 'st.notify.chWecom', dingtalk: 'st.notify.chDingtalk', feishu: 'st.notify.chFeishu',
  telegram: 'st.notify.chTelegram', serverchan: 'st.notify.chServerchan', webhook: 'st.notify.chWebhook',
  discord: 'st.notify.chDiscord', slack: 'st.notify.chSlack', ntfy: 'st.notify.chNtfy', smtp: 'st.notify.chSmtp',
}

// Webhook 输入框的 label / placeholder 随所选渠道切换；未列出的渠道走通用 Webhook 兜底
const NOTIFY_URL_FIELD: Record<string, { label: string; ph: string }> = {
  telegram: { label: 'st.notify.botToken', ph: '123456:ABC-DEF…' },
  serverchan: { label: 'st.notify.sendKey', ph: 'SCT…' },
  discord: { label: 'st.notify.webhookUrl', ph: 'https://discord.com/api/webhooks/…' },
  slack: { label: 'st.notify.webhookUrl', ph: 'https://hooks.slack.com/services/…' },
  ntfy: { label: 'st.notify.topicUrl', ph: 'https://ntfy.sh/my-topic' },
  smtp: { label: 'st.notify.smtpUrl', ph: 'smtp://user:pass@smtp.gmail.com:587?to=me@example.com' },
}

// ---------- 设置页分区导航 ----------
const SECTIONS = (t: TFn) => [
  { id: 'sec-inspect', label: t('st.sec.inspect'), icon: 'activity' as const },
  { id: 'sec-ai', label: t('st.sec.ai'), icon: 'sparkles' as const },
  { id: 'sec-notify', label: t('st.sec.notify'), icon: 'bell' as const },
  { id: 'sec-hosts', label: t('st.sec.hosts'), icon: 'server' as const },
  { id: 'sec-security', label: t('st.sec.security'), icon: 'shield' as const },
]

export default function Settings() {
  const { t } = useT()
  const [hosts, setHosts] = useState<Host[]>([])
  const [settings, setSettings] = useState<Record<string, string>>({})
  const [form, setForm] = useState({ name: '', hostname: '', username: 'root', secret: '', group_name: 'default',
    bastion_host: '', bastion_port: '22', bastion_username: 'root', bastion_secret: '' })
  const [bastionOpen, setBastionOpen] = useState(false)
  const [msg, setMsg] = useState('')
  const [provider, setProvider] = useState<any>({})
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
  const [notifyChannel, setNotifyChannel] = useState('wecom')
  const [notifyUrl, setNotifyUrl] = useState('')
  const [notifyChatId, setNotifyChatId] = useState('')
  const [notifyQuiet, setNotifyQuiet] = useState('')
  const [notifyResend, setNotifyResend] = useState('0')
  const [notifyMsg, setNotifyMsg] = useState('')
  const [testingNotify, setTestingNotify] = useState(false)
  const [notifyLog, setNotifyLog] = useState<any[]>([])
  const [statusTok, setStatusTok] = useState('')
  const [csOpen, setCsOpen] = useState(false)
  const [csLoading, setCsLoading] = useState(false)
  const [csList, setCsList] = useState<any[]>([])
  const [csMsg, setCsMsg] = useState('')
  const [authRole, setAuthRole] = useState('')
  const [users, setUsers] = useState<any[]>([])
  const [legacyPw, setLegacyPw] = useState(true)
  const [backups, setBackups] = useState<Backup[]>([])
  const [bkSaved, setBkSaved] = useState(false)
  const [bkMsg, setBkMsg] = useState('')
  const [restoreMsg, setRestoreMsg] = useState('')
  const [restoreTarget, setRestoreTarget] = useState<string | null>(null)
  const [delTarget, setDelTarget] = useState<string | null>(null)
  const notifyLabels = NOTIFY_LABELS
  const urlField = NOTIFY_URL_FIELD[notifyChannel] ?? { label: 'st.notify.webhookUrl', ph: 'https://…webhook/send?key=…' }
  const sections = SECTIONS(t)

  const load = () => Promise.all([
    api<{ hosts: Host[] }>('/fleet').then(d => setHosts(d.hosts)),
    api<Record<string, string>>('/settings').then(s => {
      setSettings(s)
      try { setProvider(JSON.parse(s.ai_provider || '{}')) } catch { /* ignore */ }
      setNotifyChannel(s.notify_channel && NOTIFY_LABELS[s.notify_channel] ? s.notify_channel : 'wecom')
      setNotifyUrl(s.webhook_url || '')
      setNotifyChatId(s.telegram_chat_id || '')
      setNotifyQuiet(s.quiet_hours || '')
      setNotifyResend(s.notify_resend_min ?? '0')
      const tv: Record<string, string> = {}
      for (const k of THRESH_KEYS) tv[k] = s[k] ?? ''
      setTh(tv)
    }),
    api<any[]>('/notify/log?limit=8').then(setNotifyLog).catch(() => { /* 留痕失败不打断 */ }),
    api<Backup[]>('/backups').then(setBackups).catch(() => { /* observer 403 / 后端重启时静默留空 */ }),
    api<{ enabled: boolean; token: string }>('/status/token')
      .then(r => setStatusTok(r.token || '')).catch(() => { /* noop */ }),
    api<{ role: string }>('/auth/status').then(s => {
      setAuthRole(s.role || '')
      if (s.role === 'admin') api<{ users: any[]; legacy_password: boolean }>('/auth/users')
        .then(r => { setUsers(r.users); setLegacyPw(r.legacy_password) }).catch(() => { /* noop */ })
    }).catch(() => { /* noop */ }),
  ])
  useEffect(() => { load(); return subscribe(() => load()) }, [])

  const saveSetting = async (k: string, v: string) => { await api('/settings', { method: 'POST', body: JSON.stringify({ [k]: v }) }); load() }
  const add = async () => {
    try {
      await api('/hosts', { method: 'POST', body: JSON.stringify({
        ...form, bastion_port: Number(form.bastion_port) || 22,
      }) })
      setMsg(t('st.hosts.added', { n: form.name }))
      setForm({ name: '', hostname: '', username: 'root', secret: '', group_name: 'default',
        bastion_host: '', bastion_port: '22', bastion_username: 'root', bastion_secret: '' })
      load()
    } catch (e: any) { setMsg(`✕ ${e.message}`) }
  }
  const del = async (h: Host) => {
    if (!confirm(t('st.hosts.delConfirm', { n: h.name }))) return
    await api(`/hosts/${h.id}`, { method: 'DELETE' }); load()
  }
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
      setModelsMsg(r.ok ? t('st.ai.fetched', { n: r.models.length }) : `✕ ${r.error}`)
    } catch (e: any) { setModelsMsg(`✕ ${e.message}`) }
    setFetching(false)
  }

  // cc-switch 一键导入：拉本机 provider 列表 → 主题化弹窗选择 → 应用到 ai_provider
  const openCcSwitch = () => {
    setCsOpen(true); setCsLoading(true); setCsMsg(''); setCsList([])
    api<{ found: boolean; reason: string; providers: any[] }>('/llm/ccswitch')
      .then(r => { setCsList(r.providers ?? []); if (r.reason) setCsMsg(r.reason) })
      .catch(e => setCsMsg(t('st.cs.readFail', { err: e.message })))
      .finally(() => setCsLoading(false))
  }
  const applyCs = (p: any) => {
    const next = { base_url: p.base_url, model: p.model ?? '', api_key: p.api_key ?? '' }
    setProvider((prev: any) => ({ ...prev, ...next }))
    api('/llm/ccswitch/apply', { method: 'POST', body: JSON.stringify({ ...p }) })
      .then(() => { setCsOpen(false); setModelsMsg(t('st.ai.imported', { n: p.name })) })
      .catch(e => setCsMsg(t('st.cs.applyFail', { err: e.message })))
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

  // ---------- 备份与恢复（恢复只暂存标记，重启面板后由后端换库生效） ----------
  const createBackup = async () => {
    try {
      await api('/backups', { method: 'POST' })
      setBkSaved(true); setTimeout(() => setBkSaved(false), 2000)
      setBkMsg(''); load()
    } catch (e: any) { setBkMsg(`✕ ${e.message}`) }
  }
  const doRestore = async () => {
    if (!restoreTarget) return
    const name = restoreTarget
    setRestoreTarget(null)
    try {
      const r = await api<{ staged: boolean; message: string }>(`/backups/${name}/restore`, { method: 'POST' })
      // 后端 message 已跟随面板语言，直接展示；缺省时回落本地文案
      setRestoreMsg(r.message || t('st.backup.restoreStaged'))
    } catch (e: any) { setRestoreMsg(`✕ ${e.message}`) }
  }
  const delBackup = async () => {
    if (!delTarget) return
    const name = delTarget
    setDelTarget(null)
    try {
      await api(`/backups/${name}`, { method: 'DELETE' })
      setBkMsg(''); load()
    } catch (e: any) { setBkMsg(`✕ ${e.message}`) }
  }

  return (
    <div className="fade-in max-w-6xl xl:flex xl:gap-8 xl:items-start">
      <div className="flex-1 min-w-0">
      <PageHead title={t('nav.settings')} sub={t('st.subtitle')} />
      <div className="xl:hidden mb-1">
        <SectionNav sections={sections} />
      </div>

      <section>
      <SectionHead id="sec-inspect" icon="activity" label={t('st.sec.inspect')} />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* 告警阈值 */}
        <div className="card p-5">
          <div className="flex items-center justify-between mb-1">
            <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{t('st.thresh.title')}</h3>
            <span className="text-[10.5px] text-[var(--text-faint)]">{t('st.thresh.leaveEmpty')}</span>
          </div>
          <p className="text-[12px] text-[var(--text-faint)] mb-3.5">{t('st.thresh.desc')}</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            {THRESHOLD_FIELDS.map(f => (
              <div key={f.key}>
                <div className="flex justify-between text-[11px] text-[var(--text-faint)] mb-1">
                  <span>{t(f.label)}</span><span>{t(f.hint, { n: f.def })}</span>
                </div>
                <input className="input num" inputMode="decimal" placeholder={f.def}
                  value={th[f.key] ?? ''} onChange={e => setTh({ ...th, [f.key]: e.target.value })} />
              </div>
            ))}
          </div>
          <div className="flex items-center gap-2.5 mt-3.5">
            <button className="btn btn-primary" onClick={saveThresholds}>{thSaved ? t('st.thresh.saved') : t('st.thresh.save')}</button>
            <span className="text-[11px] text-[var(--text-faint)]">{t('st.thresh.pollBefore')} <input className="input num inline-block text-center"
              style={{ width: 64, padding: '4px 6px' }} value={settings.poll_seconds ?? '60'}
              onChange={e => setSettings({ ...settings, poll_seconds: e.target.value })}
              onBlur={() => saveSetting('poll_seconds', settings.poll_seconds ?? '60')} /> {t('st.thresh.seconds')}</span>
            <span className="text-[11px] text-[var(--text-faint)]">{t('st.thresh.retentionBefore')} <input className="input num inline-block text-center"
              style={{ width: 56, padding: '4px 6px' }} value={settings.events_retention_days ?? '30'}
              onChange={e => setSettings({ ...settings, events_retention_days: e.target.value })}
              onBlur={() => saveSetting('events_retention_days', settings.events_retention_days ?? '30')} /> {t('st.thresh.days')}</span>
          </div>
        </div>

        {/* 提案执行（写操作总闸，与阈值并列展示） */}
        <div className="card p-5">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{t('st.exec.title')}</h3>
                <p className="text-[12px] text-[var(--text-faint)] mt-1.5 leading-relaxed">
                  {t('st.exec.desc')}
                </p>
              </div>
              <button onClick={() => saveSetting('propose_exec', execOn ? 'off' : 'on')}
                className="btn shrink-0" style={execOn
                  ? { background: 'var(--ok-bg)', color: 'var(--ok)', borderColor: 'var(--ok-border)' }
                  : { background: 'var(--neutral-bg)', color: 'var(--text-mute)', borderColor: 'var(--border)' }}>
                {execOn ? t('st.exec.allow') : t('st.state.off')}
              </button>
            </div>
        </div>

          <div className="card p-5">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{t('st.report.title')}</h3>
                <p className="text-[12px] text-[var(--text-faint)] mt-1.5 leading-relaxed">
                  {t('st.report.desc')}
                </p>
              </div>
              <button onClick={() => saveSetting('auto_report_min', autoReportOn ? '0' : '30')}
                className="btn shrink-0" style={autoReportOn
                  ? { background: 'var(--ok-bg)', color: 'var(--ok)', borderColor: 'var(--ok-border)' }
                  : { background: 'var(--neutral-bg)', color: 'var(--text-mute)', borderColor: 'var(--border)' }}>
                {autoReportOn ? t('st.report.everyN', { n: settings.auto_report_min }) : t('st.state.off')}
              </button>
            </div>
          </div>

          {/* 诊断触发报告（Aurora Actions 式留档）：crit 诊断完成自动生成一份诊断时点报告 */}
          <div className="card p-5 flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{t('st.diagrep.title')}</h3>
              <p className="text-[12px] text-[var(--text-faint)] mt-1.5 leading-relaxed">
                {t('st.diagrep.desc')}
              </p>
            </div>
            <button onClick={() => saveSetting('report_on_diag', (settings.report_on_diag === 'on') ? '' : 'on')}
              className="btn shrink-0" style={(settings.report_on_diag === 'on')
                ? { background: 'var(--ok-bg)', color: 'var(--ok)', borderColor: 'var(--ok-border)' }
                : { background: 'var(--neutral-bg)', color: 'var(--text-mute)', borderColor: 'var(--border)' }}>
              {(settings.report_on_diag === 'on') ? t('st.state.on') : t('st.state.off')}
            </button>
          </div>

          {/* 备份与恢复（SQLite 单文件运维能力：每日自动备份，恢复暂存到重启生效） */}
          <div className="card p-5">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{t('st.backup.title')}</h3>
                <p className="text-[12px] text-[var(--text-faint)] mt-1.5 leading-relaxed">
                  {t('st.backup.desc')}
                </p>
              </div>
              <button className="btn btn-primary shrink-0" onClick={createBackup}>
                {bkSaved ? t('st.backup.created') : <><Ic name="download" size={13} /> {t('st.backup.create')}</>}
              </button>
            </div>
            <div className="mt-3.5 space-y-1">
              {backups.map(b => (
                <div key={b.name} className="flex items-center gap-2.5 text-[11.5px]">
                  <Ic name="file" size={12} style={{ color: 'var(--text-faint)' }} />
                  <span className="mono text-[var(--text-mute)] truncate" title={b.name}>{b.name}</span>
                  <span className="num text-[var(--text-faint)] shrink-0" title={t('st.backup.size')}>{fmtSize(b.size)}</span>
                  <div className="ml-auto flex items-center gap-2.5 shrink-0">
                    <button className="text-[11.5px] text-[var(--text-faint)] hover:text-[var(--warn)]"
                      onClick={() => setRestoreTarget(b.name)}>{t('st.backup.restore')}</button>
                    <button className="text-[11.5px] text-[var(--text-faint)] hover:text-[var(--crit)]"
                      onClick={() => setDelTarget(b.name)}>{t('st.backup.delete')}</button>
                  </div>
                </div>
              ))}
              {!backups.length && <div className="text-[11.5px] text-[var(--text-faint)] py-1">{t('st.backup.empty')}</div>}
            </div>
            {/* 恢复暂存提示：成功走 warn 色显著提示重启生效；失败（含 observer 403）按前缀转 crit */}
            {restoreMsg && (
              <div className="inset px-3 py-2.5 mt-3 flex items-start gap-2 text-[12px] leading-relaxed"
                style={{ color: restoreMsg.startsWith('✕') ? 'var(--crit)' : 'var(--warn)' }}>
                <Ic name="alert" size={13} />
                <span>{restoreMsg}</span>
              </div>
            )}
            {bkMsg && <div className="text-[12px] mt-2.5" style={{ color: 'var(--crit)' }}>{bkMsg}</div>}
          </div>
      </div>
      </section>

      <section>
      <SectionHead id="sec-ai" icon="sparkles" label={t('st.sec.ai')} />
      <div className="card p-5 mt-4">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-2.5">
              <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{t('st.ai.masterTitle')}</h3>
              <span className="pill" style={aiOn
                ? { background: 'var(--ok-bg)', color: 'var(--ok)' }
                : { background: 'var(--neutral-bg)', color: 'var(--text-mute)' }}>
                {aiOn ? t('st.state.on') : t('st.state.off')}
              </span>
            </div>
            <p className="text-[12px] text-[var(--text-faint)] mt-1.5 leading-relaxed max-w-3xl">
              {t('st.ai.descA')}<b className="text-[var(--text-mute)]">{t('st.ai.localOnly')}</b>{t('st.ai.descB')}
            </p>
          </div>
          <button onClick={() => saveSetting('ai_outbound', aiOn ? 'off' : 'on')}
            className="btn shrink-0" style={aiOn
              ? { background: 'var(--ok-bg)', color: 'var(--ok)', borderColor: 'var(--ok-border)' }
              : { background: 'var(--neutral-bg)', color: 'var(--text-mute)', borderColor: 'var(--border)' }}>
            {aiOn ? t('st.state.on') : t('st.state.off')}
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
            <div className="text-[11px] text-[var(--text-faint)] mb-1.5">{t('st.ai.model')}</div>
            <input className="input mono" placeholder="qwen2.5:7b / deepseek-chat" list="llm-models" value={provider.model ?? ''}
              onChange={e => setProvider({ ...provider, model: e.target.value })}
              onBlur={() => saveProvider({})} />
            <datalist id="llm-models">
              {models.map(m => <option key={m} value={m} />)}
            </datalist>
          </div>
          <div>
            <div className="text-[11px] text-[var(--text-faint)] mb-1.5">{t('st.ai.apiKey')}</div>
            <input className="input mono" placeholder="sk-…" type="password" value={provider.api_key ?? ''}
              onChange={e => setProvider({ ...provider, api_key: e.target.value })}
              onBlur={() => saveProvider({})} />
          </div>
        </div>
        <div className="flex items-center gap-2.5 mt-3.5 flex-wrap">
          <button className="btn btn-primary" onClick={openCcSwitch} title={t('st.ai.importCsTitle')}>
            <Ic name="download" size={13} /> {t('st.ai.importCs')}
          </button>
          <button className="btn" disabled={fetching || !provider.base_url} onClick={fetchModels}>
            {fetching
              ? <>{t('st.ai.fetching')}</>
              : <><Ic name={models.length ? 'refresh' : 'chevron-down'} size={13} /> {models.length ? t('st.ai.refetch') : t('st.ai.fetchList')}</>}
          </button>
          <button className="btn" disabled={testing || !provider.base_url} onClick={testLlm}>
            {testing ? t('st.ai.testing') : <><Ic name="zap" size={13} /> {t('st.ai.testAlive')}</>}
          </button>
          <button className="btn" style={settings.ai_anonymize === 'on'
            ? { background: 'var(--ok-bg)', color: 'var(--ok)', borderColor: 'var(--ok-border)' }
            : undefined}
            onClick={() => saveSetting('ai_anonymize', settings.ai_anonymize === 'on' ? 'off' : 'on')}>
            {settings.ai_anonymize === 'on' ? t('st.ai.anonOn') : t('st.ai.anonOff')}
          </button>
          {models.length > 0 && (
            <select className="input mono" style={{ width: 'auto', padding: '6px 10px' }} value=""
              onChange={e => { if (e.target.value) saveProvider({ model: e.target.value }) }}>
              <option value="">{t('st.ai.pickModel', { n: models.length })}</option>
              {models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          )}
          {modelsMsg && <span className="text-[12px]" style={{ color: modelsMsg.startsWith('✓') ? 'var(--ok)' : 'var(--crit)' }}>{modelsMsg}</span>}
        </div>
        {testRes && (
          <div className="inset px-3 py-2.5 mt-3 text-[12px] mono" style={testRes.ok
            ? { color: 'var(--ok)' } : { color: 'var(--crit)' }}>
            {testRes.ok
              ? t('st.ai.testOk', { ms: testRes.latency_ms, status: testRes.status })
                + (testRes.model ? t('st.ai.testOkModel', { n: testRes.model }) : '')
                + (testRes.reply ? t('st.ai.testOkReply', { n: testRes.reply }) : '')
              : t('st.ai.testFail', { err: testRes.error })
                + (testRes.latency_ms ? t('st.ai.testFailMs', { ms: testRes.latency_ms }) : '')}
          </div>
        )}
        <p className="text-[11px] text-[var(--text-faint)] mt-3 leading-relaxed">
          {t('st.ai.footnote')}
        </p>
      </div>

      </section>

      {/* ═══ ③ 通知与分享 ═══ */}
      <section>
      <SectionHead id="sec-notify" icon="bell" label={t('st.sec.notify')} />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
        <div className="card p-5">
          <div className="flex items-center gap-2.5 mb-1.5">
            <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{t('st.statusPage.title')}</h3>
            <span className="pill" style={statusTok
              ? { background: 'var(--ok-bg)', color: 'var(--ok)' }
              : { background: 'var(--neutral-bg)', color: 'var(--text-mute)' }}>
              {statusTok ? t('st.statusPage.sharing') : t('st.statusPage.off')}
            </span>
          </div>
          <p className="text-[12px] text-[var(--text-faint)] mb-3">
            {t('st.statusPage.desc')}
          </p>
          {statusTok ? (
            <div className="space-y-2.5">
              <div className="flex flex-col sm:flex-row gap-2.5">
                <input className="input mono text-[11.5px]" readOnly value={`${location.protocol}//${location.host}/status/${statusTok}`} onFocus={e => e.target.select()} />
                <button className="btn btn-primary shrink-0"
                  onClick={() => navigator.clipboard.writeText(`${location.protocol}//${location.host}/status/${statusTok}`)}>{t('st.statusPage.copyLink')}</button>
              </div>
              <div className="flex gap-2.5">
                <a className="btn shrink-0" href={`/status/${statusTok}`} target="_blank" rel="noreferrer">{t('st.statusPage.preview')}</a>
                <button className="btn btn-ghost shrink-0" title={t('st.statusPage.rotateTitle')}
                  onClick={() => api<{ token: string }>('/status/token', { method: 'POST' }).then(r => setStatusTok(r.token))}><Ic name="refresh" size={12} /> {t('st.statusPage.rotate')}</button>
                <button className="btn btn-ghost shrink-0" style={{ color: 'var(--crit)' }}
                  onClick={() => api('/status/token', { method: 'DELETE' }).then(() => setStatusTok(''))}>{t('st.statusPage.revoke')}</button>
              </div>
            </div>
          ) : (
            <button className="btn btn-primary" onClick={() => api<{ token: string }>('/status/token', { method: 'POST' }).then(r => setStatusTok(r.token))}>
              {t('st.statusPage.generate')}
            </button>
          )}
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2.5 mb-1.5">
            <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{t('st.notify.title')}</h3>
            <span className="pill" style={notifyUrl
              ? { background: 'var(--ok-bg)', color: 'var(--ok)' }
              : { background: 'var(--neutral-bg)', color: 'var(--text-mute)' }}>
              {notifyUrl ? `● ${t(notifyLabels[notifyChannel] ?? notifyChannel)}` : t('st.notify.notConfigured')}
            </span>
          </div>
          <p className="text-[12px] text-[var(--text-faint)] mb-3">
            {t('st.notify.desc')}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 mb-2.5">
            <div>
              <div className="text-[11px] text-[var(--text-faint)] mb-1.5">{t('st.notify.channel')}</div>
              <select className="input" value={notifyChannel}
                onChange={e => { setNotifyChannel(e.target.value); saveSetting('notify_channel', e.target.value) }}>
                {Object.entries(notifyLabels).map(([k, v]) => <option key={k} value={k}>{t(v)}</option>)}
              </select>
            </div>
            <div>
              <div className="text-[11px] text-[var(--text-faint)] mb-1.5">{t(urlField.label)}</div>
              <input className="input mono" value={notifyUrl}
                placeholder={urlField.ph}
                onChange={e => setNotifyUrl(e.target.value)}
                onBlur={() => saveSetting('webhook_url', notifyUrl)} />
            </div>
            {notifyChannel === 'smtp' && (
              <div className="sm:col-span-2 text-[11px] text-[var(--text-faint)] leading-relaxed">{t('st.notify.smtpHint')}</div>
            )}
            {notifyChannel === 'telegram' && (
              <div className="sm:col-span-2">
                <div className="text-[11px] text-[var(--text-faint)] mb-1.5">{t('st.notify.chatId')}</div>
                <input className="input mono" placeholder="-100123456789" value={notifyChatId}
                  onChange={e => setNotifyChatId(e.target.value)}
                  onBlur={() => saveSetting('telegram_chat_id', notifyChatId)} />
              </div>
            )}
            <div>
              <div className="text-[11px] text-[var(--text-faint)] mb-1.5">{t('st.notify.quiet')}</div>
              <input className="input mono" placeholder="23:00-08:00" value={notifyQuiet}
                onChange={e => setNotifyQuiet(e.target.value)}
                onBlur={() => saveSetting('quiet_hours', notifyQuiet)} />
            </div>
            <div>
              <div className="text-[11px] text-[var(--text-faint)] mb-1.5">{t('st.notify.resend')}</div>
              <input className="input num" placeholder="0" value={notifyResend}
                onChange={e => setNotifyResend(e.target.value)}
                onBlur={() => saveSetting('notify_resend_min', notifyResend || '0')} />
            </div>
          </div>
          <div className="flex items-center gap-2.5 mt-3.5">
            <button className="btn" disabled={!notifyUrl || testingNotify}
              onClick={() => { setTestingNotify(true); api('/notify/test', { method: 'POST' })
                .then(() => setNotifyMsg(t('st.notify.testSent')))
                .catch(e => setNotifyMsg(`✕ ${e.message}`))
                .finally(() => setTestingNotify(false)) }}>
              {testingNotify ? t('st.notify.sending') : t('st.notify.sendTest')}
            </button>
            {notifyMsg && <span className="text-[12px]" style={{ color: notifyMsg.startsWith('✓') ? 'var(--ok)' : 'var(--crit)' }}>{notifyMsg}</span>}
          </div>
          {notifyLog.length > 0 && (
            <div className="mt-3.5">
              <div className="text-[11px] text-[var(--text-faint)] mb-1.5">{t('st.notify.recentLog')}</div>
              <div className="space-y-1">
                {notifyLog.slice(0, 5).map(l => (
                  <div key={l.id} className="flex items-center gap-2 text-[11.5px]">
                    <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: l.ok ? 'var(--ok)' : 'var(--crit)' }} />
                    <span className="text-[var(--text-faint)] num w-14 shrink-0">{fmtTime(l.ts).slice(-8)}</span>
                    <span className="pill text-[10px] shrink-0" style={{ background: 'var(--neutral-bg)', color: 'var(--text-mute)' }}>
                      {t(notifyLabels[l.channel] ?? l.channel)}
                    </span>
                    <span className="text-[var(--text)] truncate">{l.kind}: {l.text}</span>
                    {!l.ok && l.error && <span className="text-[var(--crit)] shrink-0 truncate max-w-[160px]" title={l.error}>{l.error}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="card p-5">
          <div className="flex items-center gap-2.5 mb-1.5">
            <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{t('st.mcp.title')}</h3>
            <span className="pill" style={{ background: 'var(--violet-bg)', color: 'var(--violet)', fontSize: 10 }}>{t('st.mcp.readonly')}</span>
          </div>
          <p className="text-[12px] text-[var(--text-faint)] mb-3">{t('st.mcp.desc')}</p>
          <div className="inset px-3 py-2.5">
            <code className="mono text-[12px]" style={{ color: 'var(--code-warn-text)' }}>
              {location.protocol}//{location.host}/api/mcp
            </code>
          </div>
          <div className="text-[11px] text-[var(--text-faint)] mt-2.5">{t('st.mcp.tools')}fleet_status · list_findings · get_finding · host_history · recent_events</div>
        </div>
      </div>

      </section>

      <section>
      <SectionHead id="sec-hosts" icon="server" label={t('st.sec.hosts')} />
      <div className="card p-5 mt-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{t('st.hosts.title')}</h3>
          <a href="/enroll" className="text-[12px] text-[var(--accent)] hover:underline inline-flex items-center gap-1"><Ic name="zap" size={12} /> {t('st.hosts.enrollLink')}</a>
        </div>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-[13px]">
          <thead>
            <tr className="text-[11px] text-[var(--text-faint)] text-left border-b border-[var(--border)]">
              <th className="py-2.5 font-medium">{t('st.hosts.name')}</th><th className="font-medium">{t('st.hosts.addr')}</th><th className="font-medium">{t('st.hosts.group')}</th><th className="font-medium">{t('st.hosts.type')}</th><th className="font-medium">{t('st.hosts.status')}</th><th></th>
            </tr>
          </thead>
          <tbody>
            {hosts.map(h => {
              const silenced = h.silenced_until ? h.silenced_until * 1000 > Date.now() : false
              return (
              <tr key={h.id} className="border-b border-[var(--border)] last:border-0 hover:bg-[var(--bg-hover)] transition-colors">
                <td className="py-2.5 font-medium text-[var(--text-hi)]">{h.name}</td>
                <td className="text-[var(--text-mute)] mono text-[12px]">{h.hostname}</td>
                <td className="text-[var(--text-mute)]">{h.group}</td>
                <td>
                  <span className="pill" style={h.mock
                    ? { background: 'var(--neutral-bg)', color: 'var(--text-mute)' }
                    : { background: 'var(--accent-dim)', color: 'var(--accent)' }}>
                    {h.mock ? t('st.hosts.mock') : 'SSH'}
                  </span>
                </td>
                <td>
                  {silenced
                    ? <span className="pill" style={{ background: 'var(--warn-bg)', color: 'var(--warn)', fontSize: 10 }}>{t('st.hosts.silenced')}</span>
                    : <span className="text-[11px] text-[var(--text-faint)]">—</span>}
                </td>
                <td className="text-right whitespace-nowrap">
                  <button className="text-[12px] text-[var(--text-faint)] hover:text-[var(--warn)] mr-3"
                    title={t('st.hosts.silenceTitle')}
                    onClick={() => {
                      const v = prompt(t('st.hosts.silencePrompt'), silenced ? '0' : '60')
                      if (v === null) return
                      api(`/hosts/${h.id}/silence`, { method: 'POST', body: JSON.stringify({ minutes: Number(v) || 0 }) }).then(load)
                    }}>{silenced ? t('st.hosts.unsilence') : t('st.hosts.silence')}</button>
                  {!h.mock && (
                    <button className="text-[12px] text-[var(--text-faint)] hover:text-[var(--accent)] mr-3"
                      title={t('st.hosts.trustTitle')}
                      onClick={() => api(`/hosts/${h.id}/trust-key`, { method: 'POST' }).then(load)}>{t('st.hosts.resetFp')}</button>
                  )}
                  {!h.mock && <button onClick={() => del(h)} className="text-[12px] text-[var(--text-faint)] hover:text-[var(--crit)]">{t('btn.delete')}</button>}
                </td>
              </tr>
              )
            })}
          </tbody>
        </table>
        </div>
        <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5 items-end">
          <input className="input" placeholder={t('st.hosts.name')} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
          <input className="input col-span-2 mono" placeholder={t('st.hosts.phAddr')} value={form.hostname} onChange={e => setForm({ ...form, hostname: e.target.value })} />
          <input className="input" placeholder={t('st.hosts.user')} value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} />
          <input className="input" placeholder={t('st.hosts.phSecret')} type="password" value={form.secret} onChange={e => setForm({ ...form, secret: e.target.value })} />
          <button className="btn btn-primary justify-center" onClick={add}>{t('st.hosts.add')}</button>
        </div>
        {/* 堡垒机/跳板折叠区：经跳板隧道路由 SSH（TOFU 指纹与目标机独立），口令同样加密存储 */}
        <button className="text-[11.5px] text-[var(--text-faint)] hover:text-[var(--accent)] flex items-center gap-1 mt-2.5"
          onClick={() => setBastionOpen(v => !v)}>
          <Ic name="chevron-down" size={12} style={{ transform: bastionOpen ? 'rotate(180deg)' : undefined, transition: 'transform .2s' }} />
          {t('st.hosts.bastion')}
        </button>
        <div className={`acc-body ${bastionOpen ? 'open' : ''}`}>
          <div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 pt-2.5">
              <input className="input mono" placeholder={t('st.hosts.bastionHost')} value={form.bastion_host} onChange={e => setForm({ ...form, bastion_host: e.target.value })} />
              <input className="input num" inputMode="numeric" placeholder={t('st.hosts.bastionPort')} value={form.bastion_port} onChange={e => setForm({ ...form, bastion_port: e.target.value })} />
              <input className="input" placeholder={t('st.hosts.bastionUser')} value={form.bastion_username} onChange={e => setForm({ ...form, bastion_username: e.target.value })} />
              <input className="input" type="password" placeholder={t('st.hosts.bastionSecret')} value={form.bastion_secret} onChange={e => setForm({ ...form, bastion_secret: e.target.value })} />
            </div>
            <div className="text-[11px] text-[var(--text-faint)] mt-1.5">{t('st.hosts.bastionHint')}</div>
          </div>
        </div>
        {msg && <div className="text-[12px] mt-2.5 text-[var(--text-mute)]">{msg}</div>}
        <p className="text-[11px] text-[var(--text-faint)] mt-3 leading-relaxed">
          {t('st.hosts.descA')}<a href="/enroll" className="text-[var(--accent)] hover:underline">{t('st.hosts.agentLink')}</a>{t('st.hosts.descB')}
        </p>
      </div>

      </section>

      {/* ═══ ⑤ 安全与用户 ═══ */}
      <section>
      <SectionHead id="sec-security" icon="shield" label={t('st.sec.security')} />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
          <div className="card p-5">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{t('st.auth.title')}</h3>
                <p className="text-[12px] text-[var(--text-faint)] mt-1.5 leading-relaxed">
                  {t('st.auth.desc')}
                </p>
              </div>
              <span className="pill shrink-0" style={authOn
                ? { background: 'var(--ok-bg)', color: 'var(--ok)' }
                : { background: 'var(--neutral-bg)', color: 'var(--text-mute)' }}>
                {authOn ? t('st.state.on') : t('st.state.off')}
              </span>
            </div>
            {!authOn && (
              <div className="flex flex-col sm:flex-row gap-2.5 mt-3.5">
                <input className="input" type="password" placeholder={t('st.auth.phSet')} value={authPw}
                  onChange={e => setAuthPw(e.target.value)} />
                <button className="btn btn-primary shrink-0" disabled={!authPw}
                  onClick={() => api('/auth/enable', { method: 'POST', body: JSON.stringify({ password: authPw }) })
                    .then(() => { setAuthPw(''); setAuthMsg(t('st.auth.enabledMsg')); load() })
                    .catch(e => setAuthMsg(`✕ ${e.message}`))}>{t('st.auth.enable')}</button>
              </div>
            )}
            {authOn && (
              <div className="mt-3.5 space-y-2.5">
                <div className="flex flex-col sm:flex-row gap-2.5">
                  <input className="input" type="password" placeholder={t('st.auth.phCurrent')} value={authOld}
                    onChange={e => setAuthOld(e.target.value)} />
                  <input className="input" type="password" placeholder={t('st.auth.phNew')} value={authPw}
                    onChange={e => setAuthPw(e.target.value)} />
                  <button className="btn shrink-0" disabled={!authOld || !authPw}
                    onClick={() => api('/auth/change', { method: 'POST', body: JSON.stringify({ old: authOld, new: authPw }) })
                      .then(() => { setAuthPw(''); setAuthOld(''); setAuthMsg(t('st.auth.changedMsg')) })
                      .catch(e => setAuthMsg(`✕ ${e.message}`))}>{t('st.auth.change')}</button>
                </div>
                <div className="flex flex-col sm:flex-row gap-2.5">
                  <button className="btn btn-ghost shrink-0" onClick={() => api('/auth/logout', { method: 'POST' }).then(() => { location.href = '/login' })}>{t('st.auth.logout')}</button>
                  <input className="input" type="password" placeholder={t('st.auth.phDisable')} value={authOld}
                    onChange={e => setAuthOld(e.target.value)} />
                  <button className="btn shrink-0" disabled={!authOld}
                    onClick={() => api('/auth/disable', { method: 'POST', body: JSON.stringify({ password: authOld }) })
                      .then(() => { setAuthOld(''); setAuthPw(''); setAuthMsg(''); load() })
                      .catch(e => setAuthMsg(`✕ ${e.message}`))}>{t('st.auth.disable')}</button>
                </div>
              </div>
            )}
            {authMsg && <div className="text-[12px] mt-2.5 text-[var(--text-mute)]">{authMsg}</div>}
          </div>

          {/* 用户管理（admin）：多用户 + 角色分发 */}
          {authOn && authRole === 'admin' && (
            <div className="card p-5">
              <div className="flex items-center gap-2.5 mb-1.5">
                <h3 className="font-semibold text-[14.5px] text-[var(--text-hi)]">{t('st.users.title')}</h3>
                <span className="pill" style={{ background: 'var(--accent-dim)', color: 'var(--accent)', fontSize: 10 }}>admin</span>
              </div>
              <p className="text-[12px] text-[var(--text-faint)] mb-3 leading-relaxed">
                {legacyPw
                  ? t('st.users.legacyDesc')
                  : t('st.users.rolesDesc')}
              </p>
              <div className="space-y-1.5 mb-3">
                {users.map(u => (
                  <div key={u.id} className="flex items-center gap-2.5 text-[13px] inset px-3 py-2">
                    <span className="font-semibold text-[var(--text-hi)]">{u.username}</span>
                    <span className="pill text-[10px]" style={u.role === 'admin'
                      ? { background: 'var(--accent-dim)', color: 'var(--accent)' }
                      : { background: 'var(--neutral-bg)', color: 'var(--text-mute)' }}>{u.role}</span>
                    <div className="ml-auto flex items-center gap-2.5">
                      <button className="text-[11.5px] text-[var(--text-faint)] hover:text-[var(--accent)]"
                        onClick={() => {
                          const role = u.role === 'admin' ? 'observer' : 'admin'
                          api(`/auth/users/${u.id}/role`, { method: 'POST', body: JSON.stringify({ role }) }).then(load)
                        }}>{t('st.users.changeTo', { n: u.role === 'admin' ? 'observer' : 'admin' })}</button>
                      <button className="text-[11.5px] text-[var(--text-faint)] hover:text-[var(--crit)]"
                        onClick={() => confirm(t('st.users.delConfirm', { n: u.username })) &&
                          api(`/auth/users/${u.id}`, { method: 'DELETE' }).then(load).catch(e => alert(e.message))}>{t('btn.delete')}</button>
                    </div>
                  </div>
                ))}
                {!users.length && <div className="text-[11.5px] text-[var(--text-faint)]">{t('st.users.empty')}</div>}
              </div>
              <UserAdd onAdded={load} />
            </div>
          )}

          {/* observer 只读提示 */}
          {authOn && authRole === 'observer' && (
            <div className="card p-4 mb-4 flex items-center gap-3" style={{ background: 'var(--warn-bg)', borderColor: 'var(--warn-border)' }}>
              <span style={{ color: 'var(--warn)' }}><Ic name="search" size={16} /></span>
              <span className="text-[12.5px] text-[var(--text)]">{t('st.obs.prefix')} <b>{t('st.obs.role')}</b>{t('st.obs.suffix')}</span>
            </div>
          )}
      </div>

      </section>
      </div>

      <SectionRail sections={sections} />

      {/* 备份恢复 / 删除确认弹窗 */}
      <ConfirmDialog open={!!restoreTarget} title={t('st.backup.restoreTitle', { n: restoreTarget ?? '' })}
        body={t('st.backup.restoreBody')} confirmText={t('st.backup.restore')}
        onConfirm={doRestore} onCancel={() => setRestoreTarget(null)} />
      <ConfirmDialog open={!!delTarget} title={t('st.backup.deleteTitle', { n: delTarget ?? '' })}
        body={t('st.backup.deleteBody')}
        onConfirm={delBackup} onCancel={() => setDelTarget(null)} />

      {/* cc-switch 一键导入弹窗 */}
      {csOpen && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center p-4 fade-in"
          style={{ background: 'rgba(4,10,22,.55)', backdropFilter: 'blur(2px)' }}
          onClick={() => setCsOpen(false)}>
          <div className="card p-5 w-full max-w-[560px] pop-in flex flex-col" role="dialog" aria-modal="true"
            style={{ maxHeight: '80vh' }} onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-3 mb-1">
              <div className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0"
                style={{ background: 'var(--accent-dim)', border: '1px solid var(--accent-border)', color: 'var(--accent)' }}>
                <Ic name="download" size={16} />
              </div>
              <div>
                <h3 className="font-semibold text-[15px] text-[var(--text-hi)]">{t('st.ai.importCs')}</h3>
                <p className="text-[11.5px] text-[var(--text-faint)]">{t('st.cs.modalDesc')}</p>
              </div>
              <button className="btn btn-ghost ml-auto shrink-0" onClick={() => setCsOpen(false)}>✕</button>
            </div>
            <div className="flex-1 overflow-y-auto space-y-1.5 mt-3 pr-1">
              {csLoading && <div className="text-[12.5px] text-[var(--text-faint)] py-6 text-center">{t('st.cs.loading')}</div>}
              {!csLoading && !csList.length && (
                <div className="text-[12.5px] text-[var(--text-faint)] py-6 text-center">{csMsg || t('st.cs.empty')}</div>
              )}
              {csList.map((p, i) => (
                <button key={`${p.base_url}-${i}`} onClick={() => applyCs(p)}
                  className="w-full text-left card card-hover px-3.5 py-2.5 flex items-center gap-3">
                  <span className="pill text-[10px] shrink-0" style={{ background: 'var(--accent-dim)', color: 'var(--accent)' }}>
                    {({ codex: 'Codex', claude: 'Claude', gemini: 'Gemini' } as Record<string, string>)[p.app_type] ?? p.app_type}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-semibold text-[var(--text-hi)] truncate">{p.name || p.base_url}</div>
                    <div className="text-[11px] text-[var(--text-faint)] mono truncate">
                      {p.base_url}{p.model ? ` · ${p.model}` : ''}
                    </div>
                  </div>
                  <Ic name="play" size={12} style={{ color: 'var(--accent)', opacity: .6 }} />
                </button>
              ))}
            </div>
            {csMsg && csList.length > 0 && <div className="text-[11.5px] text-[var(--text-faint)] mt-2.5">{csMsg}</div>}
          </div>
        </div>
      )}
    </div>
  )
}
