import { useMemo } from 'react'
import { ApiError } from '@/api/client'
import { useChannels, useLibrary } from '@/api/hooks'
import type { LibraryQuery } from '@/api/types'
import { Button } from '@/design/Button'
import { AnimatePresence, cardMotion, motion } from '@/design/motion'
import { SkeletonGroup } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import { VideoCard, VideoCardSkeleton } from './VideoCard'
import { useLibraryStore } from './store'

const PER = 24
const GRID = 'grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6'

export function LibraryPage() {
  const { channels: selected, page, toggle, selectAll, clear, setPage } = useLibraryStore()
  const channels = useChannels()
  const query: LibraryQuery = useMemo(() => ({ channels: selected, page, per: PER, sort: 'date_desc' }), [selected, page])
  const library = useLibrary(query)
  const allIds = (channels.data ?? []).map((c) => c.telegram_id)
  const pages = library.data ? Math.max(1, Math.ceil(library.data.total / PER)) : 1

  return (
    <section className="mx-auto w-full max-w-[1800px] p-4 sm:p-6">
      <header className="mb-4">
        <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-accent">Library</p>
        <h1 className="text-3xl font-extrabold tracking-tight">Videos</h1>
        <p className="text-sm text-muted">Browse and manage your video library</p>
      </header>

      <div className="mb-3 flex gap-2 overflow-x-auto pb-2" role="group" aria-label="Channels">
        <Button variant={selected.length === allIds.length && allIds.length ? 'primary' : 'outline'} onClick={() => selectAll(allIds)} disabled={!allIds.length}>
          All
        </Button>
        {channels.isPending ? <span className="self-center text-sm text-muted">Loading channels…</span> : null}
        {(channels.data ?? []).map((c) => {
          const on = selected.includes(c.telegram_id)
          return (
            <Button key={c.telegram_id} variant={on ? 'primary' : 'outline'} aria-pressed={on} onClick={() => toggle(c.telegram_id)} className="max-w-56">
              <span className="truncate">{c.title}</span>
              <span className={cn('text-xs', on ? 'opacity-80' : 'text-muted')}>{c.video_count}</span>
            </Button>
          )
        })}
        {selected.length ? (
          <Button variant="ghost" onClick={clear}>
            Clear
          </Button>
        ) : null}
      </div>

      {!selected.length ? (
        <div className="grid place-items-center gap-3 rounded-card border border-dashed border-line py-20 text-center">
          <p className="font-semibold">Select channels to see their videos.</p>
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
          <p className="mb-3 text-sm text-muted">
            {library.data.total.toLocaleString()} videos · {selected.length} channel{selected.length === 1 ? '' : 's'}
          </p>
          <div className={cn(GRID, library.isPlaceholderData && 'opacity-60 transition-opacity')}>
            <AnimatePresence mode="popLayout" initial={false}>
              {library.data.items.map((item, i) => (
                <motion.div key={`${item._cid}:${item.id}`} {...cardMotion(i)}>
                  <VideoCard item={item} />
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
          {library.data.items.length === 0 ? <p className="py-12 text-center text-muted">No videos match.</p> : null}
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
