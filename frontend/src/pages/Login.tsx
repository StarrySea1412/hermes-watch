import { useState } from 'react'
import { api } from '../api'

export default function Login() {
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!pw || busy) return
    setBusy(true); setErr('')
    try {
      await api('/auth/login', { method: 'POST', body: JSON.stringify({ password: pw }) })
      location.href = '/'  // 整页跳转：Gate 重新校验会话后渲染面板
    } catch (e: any) {
      setErr(e.message.includes('429') ? '尝试过于频繁，请稍后再试' : '口令错误')
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
            <div className="text-[11px] text-[var(--text-faint)]">面板已开启访问控制，请输入口令</div>
          </div>
        </div>
        <input className="input w-full" type="password" placeholder="面板口令" autoFocus
          value={pw} onChange={e => setPw(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && submit()} />
        {err && <div className="text-[12px] mt-2.5" style={{ color: 'var(--crit)' }}>✕ {err}</div>}
        <button className="btn btn-primary w-full justify-center mt-4" disabled={busy || !pw} onClick={submit}>
          {busy ? '验证中…' : '解锁面板'}
        </button>
        <div className="text-[10.5px] text-[var(--text-faint)] mt-4 leading-relaxed">
          口令在 设置 → 访问控制 中配置；连续输错 10 次将锁定 1 分钟。
        </div>
      </div>
      <div className="absolute bottom-6 inset-x-0 text-center text-[10.5px] text-[var(--text-faint)] select-none">
        🐚 Hermes Watch · 本地服务器巡检平台 · 数据不出本机
      </div>
    </div>
  )
}
