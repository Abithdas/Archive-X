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
import { PageHead } from '@/design/PageHead'
import { Skeleton } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import { ChannelChip } from '@/features/library/ChannelChip'
import { useDebounced } from '@/features/library/useDebounced'
import { useFaceGroups } from './FaceGroups'
import { PeopleRow } from './PeopleRow'
import { ContentTags } from './ContentTags'
import { Empty } from '@/design/Empty'
import { motion, spring } from '@/design/motion'
import { PeopleView } from './PeopleView'
import { FaceReview } from './FaceReview'
import { DIFFERENT_NEEDS_GROUP, differentBody, differentCopy, filterLabel } from './faces'
import { ChannelEditor } from '@/features/channels/ChannelEditor'
import { PhotoCard } from './PhotoCard'
import { PhotoViewer } from './PhotoViewer'
import { EMPTY_INDEX, LOAD_FAILED, NO_CHANNELS_SAVED, NO_SCANNED, SELECT_PROMPT, SORTS, allSelected, channelAvatar, channelSummary, channelTitle, deleteBody, deleteCopy, deleteDone, deletePath, deleteStatus, emptyText, initials, photoName, photosUrl, pruneSelection, scanTitle, selectedLabel, sortLabel, summaryText, toggleAll, toggleId, type Photo, type PhotoChannel, type PhotoPage, type PhotoSort } from './photos'
import { usePhotoStore } from './store'

const PILL = 'inline-flex h-10 items-center gap-2 rounded-full border border-line-2 bg-surface px-4 text-sm font-semibold hover:bg-fg/5'
const reason = (e: unknown, fb: string) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : fb)

function Avatar({ channel }: { channel: PhotoChannel }) {
  const [ok, setOk] = useState(true)
  const t = channelTitle(channel)
  return ok ? <img src={channelAvatar(channel)} alt="" loading="lazy" onError={() => setOk(false)} className="size-full rounded-full object-cover" /> : <span aria-hidden="true">{initials(t)}</span>
}

