import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { GlassToggle, PageHead } from '../ui'

describe('液态玻璃开关（GlassToggle）', () => {
  it('默认开：点击挂 glass-off 类并持久化，再点恢复', () => {
    localStorage.setItem('hw_glass', 'on')
    document.documentElement.classList.remove('glass-off')
    render(<GlassToggle />)
    expect(screen.getByTitle('液态玻璃：开（点击关闭）')).toBeInTheDocument()
    fireEvent.click(screen.getByTitle('液态玻璃：开（点击关闭）'))
    expect(document.documentElement.classList.contains('glass-off')).toBe(true)
    expect(localStorage.getItem('hw_glass')).toBe('off')
    expect(screen.getByTitle('液态玻璃：关（点击开启）')).toBeInTheDocument()
    fireEvent.click(screen.getByTitle('液态玻璃：关（点击开启）'))
    expect(document.documentElement.classList.contains('glass-off')).toBe(false)
    expect(localStorage.getItem('hw_glass')).toBe('on')
  })

  it('初始即关（防FOUC 已挂类）→ 按钮呈关闭态，点击可恢复', () => {
    document.documentElement.classList.add('glass-off')
    localStorage.setItem('hw_glass', 'off')
    try {
      render(<GlassToggle />)
      expect(screen.getByTitle('液态玻璃：关（点击开启）')).toBeInTheDocument()
      fireEvent.click(screen.getByTitle('液态玻璃：关（点击开启）'))
      expect(document.documentElement.classList.contains('glass-off')).toBe(false)
      expect(localStorage.getItem('hw_glass')).toBe('on')
    } finally {
      document.documentElement.classList.remove('glass-off')
    }
  })

  it('PageHead 渲染三件套（玻璃 / 语言 / 主题）', () => {
    render(<PageHead title="测试页" />)
    expect(screen.getByTitle(/液态玻璃：/)).toBeInTheDocument()
    expect(screen.getByText('EN')).toBeInTheDocument()
    expect(screen.getByTitle(/主题/)).toBeInTheDocument()
  })
})
