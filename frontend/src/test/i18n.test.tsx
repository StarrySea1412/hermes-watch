import { describe, expect, it } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { getLang, setLang, useT } from '../i18n'

describe('i18n 翻译层', () => {
  it('默认语言 zh，t() 返回中文', () => {
    localStorage.removeItem('hw_lang')
    const { result } = renderHook(() => useT())
    expect(getLang()).toBe('zh')
    expect(result.current.t('nav.fleet')).toBe('Fleet 总览')
    expect(result.current.t('status.offline')).toBe('离线')
  })

  it('en 命中英文表；未知 key 原样返回（开发期可见，不静默吞）', () => {
    localStorage.setItem('hw_lang', 'en')
    const { result } = renderHook(() => useT())
    expect(result.current.t('nav.settings')).toBe('Settings')
    expect(result.current.t('fleet.title')).toBe('Fleet Overview')
    expect(result.current.t('no.such.key')).toBe('no.such.key')
    localStorage.removeItem('hw_lang')
  })

  it('t() 支持 {n} 插值', () => {
    localStorage.setItem('hw_lang', 'en')
    const { result } = renderHook(() => useT())
    expect(result.current.t('fleet.hosts', { n: 5 })).toBe('5 hosts')
    expect(result.current.t('fleet.hostsOffline', { n: 2 })).toBe('2 offline')
    localStorage.removeItem('hw_lang')
  })

  it('setLang 派发事件，useT 响应切换', () => {
    const { result } = renderHook(() => useT())
    expect(result.current.t('nav.chat')).toBe('AI 对话')
    act(() => setLang('en'))
    expect(result.current.t('nav.chat')).toBe('AI Chat')
    act(() => setLang('zh'))
    expect(result.current.t('nav.chat')).toBe('AI 对话')
  })

  it('setLang 更新 <html lang>', () => {
    setLang('en')
    expect(document.documentElement.lang).toBe('en')
    setLang('zh')
    expect(document.documentElement.lang).toBe('zh-CN')
  })
})
