import { cn } from '@/design/cn'
import { motion, spring } from '@/design/motion'
import { useScreenView, type ScreenView } from './view'

const TABS: ReadonlyArray<{ id: ScreenView; label: string }> = [{ id: 'all', label: 'All' }, { id: 'movies', label: 'Movies' }, { id: 'series', label: 'Series' }]

/** All | Movies | Series, at the top of Movies & Shows: one page with a switch, the way Photos | People works. */
export function ScreenTabs() {
  const view = useScreenView((s) => s.view)
  const set = useScreenView((s) => s.setView)
  return (
    <div role="tablist" aria-label="Movies and shows views" className="mb-3 inline-flex gap-0.5 rounded-xl border border-line bg-surface p-[3px]">
      {TABS.map((t) => (
        <button key={t.id} type="button" role="tab" aria-selected={view === t.id} onClick={() => set(t.id)} className={cn('relative h-9 rounded-[9px] px-4 text-sm font-semibold transition-colors', view === t.id ? 'text-fg' : 'text-muted hover:text-fg')}>
          {view === t.id ? <motion.span layoutId="screen-view-pill" transition={spring} aria-hidden="true" className="absolute inset-0 rounded-[9px] bg-surface-3" /> : null}
          <span className="relative">{t.label}</span>
        </button>
      ))}
    </div>
  )
}
