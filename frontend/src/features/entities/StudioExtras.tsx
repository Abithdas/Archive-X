import { useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { useSuggested } from '@/api/hooks'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { formatBytes, formatDuration } from '@/features/library/format'
import { imagePath, initial, plural } from './studio'
import { SIMILAR_EMPTY, SUGGEST_EMPTY, brandIcon, confirmBody, confirmPath, failedConfirm, failedReject, linkedText, pending, rejectPath, rejectedText, succeeded, suggestionQuestion, suggestionTitle, suggestionToolbar, yesLabel, type Kind, type Related, type SocialLink, type Suggested } from './studioextras'
import { responsive } from '@/design/responsive'

export function SocialPanel({ links, loading, label = 'Website and social links' }: { links: SocialLink[]; loading: boolean; label?: string }) {
  return (
    <div aria-label={label} className="mt-3 flex flex-wrap gap-2">
      {links.length ? links.map((l) => (
        <a key={l.href} href={l.href} target="_blank" rel="noopener noreferrer" aria-label={`Open ${l.label}`} title={l.label} className="inline-flex h-9 items-center gap-2 rounded-full border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5">
          <Icon name={brandIcon(l.href)} className="size-4" /><span>{l.label}</span><Icon name="external" className="size-3.5 text-muted" />
        </a>
      )) : <span className="text-xs text-muted">{loading ? 'Checking saved profile links…' : 'No website or social links saved.'}</span>}
    </div>
  )
}

export function DetailsCard({ rows, related, label = 'Studio details' }: { rows: Array<[string, string]>; related: Array<Related & { id: number; name: string }>; label?: string }) {
  return (
    <section aria-label={label} className="mb-6 rounded-card border border-line bg-surface p-4">
      <div className="mb-3 flex items-baseline justify-between gap-3"><h2 className="text-lg font-bold">Details</h2><span className="text-xs text-muted">From saved profile &amp; providers</span></div>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2.5 sm:grid-cols-2 lg:grid-cols-3">
        {rows.map(([k, v]) => <div key={k} className="min-w-0 text-left"><dt className="text-xs text-muted">{k}</dt><dd className="text-sm font-semibold [overflow-wrap:anywhere]">{v}</dd></div>)}
        {related.length ? (
          <div className="min-w-0 text-left sm:col-span-2 lg:col-span-3"><dt className="text-xs text-muted">Related studios</dt>
            <dd className="mt-1 flex flex-wrap gap-2">{related.map((r) => <Link key={r.id} to="/studios/$id" params={{ id: String(r.id) }} title={r.relation || 'Related studio'} className="rounded-xl border border-line-2 px-3 py-1.5 text-sm font-semibold hover:bg-fg/5">{r.name}<small className="ml-1.5 text-xs font-normal text-muted">{r.relation || 'Related'}</small></Link>)}</dd></div>
        ) : null}
      </dl>
    </section>
  )
}

export function SimilarPanel({ items }: { items: Array<{ id: number; name: string; image?: string | null; count: number }> }) {
  if (!items.length) return <p role="tabpanel" aria-label="Similar Studios" className="rounded-card border border-dashed border-line py-12 text-center text-muted">{SIMILAR_EMPTY}</p>
  return (
    <ul role="tabpanel" aria-label="Similar Studios" className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-4">
      {items.map((s) => <li key={s.id} className="text-center"><SimilarCard s={s} /></li>)}
    </ul>
  )
}
function SimilarCard({ s }: { s: { id: number; name: string; image?: string | null; count: number } }) {
  const [ok, setOk] = useState(true)
  return (
    <Link to="/studios/$id" params={{ id: String(s.id) }} className="block rounded-card outline-none focus-visible:ring-2 focus-visible:ring-accent">
      <span className="grid aspect-square w-full place-items-center overflow-hidden rounded-card border border-line bg-surface-2">
        {ok ? <img {...responsive(imagePath(s.id, s.image), '(max-width: 639px) 46vw, 220px', [240, 320, 480, 640])} alt="" loading="lazy" className="size-full object-contain p-2" onError={() => setOk(false)} /> : <span className="text-4xl font-extrabold text-muted" aria-hidden="true">{initial(s.name)}</span>}
      </span>
      <span className="mt-2 block truncate text-sm font-semibold">{s.name}</span><span className="text-xs text-muted">{plural(s.count, 'shared video')}</span>
    </Link>
  )
}

function SuggestionCard({ s, name, kind, busy, onConfirm, onReject }: { s: Suggested; name: string; kind: Kind; busy: boolean; onConfirm: () => void; onReject: () => void }) {
  const [thumb, setThumb] = useState(true)
  const meta = [s.duration ? formatDuration(s.duration) : '', s.size ? formatBytes(s.size) : ''].filter(Boolean).join(' · ')
  return (
    <article aria-label={suggestionTitle(s)} className="overflow-hidden rounded-card border border-line bg-surface">
      <div className="relative aspect-video bg-surface-3">
        {thumb ? <img src={`/api/thumb/${encodeURIComponent(String(s.channel_id))}/${s.message_id}?r=f3`} alt="" loading="lazy" decoding="async" className="size-full object-cover" onError={() => setThumb(false)} /> : <span className="grid size-full place-items-center text-muted"><Icon name="play" className="size-8" /></span>}
      </div>
      <div className="grid gap-1.5 p-3 text-left">
        <b className="line-clamp-2 text-sm [overflow-wrap:anywhere]">{suggestionTitle(s)}</b>
        {meta ? <span className="text-xs text-muted">{meta}</span> : null}
        <span className="text-xs text-muted">{suggestionQuestion(name)}</span>
        <div className="mt-1 flex flex-wrap gap-2">
          <button type="button" disabled={busy} onClick={onConfirm} className="h-9 rounded-xl bg-accent px-3 text-xs font-bold text-accent-fg disabled:opacity-60" aria-label={`${yesLabel(kind)}: ${suggestionTitle(s)}`}>{yesLabel(kind)}</button>
          <button type="button" disabled={busy} onClick={onReject} className="h-9 rounded-xl border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5 disabled:opacity-60" aria-label={`No, don’t link: ${suggestionTitle(s)}`}>No, don’t link</button>
        </div>
      </div>
    </article>
  )
}

/** Videos the scan thinks belong to this studio: link each one, or say it does not. */
export function SuggestionsPanel({ kind = 'studio', entityId: studioId, name }: { kind?: Kind; entityId: number; name: string }) {
  const q = useSuggested(kind, studioId)
  const qc = useQueryClient()
  const [busy, setBusy] = useState<Set<number>>(new Set())
  const act = async (s: Suggested, act_: 'confirm' | 'reject') => {
    if (busy.has(s.suggestion_id)) return
    setBusy((b) => new Set(b).add(s.suggestion_id))
    const fail = act_ === 'confirm' ? failedConfirm : failedReject
    try {
      const r = await api<{ ok?: boolean; detail?: string; message?: string }>(act_ === 'confirm' ? confirmPath(s.suggestion_id) : rejectPath(s.suggestion_id), { method: 'POST', json: confirmBody(studioId, kind) })
      if (!succeeded(r)) throw new ApiError(0, r?.detail || r?.message || (act_ === 'confirm' ? 'The server did not confirm this suggestion.' : `The server did not dismiss this ${kind} link.`))
      toast(act_ === 'confirm' ? linkedText(kind) : rejectedText(kind))
      await Promise.all([q.refetch(), qc.invalidateQueries({ queryKey: [kind, studioId] })])
    } catch (e) { toast.error(fail(e instanceof ApiError && e.detail ? e.detail : e instanceof Error ? e.message : String(e))) } finally { setBusy((b) => { const n = new Set(b); n.delete(s.suggestion_id); return n }) }
  }
  const list = q.data ?? []
  if (q.isPending) return <p role="tabpanel" aria-label="Suggestions" className="py-8 text-center text-sm text-muted">Loading suggestions…</p>
  if (!list.length) return <div role="tabpanel" aria-label="Suggestions" className="rounded-card border border-dashed border-line py-12 text-center"><b>{SUGGEST_EMPTY.title}</b><p className="mt-1 text-sm text-muted">{SUGGEST_EMPTY.body}</p></div>
  return (
    <div role="tabpanel" aria-label="Suggestions" className="grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 max-w-3xl text-left"><b>Suggested Videos</b><p className="mt-1 text-xs text-muted">{suggestionToolbar(name, kind)}</p></div><span className="rounded-full bg-surface-2 px-2.5 py-1 text-xs font-semibold">{pending(list.length)}</span></div>
      <div className={cn('grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4')}>{list.map((s) => <SuggestionCard key={s.suggestion_id} s={s} name={name} kind={kind} busy={busy.has(s.suggestion_id)} onConfirm={() => void act(s, 'confirm')} onReject={() => void act(s, 'reject')} />)}</div>
    </div>
  )
}
