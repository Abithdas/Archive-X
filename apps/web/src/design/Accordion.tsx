import { useId, useState, type ReactNode } from 'react'
import { Icon } from './Icon'
import { cn } from './cn'

/**
 * The Transitions.dev accordion in place of a native <details>: the panel grows from nothing and fades in with a slight blur, the chevron
 * flips from "v" to "^". The content stays in the page while folded (like <details>), but cannot be focused or read out (`inert`).
 */
export function Disclosure({ summary, summaryClassName, className, defaultOpen = false, onToggle, children }: {
  summary: ReactNode; summaryClassName?: string; className?: string; defaultOpen?: boolean; onToggle?: (open: boolean) => void; children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  const id = useId()
  const flip = () => { const next = !open; setOpen(next); onToggle?.(next) }
  return (
    <div className={cn('t-acc', className)} data-open={open ? 'true' : 'false'}>
      <button type="button" aria-expanded={open} aria-controls={id} onClick={flip} className={cn('flex w-full cursor-pointer items-center gap-2 text-left', summaryClassName)}>
        <span className="t-acc-chevron shrink-0 text-muted"><Icon name="chevron-down" className="size-4" /></span>
        <span className="flex min-w-0 flex-1 items-center gap-2">{summary}</span>
      </button>
      <div id={id} className="t-acc-panel" role="region">
        <div className="t-acc-panel-inner" inert={!open}>{children}</div>
      </div>
    </div>
  )
}
