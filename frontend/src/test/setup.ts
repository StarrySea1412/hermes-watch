import '@testing-library/jest-dom/vitest'

// 面板前端依赖三类浏览器 API，jsdom 没有的打桩：
beforeAll(() => {
  // matchMedia：next-themes 主题查询
  if (!window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false, media: q, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    })) as any
  }
  // EventSource：api.subscribe 的 SSE
  if (!('EventSource' in window)) {
    ;(window as any).EventSource = class {
      onmessage: ((e: any) => void) | null = null
      close() {}
    }
  }
  // ResizeObserver：ECharts 相关组件
  if (!('ResizeObserver' in window)) {
    ;(window as any).ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
  }
  // Element.scrollTo：消息流自动滚动（jsdom 未实现）
  if (!Element.prototype.scrollTo) {
    ;(Element.prototype as any).scrollTo = () => {}
  }
  if (!window.requestAnimationFrame) {
    ;(window as any).requestAnimationFrame = (cb: FrameRequestCallback) => setTimeout(() => cb(Date.now()), 16)
  }
})

// fetch 桩：默认空数据应答，具体用例可覆盖 window.fetch
const okJson = (data: unknown) => new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } })
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (input: any) => {
    const url = typeof input === 'string' ? input : input.url
    if (url.includes('/api/auth/status')) return okJson({ enabled: false, authenticated: false })
    if (url.includes('/api/fleet')) return okJson({ hosts: [], generated_at: 0 })
    if (url.includes('/api/events')) return okJson([])
    if (url.includes('/api/settings')) return okJson({})
    if (url.includes('/api/notify/log')) return okJson([])
    if (url.includes('/status/token')) return okJson({ enabled: false, token: '' })
    return okJson({})
  }) as any)
})

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})
