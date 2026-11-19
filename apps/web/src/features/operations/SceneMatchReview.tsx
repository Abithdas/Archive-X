import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Confirm } from '@/design/Confirm'
import { Dialog } from '@/design/Dialog'
import { ZoomImage } from '@/design/ImageZoom'
import { AnimatePresence, motion, panelItem } from '@/design/motion'
import { smokeKey } from '@/design/smoke'
import { Skeleton } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import type { LibraryItem } from '@/api/types'
import { QuickEditDialog } from '@/features/watch/QuickEditDialog'
import { Frames } from './ChannelDuplicates'
import { DEFAULT_THRESHOLD, MAX_SELECTION, NO_FILTERS, approveAboveCopy, approveUrl, approvedToast, clamp100, countText, distinctReason, editItem, isFiltered, merge, missingLabel, needs, nextOffsets, oneDone, oneFailed, providerBadge, providerImages, providerLinkText, rejectUrl, rejectedToast, reviewUrl, safeHttp, selectLimit, thresholdOf, visibleText, type Filters, type ReviewPage, type Suggestion, type Unresolved } from './scenematch'
import { Disclosure } from '@/design/Accordion'

const BTN = 'h-9 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const PRIMARY = 'h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg disabled:opacity-50'
const why = (e: unknown, fb: string) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : fb)

