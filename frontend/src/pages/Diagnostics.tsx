import { useEffect, useMemo, useState } from 'react'
import { api, fmtTime, SEV, subscribe, type Finding, type Proposal } from '../api'
import { Ic } from '../icons'
import { PageHead } from '../ui'

const RUN_STYLE = (s: string) => s === 'ok'
  ? { background: 'var(--ok-bg)', color: 'var(--ok)' }
  : s === 'refused' ? { background: 'var(--neutral-bg)', color: 'var(--text-mute)' }
  : { background: 'var(--crit-bg)', color: 'var(--crit)' }
const RUN_LABEL: Record<string, string> = { ok: '✓ 执行成功', failed: '✕ 执行失败', timeout: '⏱ 超时终止', refused: '⛔ 已拦截' }

const SEV_STYLE = (s: string) => s === 'crit'
  ? { background: 'var(--crit-bg)', color: 'var(--crit)' }
  : s === 'warn' ? { background: 'var(--warn-bg)', color: 'var(--warn)' }
  : { background: 'var(--info-bg)', color: 'var(--info)' }
const SEV_BAR: Record<string, string> = { crit: 'var(--crit)', warn: 'var(--warn)', info: 'var(--info)' }

/* 诊断流水线：发现 → 深挖 → 根因 → 提案 → 审批 → 执行 */
const STAGES = ['发现', 'Agent 深挖', '根因结论', '修复提案', '人工审批', '执行留痕']

function stageOf(f: Finding, p?: Proposal): { active: number; done: number; blocked?: boolean } {
  if (f.status === 'open') return { active: 1, done: 0 }
  if (f.status === 'resolved') return { active: 5, done: 5 }
  if (!p) return { active: 2, done: 2 }          // 已诊断、暂无预置提案
  if (p.status === 'pending') return { active: 4, done: 3 }
  if (p.status === 'approved') {
    const executed = p.runs?.some(r => r.status === 'ok')
    return executed ? { active: 5, done: 5 } : { active: 5, done: 4 }
  }
  return { active: 4, done: 3, blocked: true }   // 已拒绝
}

