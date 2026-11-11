import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '@/api/client'
import { Confirm } from '@/design/Confirm'
import { Icon } from '@/design/Icon'
import { Skeleton } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import { EMPTY_BODY, EMPTY_TITLE, INTRO, LOAD_FAILED, MERGE_NEEDS_TWO, NEEDS_PERFORMER, CLEARED, REJECTED, analysisStatus, analyzeBody, analyzeCopy, assignBody, assignedStatus, assignedText, confirmedText, countsLine, firstDetection, groupLabel, isAssigned, jobDone, jobEndText, jobStatusText, mergeBody, mergeCopy, mergedText, photosLabel, rejectBody, scoreText, splitCopy, splitText, suggestionBody, suggestionReason, suggestionsFor, UNANALYZED, viewFilter, type FaceGroup, type FaceGroupsResult, type Source, type Suggestion, type Tone } from './faces'
import { ReferenceReview } from './ReferenceReview'
import { usePhotoStore } from './store'
import { withWidth } from '@/design/responsive'

const BTN = 'h-9 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const PRIMARY = 'h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg disabled:opacity-50'
const reason = (e: unknown, fb: string) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : fb)
type Pick = { id: number; name: string }
type Ask = null | { title: string; text: string; ok: string; run: () => Promise<void> }

function Suggestions({ group, source, items, onCompare, onConfirm, onReject }: { group: FaceGroup; source: Source; items: Suggestion[]; onCompare: (s: Suggestion) => void; onConfirm: (s: Suggestion, src: Source) => void; onReject: (s: Suggestion, src: Source) => void }) {
  if (!items.length) return null
  const title = source === 'metadata' ? 'Suggestions from source text' : 'Suggestions from your previous confirmations'
  const note = source === 'metadata' ? 'Based on exact filename, caption, or channel-title text matches.' : 'Ranked by similarity to face groups you previously assigned. These are candidates, not automatic assignments.'
  return (
    <div className="mt-2 grid gap-1.5 rounded-xl border border-line bg-bg p-2.5" aria-label={title}>
      <strong className="text-xs">{title}</strong><small className="text-[11px] text-muted">{note}</small>
      {items.map((s) => (
        <div key={`${source}:${s.id}`} className="flex flex-wrap items-center gap-2">
          <img src={s.image_url ?? ''} alt="" loading="lazy" className="size-9 rounded-full bg-surface-2 object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} />
          <span className="min-w-0 flex-1 text-left"><b className="block truncate text-sm">{s.name || 'Unknown performer'}</b><small className="block text-[11px] text-muted">{suggestionReason(s, source)}</small>{scoreText(s) ? <small className="block text-[11px] text-faint">{scoreText(s)}</small> : null}</span>
          <button type="button" className={cn(BTN, 'h-8 px-2.5 text-xs')} onClick={() => onCompare(s)} aria-label={`Compare ${s.name} with ${groupLabel(group)}`}>Compare</button>
          <button type="button" className={cn(PRIMARY, 'h-8 px-2.5 text-xs')} onClick={() => onConfirm(s, source)} aria-label={`Confirm ${s.name} for ${groupLabel(group)}`}>Confirm</button>
          <button type="button" className={cn(BTN, 'h-8 px-2.5 text-xs')} onClick={() => onReject(s, source)} aria-label={`Not ${s.name} for ${groupLabel(group)}`}>Not this</button>
        </div>
      ))}
    </div>
  )
}

function PerformerSearch({ group, picked, onPick }: { group: FaceGroup; picked: Pick | null; onPick: (p: Pick | null) => void }) {
  const [text, setText] = useState(picked?.name ?? group.performer_name ?? '')
  const [list, setList] = useState<Array<{ id: number; name: string; image_url?: string }> | null>(null)
  const [err, setErr] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])
  useEffect(() => { if (picked) { setText(picked.name); setList(null) } }, [picked])
  const type = (v: string) => {
    setText(v); if (picked && v !== picked.name) onPick(null)
    clearTimeout(timer.current)
    if (v.trim().length < 2) { setList(null); return }
    timer.current = setTimeout(async () => {
      try { const r = await api<{ items?: Array<{ id: number; name: string; image_url?: string }> }>(`/api/photos/faces/performers?limit=12&q=${encodeURIComponent(v.trim())}`); setList(Array.isArray(r?.items) ? r.items.slice(0, 12) : []); setErr('') }
      catch (e) { setList([]); setErr(reason(e, 'Performer search failed.')) }
    }, 220)
  }
  const id = `face-search-${group.id}`
  return (
    <div className="mt-2 text-left">
      <label htmlFor={id} className="text-xs font-semibold text-muted">Assign to performer</label>
      <input id={id} type="search" value={text} onChange={(e) => type(e.target.value)} placeholder="Search local performer names…" autoComplete="off" className="mt-1 h-10 w-full rounded-xl border border-line-2 bg-surface px-3 text-sm" />
      {list ? (
        <div role="listbox" aria-label={`Performers for ${groupLabel(group)}`} className="mt-1 grid max-h-48 overflow-y-auto rounded-xl border border-line bg-surface p-1">
          {list.length ? list.map((p) => (
            <button key={p.id} type="button" role="option" aria-selected={picked?.id === p.id} onClick={() => { onPick({ id: p.id, name: p.name }); setText(p.name); setList(null) }} className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-fg/5">
              <img src={withWidth(p.image_url || `/api/performers/${p.id}/image`, 160)} alt="" loading="lazy" className="size-7 rounded-full bg-surface-2 object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} /><span className="flex-1 truncate">{p.name}</span><small className="text-[11px] text-faint">Local performer</small>
            </button>
          )) : <div className="px-2 py-1.5 text-xs text-muted">{err || 'No local performer matches.'}</div>}
        </div>
      ) : null}
    </div>
  )
}

