import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Dialog } from '@/design/Dialog'
import { ZoomImage } from '@/design/ImageZoom'
import { AnimatePresence, motion, panelItem } from '@/design/motion'
import { smokeKey } from '@/design/smoke'
import { Skeleton } from '@/design/Skeleton'
import { withWidth } from '@/design/responsive'
import { thumbUrl } from '@/api/client'
import { BULK_FAILED, EMPTY, FIRST_100_NOTE, NEED_SELECTION, NOTE, ONE_FAILED, TOO_MANY, BULK_MAX, allFirstSelected, appliedLine, bulkBody, bulkDone, firstBatch, oneBody, oneDone, selectedLabel, type Action, type AutoMatch, type AutoMatchList } from './automatch'

const BTN = 'h-9 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
export const useAutoMatches = (enabled = true) => useQuery({ queryKey: ['auto-matches'] as const, enabled, queryFn: () => api<AutoMatchList>('/api/metadata/auto-match-review'), staleTime: 15_000, retry: false })

function Row({ it, busy, picked, onPick, onAct }: { it: AutoMatch; busy: boolean; picked: boolean; onPick: () => void; onAct: (a: Action) => void }) {
  const [thumb, setThumb] = useState(true)
  return (
    <div data-smoke={`am:${it.video_id}`} className="flex flex-wrap items-start gap-3 rounded-xl border border-line bg-bg p-3">
      {thumb ? <ZoomImage group="auto-match" src={withWidth(thumbUrl(String(it.channel_id), it.message_id), 240)} fullSrc={withWidth(thumbUrl(String(it.channel_id), it.message_id), 1280)} alt={it.filename || 'Video thumbnail'} loading="lazy" className="h-14 w-24 shrink-0 rounded-lg bg-surface-2 object-cover" onError={() => setThumb(false)} /> : <span className="h-14 w-24 shrink-0 rounded-lg bg-surface-2" />}
      <div className="min-w-0 flex-1 text-left">
        <label className="flex items-center gap-2 text-sm font-semibold"><input type="checkbox" checked={picked} onChange={onPick} aria-label={`Select ${it.filename || 'video'} for bulk review`} /><span className="truncate" title={it.filename}>{it.filename || '(no filename)'}</span></label>
        <p className="mt-1 text-xs [overflow-wrap:anywhere]">{appliedLine(it)}</p>
        <p className="mt-0.5 text-xs text-muted">{it.reason}</p>
      </div>
      <div className="flex gap-2"><button type="button" className={BTN} disabled={busy} onClick={() => onAct('keep')} aria-label={`Keep match for ${it.filename || 'video'}`}>Keep</button><button type="button" className="h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg disabled:opacity-50" disabled={busy} onClick={() => onAct('undo')} aria-label={`Undo match for ${it.filename || 'video'}`}>Undo</button></div>
    </div>
  )
}

/** Review of the filename matches that were applied automatically before V29.4.132. */
export function AutoMatchReview({ onClose }: { onClose: () => void }) {
  const q = useAutoMatches()
  const qc = useQueryClient()
  const items = q.data?.items ?? []
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState<Set<number>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const done = () => { void qc.invalidateQueries({ queryKey: ['auto-matches'] }); void qc.invalidateQueries({ queryKey: ['library'] }) }
  const toggle = (id: number) => setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })

  const act = async (it: AutoMatch, a: Action) => {
    setBusy((b) => new Set(b).add(it.video_id))
    try {
      const r = await api<{ ok?: boolean; detail?: string }>(`/api/metadata/auto-match-review/${it.video_id}`, { method: 'POST', json: oneBody(a) })
      if (!r?.ok) throw new ApiError(0, r?.detail || ONE_FAILED)
      await smokeKey(`am:${it.video_id}`)   // dissolves first; the rows below then slide up as it leaves the list
      qc.setQueryData<AutoMatchList>(['auto-matches'], (d) => ({ ...(d ?? {}), items: (d?.items ?? []).filter((x) => x.video_id !== it.video_id), count: Math.max(0, (d?.items?.length ?? 1) - 1) }))
      setPicked((s) => { const n = new Set(s); n.delete(it.video_id); return n })
      toast(oneDone(a)); done()
    } catch (e) { toast(e instanceof ApiError && e.detail ? e.detail : ONE_FAILED) } finally { setBusy((b) => { const n = new Set(b); n.delete(it.video_id); return n }) }
  }
  const bulk = async (a: Action) => {
    const ids = [...picked]
    if (!ids.length) return void toast(NEED_SELECTION)
    if (ids.length > BULK_MAX) return void toast(TOO_MANY)
    setBulkBusy(true)
    try {
      const r = await api<{ ok?: boolean; detail?: string; processed?: number; skipped?: number; failed?: number }>('/api/metadata/auto-match-review/bulk', { method: 'POST', json: bulkBody(a, ids) })
      if (!r?.ok) throw new ApiError(0, r?.detail || BULK_FAILED)
      await Promise.all(ids.map((id) => smokeKey(`am:${id}`)))
      setPicked(new Set()); await q.refetch(); done(); toast(bulkDone(a, r))
    } catch (e) { toast(e instanceof ApiError && e.detail ? e.detail : BULK_FAILED) } finally { setBulkBusy(false) }
  }
  const selectFirst = (on: boolean) => { setPicked(new Set(on ? firstBatch(items) : [])); if (on && items.length > BULK_MAX) toast(FIRST_100_NOTE) }
  return (
    <Dialog wide open onClose={onClose} title="Earlier automatic matches to review">
      <div className="grid gap-3 text-left">
        <p className="text-xs text-muted">{NOTE}</p>
        <div className="flex flex-wrap items-center gap-3">
          <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={allFirstSelected(items, picked)} onChange={(e) => selectFirst(e.target.checked)} />Select first 100</label>
          <span className="text-sm text-muted">{selectedLabel(picked.size)}</span>
          <button type="button" className={BTN} disabled={bulkBusy} onClick={() => void bulk('keep')}>Keep selected</button>
          <button type="button" className="h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg disabled:opacity-50" disabled={bulkBusy} onClick={() => void bulk('undo')}>Undo selected</button>
        </div>
        {q.isPending ? <Skeleton className="h-40 w-full rounded-xl" /> : q.isError ? <p role="alert" className="text-sm text-bad">Could not load the review list.</p> : !items.length ? <p className="text-sm text-muted">{EMPTY}</p>
          : <ul className="relative grid gap-2"><AnimatePresence mode="popLayout" initial={false}>{items.map((it) => <motion.li key={it.video_id} {...panelItem} className="list-none"><Row it={it} busy={busy.has(it.video_id) || bulkBusy} picked={picked.has(it.video_id)} onPick={() => toggle(it.video_id)} onAct={(a) => void act(it, a)} /></motion.li>)}</AnimatePresence></ul>}
      </div>
    </Dialog>
  )
}