export default function Diagnostics() {
  const [findings, setFindings] = useState<Finding[]>([])
  const [props, setProps] = useState<Proposal[]>([])
  const [sel, setSel] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [openStep, setOpenStep] = useState(0)
  const [sevFilter, setSevFilter] = useState<'all' | 'crit' | 'warn' | 'info'>('all')
  const [openOnly, setOpenOnly] = useState(false)
  const [copied, setCopied] = useState(false)

  const load = () => Promise.all([api<Finding[]>('/findings'), api<Proposal[]>('/proposals')])
    .then(([f, p]) => { setFindings(f); setProps(p); setSel(s => (s && f.some(x => x.id === s)) ? s : f[0]?.id ?? null) })
  useEffect(() => { load(); return subscribe(() => load()) }, [])

  const counts = useMemo(() => ({
    crit: findings.filter(f => f.severity === 'crit').length,
    warn: findings.filter(f => f.severity === 'warn').length,
    info: findings.filter(f => f.severity === 'info').length,
    open: findings.filter(f => f.status === 'open').length,
    resolved: findings.filter(f => f.status === 'resolved').length,
  }), [findings])

  const shown = useMemo(() => findings.filter(f =>
    (sevFilter === 'all' || f.severity === sevFilter) && (!openOnly || f.status !== 'resolved')),
    [findings, sevFilter, openOnly])

  const current = shown.find(f => f.id === sel) ?? shown[0]
  const proposalOf = (fid: number) => props.find(p => p.finding_id === fid)

  const analyze = async (fid: number) => {
    setBusy(true); setOpenStep(0)
    try { await api(`/findings/${fid}/analyze`, { method: 'POST' }) } finally { setBusy(false); load() }
  }
  const decide = async (pid: number, action: 'approve' | 'reject') => {
    setBusy(true)
    try { await api(`/proposals/${pid}/decide`, { method: 'POST', body: JSON.stringify({ action }) }) }
    finally { setBusy(false); load() }
  }
  const execute = async (pid: number) => {
    setBusy(true)
    try { await api(`/proposals/${pid}/execute`, { method: 'POST' }) }
    finally { setBusy(false); load() }
  }
  const copyCmd = async (text: string) => {
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500) } catch { /* noop */ }
  }

  const filterPill = (key: 'all' | 'crit' | 'warn' | 'info', label: string, color?: string, n?: number) => (
    <button onClick={() => setSevFilter(key)}
      className={`pill ${sevFilter === key ? '' : 'text-[var(--text-mute)]'}`}
      style={sevFilter === key
        ? { background: color ? `${color}1f` : 'var(--accent-dim)', color: color ?? 'var(--accent)', border: `1px solid ${color ?? 'var(--accent)'}44` }
        : { background: 'var(--neutral-bg)', border: '1px solid transparent' }}>
      {label} <b className="num">{n ?? 0}</b>
    </button>
  )

  return (
    <div className="fade-in">
      <PageHead title="诊断中心" sub="根因卡片 · 证据链 · 只读提议制（批准后才执行）" />

      {/* 统计 + 筛选 */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        {filterPill('all', '全部', undefined, findings.length)}
        {filterPill('crit', '严重', 'var(--crit)', counts.crit)}
        {filterPill('warn', '警告', 'var(--warn)', counts.warn)}
        {filterPill('info', '提示', 'var(--info)', counts.info)}
        <button onClick={() => setOpenOnly(v => !v)}
          className={`pill ${openOnly ? '' : 'text-[var(--text-mute)]'}`}
          style={openOnly
            ? { background: 'var(--accent-dim)', color: 'var(--accent)', border: '1px solid var(--accent-border)' }
            : { background: 'var(--neutral-bg)', border: '1px solid transparent' }}>
          隐藏已解决 <b className="num">{counts.resolved}</b>
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
        {/* 发现列表：粘性 + 独立滚动；key 随筛选变化 → 切筛选时整列入场 */}
        <div key={`${sevFilter}-${openOnly}`} className="lg:col-span-4 lg:sticky lg:top-4 space-y-2.5 lg:max-h-[calc(100vh-210px)] lg:overflow-y-auto lg:pr-1.5 pb-1 rise-in">
          {shown.map(f => {
            const p = proposalOf(f.id)
            const st = stageOf(f, p)
            const selected = current?.id === f.id
            return (
              <div key={f.id} onClick={() => { setSel(f.id); setOpenStep(0) }}
                className={`card card-hover p-3.5 cursor-pointer relative overflow-hidden active:scale-[.985] ${selected ? 'ring-1 ring-[var(--accent)]' : ''}`}
                style={{
                  // 简写在前、长手在后：React 按键序应用，保证左条颜色不被 borderColor 覆盖；
                  // 颜色变化走 .card 的 transition（border-color/background .18-.25s）
                  ...(selected ? { background: 'var(--accent-dim)', borderColor: 'var(--accent-border)' } : {}),
                  borderLeftWidth: 3,
                  borderLeftStyle: 'solid',
                  borderLeftColor: selected ? 'var(--accent)' : SEV_BAR[f.severity],
                }}>
                <div className="flex items-center gap-2">
                  <span className="pill" style={SEV_STYLE(f.severity)}>{SEV[f.severity].label}</span>
                  <span className="text-[13px] font-semibold text-[var(--text-hi)]">{f.host_name}</span>
                  <span className="ml-auto text-[10.5px] text-[var(--text-faint)] num">{fmtTime(f.ts).slice(5, 16)}</span>
                </div>
                <div className="text-[13.5px] mt-2 text-[var(--text)] font-medium">{f.title}</div>
                <div className="text-[11px] text-[var(--text-faint)] mt-1.5 flex items-center gap-1.5 flex-wrap">
                  <span className="w-1.5 h-1.5 rounded-full" style={{ background: f.status === 'open' ? 'var(--warn)' : f.status === 'analyzed' ? 'var(--accent)' : 'var(--ok)' }} />
                  {f.status === 'open' ? '待诊断' : f.status === 'analyzed' ? '已诊断' : '已解决'}
                  <span className="text-[var(--text-faint)]">· 流程 {Math.min(st.done, 5)}/{5}</span>
                  {p && <span className="pill" style={{
                    fontSize: 10, padding: '0 7px',
                    ...(p.status === 'approved' ? { background: 'var(--ok-bg)', color: 'var(--ok)' }
                      : p.status === 'rejected' ? { background: 'var(--neutral-bg)', color: 'var(--text-faint)' }
                        : { background: 'var(--violet-bg)', color: 'var(--violet)' }),
                  }}>{p.status === 'pending' ? '提案待审批' : p.status === 'approved' ? '提案已批准 ✓' : '提案已拒绝'}</span>}
                </div>
              </div>
            )
          })}
          {!shown.length && (
            <div className="card p-8 text-center">
              <div className="flex justify-center mb-3 text-[var(--text-faint)]">
                <Ic name={findings.length ? 'search' : 'check-circle'} size={30} sw={1.5} />
              </div>
              <div className="text-[var(--text-mute)] text-[13px]">{findings.length ? '当前筛选条件下没有匹配的发现' : '当前没有待处理的发现'}</div>
            </div>
          )}
        </div>

        {/* 根因卡 */}
        <div className="lg:col-span-8">
          {!current && <div className="card p-12 text-center text-[var(--text-faint)]">选择左侧发现查看诊断详情</div>}
          {current && (
            <div key={current.id} className="card p-6 rise-in">
              {/* 头部 */}
              <div className="flex items-start gap-3 flex-wrap">
                <span className="pill shrink-0" style={SEV_STYLE(current.severity)}>{SEV[current.severity].label}</span>
                <div className="min-w-0 flex-1">
                  <h2 className="font-bold text-[16.5px] text-[var(--text-hi)] leading-snug">{current.title}</h2>
                  <div className="text-[11.5px] text-[var(--text-faint)] mt-1 num">
                    {current.host_name} · 发现于 {fmtTime(current.ts)}
                  </div>
                </div>
                {current.status === 'open' && (
                  <button disabled={busy} onClick={() => analyze(current.id)} className="btn btn-primary ml-auto shrink-0">
                    {busy ? <><span className="pulse-dot" style={{ background: 'var(--accent)' }} /> 诊断中…</> : <><Ic name="play" size={13} /> Agent 深挖诊断</>}
                  </button>
                )}
                {current.status !== 'resolved' && !current.acked_at && (
                  <button disabled={busy} title="已知悉此告警：停止持续告警重发"
                    onClick={() => api(`/findings/${current.id}/ack`, { method: 'POST' }).then(load).catch(() => { /* noop */ })}
                    className="btn shrink-0" style={{ background: 'var(--neutral-bg)', color: 'var(--text-mute)' }}>
                    <Ic name="check-circle" size={13} /> 确认
                  </button>
                )}
                {current.acked_at ? (
                  <span className="pill shrink-0" style={{ background: 'var(--neutral-bg)', color: 'var(--text-mute)' }}>
                    ✓ 已确认（不重发）
                  </span>
                ) : null}
              </div>

              {/* 诊断流水线 */}
              {(() => {
                const p = proposalOf(current.id)
                const { active, done, blocked } = stageOf(current, p)
                return (
                  <div className="mt-5 inset px-4 py-3.5">
                    <div className="flex items-center">
                      {STAGES.map((s, i) => {
                        const isDone = i < done
                        const isActive = i === active
                        const color = blocked && i === 4 ? 'var(--crit)' : isDone || isActive ? (isActive ? 'var(--accent)' : 'var(--ok)') : 'var(--text-faint)'
                        return (
                          <div key={s} className="flex items-center" style={{ flex: i < STAGES.length - 1 ? 1 : '0 0 auto' }}>
                            <div className="flex flex-col items-center gap-1.5" style={{ width: 64 }}>
                              <span className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold num shrink-0 transition-colors"
                                style={isActive
                                  ? { background: 'var(--accent)', color: '#04121c', boxShadow: '0 0 0 4px var(--accent-dim)' }
                                  : { background: isDone ? 'var(--ok-bg)' : 'var(--neutral-bg)', color }}>
                                {isDone && !isActive ? '✓' : i + 1}
                              </span>
                              <span className="text-[10px] whitespace-nowrap" style={{ color: isActive ? 'var(--accent)' : isDone ? 'var(--text-mute)' : 'var(--text-faint)' }}>{s}</span>
                            </div>
                            {i < STAGES.length - 1 && (
                              <div className="h-0.5 flex-1 rounded-full -mt-4"
                                style={{ background: i < done ? 'var(--ok)' : 'var(--border)', opacity: i < done ? 0.7 : 1 }} />
                            )}
                          </div>
                        )
                      })}
                    </div>
                    {blocked && <div className="text-[10.5px] text-[var(--text-faint)] mt-2.5">提案被拒绝 — 流程在该阶段终止，可重新发起诊断</div>}
                  </div>
                )
              })()}

              {current.card && (
                <>
                  {/* 根因分析 */}
                  <div className="mt-4 rounded-xl p-4" style={{ background: 'var(--accent-dim)', border: '1px solid var(--accent-border)' }}>
                    <div className="flex items-center justify-between mb-2">
                      <div className="text-[11px] font-semibold tracking-wide flex items-center gap-1.5" style={{ color: 'var(--accent)' }}>
                        <Ic name="search" size={12} sw={2.4} /> 根因分析
                      </div>
                      <span className="pill" style={{ background: 'var(--ok-bg)', color: 'var(--ok)', fontSize: 10.5 }}>
                        置信度 {current.card.confidence}
                      </span>
                    </div>
                    <div className="text-[13.5px] leading-relaxed text-[var(--text-hi)]">{current.card.root_cause}</div>
                    {current.card.chain.length > 0 && (
                      <div className="mt-3.5 space-y-0">
                        {current.card.chain.map((c, i) => (
                          <div key={i} className="flex items-stretch gap-3">
                            <div className="flex flex-col items-center">
                              <span className="w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold num shrink-0"
                                style={{ background: 'var(--accent)', color: '#04121c' }}>{i + 1}</span>
                              {i < current.card!.chain.length - 1 && <span className="w-px flex-1 my-0.5" style={{ background: 'var(--accent-border)' }} />}
                            </div>
                            <span className="text-[12.5px] text-[var(--text)] pb-2.5 leading-relaxed">{c}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {(current.card.ai_narration || current.card.ai_error) && (
                    <div className="mt-3 rounded-xl p-4" style={{ background: 'var(--violet-bg)', border: '1px solid var(--violet-border)' }}>
                      <div className="flex items-center gap-2 mb-1.5">
                        <span className="pill text-[10.5px] flex items-center gap-1" style={{ background: 'var(--violet-strong-bg)', color: 'var(--violet)' }}>
                          <Ic name="sparkles" size={10} /> AI 叙事
                        </span>
                        {current.card.ai_model && (
                          <span className="text-[10.5px] text-[var(--text-faint)] mono">{current.card.ai_model}</span>
                        )}
                        <span className="ml-auto text-[10px] text-[var(--text-faint)]">附加视角 · 不替代规则引擎结论</span>
                      </div>
                      {current.card.ai_narration ? (
                        <div className="text-[13px] leading-relaxed text-[var(--text-hi)]">{current.card.ai_narration}</div>
                      ) : (
                        <div className="text-[12px] leading-relaxed text-[var(--text-mute)]">
                          AI 生成失败，规则引擎结论不受影响：<span className="mono text-[11px]">{current.card.ai_error}</span>
                        </div>
                      )}
                    </div>
                  )}

                  {/* 证据链 */}
                  <div className="mt-5">
                    <div className="text-[11px] text-[var(--text-faint)] mb-2.5 font-medium flex items-center gap-1.5">
                      <Ic name="terminal" size={12} /> Agent 执行轨迹 · 每步命令与输出可展开（证据链）
                    </div>
                    <div className="space-y-2">
                      {current.card.steps.map((s, i) => (
                        <div key={i} className="overflow-hidden"
                          style={{ background: 'var(--term-bg)', border: '1px solid #223049', borderRadius: 10 }}>
                          <button onClick={() => setOpenStep(openStep === i ? -1 : i)}
                            className="w-full px-3.5 py-2.5 flex items-center gap-2.5 text-left transition-colors"
                            style={{ borderBottom: openStep === i ? '1px solid rgba(255,255,255,.07)' : 'none' }}
                            onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,.04)')}
                            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                            <span className="w-5 h-5 rounded-md flex items-center justify-center text-[10px] font-bold num shrink-0"
                              style={{ background: 'rgba(34,211,238,.15)', color: 'var(--accent)' }}>{i + 1}</span>
                            <span className="text-[12.5px]" style={{ color: '#dbe7f5' }}>{s.label}</span>
                            <span className="ml-auto text-[10px]" style={{ color: '#5a6a85' }}>{openStep === i ? '▾ 收起' : '▸ 展开输出'}</span>
                          </button>
                          {/* 始终渲染、仅切 class —— 才能触发 0fr→1fr 过渡 */}
                          <div className={`acc-body ${openStep === i ? 'open' : ''}`}><div>
                            <div className="px-3.5 pb-3.5 pt-2.5 border-t" style={{ borderColor: 'rgba(255,255,255,.07)', background: 'var(--term-bg)' }}>
                              {/* 命令行：绿色 $ 提示符 + 亮色命令，终端保持深色（与主题无关） */}
                              <div className="flex items-start gap-2 px-1">
                                <span className="mono text-[12px] shrink-0" style={{ color: '#34d399' }}>$</span>
                                <span className="mono text-[12px] break-all leading-relaxed" style={{ color: '#e6edf5' }}>{s.command}</span>
                              </div>
                              <pre className="text-[11.5px] mono whitespace-pre-wrap leading-relaxed mt-2 mb-0"
                                style={{ color: '#8fa3bd', margin: 0 }}>
                                {s.output}
                              </pre>
                            </div>
                          </div></div>
                        </div>
                      ))}
                      {!current.card.steps.length && <div className="text-[12px] text-[var(--text-faint)] px-1">（演示主机使用内置证据）</div>}
                    </div>
                  </div>
                </>
              )}

              {(() => {
                const p = proposalOf(current.id)
                if (!p) return current.status === 'open' ? null : (
                  <div className="mt-5 text-[12px] text-[var(--text-faint)]">此发现类型暂无预置修复提案</div>
                )
                return (
                  <div className="mt-5 rounded-xl p-4" style={{ background: 'var(--violet-bg)', border: '1px solid var(--violet-border)' }}>
                    <div className="flex items-center gap-2.5 flex-wrap">
                      <span className="pill" style={{ background: 'var(--violet-strong-bg)', color: 'var(--violet)' }}>修复提案 #{p.id}</span>
                      <span className="text-[13.5px] font-semibold text-[var(--text-hi)]">{p.title}</span>
                      <span className={`ml-auto text-[11.5px] font-medium ${
                        p.status === 'pending' ? 'text-[var(--text-faint)]' : p.status === 'approved' ? 'text-[var(--ok)]' : 'text-[var(--text-faint)]'}`}>
                        {p.status === 'pending' ? '⏳ 待审批' : p.status === 'approved' ? '✓ 已批准' : '✕ 已拒绝'}
                      </span>
                    </div>
                    <div className="text-[12px] text-[var(--text-mute)] mt-2.5 leading-relaxed">{p.rationale}</div>
                    <div className="relative group mt-2.5">
                      <div className="rounded-lg overflow-hidden" style={{ background: 'var(--term-bg)', border: '1px solid #223049' }}>
                        <div className="flex items-start gap-2 px-3 py-2.5 pr-16">
                          <span className="mono text-[12px] shrink-0" style={{ color: '#34d399' }}>$</span>
                          <span className="mono text-[11.5px] break-all leading-relaxed" style={{ color: '#e6edf5' }}>{p.command}</span>
                        </div>
                      </div>
                      <button onClick={() => copyCmd(p.command)}
                        className="absolute top-2 right-2 text-[10.5px] px-2 py-1 rounded-md opacity-0 group-hover:opacity-100 transition-opacity"
                        style={{ background: '#1b2740', border: '1px solid #2c3d5e', color: copied ? 'var(--ok)' : '#8fa3bd' }}>
                        {copied ? '✓ 已复制' : '复制'}
                      </button>
                    </div>
                    {p.status === 'pending' && (
                      <div className="flex gap-2.5 mt-3.5 flex-wrap">
                        <button disabled={busy} onClick={() => decide(p.id, 'approve')} className="btn btn-ok">✓ 批准执行</button>
                        <button disabled={busy} onClick={() => decide(p.id, 'reject')} className="btn btn-ghost">✕ 拒绝</button>
                        <span className="text-[10.5px] text-[var(--text-faint)] self-center">演示环境：批准仅记录决策，不实际执行命令</span>
                      </div>
                    )}
                    {p.status === 'approved' && (
                      <div className="mt-3.5 flex items-center gap-2.5 flex-wrap">
                        <button disabled={busy} onClick={() => execute(p.id)} className="btn btn-primary">
                          {busy ? <><span className="pulse-dot" style={{ background: 'var(--accent)' }} /> 执行中…</> : <><Ic name="play" size={13} /> 现在执行（白名单校验 + 审计）</>}
                        </button>
                        <span className="text-[10.5px] text-[var(--text-faint)]">
                          已批准 · 执行是独立动作，逐段白名单校验，mock 主机在模拟环境生效{!p.exec_enabled && ' · 当前执行开关未开启（设置页打开）'}
                        </span>
                      </div>
                    )}
                    {!!p.runs?.length && (
                      <div className="mt-3.5 space-y-2">
                        <div className="text-[10.5px] text-[var(--text-faint)] font-medium">执行审计 · 每次尝试（含被拦截/超时）均留痕</div>
                        {p.runs.map(r => (
                          <div key={r.id} className="rounded-lg p-2.5" style={{ background: 'var(--term-bg)', border: '1px solid #223049' }}>
                            <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                              <span className="pill text-[10px]" style={RUN_STYLE(r.status)}>{RUN_LABEL[r.status] ?? r.status}</span>
                              <span className="text-[10px] text-[var(--text-faint)] mono">{r.mode === 'mock' ? '模拟环境' : 'SSH'}</span>
                              {r.risk && <span className="text-[10px] text-[var(--text-faint)]">风险 {r.risk}</span>}
                              <span className="ml-auto text-[10px] text-[var(--text-faint)] num">{fmtTime(r.ts).slice(5, 16)} · {r.duration_ms}ms</span>
                            </div>
                            <pre className="text-[11px] mono whitespace-pre-wrap leading-relaxed" style={{ color: '#8fa3bd', margin: 0 }}>{r.output}</pre>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })()}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
