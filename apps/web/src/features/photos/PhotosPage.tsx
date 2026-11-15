import { aspectOf, assignColumns } from './masonry'
import { useColumnCount } from './useColumnCount'
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { usePhotoChannels } from '@/api/hooks'
import { useSearch } from '@/app/search'
import { Confirm } from '@/design/Confirm'
import { Icon } from '@/design/Icon'
import { Skeleton } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import { Popover, PILL } from './Popover'
import { useDebounced } from '@/features/library/useDebounced'
import { useFaceGroups } from './useFaceGroups'
import { PeopleRow } from './PeopleRow'
import { ContentTags, useContentSummary } from './ContentTags'
import { Empty } from '@/design/Empty'
import { motion, spring } from '@/design/motion'
import { PeopleView } from './PeopleView'
import { FaceReview } from './FaceReview'
import { DIFFERENT_NEEDS_GROUP, differentBody, differentCopy } from './faces'
import { ChannelEditor } from '@/features/channels/ChannelEditor'
import { PhotoCard } from './PhotoCard'
import { PhotoViewer } from './PhotoViewer'
import { EMPTY_INDEX, LOAD_FAILED, NO_CHANNELS_SAVED, NO_SCANNED, SORTS, channelAvatar, channelTitle, deleteBody, deleteCopy, deleteDone, deletePath, deleteStatus, emptyText, initials, photoName, photosUrl, pruneSelection, scanTitle, sortLabel, toggleId, type Photo, type PhotoChannel, type PhotoPage, type PhotoSort } from './photos'
import { usePhotoStore } from './store'
import { LikeHeart } from '@/design/LikeHeart'
import { SpinCounter } from '@/design/SpinCounter'

const Tick = () => <span aria-label="selected" role="img" className="grid size-5 place-items-center rounded-full bg-good text-[11px] font-bold text-white">✓</span>
const reason = (e: unknown, fb: string) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : fb)

function Avatar({ channel }: { channel: PhotoChannel }) {
  const [ok, setOk] = useState(true)
  const t = channelTitle(channel)
  return ok ? <img src={channelAvatar(channel)} alt="" loading="lazy" onError={() => setOk(false)} className="size-full rounded-full object-cover" /> : <span aria-hidden="true">{initials(t)}</span>
}

