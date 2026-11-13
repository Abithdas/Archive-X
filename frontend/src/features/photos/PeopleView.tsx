import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Confirm } from '@/design/Confirm'
import { Icon } from '@/design/Icon'
import { Skeleton } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import { BlurImg } from '@/design/BlurImg'
import { FaceGroups, useFaceGroups } from './FaceGroups'
import { FIND_STEPS, analyzeBody, analyzeCopy, assignBody, bestGuess, findPeopleHint, findStage, groupLabel, jobDone, jobEndText, jobStatusText, peopleSections, rejectBody, suggestionBody, viewFilter, type FaceGroup, type Suggestion } from './faces'
import { usePhotoStore } from './store'

const BTN = 'h-8 rounded-lg border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5 disabled:opacity-50'
const PRIMARY = 'h-8 rounded-lg bg-accent px-3 text-xs font-bold text-accent-fg disabled:opacity-50'
const sourceOf = (s: Suggestion) => (s.source === 'profile_image' || s.source === 'confirmed_assignment' || s.source === 'metadata' ? s.source : 'metadata')
const why = (e: unknown, fb: string) => (e instanceof ApiError && e.detail ? e.detail : fb)

function Circle({ g, size = 'size-24' }: { g: FaceGroup; size?: string }) {
  const [ok, setOk] = useState(true)
  return (
    <span className={cn('grid shrink-0 place-items-center overflow-hidden rounded-full bg-surface-2 text-2xl font-bold text-muted', size)}>
      {ok && g.representative_url ? <BlurImg src={g.representative_url} alt="" loading="lazy" decoding="async" onError={() => setOk(false)} className="size-full object-cover" /> : <span aria-hidden="true">?</span>}
    </span>
  )
}

/** Type a performer name and pick one. */
function NamePicker({ onPick, onCancel }: { onPick: (p: { id: number; name: string }) => void; onCancel: () => void }) {
  const [text, setText] = useState('')
  const [items, setItems] = useState<Array<{ id: number; name: string; image_url?: string }>>([])
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])
  const type = (v: string) => {
    setText(v); clearTimeout(timer.current)
    if (v.trim().length < 2) return void setItems([])
    timer.current = setTimeout(() => { api<{ items?: typeof items }>(`/api/photos/faces/performers?limit=8&q=${encodeURIComponent(v.trim())}`).then((r) => setItems(r.items ?? [])).catch(() => setItems([])) }, 200)
  }
  return (
    <div className="grid w-full gap-1">
      <input autoFocus type="search" value={text} onChange={(e) => type(e.target.value)} onKeyDown={(e) => e.key === 'Escape' && onCancel()} placeholder="Type a performer name…" aria-label="Performer name" className="h-9 w-full rounded-lg border border-line-2 bg-bg px-3 text-sm outline-none focus:border-accent/60" />
      {items.length ? (
        <div role="listbox" aria-label="Performers" className="grid max-h-44 overflow-y-auto rounded-lg border border-line bg-surface p-1 text-left">
          {items.map((p) => <button key={p.id} type="button" role="option" aria-selected="false" onClick={() => onPick(p)} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-fg/10"><img src={p.image_url || `/api/performers/${p.id}/image?w=96`} alt="" className="size-7 rounded-full bg-surface-2 object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} />{p.name}</button>)}
        </div>
      ) : null}
      <button type="button" onClick={onCancel} className="w-fit text-xs text-muted hover:underline">Cancel</button>
    </div>
  )
}

