import { useMemo, type ReactNode } from 'react'
import { ApiError } from '@/api/client'
import { useChannels, useLibrary } from '@/api/hooks'
import type { LibraryQuery } from '@/api/types'
import { Button } from '@/design/Button'
import { AnimatePresence, cardMotion, motion } from '@/design/motion'
import { SkeletonGroup } from '@/design/Skeleton'
import { Icon } from '@/design/Icon'
import { PageHead } from '@/design/PageHead'
import { cn } from '@/design/cn'
import { useSearch } from '@/app/search'
import { Toolbar } from './Toolbar'
import { VideoCard, VideoCardSkeleton } from './VideoCard'
import { useDebounced } from './useDebounced'
import { useLibraryStore } from './store'

const PER = 24
const GRID = 'grid grid-cols-1 gap-4 min-[520px]:grid-cols-2 min-[900px]:grid-cols-3 min-[1200px]:grid-cols-4 min-[1700px]:grid-cols-5 min-[2100px]:grid-cols-6'

const initialsOf = (t: string) => {
  const w = t.trim().split(/\s+/).filter(Boolean)
  return ((w[0]?.[0] ?? '') + (w.length > 1 ? (w[1]?.[0] ?? '') : '')).toUpperCase() || '?'
}

/** Round channel avatar with its name and video count underneath (the classic quick-pick row). */
function ChannelChip({ label, count, on, disabled, onClick, children }: { label: string; count?: number; on: boolean; disabled?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} aria-pressed={on} className="group grid w-[72px] shrink-0 content-start justify-items-center gap-[5px] rounded-xl px-1 pb-1 text-center text-xs font-medium text-muted disabled:opacity-50">
      <span className={cn('grid size-[60px] place-items-center rounded-full border-2 bg-surface-2 text-lg font-bold text-fg shadow-[0_0_0_3px_transparent] transition-[border-color,box-shadow] group-hover:border-accent/60', on ? 'border-accent shadow-[0_0_0_3px_color-mix(in_srgb,var(--color-accent)_25%,transparent)]' : 'border-transparent')}>{children}</span>
      <span className={cn('line-clamp-2 w-full leading-[15px] [overflow-wrap:anywhere]', on && 'font-semibold text-fg')}>{label}</span>
      {count !== undefined ? <span className="text-[11px] text-faint">{count}</span> : null}
    </button>
  )
}

export function LibraryPage() {
  const { channels: selected, page, sort, favorites, resolution, toggle, selectAll, clear, setPage } = useLibraryStore()
  const search = useDebounced(useSearch((s) => s.q).trim(), 300)
  const channels = useChannels()
  const query: LibraryQuery = useMemo(() => ({ channels: selected, page, per: PER, sort, favorites, resolution, q: search }), [selected, page, sort, favorites, resolution, search])
  const library = useLibrary(query)
  const allIds = (channels.data ?? []).map((c) => c.telegram_id)
  const pages = library.data ? Math.max(1, Math.ceil(library.data.total / PER)) : 1

  return (
    <section className="mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
      <PageHead title="Videos" blurb="Browse and manage your video library" />

      <Toolbar />

      <div className="mb-3 flex gap-3.5 overflow-x-auto pb-2" role="group" aria-label="Channels">
        <ChannelChip label="All" count={channels.data?.reduce((n, c) => n + c.video_count, 0)} on={selected.length === allIds.length && allIds.length > 0} disabled={!allIds.length} onClick={() => selectAll(allIds)}>
          <Icon name="grid" className="size-5" />
        </ChannelChip>
        {channels.isPending ? <span className="self-center text-sm text-muted">Loading channels…</span> : null}
        {(channels.data ?? []).map((c) => (
          <ChannelChip key={c.telegram_id} label={c.title} count={c.video_count} on={selected.includes(c.telegram_id)} onClick={() => toggle(c.telegram_id)}>
            <span aria-hidden="true">{initialsOf(c.title)}</span>
          </ChannelChip>
        ))}
      </div>
      <div className="mb-5 flex flex-wrap items-center gap-2.5">
        <span className="rounded-full bg-accent/15 px-2.5 py-1 text-xs font-semibold text-accent">
          {selected.length} channel{selected.length === 1 ? '' : 's'} selected
        </span>
        {selected.length ? (
          <Button variant="ghost" onClick={clear} className="min-h-8 px-2.5 text-[13px] text-muted">
            Clear
          </Button>
        ) : null}
      </div>

      {!selected.length ? (
        <div className="grid place-items-center gap-3.5 py-16 text-center">
          <span className="grid size-14 place-items-center rounded-full bg-surface-2 text-accent"><Icon name="play" className="size-6" /></span>
          <p className="text-[17px] font-bold">Select channels to see their videos.</p>
          <Button variant="primary" onClick={() => selectAll(allIds)} disabled={!allIds.length}>
            All channels
          </Button>
        </div>
      ) : library.isPending ? (
        <SkeletonGroup label="Loading videos" className={GRID}>
          {Array.from({ length: 12 }, (_, i) => (
            <VideoCardSkeleton key={i} index={i} />
          ))}
        </SkeletonGroup>
      ) : library.isError ? (
        <div className="rounded-card border border-bad/40 p-6" role="alert">
          <p className="font-semibold">Could not load the library.</p>
          <p className="mb-3 text-sm text-muted">{library.error instanceof ApiError ? library.error.detail : 'Network error'}</p>
          <Button onClick={() => library.refetch()}>Retry</Button>
        </div>
      ) : (
        <>
          <h2 className="mb-4 flex items-baseline gap-2.5 text-xl font-extrabold tracking-tight">
            Library
            <span className="text-[13px] font-normal text-muted">
              {library.data.total.toLocaleString()} videos · {selected.length} channel{selected.length === 1 ? '' : 's'}
            </span>
          </h2>
          <div className={cn(GRID, library.isPlaceholderData && 'opacity-60 transition-opacity')}>
            <AnimatePresence mode="popLayout" initial={false}>
              {library.data.items.map((item, i) => (
                <motion.div key={`${item._cid}:${item.id}`} {...cardMotion(i)}>
                  <VideoCard item={item} />
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
          {library.data.items.length === 0 ? <p className="py-12 text-center text-muted">No videos match these filters.</p> : null}
          {pages > 1 ? (
            <nav className="mt-6 flex items-center justify-center gap-3" aria-label="Pages">
              <Button onClick={() => setPage(page - 1)} disabled={page === 0}>
                Previous
              </Button>
              <span className="text-sm text-muted">
                Page {page + 1} of {pages}
              </span>
              <Button onClick={() => setPage(page + 1)} disabled={page + 1 >= pages}>
                Next
              </Button>
            </nav>
          ) : null}
        </>
      )}
    </section>
  )
}
