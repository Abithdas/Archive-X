import { useEffect, useMemo } from 'react'
import { create } from 'zustand'
import { useStatus } from '@/api/hooks'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { buildNotifications, groupByDay, type Tone } from './notifications'
import { loadSeen, markSeen, saveSeen, unseen } from './seenNotices'
import { useUi } from './ui'

const TONE: Record<Tone, string> = { error: 'bg-bad/15 text-bad', warning: 'bg-warn/15 text-warn', success: 'bg-good/15 text-good', match: 'bg-accent/15 text-accent', info: 'bg-surface-3 text-muted' }

/** Notices derived from the live status stream. The same list feeds the header badge. */
export function useNotices() {
  const { data } = useStatus()
  const events = data?.events
  const jobs = data?.jobs
  return useMemo(() => buildNotifications(Array.isArray(events) ? (events as never[]) : [], Array.isArray(jobs) ? (jobs as never[]) : []), [events, jobs])
}

const useSeen = create<{ seen: readonly string[]; mark: (rows: readonly { key: string }[]) => void }>((set, get) => ({
  seen: loadSeen(),
  mark: (rows) => { const next = markSeen(get().seen, rows); if (next !== get().seen) { saveSeen(next); set({ seen: next }) } },
}))

/** How many notices are new: the ones the person has not opened the panel on yet. The bell shows this. */
export function useUnseenCount(): number {
  const rows = useNotices()
  const seen = useSeen((s) => s.seen)
  return useMemo(() => unseen(rows, seen).length, [rows, seen])
}

export function NotificationsPanel() {
  const open = useUi((s) => s.notifications)
  const set = useUi((s) => s.setNotifications)
  const rows = useNotices()
  const groups = groupByDay(rows)
  const mark = useSeen((s) => s.mark)
  useEffect(() => { if (open) mark(rows) }, [open, rows, mark])   // looking at the list is what clears the count
  return (
    <Dialog open={open} onClose={() => set(false)} title="Notifications" side="right">
      <p className="mb-3 text-sm text-muted">Important updates from your library</p>
      {rows.length ? (
        groups.map(([label, items]) => (
          <section key={label} aria-label={label} className="mb-3">
            <h3 className="mb-1.5 text-xs font-bold text-faint">{label}</h3>
            <ul className="grid gap-1.5">
              {items.map((r) => (
                <li key={r.key} className="flex items-start gap-3 rounded-xl bg-bg p-3">
                  <span className={cn('grid size-8 shrink-0 place-items-center rounded-full', TONE[r.tone])}><Icon name={r.icon} className="size-4" /></span>
                  <div className="min-w-0 text-sm">
                    <p className="font-semibold [overflow-wrap:anywhere]">{r.title}</p>
                    {r.meta ? <p className="text-xs text-muted">{r.meta}</p> : null}
                  </div>
                </li>
              ))}
            </ul>
          </section>
        ))
      ) : (
        <div className="py-6">
          <p className="font-bold">You’re all caught up</p>
          <p className="text-sm text-muted">Important library and system updates will appear here.</p>
        </div>
      )}
    </Dialog>
  )
}
