import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/api/client'
import { usePerformerGallery } from '@/api/hooks'
import { Button } from '@/design/Button'
import { Dialog } from '@/design/Dialog'
import { Empty } from '@/design/Empty'
import { PhotoViewer } from '@/features/photos/PhotoViewer'
import { cn } from '@/design/cn'
import { usePerformerPhotos } from './PerformerPhotos'
import { indexAfterRemoval, orderGallery, type GalleryEntry } from './unifiedGallery'
import { GALLERY_EMPTY, fromVideosNote, galleryStatus, lastSyncText, referenceNote, type FromVideosResult } from './gallery'

/**
 * The performer's gallery: every picture the providers hold of them, plus clear faces found in their own videos' snapshots, kept on this PC. They are also what the People check compares photo
 * faces with. Removing a picture ("not this performer") takes it out for good: it is not fetched again and stops being used for matching.
 */
export function PerformerGallery({ id, name }: { id: number; name: string }) {
  const q = usePerformerGallery(id)
  const qc = useQueryClient()
  const refresh = useMutation({
    mutationFn: () => api<{ started: boolean }>(`/api/performers/${id}/gallery/refresh`, { method: 'POST', json: {} }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['performer-gallery', id] }),
  })
  const [videoNote, setVideoNote] = useState('')
  const fromVideos = useMutation({
    mutationFn: () => api<FromVideosResult>(`/api/performers/${id}/gallery/from-videos`, { method: 'POST', json: {} }),
    onSuccess: (r) => { setVideoNote(fromVideosNote(r)); void qc.invalidateQueries({ queryKey: ['performer-gallery', id] }) },
    onError: () => setVideoNote('Could not look through the videos. Try again.'),
  })
  const done = (msg: string) => { toast(msg); void qc.invalidateQueries({ queryKey: ['performer', id] }); void qc.invalidateQueries({ queryKey: ['entities'] }) }
  const useAsProfile = useMutation({
    mutationFn: (e: GalleryEntry) => e.galleryId
      ? api(`/api/performers/${id}/gallery/${e.galleryId}/use-as-profile`, { method: 'POST', json: {} })
      : api(`/api/performers/${id}/photos/use-as-profile`, { method: 'POST', json: { channel_id: e.photo.channel_id, message_id: e.photo.message_id } }),
    onSuccess: () => done(`New profile picture for ${name}`),
  })
  const remove = useMutation({
    mutationFn: (e: GalleryEntry) => e.galleryId
      ? api(`/api/performers/${id}/gallery/${e.galleryId}`, { method: 'DELETE' })
      : api(`/api/performers/${id}/photos/remove`, { method: 'POST', json: { channel_id: e.photo.channel_id, message_id: e.photo.message_id } }),
    onSuccess: (_r, e) => { void qc.invalidateQueries({ queryKey: [e.galleryId ? 'performer-gallery' : 'performer-photos', id] }) },
  })
  const photos = usePerformerPhotos(id, name)
  const entries = useMemo(() => orderGallery(photos.items, q.data?.items ?? [], name), [photos.items, q.data, name])
  const [open, setOpen] = useState<number | null>(null)
  // Select several pictures that are not this person and remove them together.
  const [selecting, setSelecting] = useState(false)
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set())
  const [confirm, setConfirm] = useState(false)
  const [removing, setRemoving] = useState(false)
  const toggle = (key: string) => setPicked((s) => { const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n })
  const stopSelecting = () => { setSelecting(false); setPicked(new Set()); setConfirm(false) }
  const removePicked = async () => {
    const chosen = entries.filter((e) => picked.has(e.key))
    setRemoving(true)
    const results = await Promise.allSettled(chosen.map((e) => remove.mutateAsync(e)))
    const failed = results.filter((r) => r.status === 'rejected').length
    setRemoving(false); setConfirm(false)
    if (failed) toast.error(`${failed} of ${chosen.length} could not be removed.`)
    else toast(`Removed ${chosen.length} picture${chosen.length === 1 ? '' : 's'} that ${chosen.length === 1 ? 'is' : 'are'} not ${name}`)
    stopSelecting()
  }
  const thumbs = useRef(new Map<number, HTMLElement>())
  const originOf = useCallback((i: number) => thumbs.current.get(i)?.getBoundingClientRect() ?? null, [])

  if (q.isPending) return <p role="tabpanel" aria-label="Gallery" className="py-8 text-center text-sm text-muted">Loading the gallery…</p>
  if (q.isError) return <Empty role="tabpanel" aria-label="Gallery" icon="user">The gallery could not be loaded.</Empty>
  const g = { ...q.data, items: Array.isArray(q.data?.items) ? q.data.items : [] }
  const busy = g.syncing || refresh.isPending
  return (
    <div role="tabpanel" aria-label="Gallery" className="grid gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">{galleryStatus(g)}</p>
          <p className="text-[13px] text-muted" role="status">{busy ? 'Downloading from the providers…' : lastSyncText(g.last_synced_at, g.last_result)}</p>
        </div>
        {entries.length ? (selecting
          ? <><Button onClick={() => setPicked(new Set(entries.map((e) => e.key)))}>Select all</Button><Button variant="primary" disabled={!picked.size} onClick={() => setConfirm(true)}>{picked.size ? `Remove ${picked.size}` : 'Remove'}</Button><Button onClick={stopSelecting}>Cancel</Button></>
          : <Button onClick={() => setSelecting(true)}>Select pictures</Button>) : null}
        <Button disabled={fromVideos.isPending} onClick={() => fromVideos.mutate()}>{fromVideos.isPending ? 'Looking through videos…' : 'Find pictures in videos'}</Button>
        <Button variant="primary" disabled={busy} onClick={() => refresh.mutate()}>
          {busy ? 'Downloading…' : g.items.length ? 'Get new pictures' : 'Get pictures from the providers'}
        </Button>
      </div>
      {videoNote ? <p role="status" className="text-sm text-muted">{videoNote}</p> : null}
      {useAsProfile.isError ? <p role="alert" className="text-sm text-warn">Could not set the profile picture. Try again.</p> : null}
      {refresh.isError ? <p role="alert" className="text-sm text-warn">Could not start the download. Try again.</p> : null}
      {remove.isError ? <p role="alert" className="text-sm text-warn">Could not remove that picture. Try again.</p> : null}
      {entries.length ? (
        <>
          <ul aria-label={`Pictures of ${name}`} className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6 2xl:grid-cols-8">
            {entries.map((e, i) => (
              <li key={e.key} className={cn('group relative overflow-hidden rounded-xl border bg-surface', picked.has(e.key) ? 'border-accent ring-2 ring-accent' : 'border-line')}>
                <button type="button" ref={(el) => { if (el) thumbs.current.set(i, el); else thumbs.current.delete(i) }} onClick={() => (selecting ? toggle(e.key) : setOpen(i))} aria-label={selecting ? `${picked.has(e.key) ? 'Unselect' : 'Select'} picture: ${e.label}` : `Open picture: ${e.label}`} aria-pressed={selecting ? picked.has(e.key) : undefined} title={e.galleryId ? referenceNote({ faces: e.faces ?? 1, reference: Boolean(e.reference) }) : undefined} className="block aspect-[3/4] w-full">
                  <img src={e.photo.thumbnail_url} alt="" loading="lazy" draggable={false} className="size-full object-cover transition-transform duration-200 hover:scale-[1.03]" />
                </button>
                {selecting ? <span aria-hidden="true" className={cn('pointer-events-none absolute left-1.5 top-1.5 grid size-6 place-items-center rounded-full border text-xs font-bold', picked.has(e.key) ? 'border-accent bg-accent text-accent-fg' : 'border-white/70 bg-black/40 text-transparent')}>✓</span>
                  : <button type="button" disabled={useAsProfile.isPending} onClick={() => useAsProfile.mutate(e)} aria-label={`Use as profile picture: ${e.label}`} title="Use as the profile picture" className="absolute right-1.5 top-1.5 grid h-7 place-items-center rounded-full bg-black/60 px-2 text-[11px] font-semibold text-white backdrop-blur-sm hover:bg-black/80 focus-visible:opacity-100 disabled:opacity-40 sm:opacity-0 sm:group-hover:opacity-100">Profile picture</button>}
                <span className="pointer-events-none absolute bottom-1 left-1 right-1 flex items-center gap-1 truncate rounded-full bg-black/60 px-2 py-0.5 text-[10.5px] font-semibold text-white backdrop-blur">
                  <span aria-hidden="true" className={cn('size-1.5 shrink-0 rounded-full', e.reference ? 'bg-accent' : 'bg-white/50')} /><span className="truncate">{e.source === 'photo' ? 'Your photos' : e.label}</span>
                </span>
              </li>
            ))}
          </ul>
          {photos.q.hasNextPage ? <div className="flex justify-center"><Button disabled={photos.q.isFetchingNextPage} onClick={photos.more}>{photos.q.isFetchingNextPage ? 'Loading…' : 'Show more photos'}</Button></div> : null}
        </>
      ) : photos.q.isPending ? <p role="status" className="py-3 text-sm text-muted">Looking for {name} in your photos…</p> : <Empty icon="user">{GALLERY_EMPTY}</Empty>}
      {confirm ? (
        <Dialog open onClose={() => setConfirm(false)} title={`Remove ${picked.size} picture${picked.size === 1 ? '' : 's'}?`}>
          <p className="text-sm text-muted">These are not {name}. They stop being shown here and are no longer used to recognise {name}. Nothing is deleted from your photos.</p>
          <div className="mt-4 flex justify-end gap-2"><Button onClick={() => setConfirm(false)}>Keep them</Button><Button variant="primary" disabled={removing} onClick={() => void removePicked()}>{removing ? 'Removing…' : 'Remove'}</Button></div>
        </Dialog>
      ) : null}
      {open !== null && entries[open] ? (
        <PhotoViewer
          items={entries.map((e) => e.photo)} index={open} total={photos.total + g.items.length} onIndex={setOpen} onClose={() => setOpen(null)} onNeedMore={photos.more} origin={originOf}
          actions={() => {
            const e = entries[open]
            if (!e) return null
            const BTN = 'rounded-full px-3 py-1.5 font-semibold text-white hover:bg-white/15 disabled:opacity-40'
            return (
              <>
                <span className="rounded-full bg-white/15 px-2.5 py-1 font-semibold" title="Where this picture comes from">{e.label}</span>
                <button type="button" className={BTN} disabled={useAsProfile.isPending} onClick={() => useAsProfile.mutate(e)} aria-label={`Use this picture as the profile picture of ${name}`}>Use as profile picture</button>
                <button type="button" className={cn(BTN, 'hover:!bg-warn/30')} disabled={remove.isPending} aria-label={`Remove this picture: not ${name}`} title="Not this performer"
                  onClick={() => remove.mutate(e, { onSuccess: () => { const left = entries.length - 1; const next = indexAfterRemoval(open, left); setOpen(next) } })}>Remove</button>
              </>
            )
          }}
        />
      ) : null}
    </div>
  )
}
