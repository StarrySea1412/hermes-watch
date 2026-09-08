// 极简线性 SVG 图标集（feather 风）：24 viewBox / currentColor / 圆角线帽。
// 用法：<Ic name="clock" size={16} />；颜色跟随父级 currentColor。
import type { ReactNode, CSSProperties } from 'react'

export type IconName =
  | 'grid' | 'radar' | 'activity' | 'terminal' | 'sparkles' | 'clock' | 'file'
  | 'zap' | 'sliders' | 'trash' | 'sun' | 'moon' | 'monitor' | 'menu'
  | 'alert' | 'check-circle' | 'x-circle' | 'plus' | 'pen' | 'flask'
  | 'radio' | 'search' | 'download' | 'play' | 'refresh' | 'globe' | 'copy' | 'send'

const PATHS: Record<IconName, ReactNode> = {
  grid: (<><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>),
  radar: (<><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="4.5" /><path d="M12 12l5.5-5.5" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /></>),
  activity: (<path d="M22 12h-4l-3 8L9 4l-3 8H2" />),
  terminal: (<><path d="M4 17l6-5-6-5" /><path d="M12 19h8" /></>),
  sparkles: (<><path d="M12 3l2 5.6L20 11l-6 2.4L12 19l-2-5.6L4 11l6-2.4z" /><path d="M19 3.5v3M20.5 5h-3" /></>),
  clock: (<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.2 2" /></>),
  file: (<><path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M9 13h6M9 17h4" /></>),
  zap: (<path d="M13 2L4.5 13.5H11L10 22l8.5-11.5H12z" />),
  sliders: (<><path d="M5 21v-6M5 11V3M12 21v-9M12 8V3M19 21v-4M19 13V3" /><path d="M2.5 15h5M9.5 8h5M16.5 17h5" /></>),
  trash: (<><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1.2 14a2 2 0 0 1-2 1.8H8.2a2 2 0 0 1-2-1.8L5 6" /><path d="M10 11v6M14 11v6" /></>),
  sun: (<><circle cx="12" cy="12" r="4" /><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.6 4.6l1.8 1.8M17.6 17.6l1.8 1.8M4.6 19.4l1.8-1.8M17.6 6.4l1.8-1.8" /></>),
  moon: (<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />),
  monitor: (<><rect x="2" y="3" width="20" height="14" rx="2" /><path d="M8 21h8M12 17v4" /></>),
  menu: (<path d="M3 6h18M3 12h18M3 18h18" />),
  alert: (<><path d="M10.3 3.9L1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /><path d="M12 9v4.5M12 17h.01" /></>),
  'check-circle': (<><circle cx="12" cy="12" r="9" /><path d="M8.5 12.5l2.5 2.5 5-5.5" /></>),
  'x-circle': (<><circle cx="12" cy="12" r="9" /><path d="M15 9l-6 6M9 9l6 6" /></>),
  plus: (<path d="M12 5v14M5 12h14" />),
  pen: (<path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5L2 22l1.5-5.5z" />),
  flask: (<><path d="M9 3h6M10 3v6.5L4.6 19a2 2 0 0 0 1.8 3h11.2a2 2 0 0 0 1.8-3L14 9.5V3" /><path d="M7.5 15h9" /></>),
  radio: (<><circle cx="12" cy="12" r="2" /><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19.1 4.9a10 10 0 0 1 0 14.2M4.9 19.1a10 10 0 0 1 0-14.2" /></>),
  search: (<><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></>),
  download: (<><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><path d="M7 10l5 5 5-5M12 15V3" /></>),
  play: (<path d="M6 4l14 8-14 8z" />),
  refresh: (<><path d="M21 12a9 9 0 1 1-2.6-6.4" /><path d="M21 3v6h-6" /></>),
  globe: (<><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18" /></>),
  copy: (<><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>),
  send: (<path d="M12 19V5M5 12l7-7 7 7" />),
}

export function Ic({ name, size = 16, sw = 2, style, className }: {
  name: IconName; size?: number; sw?: number; style?: CSSProperties; className?: string
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round"
      className={className} style={{ flexShrink: 0, ...style }} aria-hidden>
      {PATHS[name]}
    </svg>
  )
}
