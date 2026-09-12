import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { navFor, RoleCtx, Layout } from '../ui'
import { UserAdd } from '../pages/UserAdd'

// RBAC 三角色：导航按角色过滤（navFor 纯函数 + Layout 实渲染）、
// 添加用户表单提供 operator 选项。写权限的后端语义在 backend/run_tests.py 覆盖。

describe('角色导航（navFor）', () => {
  it('admin 全量导航', () => {
    const items = navFor('admin').map(n => n.to)
    expect(items).toContain('/settings')
    expect(items).toContain('/enroll')
    expect(items).toContain('/terminal')
    expect(items).toHaveLength(10)
  })

  it('operator 隐藏主机录入与设置，保留终端/拨测', () => {
    const items = navFor('operator').map(n => n.to)
    expect(items).not.toContain('/settings')
    expect(items).not.toContain('/enroll')
    expect(items).toContain('/terminal')
    expect(items).toContain('/probes')
    expect(items).toHaveLength(8)
  })

  it('observer 连终端一起隐藏（只读不给 shell）', () => {
    const items = navFor('observer').map(n => n.to)
    expect(items).not.toContain('/terminal')
    expect(items).not.toContain('/settings')
    expect(items).not.toContain('/enroll')
    expect(items).toHaveLength(7)
  })
})

describe('侧边栏按 RoleCtx 实渲染', () => {
  it('observer 侧边栏无设置/终端/接入中心入口', () => {
    render(
      <RoleCtx.Provider value="observer">
        <MemoryRouter initialEntries={['/']}>
          <Layout />
        </MemoryRouter>
      </RoleCtx.Provider>,
    )
    expect(screen.queryAllByText('设置')).toHaveLength(0)
    expect(screen.queryAllByText('远程终端')).toHaveLength(0)
    expect(screen.queryAllByText('接入中心')).toHaveLength(0)
    expect(screen.getAllByText('Fleet 总览').length).toBeGreaterThan(0)
    expect(screen.getAllByText('拨测').length).toBeGreaterThan(0)
  })

  it('admin 侧边栏保留全部入口', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <Layout />
      </MemoryRouter>,
    )
    // 侧边栏宽窄屏各渲染一份，同一入口文案会出现多次，用计数断言
    expect(screen.getAllByText('设置').length).toBeGreaterThan(0)
    expect(screen.getAllByText('远程终端').length).toBeGreaterThan(0)
    expect(screen.getAllByText('接入中心').length).toBeGreaterThan(0)
  })
})

describe('添加用户表单（UserAdd）', () => {
  it('提供 observer/operator/admin 三个角色选项', () => {
    render(<UserAdd onAdded={() => {}} />)
    const sel = screen.getByDisplayValue('observer（只读）') as HTMLSelectElement
    const opts = [...sel.options].map(o => o.value)
    expect(opts).toEqual(['observer', 'operator', 'admin'])
    expect(screen.getByText('operator（值班）')).toBeInTheDocument()
  })
})