export function PhotosPage() {
  const { selected, sort, favoritesOnly, favorites, tab, setTab, face, contentTags, setContentTags, setFace, setSelected, setSort, setFavoritesOnly, toggleFavorite } = usePhotoStore()
  const search = useDebounced(useSearch((s) => s.q).trim(), 300)
  const channels = usePhotoChannels()
  const qc = useQueryClient()
  const list = channels.data?.channels ?? []
  const [viewer, setViewer] = useState<number | null>(null)
  const [ask, setAsk] = useState<null | { photo: Photo; remote: boolean }>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [editChannel, setEditChannel] = useState<string | null>(null)
  const sentinel = useRef<HTMLDivElement>(null)
  const [checking, setChecking] = useState(false)
  const effective = useMemo(() => (selected.length ? selected : list.map((c) => String(c.telegram_id))), [selected, list])
  const hasContent = Boolean(useContentSummary().data?.total_photos)
  const channelButton = !selected.length ? 'All channels' : selected.length === 1 ? channelTitle(list.find((c) => String(c.telegram_id) === selected[0]) ?? { telegram_id: selected[0]! }) : `${selected.length} channels`
  const faces = useFaceGroups(selected, true, false, 24)   // the busiest 24 people for the row, not everyone and not who they might be
  const [different, setDifferent] = useState<Photo | null>(null)

  // A channel that is no longer scanned cannot stay selected.
  useEffect(() => { if (channels.data) { const keep = pruneSelection(selected, list); if (keep.length !== selected.length) setSelected(keep) } }, [channels.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const favIds = useMemo(() => favorites, [favorites])
  const q = useInfiniteQuery({
    queryKey: ['photos', effective, search, sort, favoritesOnly, favoritesOnly ? [...favIds].sort() : null, face, contentTags] as const,
    enabled: effective.length > 0 && tab === 'library',
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const r = await api<PhotoPage>(photosUrl({ channels: effective, q: search, sort, favorites: favoritesOnly, favoriteIds: favIds, page: pageParam, face, contentTags }))
      if (!r || r.ok !== true) throw new ApiError(0, r?.detail || LOAD_FAILED)
      return r
    },
    getNextPageParam: (last, all) => (all.reduce((n, p) => n + p.items.length, 0) < last.total && last.items.length ? all.length : undefined),
    staleTime: 15_000,
  })
  const items = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data])
  const columnCount = useColumnCount()
  const columns = useMemo(() => assignColumns(items.map(aspectOf), columnCount), [items, columnCount])
  const total = q.data?.pages[0]?.total ?? 0
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = q
  const more = () => { if (hasNextPage && !isFetchingNextPage) void fetchNextPage() }

  useEffect(() => {
    const el = sentinel.current
    if (!el || typeof IntersectionObserver !== 'function') return
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) more() }, { rootMargin: '900px 0px' })
    io.observe(el)
    return () => io.disconnect()
  })

  const remove = async () => {
    if (!ask) return
    const { photo, remote } = ask
    setBusy(true); setNote(deleteStatus(remote, photoName(photo)))
    try {
      const r = await api<{ ok?: boolean; detail?: string; message?: string; cache_cleanup_warning?: string }>(deletePath(photo), { method: 'POST', json: deleteBody(remote) })
      if (!r || r.ok !== true) throw new ApiError(0, r?.detail || 'Photo deletion failed.')
      setAsk(null); setNote(deleteDone(remote, r)); toast(deleteDone(remote, r))
      await Promise.all([qc.invalidateQueries({ queryKey: ['photos'] }), qc.invalidateQueries({ queryKey: ['photo-channels'] })])
    } catch (e) { const m = reason(e, 'Photo deletion failed. The library entry was retained.'); setAsk(null); setNote(m); toast.error(m) } finally { setBusy(false) }
  }

  const separate = async () => {
    const p = different
    if (!p || !face) return
    const body = differentBody(p.id, face, faces.data?.groups ?? [])
    setDifferent(null)
    if (!body) return void setNote(DIFFERENT_NEEDS_GROUP)
    setBusy(true); setNote(`Separating ${photoName(p)} from the current face group…`)
    try {
      const r = await api<{ ok?: boolean; detail?: string; message?: string }>('/api/photos/faces/different-person', { method: 'POST', json: body })
      if (!r || r.ok !== true) throw new ApiError(0, r?.detail || 'Could not separate this photo from the face group.')
      const m = r.message || 'Moved the face detection(s) into new unassigned group(s).'
      setNote(m); toast(m)
      await Promise.all([qc.invalidateQueries({ queryKey: ['photos'] }), qc.invalidateQueries({ queryKey: ['face-groups'] })])
    } catch (e) { const m = reason(e, 'Could not separate this photo from the face group.'); setNote(m); toast.error(m) } finally { setBusy(false) }
  }
  const refresh = async () => { await Promise.all([qc.invalidateQueries({ queryKey: ['photo-channels'] }), qc.invalidateQueries({ queryKey: ['photos'] })]); toast('Photo library refreshed') }
  const empty = emptyText(search, favoritesOnly)
  const status = channels.isError ? reason(channels.error, 'Photo scan status is unavailable.')
    : note || (channels.data && !list.length ? (channels.data.savedCount ? NO_SCANNED : NO_CHANNELS_SAVED) : q.isSuccess && !total ? EMPTY_INDEX : '')

  return (
    <section className="mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
      <div className="mb-4 flex flex-wrap items-baseline gap-x-3"><h1 className="text-2xl font-bold">Photos</h1>{tab === 'library' ? <span role="status" aria-live="polite" className="text-sm text-muted">{channels.isPending ? 'Loading…' : status || <><SpinCounter value={total} /> image{total === 1 ? '' : 's'}</>}</span> : null}</div>

      <div className="mb-3 flex flex-wrap items-center gap-2.5" role="group" aria-label="Photo library tools">
        <div role="tablist" aria-label="Photos views" className="inline-flex gap-0.5 rounded-xl border border-line bg-surface p-[3px]">
          {([['library', 'Photos'], ['faces', 'People']] as const).map(([v, label]) => (
            <button key={v} type="button" role="tab" aria-selected={tab === v} onClick={() => setTab(v)} className={cn('relative h-9 rounded-[9px] px-4 text-sm font-semibold transition-colors', tab === v ? 'text-fg' : 'text-muted hover:text-fg')}>
              {tab === v ? <motion.span layoutId="photos-view-pill" transition={spring} aria-hidden="true" className="absolute inset-0 rounded-[9px] bg-surface-3" /> : null}
              <span className="relative">{label}</span>
            </button>
          ))}
        </div>
        {tab === 'library' ? (<>
          <Popover label={channelButton} icon={<Icon name="grid" className="size-4" />} active={selected.length > 0}>
            <div role="group" aria-label="Channels" className="grid gap-0.5">
              <button type="button" disabled={!list.length} onClick={() => setSelected([])} aria-pressed={!selected.length} className="flex items-center gap-3 rounded-xl px-2 py-1.5 text-left text-sm font-semibold hover:bg-fg/5">
                <span className="grid size-8 place-items-center rounded-full bg-surface-3"><Icon name="grid" className="size-4" /></span><span className="flex-1">All channels</span><span className="text-xs text-muted">{list.reduce((n, c) => n + Number(c.photo_count || 0), 0).toLocaleString()}</span>{!selected.length ? <Tick /> : <span className="size-5" />}
              </button>
              {list.map((c) => { const id = String(c.telegram_id); const on = selected.includes(id); return (
                <button key={id} type="button" aria-pressed={on} title={scanTitle(c)} onClick={() => setSelected(toggleId(selected, id))} className="flex items-center gap-3 rounded-xl px-2 py-1.5 text-left text-sm hover:bg-fg/5">
                  <span className="grid size-8 shrink-0 place-items-center overflow-hidden rounded-full bg-surface-3 text-xs font-bold"><Avatar channel={c} /></span><span className="min-w-0 flex-1 truncate">{channelTitle(c)}</span><span className="text-xs text-muted">{Number(c.photo_count || 0).toLocaleString()}</span>{on ? <Tick /> : <span className="size-5" />}
                </button>) })}
            </div>
            {selected.length === 1 ? <button type="button" onClick={() => setEditChannel(selected[0] ?? null)} aria-label="Edit selected channel" className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-full border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5"><Icon name="edit" className="size-3.5" />Edit channel</button> : null}
          </Popover>
          <label className={cn(PILL, 'relative cursor-pointer pr-9')}>
            <Icon name="sort" />
            <span>{sortLabel(sort)}</span>
            <Icon name="chevron-down" className="absolute right-3 size-4 text-muted" />
            <select value={sort} onChange={(e) => setSort(e.target.value as PhotoSort)} aria-label="Sort photos" className="absolute inset-0 cursor-pointer opacity-0">
              {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </label>
          {hasContent ? <Popover label="Filters" icon={<Icon name="filter" className="size-4" />} badge={contentTags.length} active={contentTags.length > 0}><ContentTags bare scope={{ channels: effective, q: search, favorites: favoritesOnly, favoriteIds: favIds, face, contentTags }} /></Popover> : null}
          <button type="button" aria-pressed={favoritesOnly} onClick={() => setFavoritesOnly(!favoritesOnly)} title="Show only photos starred in this browser" className={cn(PILL, favoritesOnly && 'border-warn text-warn')}>
            <LikeHeart liked={favoritesOnly} />Favourites
          </button>
          <button type="button" onClick={() => void refresh()} aria-label="Refresh photo library" title="Refresh channels and the indexed photo library" className={cn(PILL, 'w-10 justify-center px-0')}><Icon name="refresh" /></button>
        </>) : null}
      </div>
      {editChannel ? <ChannelEditor id={editChannel} onClose={() => setEditChannel(null)} /> : null}

      {tab === 'library' && face ? (
        <div role="region" aria-label="Photos of one person" className="mb-4 flex flex-wrap items-center gap-3 rounded-2xl border border-accent/30 bg-accent/10 px-4 py-3">
          <button type="button" onClick={() => { setFace(null); setTab('faces') }} className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg"><Icon name="arrow-left" className="size-4" />Back to People</button>
          <span className="min-w-0 text-sm font-semibold [overflow-wrap:anywhere]">{face.scope ? face.label : `Photos of ${face.label}`}</span>
          {!face.scope ? <button type="button" onClick={() => setChecking(true)} className="inline-flex h-9 items-center rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5">Check faces</button> : null}
          <button type="button" onClick={() => setFace(null)} className="ml-auto text-xs font-semibold text-accent hover:underline">Show all photos</button>
        </div>
      ) : null}
      {checking && face && !face.scope ? <FaceReview performerId={face.performerId} groupIds={face.groupIds.length ? face.groupIds : face.groupId && face.groupId > 0 ? [face.groupId] : []} label={face.label} onClose={() => setChecking(false)} /> : null}
      {tab === 'faces' ? <PeopleView /> : null}
      <div hidden={tab === 'faces'}>
      {contentTags.length ? (
        <div role="group" aria-label="Active filters" className="mb-3 flex flex-wrap items-center gap-2">
          {contentTags.map((t) => <button key={t} type="button" onClick={() => setContentTags(contentTags.filter((x) => x !== t))} aria-label={`Remove filter ${t}`} className="inline-flex h-7 items-center gap-1.5 rounded-full bg-accent/15 px-3 text-xs font-semibold text-accent">{t.replace(/[_-]/g, ' ')}<span aria-hidden="true">×</span></button>)}
          <button type="button" onClick={() => setContentTags([])} className="text-xs font-semibold text-accent hover:underline">Clear all</button>
        </div>
      ) : null}
      <PeopleRow groups={faces.data?.groups ?? []} onManage={() => setTab('faces')} />
      {!effective.length ? (channels.isPending ? null : <Empty icon="images" title="No photos yet">{status}</Empty>) : q.isPending ? (
        <div role="status" aria-label="Loading photos" className="columns-2 gap-2 sm:columns-3 sm:gap-3 lg:columns-4 xl:columns-6">{[236, 318, 188, 272, 214, 344, 198, 286].map((h, i) => <Skeleton key={i} style={{ height: h }} className="mb-2 break-inside-avoid rounded-xl sm:mb-3" />)}</div>
      ) : q.isError ? (
        <div role="alert" className="rounded-card border border-bad/40 p-6"><p className="font-semibold">Photo library could not be loaded.</p><p className="mt-1 text-sm text-muted">{reason(q.error, 'Check the Operations log and try again.')}</p><button type="button" onClick={() => void q.refetch()} className="mt-3 h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg">Retry</button></div>
      ) : !items.length ? (
        <Empty icon="images" title={empty.title}>{empty.body}</Empty>
      ) : (
        <div className="flex gap-2 sm:gap-3" aria-label="Photos in selected channels">
          {columns.map((col, c) => (
            <div key={c} className="flex min-w-0 flex-1 flex-col">
              {col.map((i) => { const p = items[i]!; return <PhotoCard key={`${p.channel_id}:${p.message_id}`} photo={p} favorite={favorites.has(String(p.id))} onOpen={() => setViewer(i)} onRemove={(remote) => setAsk({ photo: p, remote })} onDifferent={face ? () => setDifferent(p) : undefined} /> })}
            </div>
          ))}
        </div>
      )}
      <div ref={sentinel} aria-hidden="true" className="h-8" hidden={!hasNextPage}>{isFetchingNextPage ? <span className="mx-auto block size-5 animate-spin rounded-full border-2 border-line border-t-accent" /> : null}</div>
      {hasNextPage ? <div className="text-center"><button type="button" onClick={more} disabled={isFetchingNextPage} className="h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold">Load more</button></div> : null}
      </div>
      {viewer !== null && items[viewer] ? <PhotoViewer items={items} index={viewer} total={total} favorites={favorites} onIndex={setViewer} onClose={() => setViewer(null)} onFavorite={toggleFavorite} onNeedMore={more} /> : null}
      {different ? <Confirm title="Different person?" text={differentCopy} ok="Separate photo" danger busy={busy} onCancel={() => setDifferent(null)} onOk={() => void separate()} /> : null}
      {ask ? (() => { const c = deleteCopy(ask.remote, photoName(ask.photo), ask.photo.channel_title || 'this channel'); return <Confirm title={c.title} text={c.text} ok={c.ok} danger busy={busy} onCancel={() => setAsk(null)} onOk={() => void remove()} /> })() : null}
    </section>
  )
}