function SuggestionCard({ s, picked, busy, onPick, onAct }: { s: Suggestion; picked: boolean; busy: boolean; onPick: () => void; onAct: (a: 'approve' | 'reject') => void }) {
  const score = clamp100(s.score)
  const images = providerImages(s), url = safeHttp(s.provider_url), siteUrl = safeHttp(s.provider_site_url)
  const site = String(s.provider_site || s.studio || '').trim()
  const people = (s.performers ?? []).filter(Boolean).join(', ')
  const meta = [people, s.release_date].filter(Boolean).join(' · ')
  const local = String(s.filename || s.local_title || s.candidate_title || 'video')
  const cid = String(s.channel_id ?? '')
  const title = s.candidate_title || '(untitled candidate)'
  return (
    <article aria-label={title} data-smoke={`sm:${s.suggestion_id}`} className="grid gap-3 rounded-2xl border border-line p-3 text-left sm:grid-cols-[minmax(0,1fr)_auto]">
      <div className="grid min-w-0 gap-2">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <label className="inline-flex items-center gap-1.5 text-xs font-semibold text-muted"><input type="checkbox" checked={picked} disabled={busy} onChange={onPick} aria-label={`Select ${title} for rejection`} />Select</label>
          <span className="rounded-full bg-accent/15 px-2 py-0.5 text-xs font-bold text-accent">{score.toFixed(1)}%</span>
          <b className="min-w-0 [overflow-wrap:anywhere]">{title}</b>
          {providerBadge(s) ? <span className="rounded-full bg-surface-3 px-2 py-0.5 text-[11px] font-semibold text-muted">{providerBadge(s)}</span> : null}
        </div>
        <div className="flex flex-wrap gap-x-3 text-xs text-muted"><span>{s.channel_title || 'Channel'}</span><span>{s.filename || s.local_title || 'Untitled video'}</span>{s.message_id ? <span>Message #{s.message_id}</span> : null}</div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
          <span><b>Site</b> {site ? (siteUrl ? <a href={siteUrl} target="_blank" rel="noopener noreferrer" className="text-accent underline">{site}</a> : site) : 'Not listed'}</span>
          {s.provider_record_id ? <span><b>Record ID</b> {s.provider_record_id}</span> : null}
          {s.website_variant_conflict ? <span className="rounded-full bg-warn/15 px-2 text-warn">Possible same content · separate sites</span> : null}
          {url ? <a href={url} target="_blank" rel="noopener noreferrer" className="font-semibold text-accent underline">{providerLinkText(s)}</a> : null}
        </div>
        {needs(s.missing_fields).length ? <div className="flex flex-wrap items-center gap-1.5 text-xs"><span className="text-muted">Needs</span>{needs(s.missing_fields).map((f) => <span key={f} className="rounded-full bg-warn/15 px-2 py-0.5 font-semibold text-warn">{f}</span>)}</div> : null}
        <div className="grid gap-3 lg:grid-cols-[160px_minmax(0,1fr)]">
          <div><p className="mb-1 text-[11px] text-muted">Provider pictures{images.length > 1 ? ` · ${images.length}` : ''}</p>
            {images.length ? (
              <div className="grid grid-cols-2 gap-1.5" aria-label="Provider pictures">
                {images.map((src, i) => <ZoomImage key={src} group={`prov-${s.suggestion_id}`} src={src} alt={`Provider picture ${i + 1} of ${images.length}`} loading="lazy" referrerPolicy="no-referrer" className={cn('aspect-video w-full rounded-lg bg-surface-3 object-cover', i === 0 && images.length % 2 === 1 ? 'col-span-2' : '')} onError={(e) => (e.currentTarget.style.display = 'none')} />)}
              </div>
            ) : <p className="grid aspect-video place-items-center rounded-lg bg-surface-3 text-xs text-muted">The provider has no picture for this scene</p>}</div>
          <div><p className="mb-1 text-[11px] text-muted">Video snapshots · 4 of 36</p>{s.message_id ? <Frames cid={cid} mid={Number(s.message_id)} title={local} /> : null}</div>
        </div>
        <Disclosure className="text-xs" summaryClassName="font-semibold" summary={<span>Why this match? <span className="font-normal text-muted">Optional details</span></span>}>
          <div className="mt-1 grid gap-1.5 text-muted">
            {meta ? <p><b className="text-fg">Candidate metadata</b><br />{meta}</p> : null}{s.signals ? <p><b className="text-fg">Signals</b><br />{s.signals}</p> : null}{distinctReason(s.signals, s.reason) ? <p><b className="text-fg">Additional note</b><br />{s.reason}</p> : null}
            {!meta && !s.signals && !distinctReason(s.signals, s.reason) ? <p>No additional match details.</p> : null}
          </div></Disclosure>
      </div>
      <div className="flex gap-2 sm:grid sm:content-start"><button type="button" className={PRIMARY} disabled={busy} onClick={() => onAct('approve')} aria-label={`Approve ${title}`}>Approve</button><button type="button" className={BTN} disabled={busy} onClick={() => onAct('reject')} aria-label={`Reject ${title}`}>Reject</button></div>
    </article>
  )
}

function UnresolvedCard({ u, onEdit }: { u: Unresolved; onEdit: () => void }) {
  const title = u.title || u.filename || '(untitled video)'
  const current = [u.studio_name, (u.performers ?? []).filter(Boolean).join(', ')].filter(Boolean).join(' · ')
  return (
    <article aria-label={title} className="grid gap-2 rounded-2xl border border-line p-3 text-left sm:grid-cols-[minmax(0,1fr)_auto]">
      <div className="grid min-w-0 gap-1.5">
        <div className="flex flex-wrap items-baseline gap-2"><b className="[overflow-wrap:anywhere]">{title}</b><span className="text-xs text-muted">Needs metadata</span></div>
        <p className="text-xs"><b>Missing:</b> {missingLabel(u.missing_fields)}</p>
        <p className="text-xs text-muted [overflow-wrap:anywhere]">File: {u.filename} · Channel: {u.channel_title || 'Channel'}{u.message_id ? ` · Message ID ${u.message_id}` : ''}{current ? ` · Current: ${current}` : ''}</p>
        {u.message_id ? <div><p className="mb-1 text-[11px] text-muted">Video snapshots · 4 of 36</p><Frames cid={String(u.channel_id ?? '')} mid={Number(u.message_id)} title={title} /></div> : null}
      </div>
      <div><button type="button" className={PRIMARY} onClick={onEdit} aria-label={`Edit video ${title}`}>Edit video</button></div>
    </article>
  )
}

/** Open review: ranked suggestions to approve or reject, and videos that still need metadata. */
export function SceneMatchReview({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const [filters, setFilters] = useState<Filters>(NO_FILTERS)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [rows, setRows] = useState<Set<number>>(new Set())
  const [threshold, setThreshold] = useState(String(DEFAULT_THRESHOLD))
  const [ask, setAsk] = useState<number | null>(null)
  const [editing, setEditing] = useState<LibraryItem | null>(null)
  const q = useInfiniteQuery({
    queryKey: ['scene-review', filters] as const,
    initialPageParam: { so: 0, uo: 0 },
    queryFn: async ({ pageParam }) => { const r = await api<ReviewPage>(reviewUrl(filters, pageParam.so, pageParam.uo)); if (!r || r.detail) throw new ApiError(0, r?.detail || 'Could not load scene-match review.'); return r },
    getNextPageParam: (_last, all) => { const m = merge(all); return m.hasMore ? nextOffsets(all) : undefined },
    staleTime: 0,
  })
  const m = merge(q.data?.pages ?? [])
  const quiet = () => { void qc.invalidateQueries({ queryKey: ['scene-review'] }) }
  const lock = busy || rows.size > 0

  const act = async (s: Suggestion, a: 'approve' | 'reject') => {
    if (lock) return
    setRows((r) => new Set(r).add(s.suggestion_id))
    try {
      const r = await api<{ ok?: boolean; detail?: string }>(a === 'approve' ? approveUrl(s.suggestion_id) : rejectUrl(s.suggestion_id), { method: 'POST', json: {} })
      if (r?.ok === false) throw new ApiError(0, r.detail || oneFailed(a))
      await smokeKey(`sm:${s.suggestion_id}`)   // the card dissolves, then the list refreshes and what is below slides up
      toast(oneDone(a)); setPicked((p) => { const n = new Set(p); n.delete(s.suggestion_id); return n }); quiet()
    } catch (e) { toast(why(e, oneFailed(a))) } finally { setRows((r) => { const n = new Set(r); n.delete(s.suggestion_id); return n }) }
  }
  const rejectSelected = async () => {
    const ids = [...picked]
    if (!ids.length || lock) return
    setBusy(true)
    try {
      const r = await api<{ ok?: boolean; detail?: string; rejected_ids?: number[]; not_pending_ids?: number[]; rejected?: number; not_pending?: number; failed?: number }>('/api/metadata/scene-match-review/reject-selected', { method: 'POST', json: { suggestion_ids: ids } })
      if (r?.ok === false) throw new ApiError(0, r.detail || 'Selected suggestions could not be rejected.')
      const done = new Set([...(r.rejected_ids ?? []), ...(r.not_pending_ids ?? [])])
      await Promise.all([...done].map((id) => smokeKey(`sm:${id}`)))
      setPicked((p) => new Set([...p].filter((id) => !done.has(id)))); toast(rejectedToast(r)); quiet()
    } catch (e) { toast(why(e, 'Selected suggestions could not be rejected.')) } finally { setBusy(false) }
  }
  const approveAbove = async () => {
    const t = thresholdOf(threshold)
    setAsk(null); setBusy(true)
    try {
      const r = await api<Record<string, unknown> & { ok?: boolean; detail?: string }>('/api/metadata/scene-match-review/approve-above', { method: 'POST', json: { threshold: t } })
      if (r?.ok === false) throw new ApiError(0, String(r.detail || 'Bulk approval failed.'))
      toast(approvedToast(r)); quiet()
    } catch (e) { toast(why(e, 'Bulk approval failed.')) } finally { setBusy(false) }
  }
  const toggle = (id: number) => setPicked((p) => { if (!p.has(id) && p.size >= MAX_SELECTION) { toast(selectLimit); return p } const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const copy = approveAboveCopy(thresholdOf(threshold))
  return (
    <>
      <Dialog wide open onClose={() => !busy && onClose()} title="Scene Match Review" actions={<span className="text-xs text-muted" aria-live="polite">{q.isPending ? 'Loading…' : q.isError ? 'Load failed' : countText(m.total, m.unresolvedTotal, isFiltered(filters))}</span>}>
        <div className="grid gap-4 text-left">
          <div className="flex flex-wrap items-center gap-2 rounded-xl border border-line p-3">
            <label htmlFor="sm-threshold" className="text-sm text-muted">Bulk approve ≥</label>
            <input id="sm-threshold" type="number" min={0} max={100} step={1} value={threshold} onChange={(e) => setThreshold(e.target.value)} aria-label="Scene match approval confidence threshold" className="h-9 w-20 rounded-lg border border-line-2 bg-bg px-2 text-sm" /><span className="text-sm text-muted">%</span>
            <button type="button" className={BTN} disabled={lock} onClick={() => setAsk(thresholdOf(threshold))}>Approve above</button>
            <small className="text-xs text-muted" title="Bulk approval skips suggestions with unresolved cross-provider identity conflicts.">Unresolved conflicts are skipped</small>
          </div>
          <div className="flex flex-wrap items-end gap-3 rounded-xl border border-line p-3">
            <div className="min-w-[10rem]"><b className="text-sm">Metadata list</b><br /><small className="text-xs text-muted">Filters only this list</small></div>
            <label className="grid gap-1 text-xs text-muted">Channel
              <select value={filters.channel} onChange={(e) => setFilters({ ...filters, channel: e.target.value })} aria-label="Filter videos by channel" className="h-9 rounded-lg border border-line-2 bg-bg px-2 text-sm text-fg"><option value="">All channels</option>{m.channels.map((c) => <option key={String(c.channel_id)} value={String(c.channel_id)}>{c.title || String(c.channel_id)}</option>)}</select></label>
            <label className="grid gap-1 text-xs text-muted">Missing field
              <select value={filters.field} onChange={(e) => setFilters({ ...filters, field: e.target.value as Filters['field'] })} aria-label="Filter videos by missing field" className="h-9 rounded-lg border border-line-2 bg-bg px-2 text-sm text-fg"><option value="all">Any missing field</option><option value="performer">Performer</option><option value="studio">Studio</option></select></label>
            <button type="button" className={BTN} disabled={!isFiltered(filters)} onClick={() => setFilters(NO_FILTERS)}>Clear filters</button>
          </div>
          {q.isPending ? <div role="status" aria-label="Loading review" className="grid gap-3">{[0, 1].map((i) => <Skeleton key={i} className="h-44 rounded-2xl" />)}</div>
            : q.isError ? <div role="alert" className="rounded-xl border border-bad/40 p-4 text-sm"><p>{why(q.error, 'Review request failed.')}</p><button type="button" className={cn(PRIMARY, 'mt-2')} onClick={() => void q.refetch()}>Retry</button></div>
            : (
              <>
                <section aria-labelledby="sm-s" className="relative grid gap-2">
                  <div className="flex items-baseline justify-between gap-2"><h3 id="sm-s" className="text-base font-bold">Ranked scene suggestions</h3><span className="text-xs text-muted">{visibleText(m.suggestions.length, m.total)}</span></div>
                  <div className="flex flex-wrap items-center gap-2"><span className="text-xs text-muted">{picked.size} selected</span>
                    <button type="button" className="h-9 rounded-xl border border-bad px-3 text-sm font-semibold text-bad disabled:opacity-50" disabled={!picked.size || lock} onClick={() => void rejectSelected()}>{busy ? 'Working…' : `Reject selected${picked.size ? ` (${picked.size})` : ''}`}</button>
                    <button type="button" className={BTN} disabled={!picked.size || lock} onClick={() => setPicked(new Set())}>Clear</button></div>
                  {m.suggestions.length ? <AnimatePresence mode="popLayout" initial={false}>{m.suggestions.map((s) => <motion.div key={s.suggestion_id} {...panelItem}><SuggestionCard s={s} picked={picked.has(s.suggestion_id)} busy={lock} onPick={() => toggle(s.suggestion_id)} onAct={(a) => void act(s, a)} /></motion.div>)}</AnimatePresence> : <p className="rounded-xl border border-dashed border-line p-4 text-center text-sm text-muted">No pending scene suggestions.</p>}
                </section>
                <section aria-labelledby="sm-u" className="relative grid gap-2">
                  <div className="flex items-baseline justify-between gap-2"><h3 id="sm-u" className="text-base font-bold">Videos needing metadata</h3><span className="text-xs text-muted">{visibleText(m.unresolved.length, m.unresolvedTotal)}</span></div>
                  {m.unresolved.length ? <AnimatePresence mode="popLayout" initial={false}>{m.unresolved.map((u) => <motion.div key={u.media_id} {...panelItem}><UnresolvedCard u={u} onEdit={() => setEditing(editItem(u) as LibraryItem)} /></motion.div>)}</AnimatePresence> : <p className="rounded-xl border border-dashed border-line p-4 text-center text-sm text-muted">No unresolved catalogue videos.</p>}
                </section>
                {q.hasNextPage ? <div className="text-center"><button type="button" className={BTN} disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>{q.isFetchingNextPage ? 'Loading…' : 'Load more'}</button></div> : null}
              </>
            )}
        </div>
      </Dialog>
      {ask !== null ? <Confirm title={copy.title} text={copy.text} ok={copy.ok} onCancel={() => setAsk(null)} onOk={() => void approveAbove()} /> : null}
      {editing ? <QuickEditDialog key={`${editing.id}:${editing._cid}`} item={editing} onDone={() => { setEditing(null); quiet() }} /> : null}
    </>
  )
}
