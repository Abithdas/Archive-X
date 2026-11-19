import { ModeEmptyNote } from '@/features/content/ModeEmptyNote'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { useChannels, useLibrary } from '@/api/hooks'
import type { LibraryQuery } from '@/api/types'
import { Button } from '@/design/Button'
import { AnimatePresence, cardMotion, motion } from '@/design/motion'
import { SkeletonGroup } from '@/design/Skeleton'
import { PageHead } from '@/design/PageHead'
import { cn } from '@/design/cn'
import { useSearch } from '@/app/search'
import { ActiveFilters } from './ActiveFilters'
import { ChannelEditor } from '@/features/channels/ChannelEditor'
import { ChannelMenu } from './ChannelMenu'
import { Pagination } from './Pagination'
import { Toolbar } from './Toolbar'
import { collectLibrary } from './collect'
import { SelectionBar } from './SelectionBar'
import { NO_LIBRARY_VIDEOS, collecting, type Player } from './shuffle'
import { useShuffle } from './useShuffle'
import { useSelection } from './selection'
import { Reveal } from '@/design/Reveal'
import { VideoCard, VideoCardSkeleton } from './VideoCard'
import { useDebounced } from './useDebounced'
import { VIDEO_CARD, VIDEO_TARGET, useScreenGrid } from './screenGrid'
import { useLibraryStore } from './store'
import { SpinCounter } from '@/design/SpinCounter'
import { SfwHome } from '@/features/series/HomePage'
import { useLabels } from '@/features/content/labels'

const GRID = 'grid gap-4'

/** Videos. In SFW this is the Home page for movies and shows (Home and Discover in one: features/series/HomePage.tsx); otherwise the library of videos. */
export function LibraryPage() {
  const labels = useLabels()
  return labels.movies ? <SfwHome /> : <VideoLibrary />
}

function VideoLibrary() {
  const labels = useLabels()
  const { channels: picked, page, sort, favorites, filters, per: nominalPer, toggle, clear, setPage, setPer, resetFilters } = useLibraryStore()
  const grid = useScreenGrid(nominalPer <= 12 ? 0 : nominalPer, 16, VIDEO_CARD, VIDEO_TARGET)   // Auto: as many cards as the screen holds (two rows at least)
  const per = grid.per
  const selecting = useSelection((st) => st.active)
  useEffect(() => () => useSelection.getState().exit(), [])   // leaving the page ends select mode
  const search = useDebounced(useSearch((s) => s.q).trim(), 300)
  const channels = useChannels()
  const qc = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)
  const [editChannel, setEditChannel] = useState<string | null>(null)
  const refresh = async () => {
    setRefreshing(true)
    try {
      await api('/api/telegram/refresh', { method: 'POST' })
      await qc.invalidateQueries({ queryKey: ['channels'] })
      toast('Channel list refreshed')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.detail || 'Could not refresh channels' : 'Could not refresh channels')
    } finally {
      setRefreshing(false)
    }
  }
  const shuffle = useShuffle()
  const shuffleAll = (player: Player) => shuffle.run(player, async () => {
    if (!selected.length) return []
    const id = toast.loading(collecting(0))
    try {
      const { page: _p, per: _n, ...rest } = query
      return await collectLibrary(rest, (n) => toast.loading(collecting(n), { id }))
    } finally { toast.dismiss(id) }
  }, NO_LIBRARY_VIDEOS)
  const selected = useMemo(() => (picked.length ? picked : (channels.data ?? []).map((c) => c.telegram_id)), [picked, channels.data])   // nothing picked means every channel
  const query: LibraryQuery = useMemo(() => ({ channels: selected, page, per, sort, favorites, q: search, ...filters }), [selected, page, per, sort, favorites, filters, search])
  const library = useLibrary(query)
  const pages = library.data ? Math.max(1, Math.ceil(library.data.total / per)) : 1
  // A wider window means fewer pages. Only once the list has arrived: while it loads there is no page count yet, and treating that as one page sent a returning visitor from page 10 back to page 1.
  useEffect(() => { if (library.data && page > pages - 1) setPage(pages - 1) }, [library.data, page, pages, setPage])

  return (
    <section className="flex w-full flex-1 flex-col px-4 pb-10 pt-6 sm:px-10">
      <PageHead title={labels.videos} blurb={labels.videosBlurb} />
      <>
      <Toolbar lead={<ChannelMenu channels={channels.data ?? []} selected={picked} onToggle={toggle} onAll={clear} onEdit={setEditChannel} />} onRefresh={() => void refresh()} refreshing={refreshing} onShuffle={(pl) => void shuffleAll(pl)} shuffling={shuffle.busy} />

      {editChannel ? <ChannelEditor id={editChannel} onClose={() => setEditChannel(null)} /> : null}

      {channels.isSuccess && !selected.length ? (
        <div className="grid place-items-center gap-2 py-16 text-center" data-testid="no-channels">
          <h2 className="text-xl font-extrabold">No videos in this mode yet</h2>
          <p className="max-w-md text-sm text-muted">The library shows only channels and folders you have put in it for the current mode.</p>
          <ModeEmptyNote />
        </div>
      ) : library.isError ? (
        <div className="rounded-card border border-bad/40 p-6" role="alert">
          <p className="font-semibold">Could not load the library.</p>
          <p className="mb-3 text-sm text-muted">{library.error instanceof ApiError ? library.error.detail : 'Network error'}</p>
          <Button onClick={() => library.refetch()}>Retry</Button>
        </div>
      ) : (
        <Reveal ready={!library.isPending} skeleton={(
          <SkeletonGroup label="Loading videos" className={GRID} ref={grid.ref} style={grid.style}>
            {Array.from({ length: grid.cols * grid.rows }, (_, i) => (
              <VideoCardSkeleton key={i} index={i} />
            ))}
          </SkeletonGroup>
        )}>
        {library.data ? (<>
          <h2 className="mb-4 flex items-baseline justify-between gap-2.5 text-xl font-extrabold tracking-tight">
            Library
            <span className="text-[13px] font-normal tracking-normal text-muted">
              <SpinCounter value={library.data.total} /> videos · {selected.length} channel{selected.length === 1 ? '' : 's'}
            </span>
          </h2>
          <ActiveFilters />
          <div ref={grid.ref} style={grid.style} className={cn(GRID, library.isPlaceholderData && 'opacity-60 transition-opacity')}>
            <AnimatePresence mode="popLayout">
              {library.data.items.map((item, i) => (
                <motion.div key={`${item._cid}:${item.id}`} {...cardMotion(i)}>
                  <VideoCard item={item} />
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
          {library.data.items.length === 0 ? (
            <div className="grid place-items-center gap-3 py-12 text-center">
              <p className="text-muted">No videos match these filters.</p>
              <ModeEmptyNote />
              <Button variant="primary" onClick={resetFilters}>Reset filters</Button>
            </div>
          ) : null}
          {selecting ? <SelectionBar visible={library.data.items} /> : null}
          {library.data.total > 0 ? <Pagination page={page} pages={pages} per={nominalPer} onPage={setPage} onPer={setPer} auto={grid.per} /> : null}
        </>) : null}
        </Reveal>
      )}
      </>
    </section>
  )
}
