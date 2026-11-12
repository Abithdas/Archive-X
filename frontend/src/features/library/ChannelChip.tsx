import type { ReactNode } from 'react'
import { cn } from '@/design/cn'
import { AnimatedNumber } from '@/design/AnimatedNumber'

export const initialsOf = (t: string) => {
  const w = t.trim().split(/\s+/).filter(Boolean)
  return ((w[0]?.[0] ?? '') + (w.length > 1 ? (w[1]?.[0] ?? '') : '')).toUpperCase() || '?'
}

/** Round channel avatar with its name and video count underneath (the classic quick-pick row). */
export function ChannelChip({ label, count, on, disabled, onClick, children, tone }: { tone?: 'amber'; label: string; count?: number; on: boolean; disabled?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} aria-pressed={on} className="group grid w-[72px] shrink-0 content-start justify-items-center gap-[5px] rounded-xl px-1 pb-1 text-center text-xs font-medium text-muted disabled:opacity-50">
      <span className={cn('grid size-[60px] place-items-center rounded-full border-2 bg-surface-2 text-lg font-bold text-fg shadow-[0_0_0_3px_transparent] transition-[border-color,box-shadow] group-hover:border-accent/60', on ? (tone === 'amber' ? 'border-warn text-warn' : 'border-accent') + ' shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-accent)_25%,transparent)]' : tone === 'amber' ? 'border-transparent bg-warn/10 text-warn' : 'border-transparent')}>{children}</span>
      <span className={cn('line-clamp-2 w-full leading-[15px] [overflow-wrap:anywhere]', on && 'font-semibold text-fg')}>{label}</span>
      {count !== undefined ? <span className="text-[11px] text-faint"><AnimatedNumber value={count} /></span> : null}
    </button>
  )
}
