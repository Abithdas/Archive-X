import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Dialog } from '@/design/Dialog'
import { cn } from '@/design/cn'
import { NamePicker } from './NamePicker'

export interface ReviewFace { detection_id: number; photo_id: number; group_id: number; crop_url: string; similarity: number; different?: boolean }
/** A face from another group that the learned profile says looks like this person. */
export interface Lookalike { detection_id: number; photo_id: number; group_id: number; crop_url: string; score: number; strong: boolean }
interface Learned { items?: Array<{ detection_id: number; score: number; different?: boolean }> }
/** A face this far below the rest of the person's faces is flagged as looking different. */
const DIFFERENT_BELOW = 0.4
const LEAST_SIMILAR_COUNT = 10
export const differentFaces = (items: readonly ReviewFace[]) => items.filter((f) => f.different ?? (f.different ?? f.similarity < DIFFERENT_BELOW))
/** Put what has been learned about the person (the desktop app's face learning) over the plain review: its score and its own line for "looks different", least alike first. */
export function withLearned(items: readonly ReviewFace[], learned: Learned | null | undefined): ReviewFace[] {
  const list = learned?.items
  if (!Array.isArray(list) || !list.length) return [...items]
  const byId = new Map(list.map((l) => [l.detection_id, l]))
  return items.map((f) => { const l = byId.get(f.detection_id); return l ? { ...f, similarity: l.score, different: Boolean(l.different) } : f }).sort((a, b) => a.similarity - b.similarity || a.detection_id - b.detection_id)
}
const learnedUrl = (kind: 'outliers' | 'suggestions', performerId: number) => `/api/photos/faces/learning/${kind}?performer_id=${performerId}`
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
  const [like, setLike] = useState<Lookalike[]>([])
  const [likePicked, setLikePicked] = useState<Set<number>>(new Set())

  const load = useCallback(async () => {
    setFailed('')
    try {
      const r = await api<{ items?: ReviewFace[]; total?: number }>(reviewUrl(performerId, groupIds))
      // What has been learned about this person (only in the desktop app, only for a named person); the plain review stands without it.
      const [learned, suggested] = performerId
        ? await Promise.all([api<Learned>(learnedUrl('outliers', performerId)).catch(() => null), api<{ items?: Lookalike[] }>(learnedUrl('suggestions', performerId)).catch(() => null)])
        : [null, null]
      setItems(withLearned(r.items ?? [], learned)); setTotal(r.total ?? r.items?.length ?? 0)
      setLike(Array.isArray(suggested?.items) ? suggested.items : []); setLikePicked(new Set())
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

  const addLookalikes = async () => {
    const ids = [...likePicked]
    if (!ids.length || !performerId) return
    setBusy(true)
    try {
      const r = await api<{ ok?: boolean; moved?: number; detail?: string }>('/api/photos/faces/detections/move', { method: 'POST', json: { detection_ids: ids, performer_id: performerId } })
      if (!r?.ok) throw new ApiError(0, r?.detail || '')
      toast(`Added ${r.moved ?? ids.length} faces to ${label}`)
      await Promise.all([qc.invalidateQueries({ queryKey: ['face-groups'] }), qc.invalidateQueries({ queryKey: ['photos'] }), qc.invalidateQueries({ queryKey: ['face-learning'] })])
      await load()
    } catch (e) { toast.error(why(e, 'Could not add those faces.')) } finally { setBusy(false) }
  }
  // Turn down the ticked look-alikes: they are not this person. Nothing is moved; the model is told, so it stops offering them and faces like them.
  const rejectLookalikes = async () => {
    const ids = [...likePicked]
    if (!ids.length || !performerId) return
    setBusy(true)
    try {
      const r = await api<{ ok?: boolean; marked?: number; detail?: string }>('/api/photos/faces/learning/not-this-person', { method: 'POST', json: { performer_id: performerId, detection_ids: ids } })
      if (!r?.ok) throw new ApiError(0, r?.detail || '')
      toast(`Noted: ${r.marked ?? ids.length} ${(r.marked ?? ids.length) === 1 ? 'face is' : 'faces are'} not ${label}. Faces like ${(r.marked ?? ids.length) === 1 ? 'it' : 'them'} will be offered less.`)
      setLike((l) => l.filter((f) => !likePicked.has(f.detection_id))); setLikePicked(new Set())
      await qc.invalidateQueries({ queryKey: ['face-learning'] })
      await load()
    } catch (e) { toast.error(why(e, 'Could not save that.')) } finally { setBusy(false) }
  }
  const toggleLike = (id: number) => setLikePicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })

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
              <button key={f.detection_id} type="button" role="checkbox" aria-checked={on} aria-label={`Face ${f.detection_id}${(f.different ?? f.similarity < DIFFERENT_BELOW) ? ', looks different' : ''}`} onClick={() => toggle(f.detection_id)}
                className={cn('relative aspect-square shrink-0 overflow-hidden rounded-xl bg-surface-2 outline-none ring-2 ring-offset-2 ring-offset-surface transition focus-visible:ring-accent', on ? 'ring-bad' : (f.different ?? f.similarity < DIFFERENT_BELOW) ? 'ring-warn/60' : 'ring-transparent')}>
                <img src={f.crop_url} alt="" loading="lazy" decoding="async" className={cn('size-full object-cover transition', on && 'opacity-70')} onError={(e) => (e.currentTarget.style.visibility = 'hidden')} />
                {on ? <span aria-hidden="true" className="absolute right-1 top-1 grid size-5 place-items-center rounded-full bg-bad text-xs font-bold text-white">✕</span> : null}
              </button>
            )
          })}
        </div>
        {items && !items.length && !failed ? <p className="text-sm text-muted">No faces here.</p> : null}
        {like.length ? (
          <section aria-label="Faces that look like this person" className="grid gap-2 border-t border-line pt-3">
            <p className="text-sm text-muted"><b className="text-fg">{like.length.toLocaleString()} new {like.length === 1 ? 'face looks' : 'faces look'} like {label}.</b> Tick some, then add them to {label} or say they are not. Each answer teaches the matching.</p>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className={BTN} onClick={() => setLikePicked(new Set(like.filter((f) => f.strong).map((f) => f.detection_id)))} disabled={!like.some((f) => f.strong)}>Tick the strong matches ({like.filter((f) => f.strong).length})</button>
              <button type="button" className={BTN} onClick={() => setLikePicked(new Set())} disabled={!likePicked.size}>Clear</button>
              <button type="button" className="h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg disabled:opacity-50" disabled={!likePicked.size || busy} onClick={() => void addLookalikes()}>{likePicked.size ? `Add ${likePicked.size} to ${label}` : `Add to ${label}`}</button>
              <button type="button" className="h-9 rounded-xl bg-bad px-3 text-sm font-bold text-white disabled:opacity-50" disabled={!likePicked.size || busy} aria-label={`Say the ticked look-alikes are not ${label}`} onClick={() => void rejectLookalikes()}>{likePicked.size ? `Not ${label} (${likePicked.size})` : `Not ${label}`}</button>
            </div>
            <div role="group" aria-label="Look-alike faces" className="grid max-h-[26vh] auto-rows-min content-start grid-cols-[repeat(auto-fill,minmax(80px,1fr))] gap-2 overflow-y-auto p-1">
              {like.map((f) => {
                const on = likePicked.has(f.detection_id)
                return (
                  <button key={f.detection_id} type="button" role="checkbox" aria-checked={on} aria-label={`Look-alike ${f.detection_id}${f.strong ? ', strong match' : ''}`} onClick={() => toggleLike(f.detection_id)}
                    className={cn('relative aspect-square overflow-hidden rounded-lg bg-surface-2 outline-none ring-2 ring-offset-2 ring-offset-surface focus-visible:ring-accent', on ? 'ring-good' : 'ring-transparent')}>
                    <img src={f.crop_url} alt="" loading="lazy" decoding="async" className="size-full object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} />
                    {on ? <span aria-hidden="true" className="absolute right-1 top-1 grid size-5 place-items-center rounded-full bg-good text-xs font-bold text-white">✓</span> : null}
                  </button>
                )
              })}
            </div>
          </section>
        ) : null}
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
