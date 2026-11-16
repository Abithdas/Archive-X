import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { usePerformerGallery } from '@/api/hooks'
import { Button } from '@/design/Button'
import { Empty } from '@/design/Empty'
import { ZoomImage } from '@/design/ImageZoom'
import { cn } from '@/design/cn'
import { GALLERY_EMPTY, galleryStatus, lastSyncText, providerName, referenceNote } from './gallery'

/**
 * The performer's gallery: every picture the providers hold of them, kept on this PC. They are also what the People check compares photo
 * faces with. Removing a picture ("not this performer") takes it out for good: it is not fetched again and stops being used for matching.
 */
export function PerformerGallery({ id, name }: { id: number; name: string }) {
  const q = usePerformerGallery(id)
  const qc = useQueryClient()
  const refresh = useMutation({
    mutationFn: () => api<{ started: boolean }>(`/api/performers/${id}/gallery/refresh`, { method: 'POST', json: {} }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['performer-gallery', id] }),
  })
  const remove = useMutation({
    mutationFn: (pictureId: number) => api(`/api/performers/${id}/gallery/${pictureId}`, { method: 'DELETE' }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['performer-gallery', id] }),
  })

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
        <Button variant="primary" disabled={busy} onClick={() => refresh.mutate()}>
          {busy ? 'Downloading…' : g.items.length ? 'Get new pictures' : 'Get pictures from the providers'}
        </Button>
      </div>
      {refresh.isError ? <p role="alert" className="text-sm text-warn">Could not start the download. Try again.</p> : null}
      {remove.isError ? <p role="alert" className="text-sm text-warn">Could not remove that picture. Try again.</p> : null}
      {g.items.length ? (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-7">
          {g.items.map((i) => (
            <li key={i.id} className="group relative overflow-hidden rounded-card border border-line bg-surface">
              <ZoomImage src={i.url} alt={`${name}, picture from ${providerName(i.provider)}`} loading="lazy" className="aspect-[3/4] w-full object-cover" />
              <div className="flex items-center justify-between gap-2 px-2 py-1.5 text-[11.5px] text-muted">
                <span title={referenceNote(i)} className={cn('flex min-w-0 items-center gap-1.5 truncate', i.reference ? 'font-semibold text-accent' : '')}>
                  <span aria-hidden="true" className={cn('size-1.5 shrink-0 rounded-full', i.reference ? 'bg-accent' : 'bg-line')} />{providerName(i.provider)}
                  <span className="sr-only">{i.reference ? ' (used for face matching)' : ''}</span>
                </span>
                <button type="button" disabled={remove.isPending} onClick={() => remove.mutate(i.id)} aria-label={`Remove this picture: not ${name}`} title="Not this performer: remove it and never fetch it again"
                  className="rounded px-1.5 py-0.5 font-semibold text-muted hover:bg-bg hover:text-warn">Remove</button>
              </div>
            </li>
          ))}
        </ul>
      ) : <Empty icon="user">{GALLERY_EMPTY}</Empty>}
    </div>
  )
}
