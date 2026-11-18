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
import { useFitGrid } from './fitGrid'
import { useLibraryStore } from './store'
import { SpinCounter } from '@/design/SpinCounter'
import { useLabels } from '@/features/content/labels'
import { MoviesList } from '@/features/series/MoviesPage'
import { SeriesList } from '@/features/series/SeriesPage'
import { ScreenTabs } from '@/features/series/ScreenTabs'
import { useScreenView } from '@/features/series/view'

const GRID = 'grid gap-4'

export function LibraryPage() {
  const labels = useLabels()
  const withScreen = Boolean(labels.movies)   // SFW: Movies and Series are parts of this page
  const view = useScreenView((st) => (withScreen ? st.view : 'all'))
  const { channels: picked, page, sort, favorites, filters, per: nominalPer, toggle, clear, setPage, setPer, resetFilters } = useLibraryStore()
  const grid = useFitGrid(270, 16)
  const per = grid.fit(nominalPer)   // whole rows of whatever the window shows, so the last row is full
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
  useEffect(() => { if (page > pages - 1) setPage(pages - 1) }, [page, pages, setPage])   // a wider window means fewer pages

  return (
    <section className="mx-auto flex w-full max-w-[calc(1800px/var(--ax-ui-zoom,1))] flex-1 flex-col pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
      <PageHead title={labels.videos} blurb={labels.videosBlurb} />
      {withScreen ? <ScreenTabs /> : null}
      {view === 'movies' ? <MoviesList /> : view === 'series' ? <SeriesList /> : null}
      {view !== 'all' ? null : <>
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
            {Array.from({ length: grid.cols * 2 }, (_, i) => (
              <VideoCardSkeleton key={i} index={i} />
            ))}
          </SkeletonGroup>
        )}>
        {library.data ? (<>
          <h2 className="mb-4 flex items-baseline gap-2.5 text-xl font-extrabold tracking-tight">
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
          {library.data.total > 0 ? <Pagination page={page} pages={pages} per={nominalPer} onPage={setPage} onPer={setPer} fit={grid.fit} /> : null}
        </>) : null}
        </Reveal>
      )}
      </>}
    </section>
  )
}
