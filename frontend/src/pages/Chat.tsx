import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { fmtTime } from '../api'
import { Ic } from '../icons'
import { PageHead } from '../ui'
import { useT } from '../i18n'

type Msg = { role: 'user' | 'bot'; text: string; source?: string; ts: number; streaming?: boolean }

const QUICK_KEYS = ['chat.quick.0', 'chat.quick.1', 'chat.quick.2', 'chat.quick.3']

const SOURCE_LABEL: Record<string, string> = {
  llm: 'chat.source.llm',
  fallback: 'chat.source.fallback',
}
const SOURCE_PILL: Record<string, React.CSSProperties> = {
  llm: { background: 'var(--violet-bg)', color: 'var(--violet)' },
  fallback: { background: 'var(--warn-bg)', color: 'var(--warn)' },
}

// 多轮会话持久化：刷新不丢上下文
const CHAT_KEY = 'hw-chat-history'
const loadHistory = (): Msg[] => {
  try {
    const arr = JSON.parse(localStorage.getItem(CHAT_KEY) || '[]')
    return Array.isArray(arr) ? arr.filter((m: any) => m && (m.role === 'user' || m.role === 'bot') && typeof m.text === 'string').slice(-60) : []
  } catch { return [] }
}

export default function Chat() {
  const { t } = useT()
  const [msgs, setMsgs] = useState<Msg[]>(loadHistory)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState<number | null>(null)
  const boxRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    boxRef.current?.scrollTo({ top: boxRef.current.scrollHeight, behavior: 'smooth' })
    try { localStorage.setItem(CHAT_KEY, JSON.stringify(msgs.slice(-60))) } catch { /* noop */ }
  }, [msgs, busy])

  const grow = (el: HTMLTextAreaElement) => {
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 132) + 'px'
  }
  const onChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    grow(e.target)
  }

  const send = async (q?: string) => {
    const question = (q ?? input).trim()
    if (!question || busy) return
    // 多轮上下文：把已有对话（最近 10 条）一并提交给后端
    const history = msgs.slice(-10).map(m => ({
      role: m.role === 'user' ? 'user' : 'assistant', content: m.text,
    }))
    setMsgs(m => [...m, { role: 'user', text: question, ts: Date.now() / 1000 }])
    setInput('')
    if (taRef.current) taRef.current.style.height = 'auto'
    setBusy(true)
    // 先落一个流式占位消息，token 到达即渐进填充（SSE /api/chat/stream）
    setMsgs(m => [...m, { role: 'bot', text: '', ts: Date.now() / 1000, streaming: true }])
    try {
      const r = await fetch('/api/chat/stream', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, history }),
      })
      if (r.status === 401) { location.href = '/login'; return }
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`)
      const reader = r.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      let done = false
      while (!done) {
        const { value, done: rdDone } = await reader.read()
        if (rdDone) break
        buf += dec.decode(value, { stream: true })
        let idx
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, idx).trim()
          buf = buf.slice(idx + 2)
          if (!frame.startsWith('data:')) continue
          try {
            const evt = JSON.parse(frame.slice(5).trim())
            if (evt.type === 'meta') {
              setMsgs(m => m.map((msg, i) => i === m.length - 1 && msg.role === 'bot' ? { ...msg, source: evt.source } : msg))
            } else if (evt.type === 'delta') {
              setMsgs(m => m.map((msg, i) => i === m.length - 1 && msg.role === 'bot' ? { ...msg, text: msg.text + evt.text } : msg))
            } else if (evt.type === 'done') done = true
          } catch { /* 忽略残帧 */ }
        }
      }
    } catch (e: any) {
      setMsgs(m => m.map((msg, i) => i === m.length - 1 && msg.role === 'bot' && msg.streaming
        ? { ...msg, text: msg.text ? `${msg.text}\n\n${t('chat.err.disconnected', { n: e.message })}` : t('chat.err.requestFailed', { n: e.message }) }
        : msg))
    } finally {
      setBusy(false)
      setMsgs(m => m.map((msg, i) => i === m.length - 1 && msg.role === 'bot' ? { ...msg, streaming: false } : msg))
    }
  }

  const copyMsg = async (i: number, text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(i); setTimeout(() => setCopied(null), 1500)
    } catch { /* noop */ }
  }

  return (
    <div className="fade-in flex flex-col h-[calc(100vh-120px)]">
      <PageHead title={t('nav.chat')} sub={t('chat.headSub')}>
        {!!msgs.length && (
          <button className="btn btn-ghost" onClick={() => { setMsgs([]); try { localStorage.removeItem(CHAT_KEY) } catch { /* noop */ } }}>
            <Ic name="trash" size={13} /> {t('chat.clear')}
          </button>
        )}
      </PageHead>

      {/* 消息流：居中限宽列（Codex 式阅读节奏） */}
      <div className="flex-1 overflow-y-auto pr-1" ref={boxRef}>
        <div className="max-w-3xl mx-auto space-y-6 py-2">
          {!msgs.length && (
            <div className="card p-8 text-center mt-6">
              <div className="flex justify-center mb-4">
                <div className="w-12 h-12 rounded-2xl flex items-center justify-center"
                  style={{ background: 'linear-gradient(135deg, var(--accent-dim), var(--violet-bg))', border: '1px solid var(--accent-border)', color: 'var(--accent)' }}>
                  <Ic name="sparkles" size={22} sw={1.6} />
                </div>
              </div>
              <div className="text-[15px] font-semibold text-[var(--text-hi)] mb-2">{t('chat.welcomeTitle')}</div>
              <p className="text-[12.5px] text-[var(--text-faint)] leading-relaxed mb-5">
                {t('chat.welcomeLine1')}<br />
                {t('chat.welcomeLine2')}
              </p>
              <div className="grid grid-cols-2 gap-2.5">
                {QUICK_KEYS.map(k => {
                  const q = t(k)
                  return (
                    <button key={k} onClick={() => send(q)} className="inset px-3.5 py-2.5 text-[12.5px] text-[var(--text-mute)]
                      hover:text-[var(--accent)] hover:border-[var(--accent-border)] transition-colors text-left flex items-center gap-2">
                      <Ic name="play" size={11} style={{ opacity: 0.6 }} /> {q}
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          {msgs.map((m, i) => m.role === 'user' ? (
            // 用户消息：右侧强调气泡
            <div key={i} className="flex justify-end fade-in">
              <div className="max-w-[80%] rounded-2xl px-4 py-2.5 fade-in"
                style={{ background: 'var(--accent-dim)', border: '1px solid var(--accent-border)', borderRadius: '18px 18px 5px 18px' }}>
                <div className="text-[13.5px] leading-relaxed whitespace-pre-wrap" style={{ color: 'var(--accent)' }}>{m.text}</div>
              </div>
            </div>
          ) : (
            // 助手消息：头像列 + 名称行 + 内容卡 + 悬停复制
            <div key={i} className="flex gap-3 fade-in group">
              <div className="w-8 h-8 rounded-full shrink-0 flex items-center justify-center mt-0.5"
                style={{ background: 'var(--accent-dim)', border: '1px solid var(--accent-border)', color: 'var(--accent)' }}>
                <Ic name="sparkles" size={14} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 mb-1.5">
                  <span className="text-[12.5px] font-semibold text-[var(--text-hi)]">Hermes</span>
                  <span className="pill text-[10px]" style={SOURCE_PILL[m.source ?? ''] ?? { background: 'var(--neutral-bg)', color: 'var(--text-mute)' }}>
                    {SOURCE_LABEL[m.source ?? ''] ? t(SOURCE_LABEL[m.source ?? '']) : t('chat.source.local')}
                  </span>
                  <span className="ml-auto text-[10.5px] text-[var(--text-faint)] num opacity-0 group-hover:opacity-100 transition-opacity">{fmtTime(m.ts)}</span>
                </div>
                <div className="card px-4 py-3 relative" style={{ borderRadius: '4px 18px 18px 18px' }}>
                  {/* LLM 输出按 Markdown 渲染（react-markdown 默认不渲染裸 HTML，安全） */}
                  <div className="md-body text-[13.5px] leading-relaxed text-[var(--text)]">
                    <ReactMarkdown>{m.text}</ReactMarkdown>
                    {m.streaming && <span className="stream-cursor">▍</span>}
                  </div>
                  <button title={t('chat.copyAnswer')} onClick={() => copyMsg(i, m.text)}
                    className="absolute top-2 right-2 w-7 h-7 rounded-lg flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                    style={{ background: 'var(--neutral-bg)', border: '1px solid var(--border)', color: copied === i ? 'var(--ok)' : 'var(--text-mute)' }}>
                    {copied === i ? <Ic name="check-circle" size={12} /> : <Ic name="copy" size={12} />}
                  </button>
                </div>
              </div>
            </div>
          ))}

          {busy && msgs.at(-1)?.role === 'bot' && !msgs.at(-1)?.text && (
            <div className="flex gap-3 fade-in">
              <div className="w-8 h-8 rounded-full shrink-0 flex items-center justify-center mt-0.5"
                style={{ background: 'var(--accent-dim)', border: '1px solid var(--accent-border)', color: 'var(--accent)' }}>
                <Ic name="sparkles" size={14} />
              </div>
              <div className="card px-4 py-3 flex items-center gap-2.5" style={{ borderRadius: '4px 18px 18px 18px' }}>
                <span className="flex items-center gap-1">
                  <span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" />
                </span>
                <span className="text-[12.5px] text-[var(--text-faint)]">{t('chat.thinking')}</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 输入区：Codex 式圆角输入卡 + 自适应高度 + 内嵌发送按钮 */}
      <div className="max-w-3xl mx-auto w-full mt-3">
        <div className="card p-2 flex items-end gap-2" style={{ borderRadius: 18 }}>
          <textarea ref={taRef} rows={1}
            className="input mono flex-1"
            style={{ background: 'transparent', border: 'none', boxShadow: 'none', resize: 'none',
                     minHeight: 38, maxHeight: 132, padding: '8px 10px' }}
            placeholder={t('chat.inputPh')}
            value={input} onChange={onChange}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} />
          <button className="btn btn-primary shrink-0" style={{ borderRadius: 12, padding: '9px 14px' }}
            disabled={busy || !input.trim()} onClick={() => send()}
            title={t('chat.sendTip')}>
            {busy ? <span className="pulse-dot" style={{ background: 'var(--accent)' }} /> : <Ic name="send" size={14} />}
          </button>
        </div>
        <div className="text-[10.5px] text-[var(--text-faint)] text-center mt-2">
          {t('chat.footer')}
        </div>
      </div>
    </div>
  )
}