export function PhotosPage() {
  const { selected, sort, favoritesOnly, favorites, tab, setTab, face, contentTags, setFace, setSelected, setSort, setFavoritesOnly, toggleFavorite } = usePhotoStore()
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
  const faces = useFaceGroups(selected, true)
  const [different, setDifferent] = useState<Photo | null>(null)

  // A channel that is no longer scanned cannot stay selected.
  useEffect(() => { if (channels.data) { const keep = pruneSelection(selected, list); if (keep.length !== selected.length) setSelected(keep) } }, [channels.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const favIds = useMemo(() => favorites, [favorites])
  const q = useInfiniteQuery({
    queryKey: ['photos', selected, search, sort, favoritesOnly, favoritesOnly ? [...favIds].sort() : null, face, contentTags] as const,
    enabled: selected.length > 0 && tab === 'library',
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const r = await api<PhotoPage>(photosUrl({ channels: selected, q: search, sort, favorites: favoritesOnly, favoriteIds: favIds, page: pageParam, face, contentTags }))
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
    : note || (channels.data && !list.length ? (channels.data.savedCount ? NO_SCANNED : NO_CHANNELS_SAVED) : !selected.length ? SELECT_PROMPT : q.isSuccess && !total ? EMPTY_INDEX : '')

  return (
    <section className="mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
      <PageHead title="Photos" blurb="Browse and organize images discovered in your Telegram channels." />

      <div role="tablist" aria-label="Photos views" className="mb-4 inline-flex gap-0.5 rounded-xl border border-line bg-surface p-[3px]">
        {([['library', 'Photos'], ['faces', 'People']] as const).map(([v, label]) => (
          <button key={v} type="button" role="tab" aria-selected={tab === v} onClick={() => setTab(v)} className={cn('relative h-9 rounded-[9px] px-4 text-sm font-semibold transition-colors', tab === v ? 'text-fg' : 'text-muted hover:text-fg')}>
            {tab === v ? <motion.span layoutId="photos-view-pill" transition={spring} aria-hidden="true" className="absolute inset-0 rounded-[9px] bg-surface-3" /> : null}
            <span className="relative">{label}</span>
          </button>
        ))}
      </div>

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
      <div className="mb-4 flex flex-wrap items-center gap-2.5" aria-label="Photo library tools" role="group">
        <label className={cn(PILL, 'relative min-w-[154px] cursor-pointer pr-9')}>
          <Icon name="sort" />
          <span>{sortLabel(sort)}</span>
          <Icon name="chevron-down" className="absolute right-3 size-4 text-muted" />
          <select value={sort} onChange={(e) => setSort(e.target.value as PhotoSort)} aria-label="Sort photos" className="absolute inset-0 cursor-pointer opacity-0">
            {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </label>
        <button type="button" aria-pressed={favoritesOnly} onClick={() => setFavoritesOnly(!favoritesOnly)} title="Show only photos starred in this browser" className={cn(PILL, favoritesOnly && 'border-warn text-warn')}>
          <span aria-hidden="true">{favoritesOnly ? '★' : '☆'}</span>{favoritesOnly ? 'Showing favourites' : 'Favourites'}
        </button>
        <button type="button" onClick={() => void refresh()} aria-label="Refresh photo library" title="Refresh channels and the indexed photo library" className={cn(PILL, 'w-10 justify-center px-0')}><Icon name="refresh" /></button>
      </div>

      <section aria-label="Photo channels" className="mb-4">
        <div className="mb-2 flex flex-wrap items-end justify-between gap-2"><div><h2 className="text-lg font-bold">Channels</h2><p className="text-xs text-muted">Select one or more channels to browse their images</p></div><span className="text-xs font-semibold text-muted">{channels.isPending ? 'Loading channels…' : channelSummary(list.length)}</span></div>
        <div className="flex gap-3.5 overflow-x-auto pb-2" role="group" aria-label="Channels">
          <ChannelChip label="All" count={list.reduce((n, c) => n + Number(c.photo_count || 0), 0)} on={allSelected(list, selected)} disabled={!list.length} onClick={() => setSelected(toggleAll(list, selected))}><Icon name="grid" className="size-5" /></ChannelChip>
          {list.map((c) => { const id = String(c.telegram_id); return <ChannelChip key={id} label={channelTitle(c)} count={Number(c.photo_count || 0)} on={selected.includes(id)} onClick={() => setSelected(toggleId(selected, id))}><span title={scanTitle(c)} className="grid size-full place-items-center overflow-hidden rounded-full"><Avatar channel={c} /></span></ChannelChip> })}
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          {selected.length ? <span className="rounded-full bg-accent/15 px-2.5 py-1 text-xs font-semibold text-accent">{selectedLabel(selected.length)}</span> : null}
          <button type="button" disabled={selected.length !== 1} onClick={() => setEditChannel(selected[0] ?? null)} title={selected.length === 1 ? 'Edit this channel' : 'Select one channel'} aria-label="Edit selected channel" className="inline-flex h-8 items-center gap-1.5 rounded-full border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5 disabled:opacity-50"><Icon name="edit" className="size-3.5" />Edit</button>
          {editChannel ? <ChannelEditor id={editChannel} onClose={() => setEditChannel(null)} /> : null}
          <span role="status" aria-live="polite" className="text-xs text-muted">{status}</span>
        </div>
      </section>

      <>
      <ContentTags />
      <PeopleRow groups={faces.data?.groups ?? []} onManage={() => setTab('faces')} />
      <div className="mb-3 flex flex-wrap items-center gap-3"><h2 className="text-lg font-bold">Photos</h2><span className="text-xs text-muted">{summaryText(total)}{selected.length ? ` · ${selected.length.toLocaleString()} channel${selected.length === 1 ? '' : 's'}` : ''}</span>
        {face ? <span className="inline-flex items-center gap-2 rounded-full bg-accent/15 px-3 py-1 text-xs font-semibold text-accent">{filterLabel(face)}<button type="button" onClick={() => setFace(null)} className="rounded-full border border-accent/40 px-2 py-0.5 hover:bg-accent/10">Clear group filter</button></span> : null}</div>
      {!selected.length ? null : q.isPending ? (
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

      </>
      </div>
      {viewer !== null && items[viewer] ? <PhotoViewer items={items} index={viewer} total={total} favorites={favorites} onIndex={setViewer} onClose={() => setViewer(null)} onFavorite={toggleFavorite} onNeedMore={more} /> : null}
      {different ? <Confirm title="Different person?" text={differentCopy} ok="Separate photo" danger busy={busy} onCancel={() => setDifferent(null)} onOk={() => void separate()} /> : null}
      {ask ? (() => { const c = deleteCopy(ask.remote, photoName(ask.photo), ask.photo.channel_title || 'this channel'); return <Confirm title={c.title} text={c.text} ok={c.ok} danger busy={busy} onCancel={() => setAsk(null)} onOk={() => void remove()} /> })() : null}
    </section>
  )
}

