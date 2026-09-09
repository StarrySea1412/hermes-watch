// 轻量 i18n：翻译表 + React hook，不引第三方依赖。
// - 默认 zh；语言存 localStorage（hw_lang），后端 settings 表同步一份（hw_lang），
//   切换由设置页的语言按钮触发（useT 内只读）。
// - t(key)：key 形如 'nav.fleet'；en 表未命中的 key 回落 zh。
// - 骨架覆盖层：导航、页头、状态词、通用按钮。页面长文案逐步迁移，
//   未迁移的 key 在 zh 表里就是原文，en 缺失时自动回落中文（不会出现裸 key）。
import { useEffect, useState } from 'react'
import { api } from './api'

export type Lang = 'zh' | 'en'

const ZH: Record<string, string> = {
  // 导航
  'nav.fleet': 'Fleet 总览',
  'nav.topology': '拓扑视图',
  'nav.diagnostics': '诊断中心',
  'nav.terminal': '远程终端',
  'nav.chat': 'AI 对话',
  'nav.timeline': '巡检时间线',
  'nav.reports': '报告中心',
  'nav.enroll': '接入中心',
  'nav.settings': '设置',
  // 侧栏状态
  'app.loopRunning': '巡检循环运行中',
  'app.tagline': 'AI 服务器巡检 · 全本地',
  // 通用状态词（发现/主机）
  'status.ok': '健康',
  'status.warn': '警告',
  'status.crit': '严重',
  'status.offline': '离线',
  'status.resolved': '已解决',
  'status.analyzed': '已诊断',
  'status.open': '待诊断',
  'sev.crit': '严重',
  'sev.warn': '警告',
  'sev.info': '提示',
  // 通用按钮
  'btn.save': '保存',
  'btn.delete': '删除',
  'btn.retry': '重试',
  'btn.refresh': '刷新',
  'btn.generate': '生成健康报告',
  'btn.send': '发送',
  'btn.confirm': '确认',
  'btn.cancel': '取消',
  'btn.close': '关闭',
  'btn.copy': '复制',
  // Fleet 页
  'fleet.title': 'Fleet 总览',
  'fleet.avgScore': 'Fleet 整体健康分',
  'fleet.healthyHosts': '健康主机',
  'fleet.openFindings': '待处理发现',
  'fleet.inspectStatus': '巡检状态',
  'fleet.running': '运行中',
  'fleet.hostsOffline': '{n} 台离线',
  'fleet.hosts': '{n} 台主机',
  'fleet.onlineRate': '24h 在线 {p}%',
  // 主题/语言
  'lang.title': '语言：中文（点击切换 English）',
}

// en：骨架先行，未列出的 key 自动回落 zh
const EN: Record<string, string> = {
  'nav.fleet': 'Fleet Overview',
  'nav.topology': 'Topology',
  'nav.diagnostics': 'Diagnostics',
  'nav.terminal': 'Terminal',
  'nav.chat': 'AI Chat',
  'nav.timeline': 'Timeline',
  'nav.reports': 'Reports',
  'nav.enroll': 'Enroll',
  'nav.settings': 'Settings',
  'app.loopRunning': 'Inspection loop running',
  'app.tagline': 'AI server inspection · fully local',
  'status.ok': 'Healthy',
  'status.warn': 'Warning',
  'status.crit': 'Critical',
  'status.offline': 'Offline',
  'status.resolved': 'Resolved',
  'status.analyzed': 'Analyzed',
  'status.open': 'Open',
  'sev.crit': 'Critical',
  'sev.warn': 'Warning',
  'sev.info': 'Info',
  'btn.save': 'Save',
  'btn.delete': 'Delete',
  'btn.retry': 'Retry',
  'btn.refresh': 'Refresh',
  'btn.generate': 'Generate Report',
  'btn.send': 'Send',
  'btn.confirm': 'Confirm',
  'btn.cancel': 'Cancel',
  'btn.close': 'Close',
  'btn.copy': 'Copy',
  'fleet.title': 'Fleet Overview',
  'fleet.avgScore': 'Fleet Health Score',
  'fleet.healthyHosts': 'Healthy Hosts',
  'fleet.openFindings': 'Open Findings',
  'fleet.inspectStatus': 'Inspection',
  'fleet.running': 'Running',
  'fleet.hostsOffline': '{n} offline',
  'fleet.hosts': '{n} hosts',
  'fleet.onlineRate': '24h uptime {p}%',
  'lang.title': 'Language: English (click for 中文)',
}

const TABLES: Record<Lang, Record<string, string>> = { zh: ZH, en: EN }

export function getLang(): Lang {
  const v = localStorage.getItem('hw_lang')
  return v === 'en' ? 'en' : 'zh'
}

export function setLang(lang: Lang) {
  localStorage.setItem('hw_lang', lang)
  document.documentElement.lang = lang === 'en' ? 'en' : 'zh-CN'
  window.dispatchEvent(new CustomEvent('hw-lang', { detail: lang }))
}

export function useT() {
  const [lang, setLangState] = useState<Lang>(getLang)
  useEffect(() => {
    const on = (e: Event) => setLangState((e as CustomEvent).detail as Lang)
    window.addEventListener('hw-lang', on)
    return () => window.removeEventListener('hw-lang', on)
  }, [])
  // 首次把面板语言偏好同步到后端（供报告等未来多语言化的读取基准），失败静默
  useEffect(() => {
    if (getLang() === lang) api('/settings', { method: 'POST', body: JSON.stringify({ hw_lang: lang }) }).catch(() => { /* noop */ })
  }, [lang])
  const t = (key: string, vars?: Record<string, string | number>) => {
    const raw = TABLES[lang][key] ?? ZH[key] ?? key
    if (!vars) return raw
    return raw.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? `{${k}}`))
  }
  return { t, lang }
}
