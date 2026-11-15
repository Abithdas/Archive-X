import { StatusMark, type MarkState } from '@/design/StatusMark'
import { cn } from '@/design/cn'

export interface FeedStep { id: string; label: string; state: MarkState; detail?: string }

/** A short list of steps, each with a mark that spins while it works and turns into a check (or a cross) when it ends. */
export function StepsFeed({ steps, label, className }: { steps: readonly FeedStep[]; label: string; className?: string }) {
  return (
    <ol aria-label={label} className={cn('grid gap-2', className)}>
      {steps.map((s) => (
        <li key={s.id} aria-current={s.state === 'active' ? 'step' : undefined} className="flex items-start gap-3 text-sm">
          <span className="mt-px"><StatusMark state={s.state} /></span>
          <span className="min-w-0">
            <span className={cn('block', s.state === 'pending' ? 'text-faint' : s.state === 'error' ? 'font-semibold text-bad' : 'font-semibold text-fg')}>{s.label}</span>
            {s.detail ? <span className={cn('block text-xs [overflow-wrap:anywhere]', s.state === 'error' ? 'text-bad' : 'text-muted')}>{s.detail}</span> : null}
          </span>
        </li>
      ))}
    </ol>
  )
}
