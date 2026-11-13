import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'

export const PILL = 'inline-flex h-10 items-center gap-2 rounded-full border border-line-2 bg-surface px-4 text-sm font-semibold hover:bg-fg/5'

/** A toolbar button that opens a small panel under it; closes on outside click or Escape. */
export function Popover({ label, icon, badge, active, align = 'left', children }: { label: ReactNode; icon?: ReactNode; badge?: number; active?: boolean; align?: 'left' | 'right'; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const down = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false) }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', down); document.addEventListener('keydown', key)
    return () => { document.removeEventListener('mousedown', down); document.removeEventListener('keydown', key) }
  }, [open])
  return (
    <div ref={box} className="relative">
      <button type="button" aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen(!open)} className={cn(PILL, (open || active) && 'border-accent')}>
        {icon}<span className="max-w-[180px] truncate">{label}</span>
        {badge ? <span className="grid h-5 min-w-5 place-items-center rounded-full bg-accent px-1.5 text-xs font-bold text-accent-fg">{badge}</span> : null}
        <Icon name="chevron-down" className="size-4 text-muted" />
      </button>
      {open ? <div role="dialog" aria-label={typeof label === 'string' ? label : undefined} className={cn('absolute top-full z-30 mt-2 max-h-[70vh] w-[min(92vw,420px)] overflow-y-auto rounded-2xl border border-line-2 bg-surface p-3 shadow-xl', align === 'right' ? 'right-0' : 'left-0')}>{children}</div> : null}
    </div>
  )
}
