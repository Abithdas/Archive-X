import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import type { LibraryItem } from '@/api/types'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { formatDuration } from '@/features/library/format'
import { useDebounced } from '@/features/library/useDebounced'
import { LOOKUP_FAILED, LOOKUP_IDLE, LOOKUP_NEEDS_INPUT, LOOKUP_SEARCHING, applyMatch, candidateView, hintChips, initialPeople, initialStudio, lookupAllowed, lookupBody, lookupStatus, originLabel, pickPerformer, pickStudio, safeHttpUrl, samePerformer, saveBody, saveOutcome, searchItems, stagedItems, type CaptionSync, type Item, type LocalHit, type LookupResult, type Match, type Person, type ProviderHit, type Studio } from './quickedit'

const INPUT = 'h-11 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60'
const PANE = 'grid min-w-0 content-start gap-2 rounded-2xl border border-line p-3'
const asList = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : [])

/** One searchable list (performers or studios): local rows, provider rows and "create new". */
function Picker({ kind, q, onQ, items, loading, onPick }: { kind: 'performer' | 'studio'; q: string; onQ: (v: string) => void; items: Item[]; loading: boolean; onPick: (i: number) => void }) {
  return (
    <>
      <input value={q} onChange={(e) => onQ(e.target.value)} placeholder={`Search ${kind}…`} aria-label={`Search ${kind}`} autoComplete="off" className={INPUT} />
      <div role="listbox" aria-label={`${kind === 'performer' ? 'Performer' : 'Studio'} results`} className="max-h-56 overflow-y-auto rounded-xl border border-line">
        {loading ? <p role="status" className="p-3 text-sm text-muted">Searching…</p>
          : items.length ? items.map((x, i) => (
            <button key={`${x.kind}:${x.provider ?? ''}:${x.provider_id ?? x.id ?? ''}:${x.name}`} type="button" role="option" aria-selected={Boolean(x.selected)} onClick={() => onPick(i)} className={cn('flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-fg/5', x.selected && 'bg-accent/15')}>
              <span className="grid size-8 shrink-0 place-items-center rounded-full bg-surface-2 text-xs font-bold text-muted" aria-hidden="true">{x.create ? <Icon name="plus" className="size-3.5" /> : (x.name.trim().charAt(0).toUpperCase() || '?')}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-semibold">{x.create ? `Create “${x.name}”` : x.name}</span>
                <span className="block truncate text-[11px] text-muted">{originLabel(x, kind)}{x.provider_id ? ` · ID ${String(x.provider_id)}` : ''}</span>
              </span>
              <span className="shrink-0 text-[11px] font-bold text-muted">{x.create ? '＋ Create' : x.kind === 'provider' ? (x.selected ? 'Added' : 'API') : (x.count ?? 0)}</span>
            </button>
          )) : <p className="p-3 text-sm text-muted">No matches in local catalogue or configured providers.</p>}
      </div>
    </>
  )
}

