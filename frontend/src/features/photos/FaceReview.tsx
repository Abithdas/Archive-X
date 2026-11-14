import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Dialog } from '@/design/Dialog'
import { cn } from '@/design/cn'
import { NamePicker } from './NamePicker'

export interface ReviewFace { detection_id: number; photo_id: number; group_id: number; crop_url: string; similarity: number }
/** A face this far below the rest of the person's faces is flagged as looking different. */
export const DIFFERENT_BELOW = 0.4
export const LEAST_SIMILAR_COUNT = 10
export const differentFaces = (items: readonly ReviewFace[]) => items.filter((f) => f.similarity < DIFFERENT_BELOW)
export const reviewUrl = (performerId: number | null | undefined, groupIds: readonly number[]) => performerId ? `/api/photos/faces/review?performer_id=${performerId}` : `/api/photos/faces/review?group_ids=${groupIds.join(',')}`
const why = (e: unknown, fb: string) => (e instanceof ApiError && e.detail ? e.detail : fb)

/** All the faces of one person at once, least alike first; tick the wrong ones and take them out together (or move them to the right person). */
export function FaceReview({ performerId, groupIds = [], label, onClose }: { performerId?: number | null; groupIds?: number[]; label: string; onClose: () => void }) {
  const qc = useQueryClient()
  const [items, setItems] = useState<ReviewFace[] | null>(null)
  const [total, setTotal] = useState(0)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [moving, setMoving] = useState(false)
  const [failed, setFailed] = useState('')

  const load = useCallback(async () => {
    setFailed('')
    try {
      const r = await api<{ items?: ReviewFace[]; total?: number }>(reviewUrl(performerId, groupIds))
      setItems(r.items ?? []); setTotal(r.total ?? r.items?.length ?? 0)
    } catch (e) { setFailed(why(e, 'Could not load the faces.')); setItems([]) }
  }, [performerId, groupIds.join(',')]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { void load() }, [load])

  const odd = useMemo(() => differentFaces(items ?? []), [items])
  const toggle = (id: number) => setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const pickLeast = () => setPicked(new Set((items ?? []).slice(0, LEAST_SIMILAR_COUNT).map((f) => f.detection_id)))
  const pickOdd = () => setPicked(new Set(odd.map((f) => f.detection_id)))

  const move = async (to?: { id: number; name: string }) => {
    const ids = [...picked]
    if (!ids.length) return
    setBusy(true)
    try {
      const r = await api<{ ok?: boolean; moved?: number; detail?: string }>('/api/photos/faces/detections/move', { method: 'POST', json: { detection_ids: ids, performer_id: to?.id ?? null } })
      if (!r?.ok) throw new ApiError(0, r?.detail || '')
      toast(to ? `Moved ${r.moved ?? ids.length} faces to ${to.name}` : `Took ${r.moved ?? ids.length} faces out of ${label}. They are under “Who are these?”`)
      setPicked(new Set()); setMoving(false)
      await Promise.all([qc.invalidateQueries({ queryKey: ['face-groups'] }), qc.invalidateQueries({ queryKey: ['photos'] })])
      await load()
    } catch (e) { toast.error(why(e, 'Could not move those faces.')) } finally { setBusy(false) }
  }

  const BTN = 'h-9 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
  return (
    <Dialog wide open onClose={onClose} title={`Check faces: ${label}`}>
      <div className="grid min-h-0 gap-3 overflow-hidden">
        <p className="text-sm text-muted">{items ? `${total.toLocaleString()} faces. The ones least like the rest come first. Tick any that are not ${label}.` : 'Loading faces…'}</p>
        {failed ? <p role="alert" className="text-sm text-bad">{failed}</p> : null}
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Quick picks">
          <button type="button" className={BTN} disabled={!items?.length} onClick={pickLeast}>Tick the {LEAST_SIMILAR_COUNT} least alike</button>
          <button type="button" className={BTN} disabled={!odd.length} onClick={pickOdd}>Tick all that look different ({odd.length})</button>
          <button type="button" className={BTN} disabled={!picked.size} onClick={() => setPicked(new Set())}>Clear</button>
          <span role="status" aria-live="polite" className="ml-auto text-sm font-semibold">{picked.size ? `${picked.size} ticked` : ''}</span>
        </div>
        <div role="group" aria-label="Faces" className="grid max-h-[46vh] auto-rows-min content-start grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-3 overflow-y-auto p-2">
          {(items ?? []).map((f) => {
            const on = picked.has(f.detection_id)
            return (
              <button key={f.detection_id} type="button" role="checkbox" aria-checked={on} aria-label={`Face ${f.detection_id}${f.similarity < DIFFERENT_BELOW ? ', looks different' : ''}`} onClick={() => toggle(f.detection_id)}
                className={cn('relative aspect-square shrink-0 overflow-hidden rounded-xl bg-surface-2 outline-none ring-2 ring-offset-2 ring-offset-surface transition focus-visible:ring-accent', on ? 'ring-bad' : f.similarity < DIFFERENT_BELOW ? 'ring-warn/60' : 'ring-transparent')}>
                <img src={f.crop_url} alt="" loading="lazy" decoding="async" className={cn('size-full object-cover transition', on && 'opacity-70')} onError={(e) => (e.currentTarget.style.visibility = 'hidden')} />
                {on ? <span aria-hidden="true" className="absolute right-1 top-1 grid size-5 place-items-center rounded-full bg-bad text-xs font-bold text-white">✕</span> : null}
              </button>
            )
          })}
        </div>
        {items && !items.length && !failed ? <p className="text-sm text-muted">No faces here.</p> : null}
        {total > (items?.length ?? 0) ? <p className="text-xs text-muted">Showing the {items?.length.toLocaleString()} least alike of {total.toLocaleString()}.</p> : null}
        <div className="flex flex-wrap items-start gap-2 border-t border-line pt-3">
          <button type="button" disabled={!picked.size || busy} onClick={() => void move()} className="h-9 rounded-xl bg-bad px-3 text-sm font-bold text-white disabled:opacity-50">{picked.size ? `Not ${label} (${picked.size})` : `Not ${label}`}</button>
          {moving ? <div className="w-[280px]"><NamePicker onPick={(p) => void move(p)} onCancel={() => setMoving(false)} /></div> : <button type="button" className={BTN} disabled={!picked.size || busy} onClick={() => setMoving(true)}>Move to someone else…</button>}
          <button type="button" className={cn(BTN, 'ml-auto')} onClick={onClose}>Done</button>
        </div>
      </div>
    </Dialog>
  )
}
