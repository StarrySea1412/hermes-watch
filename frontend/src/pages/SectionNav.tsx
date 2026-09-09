import { useEffect, useState } from 'react'
import { Ic, type IconName } from '../icons'

/** 设置页分区导航：sticky 胶囊 + 滚动 spy，点击平滑滚到锚点 */
export function SectionNav({ sections }: {
  sections: readonly { id: string; label: string; icon: string }[]
}) {
  const [active, setActive] = useState(sections[0]?.id ?? '')
  useEffect(() => {
    const onScroll = () => {
      let cur = sections[0]?.id ?? ''
      for (const s of sections) {
        const el = document.getElementById(s.id)
        if (el && el.getBoundingClientRect().top < 130) cur = s.id
      }
      setActive(cur)
    }
    onScroll()
    const m = document.querySelector('main')
    m?.addEventListener('scroll', onScroll, { passive: true })
    return () => m?.removeEventListener('scroll', onScroll)
  }, [sections])

  const jump = (id: string) => (e: React.MouseEvent) => {
    e.preventDefault()
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="sticky top-0 z-20 -mx-1 px-1 py-2 mb-4 flex gap-2 flex-wrap backdrop-blur-md"
      style={{ background: 'color-mix(in srgb, var(--bg-base) 78%, transparent)' }}>
      {sections.map(s => {
        const on = active === s.id
        return (
          <a key={s.id} href={`#${s.id}`} onClick={jump(s.id)}
            className="pill inline-flex items-center gap-1.5"
            style={on
              ? { background: 'var(--accent-dim)', color: 'var(--accent)', border: '1px solid var(--accent-border)' }
              : { background: 'var(--neutral-bg)', color: 'var(--text-mute)', border: '1px solid transparent' }}>
            <Ic name={s.icon as IconName} size={12} /> {s.label}
          </a>
        )
      })}
    </div>
  )
}

/** 分区标题（导航锚点落点） */
export function SectionHead({ id, icon, label }: { id: string; icon: string; label: string }) {
  return (
    <div id={id} className="scroll-mt-16 flex items-center gap-2 mt-7 first:mt-0 mb-3">
      <span style={{ color: 'var(--accent)' }}><Ic name={icon as IconName} size={14} /></span>
      <span className="text-[12px] font-bold text-[var(--text-hi)] tracking-[.18em]">{label}</span>
      <span className="flex-1 h-px" style={{ background: 'var(--border)' }} />
    </div>
  )
}
