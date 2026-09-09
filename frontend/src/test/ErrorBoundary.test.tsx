import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ErrorBoundary } from '../ErrorBoundary'

function Boom() {
  throw new Error('【测试】页面炸了')
}

describe('ErrorBoundary 兜底', () => {
  it('子组件抛错 → 显示错误卡而非白屏，含错误信息与重试入口', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<ErrorBoundary><Boom /></ErrorBoundary>)
    expect(screen.getByText('页面渲染出错')).toBeInTheDocument()
    expect(screen.getByText(/【测试】页面炸了/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '回总览' })).toBeInTheDocument()
    spy.mockRestore()
  })

  it('正常子树不受边界影响，直接渲染', () => {
    render(<ErrorBoundary><div>正常内容</div></ErrorBoundary>)
    expect(screen.getByText('正常内容')).toBeInTheDocument()
  })
})
