import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { useChannels, useLibrary } from '@/api/hooks'
import type { LibraryQuery } from '@/api/types'
import { Button } from '@/design/Button'
import { AnimatePresence, cardMotion, motion } from '@/design/motion'
import { SkeletonGroup } from '@/design/Skeleton'
import { Icon } from '@/design/Icon'
import { PageHead } from '@/design/PageHead'
import { cn } from '@/design/cn'
import { useSearch } from '@/app/search'
import { ActiveFilters } from './ActiveFilters'
import { ChannelEditor } from '@/features/channels/ChannelEditor'
import { ChannelChip, initialsOf } from './ChannelChip'
import { Pagination } from './Pagination'
import { Toolbar } from './Toolbar'
import { collectLibrary } from './collect'
import { SelectionBar } from './SelectionBar'
import { NO_LIBRARY_VIDEOS, collecting, type Player } from './shuffle'
import { useShuffle } from './useShuffle'
import { useSelection } from './selection'
import { VideoCard, VideoCardSkeleton } from './VideoCard'
import { useDebounced } from './useDebounced'
import { useLibraryStore } from './store'

const GRID = 'grid grid-cols-1 gap-4 min-[520px]:grid-cols-2 min-[900px]:grid-cols-3 min-[1200px]:grid-cols-4 min-[1700px]:grid-cols-5 min-[2100px]:grid-cols-6'

export function LibraryPage() {
  const { channels: selected, page, sort, favorites, filters, per, toggle, selectAll, setPage, setPer, setFavorites, resetFilters } = useLibraryStore()
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
  const query: LibraryQuery = useMemo(() => ({ channels: selected, page, per, sort, favorites, q: search, ...filters }), [selected, page, per, sort, favorites, filters, search])
  const library = useLibrary(query)
  const allIds = (channels.data ?? []).map((c) => c.telegram_id)
  const pages = library.data ? Math.max(1, Math.ceil(library.data.total / per)) : 1

  return (
    <section className="mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
      <PageHead title="Videos" blurb="Browse and manage your video library" />

      <Toolbar onRefresh={() => void refresh()} refreshing={refreshing} onShuffle={(pl) => void shuffleAll(pl)} shuffling={shuffle.busy} />

      <div className="mb-3 flex gap-3.5 overflow-x-auto pb-2" role="group" aria-label="Channels">
        <ChannelChip label="All" count={channels.data?.reduce((n, c) => n + c.video_count, 0)} on={selected.length === allIds.length && allIds.length > 0} disabled={!allIds.length} onClick={() => selectAll(allIds)}>
          <Icon name="grid" className="size-5" />
        </ChannelChip>
        {channels.isPending ? <span className="self-center text-sm text-muted">Loading channels…</span> : null}
        <ChannelChip label="Favourites" on={favorites} onClick={() => setFavorites(!favorites)} tone="amber">
          <Icon name="star" className="size-5" />
        </ChannelChip>
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
        <button type="button" disabled={selected.length !== 1} onClick={() => setEditChannel(selected[0] ?? null)} title={selected.length === 1 ? 'Edit this channel' : 'Select one channel'} aria-label="Edit selected channel" className="inline-flex h-8 items-center gap-1.5 rounded-full border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5 disabled:opacity-50"><Icon name="edit" className="size-3.5" />Edit</button>
        {editChannel ? <ChannelEditor id={editChannel} onClose={() => setEditChannel(null)} /> : null}
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
          <ActiveFilters />
          <div className={cn(GRID, library.isPlaceholderData && 'opacity-60 transition-opacity')}>
            <AnimatePresence mode="popLayout" initial={false}>
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
              <Button variant="primary" onClick={resetFilters}>Reset filters</Button>
            </div>
          ) : null}
          {selecting ? <SelectionBar visible={library.data.items} /> : null}
          {library.data.total > 0 ? <Pagination page={page} pages={pages} per={per} onPage={setPage} onPer={setPer} /> : null}
        </>
      )}
    </section>
  )
}