function Card({ g, busy, onOpen, onYes, onNo, onName }: { g: FaceGroup; busy: boolean; onOpen: () => void; onYes: (s: Suggestion) => void; onNo: (s: Suggestion) => void; onName: (p: { id: number; name: string }) => void }) {
  const guess = bestGuess(g)
  const [naming, setNaming] = useState(false)
  const named = Boolean(g.performer_id)
  return (
    <article aria-label={groupLabel(g)} className="grid w-[190px] shrink-0 content-start justify-items-center gap-2 rounded-2xl border border-line bg-surface p-3 text-center">
      <button type="button" onClick={onOpen} aria-label={`Show photos of ${groupLabel(g)}`} className="rounded-full outline-none ring-accent/60 transition hover:scale-[1.03] focus-visible:ring-2"><Circle g={g} /></button>
      <div className="min-w-0 max-w-full">
        <b className="block truncate text-sm">{named ? groupLabel(g) : guess ? `Is this ${guess.name}?` : 'Who is this?'}</b>
        <small className="text-xs text-muted">{Number(g.photo_count ?? 0).toLocaleString()} photos</small>
      </div>
      {named ? null : naming ? <NamePicker onPick={(p) => { setNaming(false); onName(p) }} onCancel={() => setNaming(false)} /> : guess ? (
        <div className="flex flex-wrap justify-center gap-1.5">
          <button type="button" disabled={busy} className={PRIMARY} onClick={() => onYes(guess)} aria-label={`Yes, this is ${guess.name}`}>Yes</button>
          <button type="button" disabled={busy} className={BTN} onClick={() => onNo(guess)} aria-label={`No, this is not ${guess.name}`}>No</button>
          <button type="button" className={BTN} onClick={() => setNaming(true)}>Someone else</button>
        </div>
      ) : <button type="button" className={PRIMARY} onClick={() => setNaming(true)}>Name</button>}
    </article>
  )
}