function Card({ group, checked, onCheck, busy, onView, onAssign, onClear, onSplit, onConfirm, onReject }: {
  group: FaceGroup; checked: boolean; onCheck: () => void; busy: boolean; onView: () => void
  onAssign: (p: Pick | null) => Promise<boolean> | void; onClear: () => void; onSplit: (detection: number) => void; onConfirm: (s: Suggestion, src: Source) => void; onReject: (s: Suggestion, src: Source) => void
}) {
  const [picked, setPicked] = useState<Pick | null>(group.performer_id ? { id: Number(group.performer_id), name: group.performer_name ?? '' } : null)
  const [cover, setCover] = useState(true)
  const [reviewing, setReviewing] = useState(false)
  const compare = (s: Suggestion) => { setPicked({ id: s.id, name: s.name ?? '' }); setReviewing(true) }
  const sug = suggestionsFor(group)
  const label = groupLabel(group)
  const det = firstDetection(group)
  return (
    <article aria-label={label} className="grid content-start gap-2 rounded-2xl border border-line bg-surface p-3">
      <label className="inline-flex items-center gap-2 text-xs font-semibold text-muted"><input type="checkbox" checked={checked} onChange={onCheck} aria-label={`Select cluster ${label} to merge`} />Select cluster to merge</label>
      <button type="button" onClick={onView} title={`View photos in ${label}`} className="relative block aspect-square w-full overflow-hidden rounded-xl bg-surface-3">
        {cover ? <img src={group.representative_url ?? ''} alt={`Representative face for ${label}`} loading="lazy" decoding="async" className="size-full object-cover" onError={() => setCover(false)} /> : <span className="grid size-full place-items-center text-xs text-muted">No preview</span>}
        <span className="absolute bottom-2 left-2 rounded-full bg-black/60 px-2 py-0.5 text-xs font-semibold text-white">{photosLabel(group)}</span>
      </button>
      <div className="flex items-center gap-2 text-left">
        {group.performer_id && group.performer_image_url ? <img src={`${group.performer_image_url}?v=${encodeURIComponent(group.updated_at ?? '')}`} alt="" loading="lazy" className="size-10 rounded-full bg-surface-2 object-cover" onError={(e) => (e.currentTarget.style.display = 'none')} /> : null}
        <div className="min-w-0"><h3 className="truncate text-base font-bold">{label}</h3><p className="text-xs text-muted">{countsLine(group)}</p></div>
      </div>
      <span className={cn('w-fit rounded-full px-2.5 py-0.5 text-xs font-semibold', isAssigned(group) ? 'bg-good/15 text-good' : 'bg-warn/15 text-warn')}>{assignedText(group)}</span>
      <Suggestions group={group} source="confirmed_assignment" items={sug.confirmed} onCompare={compare} onConfirm={onConfirm} onReject={onReject} />
      <Suggestions group={group} source="metadata" items={sug.metadata} onCompare={compare} onConfirm={onConfirm} onReject={onReject} />
      <PerformerSearch group={group} picked={picked} onPick={setPicked} />
      <div className="flex flex-wrap gap-2">
        <button type="button" className={BTN} onClick={() => setReviewing(true)} title="Open an unblurred side-by-side review; choose a performer here if one is not selected" aria-label={`Review profile/provider images for ${label}`}>Review profile/provider images</button>
        <button type="button" disabled={busy} className={PRIMARY} onClick={() => void onAssign(picked)} aria-label={`Save assignment for ${label}`}>Save assignment</button>
        <button type="button" disabled={busy} className={BTN} onClick={() => { setPicked(null); onClear() }} aria-label={`Clear assignment for ${label}`}>Clear</button>
        <button type="button" className={BTN} onClick={onView} aria-label={`View photos in ${label}`}>View photos</button>
        {det ? <button type="button" disabled={busy} className={BTN} onClick={() => onSplit(det)} title="Move one face detection to a new cluster" aria-label={`Separate one face from ${label}`}>Separate one face</button> : null}
      </div>
      {reviewing ? <ReferenceReview group={group} picked={picked} onPick={setPicked} onSave={async () => (picked ? Boolean(await onAssign(picked)) : false)} onClose={() => setReviewing(false)} /> : null}
    </article>
  )
}