/** "Edit video": title, performers, studio and a provider video-data lookup. Nothing is saved until Save changes. */
export function QuickEditDialog({ item, onDone }: { item: LibraryItem; onDone: () => void }) {
  const qc = useQueryClient()
  const [title, setTitle] = useState(item.name)
  const [performers, setPerformers] = useState<Person[]>(() => initialPeople(item.performers))
  const [studio, setStudio] = useState<Studio | null>(() => initialStudio(item))
  const [performerPicked, setPerformerPicked] = useState(false)
  const [studioPicked, setStudioPicked] = useState(false)
  const [pq, setPq] = useState('')
  const [sq, setSq] = useState('')
  const [matches, setMatches] = useState<Match[]>([])
  const [hints, setHints] = useState<ReturnType<typeof hintChips>>([])
  const [status, setStatus] = useState('Loading current metadata…')
  const [note, setNote] = useState('')
  const [looking, setLooking] = useState(false)
  const [saving, setSaving] = useState(false)
  const lookupSeq = useRef(0)

  // ---- searches (local + provider), debounced
  const pTerm = useDebounced(pq.trim(), 140), sTerm = useDebounced(sq.trim(), 140)
  const pLocal = useQuery({ queryKey: ['qe-performers', pTerm], queryFn: () => api<LocalHit[]>(`/api/performers?limit=18&q=${encodeURIComponent(pTerm)}`).catch(() => [] as LocalHit[]), enabled: pTerm !== '', staleTime: 10_000 })
  const pProv = useQuery({ queryKey: ['qe-performers-prov', pTerm], queryFn: () => api<ProviderHit[]>(`/api/performers/provider-search?limit=8&q=${encodeURIComponent(pTerm)}`).catch(() => [] as ProviderHit[]), enabled: pTerm.length >= 2, staleTime: 30_000 })
  const sLocal = useQuery({ queryKey: ['qe-studios', sTerm], queryFn: () => api<LocalHit[]>(`/api/studios?limit=18&q=${encodeURIComponent(sTerm)}`).catch(() => [] as LocalHit[]), enabled: sTerm !== '', staleTime: 10_000 })
  const sProv = useQuery({ queryKey: ['qe-studios-prov', sTerm], queryFn: () => api<ProviderHit[]>(`/api/studios/provider-search?limit=8&q=${encodeURIComponent(sTerm)}`).catch(() => [] as ProviderHit[]), enabled: sTerm.length >= 2, staleTime: 30_000 })
  const staged = { performers, studio }
  const pItems: Item[] = pq.trim() ? (pTerm === pq.trim() ? searchItems(pTerm, asList(pLocal.data), asList(pProv.data), staged).map((x) => ({ ...x, selected: x.create ? false : performers.some((p) => samePerformer(p, x)) })) : []) : stagedItems(performers, studio, 'performer')
  const sItems: Item[] = sq.trim() ? (sTerm === sq.trim() ? searchItems(sTerm, asList(sLocal.data), asList(sProv.data), staged) : []) : stagedItems(performers, studio, 'studio')
  const pLoading = pq.trim() !== '' && (pTerm !== pq.trim() || pLocal.isFetching || (pTerm.length >= 2 && pProv.isFetching))
  const sLoading = sq.trim() !== '' && (sTerm !== sq.trim() || sLocal.isFetching || (sTerm.length >= 2 && sProv.isFetching))

  // ---- provider video-data lookup
  const lookup = async () => {
    const t = title.trim()
    const performer = pq.trim() || String(performers[0]?.name ?? '').trim()
    const studioName = sq.trim() || String(studio?.name ?? '').trim()
    if (!lookupAllowed(t, performer, studioName)) { setMatches([]); setStatus(LOOKUP_NEEDS_INPUT); return }
    const seq = ++lookupSeq.current
    setLooking(true); setStatus(LOOKUP_SEARCHING)
    try {
      const r = await api<LookupResult>('/api/media/quick-edit/lookup', { method: 'POST', json: lookupBody({ cid: item._cid, mid: item.id, title: t, performerQuery: pq.trim(), studioQuery: sq.trim(), performers, studio, performerPicked, studioPicked }) })
      if (seq !== lookupSeq.current) return
      if (!r?.ok) { setStatus(r?.detail || 'Video-data lookup failed. Check provider configuration and try again.'); return }
      setMatches(r.matches ?? []); setHints(hintChips(r.filename_hints)); setStatus(lookupStatus(r))
    } catch { if (seq === lookupSeq.current) setStatus(LOOKUP_FAILED) } finally { if (seq === lookupSeq.current) setLooking(false) }
  }
  const lookupRef = useRef(lookup)
  lookupRef.current = lookup
  const first = useRef(true)
  // Looks up on open and again shortly after the title, the staged people/studio or a search box changes.
  useEffect(() => {
    const t = setTimeout(() => void lookupRef.current(), first.current ? 0 : 500)
    first.current = false
    return () => clearTimeout(t)
  }, [title, pq, sq, performers, studio])
  useEffect(() => () => { lookupSeq.current += 1 }, [])

  const pick = (kind: 'performer' | 'studio', i: number) => {
    const x = (kind === 'performer' ? pItems : sItems)[i]
    if (!x) return
    if (kind === 'performer') { setPerformers((cur) => pickPerformer(cur, x)); setPerformerPicked(true); setPq('') }
    else { setStudio(pickStudio(x)); setStudioPicked(true); setSq('') }
  }
  const apply = (m: Match) => {
    const a = applyMatch(m)
    if (a.title) setTitle(a.title)
    setPerformers(a.performers)
    if (a.studio) setStudio(a.studio)
    setNote(a.message)
  }

  const save = async () => {
    setSaving(true)
    try {
      const r = await api<{ ok?: boolean; detail?: string; caption_sync?: CaptionSync }>('/api/media/quick-edit', { method: 'POST', json: saveBody(item._cid, item.id, title, performers, studio) })
      if (!r?.ok) { toast.error(r?.detail || 'Video changes could not be saved.'); return }
      void qc.invalidateQueries({ queryKey: ['library'] }); void qc.invalidateQueries({ queryKey: ['video'] }); void qc.invalidateQueries({ queryKey: ['entities'] })
      const out = saveOutcome(r.caption_sync)
      toast(out.toast)
      if (out.close) onDone(); else setStatus(out.status)
    } catch (e) {
      toast.error(e instanceof ApiError && e.detail ? e.detail : 'Save failed. Check the connection and try again.')
    } finally { setSaving(false) }
  }

  const summary = [item.filename || item.name || 'Video', item.duration ? formatDuration(item.duration) : ''].filter(Boolean).join(' · ')
  return (
    <Dialog wide open onClose={() => !saving && onDone()} title="Edit video"
      footer={
        <div className="flex flex-wrap items-center justify-end gap-3 border-t border-line pt-3">
          <span className="mr-auto text-[11px] text-muted">Saving also syncs the Telegram caption.</span>
          <button type="button" disabled={saving} onClick={onDone} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button>
          <button type="button" disabled={saving} onClick={() => void save()} className="h-11 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60">{saving ? 'Saving & syncing…' : 'Save changes'}</button>
        </div>
      }>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
        <p className="text-xs text-muted">{summary}</p>
        <section className={PANE} aria-label="Title">
          <label htmlFor="qe-title" className="text-xs font-semibold text-muted">Title</label>
          <div className="flex gap-2">
            <input id="qe-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Video title" className={INPUT} />
            <button type="button" disabled={looking} onClick={() => void lookup()} aria-label="Find video data" title="Find video data" className="inline-flex h-11 shrink-0 items-center gap-2 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-60">
              <Icon name="search" className="size-4" /><span className="hidden sm:inline">{looking ? 'Searching…' : 'Find video data'}</span>
            </button>
          </div>
          {note ? <p role="status" className="rounded-lg bg-accent/10 px-3 py-2 text-xs text-accent">{note}</p> : null}
          {hints.length ? <div aria-label="Filename clues" className="flex flex-wrap gap-1.5">{hints.map((h) => <span key={`${h.kind}:${h.label}`} className={cn('rounded-full border px-2.5 py-0.5 text-[11px] font-semibold', h.kind === 'unverified' ? 'border-dashed border-line-2 text-muted' : h.kind === 'date' ? 'border-line-2 text-muted' : 'border-accent/40 text-accent')}>{h.label}</span>)}</div> : null}
        </section>

        <div className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-3">
          <section className={PANE} aria-label="Performer">
            <h3 className="text-sm font-bold">Performer</h3>
            <div role="group" aria-label="Selected performers" className="flex min-h-9 flex-wrap gap-1.5">
              {performers.length ? performers.map((p, i) => (
                <span key={`${p.name}:${i}`} className="inline-flex max-w-full items-center gap-1 rounded-full border border-line-2 bg-bg py-0.5 pl-2.5 pr-1 text-sm font-semibold">
                  <span className="truncate">{p.name}</span>
                  <button type="button" aria-label={`Remove ${p.name}`} onClick={() => setPerformers((cur) => cur.filter((_, j) => j !== i))} className="grid size-6 place-items-center rounded-full text-muted hover:bg-fg/10 hover:text-fg"><Icon name="close" className="size-3.5" /></button>
                </span>
              )) : <span className="text-sm text-muted">No performers selected</span>}
            </div>
            <Picker kind="performer" q={pq} onQ={setPq} items={pItems} loading={pLoading} onPick={(i) => pick('performer', i)} />
          </section>

          <section className={PANE} aria-label="Studio">
            <h3 className="text-sm font-bold">Studio</h3>
            <div role="group" aria-label="Selected studio" className="min-h-9">
              {studio ? (
                <span className="inline-flex max-w-full items-center gap-1 rounded-full border border-line-2 bg-bg py-0.5 pl-2.5 pr-1 text-sm font-semibold">
                  <span className="truncate">{studio.name}</span>
                  <button type="button" aria-label="Remove studio" onClick={() => setStudio(null)} className="grid size-6 place-items-center rounded-full text-muted hover:bg-fg/10 hover:text-fg"><Icon name="close" className="size-3.5" /></button>
                </span>
              ) : <span className="text-sm text-muted">No studio selected</span>}
            </div>
            <Picker kind="studio" q={sq} onQ={setSq} items={sItems} loading={sLoading} onPick={(i) => pick('studio', i)} />
          </section>

          <section className={PANE} aria-label="Matched metadata">
            <h3 className="text-sm font-bold">Matched metadata</h3>
            <div className="grid max-h-72 gap-2 overflow-y-auto">
              {matches.length ? matches.map((m, i) => {
                const v = candidateView(m)
                return (
                  <article key={i} className="grid gap-2 rounded-xl border border-line p-2.5">
                    <div className="flex gap-2.5">
                      {safeHttpUrl(v.thumb) ? <img src={v.thumb} alt="" loading="lazy" className="h-12 w-20 shrink-0 rounded-lg bg-surface-2 object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} /> : <span className="h-12 w-20 shrink-0 rounded-lg bg-surface-2" aria-hidden="true" />}
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-bold" title={v.title}>{v.title}</p>
                        <p className="truncate text-[11px] text-muted">{v.detail}</p>
                        <p className="truncate text-[11px] text-muted">{v.cast}</p>
                      </div>
                      <span className={cn('h-fit shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold', v.tone === 'good' ? 'bg-good/15 text-good' : v.tone === 'warn' ? 'bg-warn/15 text-warn' : v.tone === 'bad' ? 'bg-bad/15 text-bad' : 'bg-surface-3 text-muted')}>{v.confidence}</span>
                    </div>
                    <button type="button" onClick={() => apply(m)} className="h-9 rounded-lg border border-line-2 text-sm font-semibold hover:bg-fg/5" aria-label={`Apply match: ${v.title}`}>Apply</button>
                  </article>
                )
              }) : <p className="text-sm text-muted">No matching provider video data.</p>}
            </div>
            <p role="status" aria-live="polite" className="text-[11px] text-muted">{status === 'Loading current metadata…' && !looking ? LOOKUP_IDLE : status}</p>
          </section>
        </div>
      </div>
    </Dialog>
  )
}
