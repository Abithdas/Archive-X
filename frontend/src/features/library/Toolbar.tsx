import { useState } from 'react'
import type { SortKey } from '@/api/types'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { FiltersDrawer } from './FiltersDrawer'
import { MoreActions } from './MoreActions'
import type { Player } from './shuffle'
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

const PILL_BASE = 'h-10 items-center gap-2 rounded-xl border border-line bg-surface px-3.5 text-sm font-semibold transition-colors hover:bg-surface-2'
const PILL = `inline-flex ${PILL_BASE}`
/** Shuffle pills: shown from tablet width up. They must not carry `inline-flex` themselves or it would beat `hidden`. */
const SHUFFLE = `hidden bg-accent/15 text-accent hover:bg-accent/25 md:inline-flex ${PILL_BASE}`

/** Filters, sort, refresh and the ⋯ menu: the classic Videos toolbar controls that are wired to the API so far. */
export function Toolbar({ onRefresh, refreshing, onShuffle, shuffling }: { onRefresh: () => void; refreshing: boolean; onShuffle: (player: Player) => void; shuffling: boolean }) {
  const sort = useLibraryStore((s) => s.sort)
  const setSort = useLibraryStore((s) => s.setSort)
  const filters = useLibraryStore((s) => s.filters)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  const count = Object.values(filters).filter(Boolean).length
  return (
    <div className="mb-5 flex flex-wrap items-center gap-2" role="toolbar" aria-label="Video library toolbar">
      <button type="button" className={PILL} aria-haspopup="dialog" onClick={() => setFiltersOpen(true)}>
        <Icon name="filter" />
        Filters
        {count ? <span className="grid size-5 place-items-center rounded-full bg-accent text-[11px] font-bold text-accent-fg">{count}</span> : null}
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
      <button type="button" className={cn(PILL, 'w-10 justify-center px-0')} onClick={onRefresh} aria-label="Refresh channel list" title="Refresh channel list from Telegram" disabled={refreshing}>
        <Icon name="refresh" className={cn(refreshing && 'animate-spin')} />
      </button>
      <button type="button" className={SHUFFLE} onClick={() => onShuffle('vlc')} disabled={shuffling} aria-label="Shuffle in VLC" title="Shuffle the list in VLC">
        <Icon name="brand-vlc" className="size-4" />VLC<Icon name="shuffle" className="size-4" />
      </button>
      <button type="button" className={SHUFFLE} onClick={() => onShuffle('mpc')} disabled={shuffling} aria-label="Shuffle in MPC-HC" title="Shuffle the list in MPC-HC">
        MPC-HC<Icon name="shuffle" className="size-4" />
      </button>
      <button type="button" className={cn(PILL, 'w-10 justify-center px-0')} onClick={() => setMoreOpen(true)} aria-haspopup="dialog" aria-label="More library actions" title="More actions">
        <span aria-hidden="true" className="text-base leading-none tracking-widest">•••</span>
      </button>
      <FiltersDrawer open={filtersOpen} onClose={() => setFiltersOpen(false)} />
      <MoreActions open={moreOpen} onClose={() => setMoreOpen(false)} onShuffle={onShuffle} />
    </div>
  )
}
