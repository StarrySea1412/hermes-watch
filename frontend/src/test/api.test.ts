import { describe, expect, it, vi, afterEach } from 'vitest'
import { api, fmtTime, fmtNet } from '../api'

describe('api 客户端', () => {
  afterEach(() => { vi.restoreAllMocks() })

  it('成功应答返回 JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: 1 }), { status: 200 })))
    expect(await api<{ ok: number }>('/fleet')).toEqual({ ok: 1 })
  })

  it('401 → 抛「面板未登录」（由 api 层触发登录跳转）', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})  // jsdom 对 location 赋值会打 not-implemented
    vi.stubGlobal('fetch', vi.fn(async () => new Response('未登录', { status: 401 })))
    await expect(api('/fleet')).rejects.toThrow('面板未登录')
    errSpy.mockRestore()
  })

  it('非 2xx → 抛出响应体文本', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('主机不存在', { status: 404 })))
    await expect(api('/hosts/999', { method: 'DELETE' })).rejects.toThrow('主机不存在')
  })
})

describe('格式化工具', () => {
  it('fmtNet 按 B/KB/MB 分档（十进制换算）', () => {
    expect(fmtNet(500)).toBe('500 B/s')
    expect(fmtNet(2048)).toBe('2 KB/s')
    expect(fmtNet(3_000_000)).toBe('3.0 MB/s')
  })

  it('fmtTime 输出 zh-CN 本地格式（不含时分秒外的毫秒）', () => {
    const s = fmtTime(1788910666)
    expect(s).toMatch(/^\d{4}\/\d{1,2}\/\d{1,2} \d{2}:\d{2}:\d{2}$/)
  })
})
