import { useEffect, useState } from 'react'
import { api } from '../api'
import { useT } from '../i18n'

export default function Login() {
  const { t } = useT()
  const [user, setUser] = useState('')
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [multiUser, setMultiUser] = useState(false)

  useEffect(() => {
    api<{ multi_user: boolean }>('/auth/status').then(s => setMultiUser(s.multi_user)).catch(() => { /* noop */ })
  }, [])

  const submit = async () => {
    if (!pw || busy) return
    setBusy(true); setErr('')
    try {
      await api('/auth/login', { method: 'POST', body: JSON.stringify({ username: user, password: pw }) })
      location.href = '/'  // 整页跳转：Gate 重新校验会话后渲染面板
    } catch (e: any) {
      setErr(e.message.includes('429') ? t('login.ratelimited') : t('login.badPass'))
    } finally { setBusy(false) }
  }

  return (
    <div className="h-screen flex items-center justify-center relative overflow-hidden">
      <div className="pop-in card p-8 w-[360px] relative z-10">
        <div className="flex items-center gap-3 mb-6">
          <div className="w-11 h-11 rounded-xl flex items-center justify-center text-xl"
            style={{ background: 'linear-gradient(135deg, var(--accent-dim), var(--violet-bg))', border: '1px solid var(--accent-border)' }}>
            🐚
          </div>
          <div>
            <div className="font-bold text-[16px] text-[var(--text-hi)]">Hermes Watch</div>
            <div className="text-[11px] text-[var(--text-faint)]">{t('login.sub')}</div>
          </div>
        </div>
        {multiUser && (
          <input className="input w-full mb-2.5" placeholder={t('login.user')} autoFocus
            value={user} onChange={e => setUser(e.target.value)} />
        )}
        <input className="input w-full" type="password" placeholder={multiUser ? t('login.pw') : t('login.pwPanel')}
          autoFocus={!multiUser}
          value={pw} onChange={e => setPw(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && submit()} />
        {err && <div className="text-[12px] mt-2.5" style={{ color: 'var(--crit)' }}>✕ {err}</div>}
        <button className="btn btn-primary w-full justify-center mt-4" disabled={busy || !pw} onClick={submit}>
          {busy ? t('login.verifying') : t('login.unlock')}
        </button>
        <div className="text-[10.5px] text-[var(--text-faint)] mt-4 leading-relaxed">
          {multiUser ? t('login.hintMulti') : t('login.hintSingle')}
        </div>
      </div>
      <div className="absolute bottom-6 inset-x-0 text-center text-[10.5px] text-[var(--text-faint)] select-none">
        🐚 Hermes Watch · {t('login.footer')}
      </div>
    </div>
  )
}