export function useFaceGroups(selected: readonly string[], enabled = true) {
  return useQuery({
    queryKey: ['face-groups', selected] as const,
    enabled,
    queryFn: async () => { const r = await api<FaceGroupsResult>(`/api/photos/faces/groups${selected.length ? `?channels=${encodeURIComponent(selected.join(','))}` : ''}`); if (!r || r.ok !== true) throw new ApiError(0, r?.detail || LOAD_FAILED); return r },
    staleTime: 0,
  })
}

/** The Image Analysis tab: anonymous face groups, with analysis, merge, split, and manual or suggested performer assignment. */
export function FaceGroups({ onView }: { onView: () => void }) {
  const { selected, setSelected, setFace, setTab } = usePhotoStore()
  const qc = useQueryClient()
  const q = useFaceGroups(selected)
  const groups = q.data?.groups ?? []
  const [note, setNote] = useState<{ text: string; tone: Tone } | null>(null)
  const [clusters, setClusters] = useState<Set<number>>(new Set())
  const [watching, setWatching] = useState(false)
  const [busy, setBusy] = useState(false)
  const [ask, setAsk] = useState<Ask>(null)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const say = (text: string, tone: Tone = '') => setNote({ text, tone })
  const refresh = useCallback(async () => { await q.refetch() }, [q])
  const changed = async () => { void qc.invalidateQueries({ queryKey: ['photos'] }); await refresh() }

  const st = q.data ? analysisStatus(groups, q.data.analysis ?? {}, selected.length, watching) : null
  const shown = note ?? (st ? { text: st.text, tone: st.tone } : { text: UNANALYZED, tone: '' as Tone })

  const analyze = () => {
    const c = analyzeCopy(selected.length)
    setAsk({ title: c.title, text: c.text, ok: c.ok, run: async () => {
      say('Queueing image-based face analysis…')
      try {
        const r = await api<{ id?: string; detail?: string }>('/api/photos/faces/analyze', { method: 'POST', json: analyzeBody(selected) })
        if (!r?.id) throw new ApiError(0, r?.detail || 'Could not queue face analysis.')
        say(`Face analysis queued as job ${r.id}. View progress in Operations.`, 'success')
        setWatching(true)
        for (let i = 0; i < 7200 && alive.current; i++) {
          await new Promise((res) => setTimeout(res, 2000))
          let s: { status?: string; progress?: number; current_item?: string } | null = null
          try { s = await api(`/api/photos/scan-status/${encodeURIComponent(r.id)}`) } catch { continue }
          if (!s || !alive.current) continue
          const status = String(s.status || '').toUpperCase()
          say(jobStatusText(status, s.progress, s.current_item))
          if (jobDone(status)) {
            const ok = status === 'COMPLETED' || status === 'COMPLETED_WITH_ERRORS'
            say(jobEndText(status), ok ? 'success' : 'error')
            setNote(null); await changed(); void qc.invalidateQueries({ queryKey: ['photo-channels'] })
            break
          }
        }
      } catch (e) { say(reason(e, 'Could not start face analysis.'), 'error') } finally { if (alive.current) setWatching(false) }
    } })
  }

  const merge = () => {
    const ids = [...clusters]
    if (ids.length < 2) return void say(MERGE_NEEDS_TWO, 'error')
    const c = mergeCopy(ids.length)
    setAsk({ title: c.title, text: c.text, ok: c.ok, run: async () => {
      setBusy(true)
      try {
        const r = await api<{ ok?: boolean; target_group_id?: number; detail?: string }>('/api/photos/faces/groups/merge', { method: 'POST', json: mergeBody(ids) })
        if (!r || r.ok !== true) throw new ApiError(0, r?.detail || 'Could not merge face clusters.')
        setClusters(new Set()); say(mergedText(ids.length, r.target_group_id), 'success'); await changed()
      } catch (e) { say(reason(e, 'Could not merge face clusters.'), 'error') } finally { setBusy(false) }
    } })
  }
  const split = (detection: number) => setAsk({ title: splitCopy.title, text: splitCopy.text, ok: splitCopy.ok, run: async () => {
    setBusy(true)
    try {
      const r = await api<{ ok?: boolean; new_group_id?: number; detail?: string }>(`/api/photos/faces/detections/${detection}/split`, { method: 'POST', json: {} })
      if (!r || r.ok !== true) throw new ApiError(0, r?.detail || 'Could not split face detection.')
      setClusters(new Set()); say(splitText(r.new_group_id), 'success'); await changed()
    } catch (e) { say(reason(e, 'Could not split face detection.'), 'error') } finally { setBusy(false) }
  } })

  const post = async (g: FaceGroup, body: object, okText: string, failText: string): Promise<boolean> => {
    setBusy(true)
    try {
      const r = await api<{ ok?: boolean; detail?: string }>(`/api/photos/faces/groups/${g.id}/assign`, { method: 'POST', json: body })
      if (!r || r.ok !== true) throw new ApiError(0, r?.detail || failText)
      say(okText, 'success'); await changed(); return true
    } catch (e) { say(reason(e, failText), 'error'); return false } finally { setBusy(false) }
  }
  const assign = async (g: FaceGroup, p: Pick | null) => { if (!p) { say(NEEDS_PERFORMER, 'error'); return false } return post(g, assignBody(g, p.id), assignedStatus(p.name), 'Could not save performer assignment.') }
  const clear = (g: FaceGroup) => void post(g, assignBody(g, null), CLEARED, 'Could not clear performer assignment.')
  const confirmSuggestion = (g: FaceGroup, s: Suggestion, src: Source) => void post(g, suggestionBody(g, s, src, s.basis ?? ''), confirmedText(s.name ?? ''), 'Could not confirm this suggestion.')
  const reject = async (g: FaceGroup, s: Suggestion, src: Source) => {
    setBusy(true)
    try {
      const r = await api<{ ok?: boolean; detail?: string }>('/api/photos/faces/suggestions/reject', { method: 'POST', json: rejectBody(g, s, src) })
      if (!r || r.ok !== true) throw new ApiError(0, r?.detail || 'Could not save this rejection.')
      say(REJECTED, 'success'); await refresh()
    } catch (e) { say(reason(e, 'Could not reject this suggestion.'), 'error') } finally { setBusy(false) }
  }
  const view = (g: FaceGroup) => { const v = viewFilter(g); if (v.channels.length) setSelected(v.channels); setFace(v.filter); setTab('library'); onView() }
  const toggle = (id: number) => setClusters((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })

  return (
    <section role="tabpanel" aria-label="Image Analysis" className="grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 max-w-3xl"><h2 className="text-lg font-bold">Face groups</h2><p className="text-xs text-muted">{INTRO}</p></div>
        <button type="button" disabled={!st || st.analyzeDisabled} onClick={analyze} className={cn(PRIMARY, 'inline-flex h-10 items-center gap-2')}><Icon name="images" className="size-4" />{st?.analyzeLabel ?? 'Analyze images for faces'}</button>
      </div>
      <p role="status" aria-live="polite" className={cn('text-sm', shown.tone === 'error' ? 'text-bad' : shown.tone === 'success' ? 'text-good' : 'text-muted')}>{q.isError ? reason(q.error, LOAD_FAILED) : shown.text}</p>
      {groups.length ? (
        <div role="group" aria-label="Face cluster corrections" className="flex flex-wrap items-center gap-2 rounded-xl border border-line p-2.5">
          <span className="text-sm">{clusters.size} clusters selected</span>
          <button type="button" className={PRIMARY} disabled={busy || clusters.size < 2} onClick={merge}>Merge selected clusters</button>
          <button type="button" className={BTN} disabled={!clusters.size} onClick={() => setClusters(new Set())}>Clear selection</button>
        </div>
      ) : null}
      {q.isPending ? <div role="status" aria-label="Loading face groups" className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-72 rounded-2xl" />)}</div>
        : q.isError ? <button type="button" onClick={() => void q.refetch()} className={cn(PRIMARY, 'w-fit')}>Retry</button>
        : !groups.length ? <div className="rounded-card border border-dashed border-line p-8 text-center"><h3 className="font-bold">{EMPTY_TITLE}</h3><p className="mx-auto mt-1 max-w-xl text-sm text-muted">{EMPTY_BODY}</p></div>
        : <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">{groups.map((g) => <Card key={g.id} group={g} checked={clusters.has(g.id)} busy={busy} onCheck={() => toggle(g.id)} onView={() => view(g)} onAssign={(p) => assign(g, p)} onClear={() => clear(g)} onSplit={split} onConfirm={(s, src) => confirmSuggestion(g, s, src)} onReject={(s, src) => void reject(g, s, src)} />)}</div>}
      {ask ? <Confirm title={ask.title} text={ask.text} ok={ask.ok} onCancel={() => setAsk(null)} onOk={() => { const go = ask.run; setAsk(null); void go() }} /> : null}
    </section>
  )
}
