import { useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, cardMotion, motion } from '@/design/motion'
import { SubtleNumber } from '@/design/SubtleNumber'
import { newMatchesText, useReviewCounts, type ReviewCount } from './useFaceReview'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Confirm } from '@/design/Confirm'
import { Icon } from '@/design/Icon'
import { Skeleton } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import { BlurImg } from '@/design/BlurImg'
import { useFaceGroups } from './useFaceGroups'
import { clusterIds, FIND_STEPS, scopeChips, analyzeBody, analyzeCopy, assignBody, bestGuess, findPeopleHint, findStage, groupLabel, jobDone, jobEndText, jobStatusText, peopleSections, rejectBody, suggestionBody, viewFilter, type FaceGroup, type Suggestion } from './faces'
import { useSearch } from '@/app/search'
import { nameMatches } from '@/features/entities/filter'
import { usePhotoStore } from './store'
import { FaceReview } from './FaceReview'
import { NamePicker } from './NamePicker'
import { PeopleStatus } from './PeopleStatus'
import { usePhotoChannels } from '@/api/hooks'

const BTN = 'h-9 rounded-lg border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5 disabled:opacity-50 sm:h-8'
const PRIMARY = 'h-9 rounded-lg bg-accent px-3 text-xs font-bold text-accent-fg disabled:opacity-50 sm:h-8'
const sourceOf = (s: Suggestion) => (s.source === 'profile_image' || s.source === 'confirmed_assignment' || s.source === 'metadata' ? s.source : 'metadata')
const why = (e: unknown, fb: string) => (e instanceof ApiError && e.detail ? e.detail : fb)

function Circle({ g, size = 'size-20 sm:size-24' }: { g: FaceGroup; size?: string }) {
  const [ok, setOk] = useState(true)
  return (
    <span className={cn('grid shrink-0 place-items-center overflow-hidden rounded-full bg-surface-2 text-2xl font-bold text-muted', size)}>
      {ok && g.representative_url ? <BlurImg src={g.representative_url} alt="" loading="lazy" decoding="async" onError={() => setOk(false)} className="size-full object-cover" /> : <span aria-hidden="true">?</span>}
    </span>
  )
}

