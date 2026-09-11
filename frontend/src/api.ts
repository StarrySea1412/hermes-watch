// API client + shared types
export type Host = { id: number; name: string; hostname: string; group: string; username: string; mock: boolean; silenced_until?: number; trusted?: boolean; host_key_fp?: string; host_key_pending?: string }
export type HostCard = {
  id: number; name: string; hostname: string; group: string; mock: boolean
  score: number; status: 'ok' | 'warn' | 'crit' | 'offline'
  online: boolean; last_ok_ts: number; last_error: string
  silenced_until?: number
  trusted?: boolean; host_key_fp?: string; host_key_pending?: string
  latest: { cpu: number; mem: number; disk: number; load1: number; net_in: number; net_out: number; ts: number } | null
  spark: { disk: number; mem: number; cpu: number }[]
  open_findings: number; worst: 'crit' | 'warn' | 'info' | null
  uptime?: number | null
}
export type Finding = {
  id: number; host_id: number; host_name?: string; ts: number
  type: string; severity: 'crit' | 'warn' | 'info'
  title: string; detail: string; evidence: any; status: string
  acked_at?: number | null
  card: {
    root_cause: string; chain: string[]; confidence: string
    steps: { label: string; command: string; output: string }[]
    proposal_id: number | null
    ai_narration?: string | null; ai_model?: string | null; ai_error?: string | null
  } | null
}
export type ProposalRun = {
  id: number; ts: number; proposal_id: number; host_id: number
  mode: string; command: string; risk: string
  status: 'ok' | 'failed' | 'timeout' | 'refused'; exit_code: number | null
  output: string; duration_ms: number
}
export type Proposal = {
  id: number; ts: number; host_name: string; finding_id: number
  title: string; command: string; rationale: string; status: string
  host_mock?: number; runs?: ProposalRun[]; exec_enabled?: boolean
}
export type Event = { id: number; ts: number; host_name: string | null; kind: string; message: string; data?: any }

export async function api<T>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' }, ...opts,
  })
  if (r.status === 401) {
    // 会话失效：整页跳登录（App 级状态门会渲染登录页）
    if (!location.pathname.startsWith('/login')) { location.href = '/login' }
    throw new Error('面板未登录')
  }
  if (!r.ok) throw new Error(`${await r.text() || r.status}`)
  return r.json()
}

export function subscribe(onEvent: (e: any) => void): () => void {
  const es = new EventSource('/api/stream')
  es.onmessage = (m) => { try { onEvent(JSON.parse(m.data)) } catch { /* ignore */ } }
  return () => es.close()
}

export const fmtTime = (ts: number) =>
  new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })

export const fmtNet = (v: number) =>
  v > 1e6 ? `${(v / 1e6).toFixed(1)} MB/s` : v > 1e3 ? `${(v / 1e3).toFixed(0)} KB/s` : `${v} B/s`

export const SEV: Record<string, { bg: string; text: string; label: string }> = {
  crit: { bg: 'bg-red-500/15', text: 'text-red-400', label: '严重' },
  warn: { bg: 'bg-amber-500/15', text: 'text-amber-400', label: '警告' },
  info: { bg: 'bg-sky-500/15', text: 'text-sky-400', label: '提示' },
}
