import { useState } from 'react'
import type { ResolutionKey, SortKey } from '@/api/types'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { useLibraryStore } from './store'

export const SORTS: { value: SortKey; label: string }[] = [
  { value: 'date_desc', label: 'Newest first' },
  { value: 'date_asc', label: 'Oldest first' },
  { value: 'size_desc', label: 'Largest first' },
  { value: 'dur_desc', label: 'Longest first' },
  { value: 'name_asc', label: 'Name A–Z' },
  { value: 'studio_asc', label: 'Studio A–Z' },
  { value: 'performer_asc', label: 'Performer A–Z' },
  { value: 'tag_asc', label: 'Tag A–Z' },
]
export const RESOLUTIONS: { value: ResolutionKey; label: string }[] = [
  { value: '', label: 'All' },
  { value: '2160', label: '4K' },
  { value: '1440', label: '1440p' },
  { value: '1080', label: '1080p' },
  { value: '720', label: '720p' },
  { value: '480', label: 'Below 720p' },
]

const PILL = 'inline-flex h-10 items-center gap-2 rounded-xl border border-line bg-surface px-3.5 text-sm font-semibold transition-colors hover:bg-surface-2'

/** Filters, sort and favourites: the controls on the classic Videos toolbar that are wired to the API so far. */
export function Toolbar() {
  const { sort, favorites, resolution, setSort, setFavorites, setResolution, resetFilters } = useLibraryStore()
  const [open, setOpen] = useState(false)
  const active = (resolution ? 1 : 0) + (favorites ? 1 : 0)
  return (
    <div className="relative mb-5 flex flex-wrap items-center gap-2">
      <button type="button" className={cn(PILL, open && 'border-accent/50')} aria-expanded={open} aria-controls="library-filters" onClick={() => setOpen(!open)}>
        <Icon name="filter" />
        Filters
        {active ? <span className="grid size-5 place-items-center rounded-full bg-accent text-[11px] font-bold text-accent-fg">{active}</span> : null}
      </button>
      <label className={cn(PILL, 'relative min-w-[154px] cursor-pointer pr-9')}>
        <Icon name="sort" />
        <span>{SORTS.find((s) => s.value === sort)?.label}</span>
        <Icon name="chevron-down" className="absolute right-3 size-4 text-muted" />
        <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort videos" className="absolute inset-0 cursor-pointer opacity-0">
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
      </label>
      <button type="button" className={cn(PILL, favorites && 'border-accent/50 text-accent')} aria-pressed={favorites} onClick={() => setFavorites(!favorites)}>
        <Icon name="star" />
        Favourites
      </button>
      {open ? (
        <div id="library-filters" role="group" aria-label="Filters" className="absolute left-0 top-12 z-10 grid w-[min(360px,100%)] gap-3 rounded-[18px] border border-line-2 bg-surface p-4 shadow-[0_18px_50px_rgb(0_0_0/0.5)]">
          <div>
            <p className="mb-2 text-xs font-bold text-muted">Resolution</p>
            <div className="flex flex-wrap gap-1.5">
              {RESOLUTIONS.map((r) => (
                <button key={r.value} type="button" aria-pressed={resolution === r.value} onClick={() => setResolution(r.value)} className={cn('h-8 rounded-full px-3 text-[13px] font-semibold', resolution === r.value ? 'bg-accent text-accent-fg' : 'bg-surface-3 text-muted hover:text-fg')}>
                  {r.label}
                </button>
              ))}
            </div>
          </div>
          <div className="flex justify-between">
            <button type="button" onClick={resetFilters} className="text-sm font-semibold text-muted hover:text-fg">Reset</button>
            <button type="button" onClick={() => setOpen(false)} className="text-sm font-semibold text-accent">Done</button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
