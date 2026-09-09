import { useState } from 'react'
import { api } from '../api'

/** 添加用户行内表单（设置 → 用户管理，admin 专用） */
export function UserAdd({ onAdded }: { onAdded: () => void }) {
  const [name, setName] = useState('')
  const [pw, setPw] = useState('')
  const [role, setRole] = useState('observer')
  const [msg, setMsg] = useState('')
  const add = () => {
    api('/auth/users', { method: 'POST', body: JSON.stringify({ username: name, password: pw, role }) })
      .then(() => { setName(''); setPw(''); setMsg(''); onAdded() })
      .catch(e => setMsg(e.message))
  }
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 items-end">
      <input className="input" placeholder="用户名（≥2 位）" value={name} onChange={e => setName(e.target.value)} />
      <input className="input" type="password" placeholder="口令（≥4 位）" value={pw} onChange={e => setPw(e.target.value)} />
      <select className="input" value={role} onChange={e => setRole(e.target.value)}>
        <option value="observer">observer（只读）</option>
        <option value="admin">admin（可写）</option>
      </select>
      <button className="btn btn-primary justify-center" disabled={!name || !pw} onClick={add}>＋ 添加用户</button>
      {msg && <div className="col-span-4 text-[11.5px]" style={{ color: 'var(--crit)' }}>✕ {msg}</div>}
    </div>
  )
}