function Card({ g, fresh, naming, setNaming, onOpen, onCheck, onYes, onNo, onName }: { g: FaceGroup; fresh?: ReviewCount; naming: boolean; setNaming: (on: boolean) => void; onOpen: () => void; onCheck: () => void; onYes: (s: Suggestion) => void; onNo: (s: Suggestion) => void; onName: (p: { id: number; name: string }) => void }) {
  const guess = bestGuess(g)
  const named = Boolean(g.performer_id)
  return (
    <article aria-label={groupLabel(g)} className="grid h-full w-full min-w-0 content-start justify-items-center gap-2 rounded-2xl border border-line bg-surface p-3 text-center">
      <span className="relative">
        <button type="button" onClick={onOpen} aria-label={`Show photos of ${groupLabel(g)}`} className="rounded-full outline-none ring-accent/60 transition hover:scale-[1.03] focus-visible:ring-2"><Circle g={g} /></button>
        {named && fresh ? <span title={newMatchesText(fresh.suggestions)} aria-hidden="true" className="absolute -right-1.5 -top-1.5 grid h-6 min-w-6 place-items-center rounded-full bg-accent px-1.5 text-[11px] font-bold text-accent-fg shadow ring-2 ring-surface"><SubtleNumber value={fresh.suggestions} /></span> : null}
      </span>
      <div className="min-w-0 max-w-full">
        <b className="block truncate text-sm">{named ? groupLabel(g) : guess ? `Is this ${guess.name}?` : 'Who is this?'}</b>
        <small className="text-xs text-muted">{Number(g.photo_count ?? 0).toLocaleString()} photos</small>
      </div>
      {named ? <button type="button" className={fresh ? PRIMARY : BTN} onClick={onCheck} aria-label={`Check faces of ${groupLabel(g)}${fresh ? `, ${newMatchesText(fresh.suggestions)}` : ''}`}>{fresh ? 'Review new matches' : 'Check faces'}</button> : naming ? <NamePicker onPick={(p) => { setNaming(false); onName(p) }} onCancel={() => setNaming(false)} /> : guess ? (
        <div className="flex flex-wrap justify-center gap-1.5">
          <button type="button" className={PRIMARY} onClick={() => onYes(guess)} aria-label={`Yes, this is ${guess.name}`}>Yes</button>
          <button type="button" className={BTN} onClick={() => onNo(guess)} aria-label={`No, this is not ${guess.name}`}>No</button>
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
  const channels = usePhotoChannels()
  const groups = q.data?.groups ?? []
  const search = useSearch((s) => s.q)
  const shown = useMemo(() => (search.trim() ? groups.filter((g) => nameMatches(groupLabel(g), search)) : groups), [groups, search])
  const fresh = useReviewCounts().data
  // Cards whose Yes or No is on its way to the server. Each answers on its own: the card steps aside at once and the next one can be answered
  // straight away, instead of every button waiting for the slowest request.
  const [answering, setAnswering] = useState<ReadonlySet<number>>(new Set())
  const mark = (id: number, on: boolean) => setAnswering((s) => { const n = new Set(s); if (on) n.add(id); else n.delete(id); return n })
  const all = peopleSections(shown)
  const sec = { ...all, guesses: all.guesses.filter((g) => !answering.has(Number(g.id))) }
  const [finding, setFinding] = useState<null | { stage: number; progress: number; text: string }>(null)
  const [ask, setAsk] = useState(false)
  const [showRare, setShowRare] = useState(false)
  const [checking, setChecking] = useState<FaceGroup | null>(null)
  const [namingId, setNamingId] = useState<number | null>(null)   // only one card has its name search open at a time
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const changed = async () => { await Promise.all([qc.invalidateQueries({ queryKey: ['face-groups'] }), qc.invalidateQueries({ queryKey: ['photos'] }), qc.invalidateQueries({ queryKey: ['face-learning'] })]) }
  const open = (g: FaceGroup) => { const v = viewFilter(g); if (v.channels.length) setSelected(v.channels); setFace(v.filter); setTab('library') }
  const send = async (g: FaceGroup, body: object, ok: string) => {
    mark(Number(g.id), true)
    try { const r = await api<{ ok?: boolean; detail?: string }>(`/api/photos/faces/groups/${g.id}/assign`, { method: 'POST', json: body }); if (!r?.ok) throw new ApiError(0, r?.detail || ''); toast(ok); await changed() }
    catch (e) { toast.error(why(e, 'Could not save the name.')) } finally { mark(Number(g.id), false) }
  }
  const doneText = (pid: number, nm: string) => (sec.named.some((g) => Number(g.performer_id) === pid) ? `Added to the existing ${nm}` : `Named ${nm}`)
  const yes = (g: FaceGroup, s: Suggestion) => void send(g, suggestionBody(g, s, sourceOf(s), s.basis ?? ''), doneText(s.id, s.name ?? ''))
  const name = (g: FaceGroup, p: { id: number; name: string }) => void send(g, assignBody(g, p.id), doneText(p.id, p.name))
  const openScope = (c: ReturnType<typeof scopeChips>[number]) => { setSelected((channels.data?.channels ?? []).map((ch) => String(ch.telegram_id))); setFace({ performerId: null, groupId: null, groupIds: [], label: c.label, scope: c.scope }); setTab('library') }
  const no = async (g: FaceGroup, s: Suggestion) => {
    mark(Number(g.id), true)
    try { const r = await api<{ ok?: boolean; detail?: string }>('/api/photos/faces/suggestions/reject', { method: 'POST', json: rejectBody(g, s, sourceOf(s)) }); if (!r?.ok) throw new ApiError(0, r?.detail || ''); await changed() }
    catch (e) { toast.error(why(e, 'Could not save that.')) } finally { mark(Number(g.id), false) }
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

  // The cards share the row equally and fill it to the right edge (150 px wide at least on a phone, 180 px from tablet width). The one being named
  // takes two cells (the whole row on a phone) so its search has room.
  const GRID = 'relative grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3 sm:grid-cols-[repeat(auto-fill,minmax(180px,1fr))]'
  const grid = (list: FaceGroup[]) => (
    <div className={GRID}>
      <AnimatePresence mode="popLayout" initial={false}>
        {list.map((g) => (
          <motion.div key={g.id} {...cardMotion(0)} className={cn('min-w-0', namingId === g.id && 'col-span-full sm:col-span-2')}>
            <Card g={g} fresh={g.performer_id ? fresh?.[Number(g.performer_id)] : undefined} naming={namingId === g.id} setNaming={(on) => setNamingId(on ? g.id : null)} onOpen={() => open(g)} onCheck={() => setChecking(g)} onYes={(s) => yes(g, s)} onNo={(s) => void no(g, s)} onName={(p) => name(g, p)} />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
  const C = analyzeCopy(0)
  return (
    <section aria-label="People" className="grid gap-6">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
        <PeopleStatus headline={finding ? 'Finding people…' : (q.isSuccess ? findPeopleHint(groups, q.data?.analysis) : '')} />
        <button type="button" disabled={!!finding} onClick={() => setAsk(true)} className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60 sm:h-10 sm:w-auto"><Icon name="images" className="size-4" />{finding ? 'Finding people…' : 'Find people'}</button>
      </div>
      {finding ? <FindProgress {...finding} /> : null}

      {q.isPending ? <div role="status" aria-label="Loading people" className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3 sm:grid-cols-[repeat(auto-fill,minmax(180px,1fr))]">{[0, 1, 2, 3].map((i) => <div key={i} className="grid h-[236px] content-start justify-items-center gap-3 rounded-2xl border border-line bg-surface p-3"><Skeleton className="size-24 rounded-full" /><Skeleton className="h-4 w-28" /><Skeleton className="h-3 w-16" /><Skeleton className="h-8 w-32 rounded-lg" /></div>)}</div>
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

      {q.isSuccess && scopeChips(q.data?.photo_scopes).length ? (
        <section aria-label="Photos without a named person" className="grid gap-2 rounded-2xl border border-line p-4">
          <h2 className="text-lg font-bold">Photos without a named person</h2>
          <ul className="grid gap-1.5">
            {scopeChips(q.data?.photo_scopes).map((c) => (
              <li key={c.scope} className="flex flex-wrap items-center gap-2 text-sm">
                <button type="button" onClick={() => openScope(c)} className="font-semibold text-accent hover:underline"><span className="tabular-nums">{c.count.toLocaleString()}</span> photos {c.text}</button>
                <span className="text-xs text-muted">{c.hint}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {checking ? <FaceReview performerId={checking.performer_id ? Number(checking.performer_id) : null} groupIds={clusterIds(checking)} label={groupLabel(checking)} onClose={() => setChecking(null)} /> : null}
      {ask ? <Confirm title={C.title} text={C.text} ok={C.ok} onCancel={() => setAsk(false)} onOk={() => void find()} /> : null}
    </section>
  )
}
