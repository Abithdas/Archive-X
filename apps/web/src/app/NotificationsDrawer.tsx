import { useEffect, useMemo, useRef, useState } from 'react'
import { Button } from '@/design/Button'
import { Empty } from '@/design/Empty'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { CATEGORIES, groupByDay, matchesFilter, type NoticeFilter, type Tone } from './notifications'
import { useCleared, useNotices, useSeen } from './NotificationsPanel'
import { useUi } from './ui'

const TONE: Record<Tone, string> = { error: 'bg-bad/15 text-bad', warning: 'bg-warn/15 text-warn', success: 'bg-good/15 text-good', match: 'bg-accent/15 text-accent', info: 'bg-surface-3 text-muted' }

/**
 * Notifications: a panel that slides in from the right edge, under the top bar, as part of the app (not a page, not a pop-up with a dimmed back).
 * Kinds to filter by, notices grouped by day, a dismiss button on each, and Clear all. It closes with Esc, the close button, or a press outside it.
 */
export function NotificationsDrawer() {
  const open = useUi((s) => s.notifications)
  const set = useUi((s) => s.setNotifications)
  const rows = useNotices()
  const mark = useSeen((s) => s.mark)
  const dismiss = useCleared((s) => s.dismiss)
  const [filter, setFilter] = useState<NoticeFilter>('all')
  const box = useRef<HTMLElement>(null)
  useEffect(() => { if (open) mark(rows) }, [open, rows, mark])   // looking at the list is what clears the bell's count
  useEffect(() => {
    if (!open) return
    const away = (e: PointerEvent) => { const t = e.target as HTMLElement; if (!box.current?.contains(t) && !t.closest('[data-notifications-toggle]')) set(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') set(false) }
    document.addEventListener('pointerdown', away)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('pointerdown', away); document.removeEventListener('keydown', esc) }
  }, [open, set])
  const chips = useMemo(() => {
    const list: Array<{ id: NoticeFilter; label: string }> = [{ id: 'all', label: 'All' }, { id: 'attention', label: 'Needs attention' }, ...CATEGORIES]
    return list.map((c) => ({ ...c, count: rows.filter((r) => matchesFilter(r, c.id)).length })).filter((c) => c.id === 'all' || c.count > 0 || c.id === filter)
  }, [rows, filter])
  const groups = groupByDay(rows.filter((r) => matchesFilter(r, filter)))
  return (
    <aside ref={box} aria-label="Notifications" aria-hidden={!open} inert={!open}
      className={cn('fixed bottom-0 right-0 top-16 z-30 flex w-[min(420px,100vw)] flex-col border-l border-line bg-surface shadow-[-18px_0_40px_rgb(0_0_0/0.35)] transition-transform duration-300 ease-out', open ? 'translate-x-0' : 'translate-x-full')}>
      <div className="flex shrink-0 items-center gap-2 border-b border-line px-4 py-3">
        <h2 className="mr-auto text-lg font-extrabold">Notifications</h2>
        <Button disabled={!rows.length} onClick={() => dismiss(rows.map((r) => r.key))}>Clear all</Button>
        <button type="button" onClick={() => set(false)} aria-label="Close notifications" className="grid size-9 place-items-center rounded-xl border border-line-2 text-muted hover:text-fg"><Icon name="close" className="size-4" /></button>
      </div>
      <div role="tablist" aria-label="Kinds of notification" className="flex shrink-0 flex-wrap gap-1.5 border-b border-line px-4 py-3">
        {chips.map((c) => (
          <button key={c.id} type="button" role="tab" aria-selected={filter === c.id} onClick={() => setFilter(c.id)}
            className={cn('inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-[13px] font-semibold transition-colors', filter === c.id ? 'border-accent/50 bg-accent/15 text-fg' : 'border-line text-muted hover:bg-fg/5 hover:text-fg')}>
            {c.label}<span className="rounded-full bg-fg/10 px-1.5 text-[11px] tabular-nums">{c.count}</span>
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-3">
        {groups.length ? groups.map(([label, items]) => (
          <section key={label} aria-label={label} className="mb-5">
            <h3 className="mb-2 text-xs font-bold uppercase tracking-[0.1em] text-faint">{label}</h3>
            <ul className="grid gap-2">
              {items.map((r) => (
                <li key={r.key} className="flex items-start gap-3 rounded-2xl border border-line bg-bg p-3">
                  <span className={cn('grid size-8 shrink-0 place-items-center rounded-full', TONE[r.tone])}><Icon name={r.icon} className="size-4" /></span>
                  <div className="min-w-0 flex-1 text-sm">
                    <p className="font-semibold [overflow-wrap:anywhere]">{r.title}</p>
                    {r.meta ? <p className="text-xs text-muted">{r.meta}</p> : null}
                  </div>
                  <button type="button" onClick={() => dismiss([r.key])} aria-label={`Dismiss: ${r.title}`} title="Dismiss" className="grid size-7 shrink-0 place-items-center rounded-lg text-muted hover:bg-fg/10 hover:text-fg"><Icon name="close" className="size-3.5" /></button>
                </li>
              ))}
            </ul>
          </section>
        )) : (
          <Empty icon="check" title={rows.length ? 'Nothing in this kind' : 'You’re all caught up'}>{rows.length ? 'Choose another kind above.' : 'Important library and system updates will appear here.'}</Empty>
        )}
      </div>
    </aside>
  )
}
