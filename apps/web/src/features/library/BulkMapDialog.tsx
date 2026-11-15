import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { canSave, comboDone, comboSteps, heading, hint, n, performerBody, performerChoices, performerLinked, performerNote, saveLabel, saveProblem, studioBody, studioHits, studioLinked, titleOnlyDone, wantsProviderSearch, type MapMode, type PerformerChoice, type PerformerHit, type ProviderHit } from './bulkmap'
import { choiceLabel, choiceNote, studioChoices, type StudioChoice } from './mapping'
import type { Picked } from './selection'
import { useDebounced } from './useDebounced'
import { ClearField } from '@/design/ClearField'

const INPUT = 'h-11 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60'

interface Row { key: string; label: string; note: string; icon: string }

/** A search box with a keyboard-navigable result list. `rows` is what to show for the current (debounced) text. */
function SearchPane({ label, placeholder, empty, query, onQuery, rows, loading, current, onClear, onPick, compact }: {
  compact?: boolean; label: string; placeholder: string; empty: string; query: string; onQuery: (v: string) => void; rows: Row[]; loading: boolean
  current?: string | null; onClear?: () => void; onPick: (i: number) => void
}) {
  const id = useId()
  const [active, setActive] = useState(0)
  const at = Math.min(active, Math.max(0, rows.length - 1))
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(Math.min(rows.length - 1, at + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(Math.max(0, at - 1)) }
    else if (e.key === 'Enter' && rows[at]) { e.preventDefault(); onPick(at) }
  }
  return (
    <section className="grid min-w-0 content-start gap-2" aria-label={label}>
      <h4 className="text-sm font-bold">{label}</h4>
      {onClear ? (
        <div className="min-h-9">
          {current ? (
            <span className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-line-2 bg-bg py-1 pl-3 pr-1.5 text-sm font-semibold">
              <span className="truncate">{current}</span>
              <button type="button" aria-label={`Remove ${label.toLowerCase()}`} onClick={onClear} className="grid size-6 place-items-center rounded-full text-muted hover:bg-fg/10 hover:text-fg"><Icon name="close" className="size-3.5" /></button>
            </span>
          ) : <span className="text-sm text-muted">No {label.toLowerCase()} selected.</span>}
        </div>
      ) : null}
      <ClearField value={query} onChange={(v) => { onQuery(v); setActive(0) }}  placeholder={placeholder} label={`Search ${label.toLowerCase()}`} clearLabel={`Clear ${label.toLowerCase()} search`} className="h-11 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5"
        inputProps={{ role: 'combobox', 'aria-expanded': rows.length > 0, 'aria-controls': id, 'aria-autocomplete': 'list', 'aria-activedescendant': rows.length ? `${id}-${at}` : undefined, onKeyDown: onKey }} />
      <div id={id} role="listbox" aria-label={`${label} results`} className={cn('overflow-y-auto rounded-xl border border-line', compact ? 'max-h-32 md:max-h-60' : 'max-h-60')}>
        {loading ? <p className="p-3 text-sm text-muted" role="status">Searching…</p>
          : rows.length ? rows.map((r, i) => (
            <div key={r.key} id={`${id}-${i}`} role="option" aria-selected={i === at} onMouseDown={(e) => { e.preventDefault(); onPick(i) }} className={cn('flex cursor-pointer items-center gap-3 px-3 py-2.5 hover:bg-fg/5', i === at && 'bg-accent/15')}>
              <span className="grid size-7 shrink-0 place-items-center rounded-full bg-surface-2 text-muted" aria-hidden="true"><Icon name={r.icon} className="size-3.5" /></span>
              <span className="min-w-0"><span className="block truncate text-sm">{r.label}</span>{r.note ? <span className="block text-[11px] text-muted">{r.note}</span> : null}</span>
            </div>
          )) : <p className="p-3 text-sm text-muted">{empty}</p>}
      </div>
    </section>
  )
}

function usePerformerRows(q: string) {
  const term = useDebounced(q.trim(), 140)
  const local = useQuery({ queryKey: ['performer-search', term], queryFn: () => api<PerformerHit[]>(`/api/performers?limit=20${term ? `&q=${encodeURIComponent(term)}` : ''}`), staleTime: 10_000, retry: false })
  const prov = useQuery({ queryKey: ['performer-provider-search', term], queryFn: () => api<ProviderHit[]>(`/api/performers/provider-search?limit=8&q=${encodeURIComponent(term)}`).catch(() => [] as ProviderHit[]), enabled: wantsProviderSearch(term), staleTime: 30_000, retry: false })
  const choices = performerChoices(Array.isArray(local.data) ? local.data : [], wantsProviderSearch(term) && Array.isArray(prov.data) ? prov.data : [])
  const loading = term !== q.trim() || local.isFetching || (wantsProviderSearch(term) && prov.isFetching)
  return { choices, loading }
}
function useStudioRows(q: string) {
  const term = useDebounced(q.trim(), 140)
  const res = useQuery({ queryKey: ['studio-search', term, 'bulk'], queryFn: () => api<unknown>(`/api/studios?limit=20${term ? `&q=${encodeURIComponent(term)}` : ''}`), staleTime: 10_000, retry: false })
  const choices = studioChoices(studioHits(res.data), term, { listWhenEmpty: true })
  return { choices, loading: term !== q.trim() || res.isFetching }
}

const performerRow = (c: PerformerChoice): Row => ({ key: `${c.kind}:${c.provider ?? ''}:${c.provider_id ?? c.id}:${c.name}`, label: c.name, note: performerNote(c), icon: 'user' })
const studioRow = (c: StudioChoice): Row => ({ key: `${c.id ?? 'new'}:${c.name}`, label: choiceLabel(c), note: choiceNote(c), icon: c.create ? 'plus' : 'circle' })