/** Where "Find people" is: four steps with the current one marked, a bar, and the job's own words. */
function FindProgress({ stage, progress, text }: { stage: number; progress: number; text: string }) {
  return (
    <section aria-label="Finding people" className="grid gap-3 rounded-2xl border border-line bg-surface p-4">
      <ol className="grid gap-2 sm:grid-cols-4">
        {FIND_STEPS.map((label, n) => (
          <li key={label} aria-current={n === stage ? 'step' : undefined} className={cn('flex items-center gap-2 text-sm', n < stage ? 'text-fg' : n === stage ? 'font-semibold text-fg' : 'text-faint')}>
            <span aria-hidden="true" className={cn('grid size-5 shrink-0 place-items-center rounded-full text-[11px] font-bold', n < stage ? 'bg-good text-white' : n === stage ? 'bg-accent text-accent-fg' : 'bg-surface-2 text-muted')}>{n < stage ? '✓' : n + 1}</span>
            {label}
          </li>
        ))}
      </ol>
      <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress)} aria-label="Progress" className="h-1.5 overflow-hidden rounded-full bg-surface-2"><div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${Math.max(3, Math.min(100, progress))}%` }} /></div>
      <p className="text-xs text-muted [overflow-wrap:anywhere]">{text}</p>
    </section>
  )
}

/** People inside Photos: faces found in your photos, in plain groups; click a person to see their photos. */
export function PeopleView() {
  const qc = useQueryClient()
  const q = useFaceGroups([])
  const { setFace, setSelected, setTab } = usePhotoStore()
  const groups = q.data?.groups ?? []
  const sec = peopleSections(groups)
  const [busy, setBusy] = useState(false)
  const [finding, setFinding] = useState<null | { stage: number; progress: number; text: string }>(null)
  const [ask, setAsk] = useState(false)
  const [showRare, setShowRare] = useState(false)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const changed = async () => { await Promise.all([qc.invalidateQueries({ queryKey: ['face-groups'] }), qc.invalidateQueries({ queryKey: ['photos'] })]) }
  const open = (g: FaceGroup) => { const v = viewFilter(g); if (v.channels.length) setSelected(v.channels); setFace(v.filter); setTab('library') }
  const send = async (g: FaceGroup, body: object, ok: string) => {
    setBusy(true)
    try { const r = await api<{ ok?: boolean; detail?: string }>(`/api/photos/faces/groups/${g.id}/assign`, { method: 'POST', json: body }); if (!r?.ok) throw new ApiError(0, r?.detail || ''); toast(ok); await changed() }
    catch (e) { toast.error(why(e, 'Could not save the name.')) } finally { setBusy(false) }
  }
  const yes = (g: FaceGroup, s: Suggestion) => void send(g, suggestionBody(g, s, sourceOf(s), s.basis ?? ''), `Named ${s.name}`)
  const name = (g: FaceGroup, p: { id: number; name: string }) => void send(g, assignBody(g, p.id), `Named ${p.name}`)
  const no = async (g: FaceGroup, s: Suggestion) => {
    setBusy(true)
    try { const r = await api<{ ok?: boolean; detail?: string }>('/api/photos/faces/suggestions/reject', { method: 'POST', json: rejectBody(g, s, sourceOf(s)) }); if (!r?.ok) throw new ApiError(0, r?.detail || ''); await changed() }
    catch (e) { toast.error(why(e, 'Could not save that.')) } finally { setBusy(false) }
  }

  const find = async () => {
    setAsk(false); setFinding({ stage: 0, progress: 0, text: 'Starting…' })
    try {
      const r = await api<{ id?: string; detail?: string }>('/api/photos/faces/analyze', { method: 'POST', json: analyzeBody([]) })
      if (!r?.id) throw new ApiError(0, r?.detail || 'Could not start.')
      for (let i = 0; i < 7200 && alive.current; i++) {
        await new Promise((res) => setTimeout(res, 2000))
        let s: { status?: string; progress?: number; current_item?: string; operation?: string } | null = null
        try { s = await api(`/api/photos/scan-status/${encodeURIComponent(r.id)}`) } catch { continue }
        if (!s || !alive.current) continue
        const status = String(s.status || '').toUpperCase()
        setFinding({ stage: findStage(s.operation, status), progress: Number(s.progress || 0), text: s.operation || jobStatusText(status, s.progress, s.current_item) })
        if (jobDone(status)) { toast(jobEndText(status)); await changed(); break }
      }
    } catch (e) { toast.error(why(e, 'Could not start finding people.')) } finally { if (alive.current) setFinding(null) }
  }

  const grid = (list: FaceGroup[]) => <div className="flex flex-wrap gap-3">{list.map((g) => <Card key={g.id} g={g} busy={busy} onOpen={() => open(g)} onYes={(s) => yes(g, s)} onNo={(s) => void no(g, s)} onName={(p) => name(g, p)} />)}</div>
  const C = analyzeCopy(0)
  return (
    <section aria-label="People" className="grid gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p role="status" aria-live="polite" className="max-w-2xl text-sm text-muted">{finding ? '' : (q.isSuccess ? findPeopleHint(groups, q.data?.analysis) : '')}</p>
        <button type="button" disabled={!!finding} onClick={() => setAsk(true)} className="inline-flex h-10 items-center gap-2 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60"><Icon name="images" className="size-4" />{finding ? 'Finding people…' : 'Find people'}</button>
      </div>
      {finding ? <FindProgress {...finding} /> : null}

      {q.isPending ? <div role="status" aria-label="Loading people" className="flex flex-wrap gap-3">{[0, 1, 2, 3].map((i) => <div key={i} className="grid h-[236px] w-[190px] content-start justify-items-center gap-3 rounded-2xl border border-line bg-surface p-3"><Skeleton className="size-24 rounded-full" /><Skeleton className="h-4 w-28" /><Skeleton className="h-3 w-16" /><Skeleton className="h-8 w-32 rounded-lg" /></div>)}</div>
        : q.isError ? <button type="button" onClick={() => void q.refetch()} className={cn(PRIMARY, 'w-fit')}>Could not load people. Retry</button>
        : (<>
          {sec.guesses.length ? <section aria-label="Is this the right person?" className="grid gap-2"><h2 className="text-lg font-bold">Is this the right person?</h2>{grid(sec.guesses)}</section> : null}
          {sec.named.length ? <section aria-label="Named people" className="grid gap-2"><h2 className="text-lg font-bold">People</h2>{grid(sec.named)}</section> : null}
          {sec.toName.length ? <section aria-label="Faces to name" className="grid gap-2"><h2 className="text-lg font-bold">Who are these?</h2>{grid(sec.toName)}</section> : null}
          {sec.rare.length ? (
            <section aria-label="Other faces" className="grid gap-2">
              <button type="button" onClick={() => setShowRare(!showRare)} aria-expanded={showRare} className="w-fit text-sm font-semibold text-accent hover:underline">{showRare ? 'Hide' : 'Show'} {sec.rare.length.toLocaleString()} faces seen only once or twice</button>
              {showRare ? grid(sec.rare) : null}
            </section>
          ) : null}
        </>)}

      <details className="rounded-2xl border border-line p-3">
        <summary className="cursor-pointer text-sm font-bold">Advanced: merge, split and review groups</summary>
        <div className="mt-3"><FaceGroups onView={() => setTab('library')} /></div>
      </details>
      {ask ? <Confirm title={C.title} text={C.text} ok={C.ok} onCancel={() => setAsk(false)} onOk={() => void find()} /> : null}
    </section>
  )
}
