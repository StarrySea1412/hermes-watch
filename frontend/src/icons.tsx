// 图标层：lucide-react 托管（Feather 系现代继任，曲线/一致性远优于早期手写 path）。
// 兼容约定：页面继续用 <Ic name="xxx" size={} sw={} />，name → lucide 组件映射，
// 旧调用点零改动。未知 name 回落 CircleHelp（开发期肉眼可见，不静默吞）。
import type { CSSProperties } from 'react'
import {
  LayoutGrid, Radar, Activity, SquareTerminal, Sparkles, Clock, FileText,
  Zap, SlidersHorizontal, Trash2, Sun, Moon, Monitor, Menu,
  TriangleAlert, CircleCheck, CircleX, Plus, Pencil, FlaskConical,
  RadioTower, Search, Download, Play, RefreshCw, Globe, Copy, Send,
  Bell, WifiOff, CircleHelp, ChevronDown, Server, ShieldCheck, Droplet,
  type LucideIcon,
} from 'lucide-react'

const ICONS: Record<string, LucideIcon> = {
  grid: LayoutGrid,
  radar: Radar,
  activity: Activity,
  terminal: SquareTerminal,
  sparkles: Sparkles,
  clock: Clock,
  file: FileText,
  zap: Zap,
  sliders: SlidersHorizontal,
  trash: Trash2,
  sun: Sun,
  moon: Moon,
  monitor: Monitor,
  menu: Menu,
  alert: TriangleAlert,
  'check-circle': CircleCheck,
  'x-circle': CircleX,
  plus: Plus,
  pen: Pencil,
  flask: FlaskConical,
  radio: RadioTower,
  search: Search,
  download: Download,
  play: Play,
  refresh: RefreshCw,
  globe: Globe,
  copy: Copy,
  send: Send,
  bell: Bell,
  'wifi-off': WifiOff,
  'chevron-down': ChevronDown,
  server: Server,
  shield: ShieldCheck,
  droplet: Droplet,
}

export type IconName = keyof typeof ICONS & string

export function Ic({ name, size = 16, sw = 2, style, className }: {
  name: IconName; size?: number; sw?: number; style?: CSSProperties; className?: string
}) {
  const Cmp = ICONS[name] ?? CircleHelp
  return (
    <Cmp size={size} strokeWidth={sw} color="currentColor"
      className={className} style={{ flexShrink: 0, ...style }} aria-hidden />
  )
}
