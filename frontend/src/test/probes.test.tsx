import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import Probes from '../pages/Probes'

// 拨测页组件测试：核心渲染路径（卡片/状态 pill/徽章入口/PUSH 上报地址）不再零覆盖。
// fetch 桩沿用 setup.ts 的按路由应答模式，这里覆盖为本页所需数据。

const now = Date.now() / 1000
const base = {
  fail_streak: 0, succ_streak: 0, fail_threshold: 3, success_threshold: 2, timeout_s: 10,
  keyword: '', max_latency_ms: 0, cert_days_min: 0, interval_s: 0,
  last_ts: now, last_latency: null, last_error: '', last_flip_ts: now, created_at: now,
}
const PROBES = [
  { ...base, id: 1, name: 'cache-1', kind: 'url', target: 'https://cache.internal:8080', up: 1, last_latency: 12.4 },
  { ...base, id: 2, name: 'db-1', kind: 'tcp', target: 'db.internal:5432', up: 0, last_error: 'connect timeout' },
  { ...base, id: 3, name: 'cron 备份', kind: 'push', target: 'push', push_token: 'hw_ptok', push_grace_s: 600, up: 1 },
]

describe('拨测页（Probes）', () => {
  it('渲染全部目标卡与状态 pill，PUSH 卡展示上报地址', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: any) => {
      const url = typeof input === 'string' ? input : input.url
      if (url.endsWith('/api/probes')) return new Response(JSON.stringify(PROBES), { status: 200 })
      if (url.includes('/api/probes/') && url.includes('/log')) return new Response(JSON.stringify([]), { status: 200 })
      if (url.includes('/api/settings')) return new Response(JSON.stringify({ probe_interval: '30' }), { status: 200 })
      if (url.includes('/status/token')) return new Response(JSON.stringify({ enabled: true, token: 'hw_pub' }), { status: 200 })
      return new Response(JSON.stringify({}), { status: 200 })
    }) as any)
    render(<Probes />)
    expect(await screen.findByText('cache-1')).toBeInTheDocument()
    expect(screen.getByText('db-1')).toBeInTheDocument()
    expect(screen.getByText('cron 备份')).toBeInTheDocument()
    // 状态 pill：两在线一下线
    expect(screen.getAllByText('在线').length).toBe(2)
    expect(screen.getByText('下线')).toBeInTheDocument()
    // kind 徽标：表单选择器 + 目标卡各一份
    expect(screen.getAllByText('URL').length).toBe(2)
    expect(screen.getAllByText('TCP').length).toBe(2)
    expect(screen.getAllByText('PUSH').length).toBe(2)
    // PUSH 卡展示上报地址（含 token），带一键复制
    expect(screen.getByText(/\/api\/push\/hw_ptok/)).toBeInTheDocument()
    // 状态页开启 → 徽章入口：每卡一枚 + 页头总览一枚
    expect(screen.getAllByText('徽章').length).toBe(3)
    expect(screen.getByText('总览徽章')).toBeInTheDocument()
    // 下线目标展示最近错误
    expect(screen.getByText(/connect timeout/)).toBeInTheDocument()
  })

  it('状态页未开启 → 徽章入口隐藏', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: any) => {
      const url = typeof input === 'string' ? input : input.url
      if (url.endsWith('/api/probes')) return new Response(JSON.stringify(PROBES), { status: 200 })
      if (url.includes('/api/probes/') && url.includes('/log')) return new Response(JSON.stringify([]), { status: 200 })
      if (url.includes('/api/settings')) return new Response(JSON.stringify({}), { status: 200 })
      if (url.includes('/status/token')) return new Response(JSON.stringify({ enabled: false, token: '' }), { status: 200 })
      return new Response(JSON.stringify({}), { status: 200 })
    }) as any)
    render(<Probes />)
    expect(await screen.findByText('cache-1')).toBeInTheDocument()
    expect(screen.queryByText('徽章')).not.toBeInTheDocument()
    expect(screen.queryByText('总览徽章')).not.toBeInTheDocument()
  })
})
