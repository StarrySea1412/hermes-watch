// ECharts 组件集：独立模块让 echarts 只进异步 chunk（配合路由懒加载）。
// 颜色一律从 CSS 变量取（useChartPalette），theme 变化 → palette 变化 → chart 重建
import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'
import { alpha, useChartPalette } from './theme'

export function useEChart(render: (c: echarts.ECharts) => void, deps: any[]) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current)
    render(chart)
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); chart.dispose() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return ref
}

export function HealthRing({ score, size = 84, thickness = 8 }: { score: number; size?: number; thickness?: number }) {
  const P = useChartPalette()
  const ready = Object.keys(P).length > 0
  const color = score >= 90 ? P['--ok'] : score >= 70 ? P['--warn'] : P['--crit']
  const ref = useEChart(c => c.setOption({
    animation: true, animationDuration: 700,
    series: [{
      type: 'gauge', startAngle: 90, endAngle: -270, radius: '92%',
      pointer: { show: false },
      progress: { show: true, width: thickness, roundCap: true,
                  itemStyle: { color, shadowBlur: 10, shadowColor: alpha(color, 0.45), shadowOffsetY: 1 } },
      axisLine: { lineStyle: { width: thickness, color: [[1, P['--border'] ?? '#1c2941']] }, roundCap: true },
      axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
      detail: { fontSize: size / 3.4, color, offsetCenter: [0, 0], fontWeight: 700,
                fontFamily: 'Segoe UI', formatter: '{value}' },
      data: [{ value: score }],
    }],
  }), [score, ready, P['--ok']])
  return <div ref={ref} style={{ width: size, height: size }} />
}

export function Spark({ data, metric, color, height = 36 }: {
  data: { [k: string]: number }[]; metric: string; color: string; height?: number
}) {
  const ref = useEChart(c => c.setOption({
    animation: false, grid: { left: 0, right: 0, top: 3, bottom: 0 },
    xAxis: { type: 'category', show: false, data: data.map((_, i) => i) },
    yAxis: { type: 'value', show: false, min: 0, max: 100 },
    series: [{
      type: 'line', data: data.map(d => d[metric]), showSymbol: false, smooth: 0.4,
      lineStyle: { color, width: 1.6 },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: alpha(color, 0.33) }, { offset: 1, color: alpha(color, 0) }]) },
    }],
  }), [data, color])
  return <div ref={ref} style={{ width: '100%', height }} />
}

/** 通用时间序列折线图：异常发现时间点叠加标记线 */
export function LineChart({ data, series, marks = [], height = 260, yUnit = '%' }: {
  data: number[][]  // [ts, ...values]
  series: { name: string; key: number; color: string }[]
  marks?: { ts: number; label: string }[]
  height?: number
  yUnit?: string
}) {
  const P = useChartPalette()
  const ready = Object.keys(P).length > 0
  const ref = useEChart(c => c.setOption({
    animation: false, backgroundColor: 'transparent',
    grid: { left: 48, right: 16, top: 34, bottom: 28 },
    legend: { textStyle: { color: P['--text-mute'], fontSize: 11 }, top: 2, icon: 'roundRect', itemWidth: 14, itemHeight: 4 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: P['--bg-panel'], borderColor: P['--border-strong'], borderWidth: 1,
      textStyle: { color: P['--text'], fontSize: 12 },
      extraCssText: 'backdrop-filter: blur(8px); box-shadow: 0 8px 24px rgba(0,0,0,.35); border-radius: 10px;',
      axisPointer: { type: 'cross', crossStyle: { color: P['--border-strong'] } },
    },
    xAxis: { type: 'time',
      axisLabel: { color: P['--axis-label'], fontSize: 10.5, formatter: (v: number) =>
        new Date(v).toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' }) },
      axisLine: { lineStyle: { color: P['--axis-line'] } }, splitLine: { show: false } },
    yAxis: { type: 'value', axisLabel: { color: P['--axis-label'], fontSize: 10.5, formatter: `{value}${yUnit}` },
      splitLine: { lineStyle: { color: P['--split-line'] } } },
    series: series.map(s => ({
      name: s.name, type: 'line', showSymbol: false, smooth: 0.35, sampling: 'lttb',
      data: data.map(d => [d[0], d[s.key]]),
      lineStyle: { color: s.color, width: 1.8 },
      itemStyle: { color: s.color },
      areaStyle: series.length === 1
        ? { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: alpha(s.color, 0.19) }, { offset: 1, color: alpha(s.color, 0) }]) }
        : undefined,
      markLine: marks.length ? {
        silent: true, symbol: 'none',
        lineStyle: { color: P['--crit'], type: 'dashed', width: 1, opacity: 0.7 },
        label: { show: false },
        data: marks.map(m => ({ xAxis: m.ts * 1000 })),
      } : undefined,
    })),
  }), [JSON.stringify(data), JSON.stringify(series), JSON.stringify(marks), ready])
  return <div ref={ref} style={{ width: '100%', height }} />
}
