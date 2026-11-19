import { useMemo, useState } from 'react'
import { Button } from '@/design/Button'
import { Empty } from '@/design/Empty'
import { Icon } from '@/design/Icon'
import { PageHead } from '@/design/PageHead'
import { cn } from '@/design/cn'
import { CATEGORIES, groupByDay, matchesFilter, type NoticeFilter, type Tone } from './notifications'
import { useCleared, useNotices } from './NotificationsPanel'

const TONE: Record<Tone, string> = { error: 'bg-bad/15 text-bad', warning: 'bg-warn/15 text-warn', success: 'bg-good/15 text-good', match: 'bg-accent/15 text-accent', info: 'bg-surface-3 text-muted' }

/** The Notifications page: a page of the app like the others (not a pop-up), with filters by kind and a way to clear what has been read. */
export function NotificationsPage() {
  const rows = useNotices()
  const dismiss = useCleared((s) => s.dismiss)
  const [filter, setFilter] = useState<NoticeFilter>('all')
  const chips = useMemo(() => {
    const list: Array<{ id: NoticeFilter; label: string }> = [{ id: 'all', label: 'All' }, { id: 'attention', label: 'Needs attention' }, ...CATEGORIES]
    return list.map((c) => ({ ...c, count: rows.filter((r) => matchesFilter(r, c.id)).length })).filter((c) => c.id === 'all' || c.count > 0 || c.id === filter)
  }, [rows, filter])
  const shown = rows.filter((r) => matchesFilter(r, filter))
  const groups = groupByDay(shown)
  return (
    <section className="flex w-full flex-1 flex-col px-4 pb-10 pt-6 sm:px-10">
      <PageHead eyebrow="Activity" title="Notifications" blurb="Important updates from your library and your transfers">
        <Button disabled={!rows.length} onClick={() => dismiss(rows.map((r) => r.key))}>Clear all</Button>
      </PageHead>
      <div role="tablist" aria-label="Kinds of notification" className="mb-5 flex flex-wrap gap-2">
        {chips.map((c) => (
          <button key={c.id} type="button" role="tab" aria-selected={filter === c.id} onClick={() => setFilter(c.id)}
            className={cn('inline-flex h-9 items-center gap-2 rounded-full border px-3.5 text-sm font-semibold transition-colors', filter === c.id ? 'border-accent/50 bg-accent/15 text-fg' : 'border-line text-muted hover:bg-fg/5 hover:text-fg')}>
            {c.label}<span className="rounded-full bg-fg/10 px-1.5 text-[11px] tabular-nums">{c.count}</span>
          </button>
        ))}
      </div>
      {groups.length ? groups.map(([label, items]) => (
        <section key={label} aria-label={label} className="mb-6">
          <h2 className="mb-2 text-xs font-bold uppercase tracking-[0.1em] text-faint">{label}</h2>
          <ul className="grid gap-2">
            {items.map((r) => (
              <li key={r.key} className="flex items-start gap-3 rounded-[18px] border border-line bg-surface p-3.5">
                <span className={cn('grid size-9 shrink-0 place-items-center rounded-full', TONE[r.tone])}><Icon name={r.icon} className="size-4" /></span>
                <div className="min-w-0 flex-1">
                  <p className="font-semibold [overflow-wrap:anywhere]">{r.title}</p>
                  {r.meta ? <p className="text-sm text-muted">{r.meta}</p> : null}
                </div>
                <button type="button" onClick={() => dismiss([r.key])} aria-label={`Dismiss: ${r.title}`} title="Dismiss" className="grid size-8 shrink-0 place-items-center rounded-lg text-muted hover:bg-fg/10 hover:text-fg"><Icon name="close" className="size-4" /></button>
              </li>
            ))}
          </ul>
        </section>
      )) : (
        <Empty icon="check" title={rows.length ? 'Nothing in this kind' : 'You’re all caught up'}>{rows.length ? 'Choose another kind above.' : 'Important library and system updates will appear here.'}</Empty>
      )}
    </section>
  )
}
