import { useEffect, useState } from 'react'
import { useSuggestions } from '@/api/hooks'
import { Dialog } from '@/design/Dialog'
import { cn } from '@/design/cn'
import { NO_FILTERS, useLibraryStore, type Filters } from './store'

const FIELD = 'h-11 w-full rounded-xl border border-line-2 bg-bg px-3 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60'
const CHIP = 'h-8 rounded-full border px-3 text-[13px] font-semibold transition-colors'
export const RESOLUTIONS = [
  { value: '', label: 'Any' },
  { value: '2160', label: '4K' },
  { value: '1440', label: '1440p' },
  { value: '1080', label: '1080p' },
  { value: '720', label: '720p' },
  { value: '480', label: '<720p' },
] as const
export const QUICK_RANGES = [
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
  { days: 365, label: '1 year' },
] as const

/** Local calendar date as YYYY-MM-DD (toISOString would shift it to UTC and can land on the wrong day). */
export const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
export function rangeEndingToday(days: number, now = new Date()): { dateFrom: string; dateTo: string } {
  const from = new Date(now)
  from.setDate(from.getDate() - days)
  return { dateFrom: isoDay(from), dateTo: isoDay(now) }
}

function Label({ children }: { icon?: string; children: string }) {
  return <span className="text-xs font-semibold text-muted">{children}</span>
}

/** The classic "Filters" sheet: studio, performer, tag, upload date (with quick ranges) and resolution. Body scrolls, Apply stays put. */
export function FiltersDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const applied = useLibraryStore((s) => s.filters)
  const setFilters = useLibraryStore((s) => s.setFilters)
  const [draft, setDraft] = useState<Filters>(applied)
  const sugg = useSuggestions(open)
  useEffect(() => {
    if (open) setDraft(applied)
  }, [open, applied])
  const set = (k: keyof Filters) => (e: React.ChangeEvent<HTMLInputElement>) => setDraft({ ...draft, [k]: e.target.value })
  const apply = () => {
    setFilters({ ...draft, studio: draft.studio.trim(), performer: draft.performer.trim(), tag: draft.tag.trim() })
    onClose()
  }
  const text = (k: 'studio' | 'performer' | 'tag', label: string, icon: string, ph: string, list: string[] | undefined) => (
    <label className="grid gap-1.5">
      <Label icon={icon}>{label}</Label>
      <input className={FIELD} list={`sg-${k}`} value={draft[k]} onChange={set(k)} placeholder={ph} autoComplete="off" />
      <datalist id={`sg-${k}`}>{(list ?? []).map((n) => <option key={n} value={n} />)}</datalist>
    </label>
  )
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Filters"
      side="right"
      actions={<button type="button" onClick={() => setDraft(NO_FILTERS)} className="h-9 rounded-xl border border-line-2 px-3 text-[13px] font-semibold">Reset</button>}
      footer={<button type="submit" form="filters-form" className="h-12 w-full rounded-xl bg-accent text-[15px] font-bold text-accent-fg hover:brightness-105">Apply filters</button>}
    >
      <form id="filters-form" className="grid gap-4" onSubmit={(e) => { e.preventDefault(); apply() }}>
        {text('studio', 'Studio', 'studio', 'Any studio…', sugg.data?.studios)}
        {text('performer', 'Performer', 'user', 'Any performer…', sugg.data?.performers)}
        {text('tag', 'Tag', 'tag', 'Any tag…', sugg.data?.tags)}
        <div className="grid gap-1.5">
          <Label icon="clock">Upload date</Label>
          <div className="grid grid-cols-2 gap-2">
            <input type="date" aria-label="Upload date from" className={FIELD} value={draft.dateFrom} onChange={set('dateFrom')} max={draft.dateTo || undefined} />
            <input type="date" aria-label="Upload date to" className={FIELD} value={draft.dateTo} onChange={set('dateTo')} min={draft.dateFrom || undefined} />
          </div>
          <div role="group" aria-label="Quick upload date ranges" className="flex flex-wrap gap-1.5">
            {QUICK_RANGES.map((r) => (
              <button key={r.days} type="button" onClick={() => setDraft({ ...draft, ...rangeEndingToday(r.days) })} className={cn(CHIP, 'border-line bg-surface-2 text-muted hover:text-fg')}>
                {r.label}
              </button>
            ))}
          </div>
        </div>
        <div className="grid gap-1.5">
          <Label icon="picture">Resolution</Label>
          <div role="group" aria-label="Resolution" className="flex flex-wrap gap-1.5">
            {RESOLUTIONS.map((r) => (
              <button key={r.value} type="button" aria-pressed={draft.resolution === r.value} onClick={() => setDraft({ ...draft, resolution: r.value })} className={cn(CHIP, draft.resolution === r.value ? 'border-accent/60 bg-accent/15 text-accent' : 'border-line bg-surface-2 text-muted hover:text-fg')}>
                {r.label}
              </button>
            ))}
          </div>
        </div>
      </form>
    </Dialog>
  )
}
