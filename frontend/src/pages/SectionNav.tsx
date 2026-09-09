import { useEffect, useState } from 'react'
import { Ic, type IconName } from '../icons'

export type SectionDef = { id: string; label: string; icon: string }

/** 分区导航 hook：滚动 spy 高亮当前分区（main 滚动容器 + window 双保险） */
function useScrollSpy(sections: readonly SectionDef[]) {
  const [active, setActive] = useState(sections[0]?.id ?? '')
  useEffect(() => {
    const onScroll = () => {
      let cur = sections[0]?.id ?? ''
      for (const s of sections) {
        const el = document.getElementById(s.id)
        if (el && el.getBoundingClientRect().top < 160) cur = s.id
      }
      setActive(cur)
    }
    onScroll()
    const m = document.querySelector('main')
    m?.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      m?.removeEventListener('scroll', onScroll)
      window.removeEventListener('scroll', onScroll)
    }
  }, [sections])
  return active
}

const jump = (id: string) => (e: React.MouseEvent) => {
  e.preventDefault()
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

/** 横排药丸导航（窄屏回落） */
export function SectionNav({ sections }: { sections: readonly SectionDef[] }) {
  const active = useScrollSpy(sections)
  return (
    <div className="flex items-center gap-2 flex-wrap mb-1">
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

/** 右侧竖排锚点栏（宽屏）：sticky 跟随，当前分区左侧亮条 + 高亮 */
export function SectionRail({ sections }: { sections: readonly SectionDef[] }) {
  const active = useScrollSpy(sections)
  return (
    // sticky 必须挂在 flex 子项（aside）本身：外层 xl:items-start 把 aside 压成
    // 内容高，sticky 写在内层会因没有滚动行程而失效
    <aside className="hidden xl:block w-44 shrink-0 self-start sticky top-6">
      <div className="flex flex-col gap-0.5">
        {sections.map(s => {
          const on = active === s.id
          return (
            <a key={s.id} href={`#${s.id}`} onClick={jump(s.id)}
              className="flex items-center gap-2 pl-3 pr-2 py-2 text-[12.5px] rounded-lg border-l-2 transition-colors"
              style={on
                ? { borderColor: 'var(--accent)', color: 'var(--accent)', background: 'var(--accent-dim)', fontWeight: 600 }
                : { borderColor: 'var(--border)', color: 'var(--text-mute)' }}>
              <Ic name={s.icon as IconName} size={13} /> {s.label}
            </a>
          )
        })}
      </div>
    </aside>
  )
}

/** 分区标题（导航锚点落点） */
export function SectionHead({ id, icon, label }: { id: string; icon: string; label: string }) {
  return (
    <div id={id} className="scroll-mt-20 flex items-center gap-2 mt-8 first:mt-1 mb-3">
      <span style={{ color: 'var(--accent)' }}><Ic name={icon as IconName} size={14} /></span>
      <span className="text-[13px] font-bold text-[var(--text-hi)] tracking-[.18em]">{label}</span>
      <span className="flex-1 h-px" style={{ background: 'var(--border)' }} />
    </div>
  )
}
