// 主题体系：next-themes 管理 light/dark 切换 + localStorage 持久化 + 跟随系统。
// 调色板唯一来源是 index.css 的 CSS 变量（html.light / html.dark 两套 token）。
// ECharts / xterm 无法读 CSS 变量，这里用 getComputedStyle 在主题切换时动态取值。
import { useTheme } from 'next-themes'
import { useEffect, useState } from 'react'

/** 用 next-themes 的 resolvedTheme（light/dark），未挂载前返回 undefined */
export function useResolvedTheme(): string | undefined {
  const { resolvedTheme } = useTheme()
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])
  return mounted ? resolvedTheme : undefined
}

/** 从当前生效的 CSS 变量取色，供 ECharts / xterm 等画布类组件使用 */
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

/** 把 #rrggbb（或 var 取出的色值）转 rgba 并加透明度，供渐变/底色使用；
 *  ECharts 的 addColorStop 不认识 CSS 变量字符串，必须在这里解开成真实色值 */
export function alpha(color: string | undefined, a: number): string {
  if (!color) return `rgba(0,0,0,${a})`
  const c = color.trim()
  const m = /^#([0-9a-f]{6})$/i.exec(c)
  if (m) {
    const n = parseInt(m[1], 16)
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`
  }
  const r = /^rgba?\(([^)]+)\)$/i.exec(c)
  if (r) {
    const parts = r[1].split(',').map(s => parseFloat(s))
    return `rgba(${parts[0]},${parts[1]},${parts[2]},${a})`
  }
  return c
}

/** 一次性取全部图表/终端需要的颜色，主题切换时返回新值。
 *  不只依赖 next-themes 的状态传播（system 档的 resolvedTheme 更新时序不可靠），
 *  同时用 MutationObserver 盯 html 的 class 翻转——类一变立刻重取色，画布必然跟进。 */
export function useChartPalette(): Record<string, string> {
  const theme = useResolvedTheme()
  const [palette, setPalette] = useState<Record<string, string>>({})
  useEffect(() => {
    if (!theme) return
    const names = ['--bg-panel', '--bg-inset', '--border', '--border-strong', '--split-line',
      '--text-hi', '--text', '--text-mute', '--text-faint', '--ok', '--warn', '--crit',
      '--info', '--accent', '--violet', '--node-ok', '--node-warn', '--node-crit',
      '--m-cpu', '--m-mem', '--m-disk', '--m-net', '--term-bg', '--term-fg']
    const read = () => {
      const next: Record<string, string> = {}
      for (const n of names) next[n] = cssVar(n)
      setPalette(next)
    }
    read()
    const mo = new MutationObserver(read)
    mo.observe(document.documentElement, { attributeFilter: ['class'] })
    return () => mo.disconnect()
  }, [theme])
  return palette
}