/** Set performer / studio / both / everything on the picked videos. Single modes apply on click; combined modes save once. */
export function BulkMapDialog({ mode, picked, prefill, onDone }: { mode: MapMode; picked: Picked[]; prefill?: { performer?: string; studio?: string }; onDone: (applied: boolean) => void }) {
  const qc = useQueryClient()
  const [title, setTitle] = useState('')
  const [performer, setPerformer] = useState<PerformerChoice | null>(mode === 'both' && prefill?.performer ? { name: prefill.performer, id: null, count: 0, image: null, kind: 'local' } : null)
  const [studio, setStudio] = useState<StudioChoice | null>(mode === 'both' && prefill?.studio ? { name: prefill.studio, id: null, count: 0, create: false } : null)
  const [pq, setPq] = useState('')
  const [sq, setSq] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState(hint(mode))
  const perfs = usePerformerRows(pq)
  const studios = useStudioRows(sq)
  const combined = mode === 'both' || mode === 'all'

  const refresh = () => { void qc.invalidateQueries({ queryKey: ['library'] }); void qc.invalidateQueries({ queryKey: ['entities'] }); void qc.invalidateQueries({ queryKey: ['channels'] }); void qc.invalidateQueries({ queryKey: ['performer'] }); void qc.invalidateQueries({ queryKey: ['studio'] }) }
  const detail = (e: unknown, fb: string) => (e instanceof ApiError ? e.detail || fb : fb)

  const applyOne = async (kind: 'performer' | 'site', i: number) => {
    const p = kind === 'performer' ? perfs.choices[i] : undefined, s = kind === 'site' ? studios.choices[i] : undefined
    if (!p && !s) return
    setBusy(true)
    try {
      const r = await api<{ ok?: boolean; changed?: number; detail?: string }>('/api/mapping/bulk', { method: 'POST', json: p ? performerBody(picked, p) : studioBody(picked, s!) })
      if (!r?.ok) throw new ApiError(0, r?.detail || 'unknown error')
      const k = n(r.changed, picked.length)
      toast(p ? performerLinked(p.name.trim(), k) : studioLinked(s!.name.trim(), k))
      refresh(); onDone(true)
    } catch (e) { toast.error(`Could not map ${p ? 'performer' : 'studio'}: ${detail(e, 'unknown error')}`) } finally { setBusy(false) }
  }

  const save = async () => {
    const combo = { title, performer, studio }
    if (!canSave(mode, combo)) return void toast(saveProblem(mode))
    const steps = comboSteps(picked, combo)
    setBusy(true); setStatus('Saving mapping…')
    let changed = picked.length
    try {
      for (const step of steps) {
        const r = await api<{ ok?: boolean; changed?: number; detail?: string }>('/api/mapping/bulk', { method: 'POST', json: step.body })
        if (!r?.ok) throw new ApiError(0, r?.detail || step.failure)
        changed = n(r.changed, picked.length)
      }
      const last = steps[steps.length - 1]!
      toast(last.kind === 'title' ? titleOnlyDone(changed) : comboDone(changed, Boolean(title.trim()), last.body.field as 'performer_site' | 'performer' | 'site'))
      refresh(); onDone(true)
    } catch (e) {
      setStatus('Save failed')
      const saved = steps.findIndex((s) => s.kind === 'title') >= 0 && title.trim() ? ' (The title may already be saved.)' : ''
      toast.error(`${detail(e, 'Combined mapping failed.')}${steps.length > 1 ? saved : ''}`)
    } finally { setBusy(false) }
  }

  const perfPane = (
    <SearchPane compact={combined} label="Performer" placeholder="Search performer…" empty="Type a name to search your performers." query={pq} onQuery={setPq} rows={perfs.choices.map(performerRow)} loading={perfs.loading}
      current={combined ? performer?.name : undefined} onClear={combined ? () => setPerformer(null) : undefined}
      onPick={(i) => (combined ? (setPerformer(perfs.choices[i] ?? null), setPq('')) : void applyOne('performer', i))} />
  )
  const studioPane = (
    <SearchPane compact={combined} label="Studio" placeholder="Search studio…" empty="Type a studio name to search your studios." query={sq} onQuery={setSq} rows={studios.choices.map(studioRow)} loading={studios.loading}
      current={combined ? studio?.name : undefined} onClear={combined ? () => setStudio(null) : undefined}
      onPick={(i) => (combined ? (setStudio(studios.choices[i] ?? null), setSq('')) : void applyOne('site', i))} />
  )
  const count = `${picked.length} video${picked.length === 1 ? '' : 's'} selected`

  return (
    <Dialog open onClose={() => !busy && onDone(false)} title={heading(mode)}>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
        <p className="text-xs text-muted">{count}{combined ? '' : ' · choose one below'}</p>
        {mode === 'all' ? (
          <label className="grid gap-1.5 text-xs font-semibold text-muted">Title <span className="font-normal">(optional, applied to every selected video)</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Leave blank to keep each video's title" autoComplete="off" className={INPUT} />
          </label>
        ) : null}
        {mode === 'performer' ? perfPane : mode === 'site' ? studioPane : (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-2">{perfPane}{studioPane}</div>
        )}
        {combined ? (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p role="status" aria-live="polite" className="text-[11px] text-muted">{status}</p>
            <div className="flex gap-2">
              <button type="button" disabled={busy} onClick={() => onDone(false)} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button>
              <button type="button" disabled={busy || !canSave(mode, { title, performer, studio })} onClick={() => void save()} className="h-11 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60">{saveLabel(mode)}</button>
            </div>
          </div>
        ) : <div className="flex justify-end"><button type="button" disabled={busy} onClick={() => onDone(false)} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button></div>}
      </div>
    </Dialog>
  )
}
