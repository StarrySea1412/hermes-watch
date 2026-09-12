import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import Chat from '../pages/Chat'

// AI 对话页：思考链 / 工具调用折叠块（默认收起）+ 流式正文渲染。
// SSE 帧用 ReadableStream 桩注入，模拟后端 chat_stream 新协议。

function sseResponse(frames: string[]) {
  const enc = new TextEncoder()
  const stream = new ReadableStream({
    start(c) {
      for (const f of frames) c.enqueue(enc.encode(f))
      c.close()
    },
  })
  return new Response(stream, { status: 200 })
}

const SSE = [
  `data: ${JSON.stringify({ type: 'meta', source: 'llm' })}\n\n`,
  `data: ${JSON.stringify({ type: 'think', text: '先看整体健康度，' })}\n\n`,
  `data: ${JSON.stringify({ type: 'think', text: '再查一条工具确认' })}\n\n`,
  `data: ${JSON.stringify({ type: 'tool', name: 'fleet_status', args: '{}',
    preview: 'Fleet 状态:\n- [1] web-1: 健康 92' })}\n\n`,
  `data: ${JSON.stringify({ type: 'delta', text: 'Fleet 整体健康，4 台主机全部在线。' })}\n\n`,
  'data: [DONE]\n\n',
]

async function ask() {
  vi.stubGlobal('fetch', vi.fn(async () => sseResponse(SSE)))
  render(<Chat />)
  const ta = document.querySelector('textarea') as HTMLTextAreaElement
  fireEvent.change(ta, { target: { value: '整体怎么样？' } })
  fireEvent.keyDown(ta, { key: 'Enter' })
  // 正文到达 = 全部 SSE 帧已处理
  expect(await screen.findByText('Fleet 整体健康，4 台主机全部在线。')).toBeInTheDocument()
}

describe('AI 对话折叠块（思考链 / 工具调用）', () => {
  it('默认折叠：标题可见，内容不可见；点开展开', async () => {
    await ask()
    expect(screen.getByText('思考链')).toBeInTheDocument()
    expect(screen.getByText('工具调用')).toBeInTheDocument()
    // 折叠中：思考与工具结果正文都不可见
    expect(screen.queryByText(/先看整体健康度/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Fleet 状态:/)).not.toBeInTheDocument()
    // 展开工具调用 → 命令与结果预览可见
    fireEvent.click(screen.getByText('工具调用'))
    expect(screen.getByText(/\$ fleet_status\(\{\}\)/)).toBeInTheDocument()
    expect(screen.getByText(/web-1: 健康 92/)).toBeInTheDocument()
    // 展开思考链
    fireEvent.click(screen.getByText('思考链'))
    expect(screen.getByText(/先看整体健康度，再查一条工具确认/)).toBeInTheDocument()
  })

  it('来源徽章为 LLM，无折叠数据时不渲染折叠块', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse([
      `data: ${JSON.stringify({ type: 'meta', source: 'llm' })}\n\n`,
      `data: ${JSON.stringify({ type: 'delta', text: '直接回答。' })}\n\n`,
      'data: [DONE]\n\n',
    ])))
    render(<Chat />)
    const ta = document.querySelector('textarea') as HTMLTextAreaElement
    fireEvent.change(ta, { target: { value: '你好' } })
    fireEvent.keyDown(ta, { key: 'Enter' })
    expect(await screen.findByText('直接回答。')).toBeInTheDocument()
    expect(screen.queryByText('思考链')).not.toBeInTheDocument()
    expect(screen.queryByText('工具调用')).not.toBeInTheDocument()
  })
})
