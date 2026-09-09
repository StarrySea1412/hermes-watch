import { Component, type ReactNode } from 'react'
import { useT } from './i18n'

/** 页面级错误边界：任一页面渲染抛错时显示可重试的兜底页，而不是整屏白屏。 */
export class ErrorBoundary extends Component<{ children: ReactNode }, { err: Error | null }> {
  state = { err: null as Error | null }

  static getDerivedStateFromError(err: Error) {
    return { err }
  }

  render() {
    if (!this.state.err) return this.props.children
    return (
      <ErrorFallback message={this.state.err.message || String(this.state.err)}
        onRetry={() => this.setState({ err: null })} />
    )
  }
}

/* 类组件不能用 hook：兜底 UI 拆成函数组件以接入 useT */
function ErrorFallback({ message, onRetry }: { message: string; onRetry: () => void }) {
  const { t } = useT()
  return (
    <div className="h-full flex items-center justify-center p-6">
      <div className="card p-8 max-w-md text-center">
        <div className="flex justify-center mb-3 text-[var(--crit)]">
          <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10.3 3.9L1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
            <path d="M12 9v4.5M12 17h.01" />
          </svg>
        </div>
        <div className="text-[14px] font-semibold text-[var(--text-hi)] mb-1.5">{t('ui.error.title')}</div>
        <div className="text-[11.5px] text-[var(--text-faint)] mono mb-4 break-all">
          {message}
        </div>
        <div className="flex justify-center gap-2.5">
          <button className="btn btn-primary" onClick={onRetry}>{t('btn.retry')}</button>
          <button className="btn btn-ghost" onClick={() => location.href = '/'}>{t('ui.error.backHome')}</button>
        </div>
      </div>
    </div>
  )
}
