import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '@/api/client'
import { Dialog } from '@/design/Dialog'
import { cn } from '@/design/cn'
import { FOCUS_HINT, NO_PROVIDER, REVIEW_CHOOSE, REVIEW_LOADING, REVIEW_NOTE, REVIEW_PAGE, REVIEW_SEARCH_HINT, REVIEW_TITLE, candidateLabel, candidatesUrl, groupLabel, loadedText, lookupFailed, providerName, reviewMeta, reviewSubtitle, saveLabel, type Candidate, type Candidates, type FaceGroup } from './faces'
import { withWidth } from '@/design/responsive'

export interface Pick { id: number; name: string }
const BTN = 'h-9 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const PRIMARY = 'h-9 rounded-xl bg-accent px-3 text-sm font-bold text-accent-fg disabled:opacity-50'

/** Compare an anonymous face group with a performer's saved profile picture and provider images before mapping it. */
export function ReferenceReview({ group, picked, onPick, onSave, onClose }: { group: FaceGroup; picked: Pick | null; onPick: (p: Pick) => void; onSave: () => Promise<boolean>; onClose: () => void }) {
  const [text, setText] = useState(picked?.name ?? '')
  const [found, setFound] = useState<Array<{ id: number; name: string; image_url?: string }> | null>(null)
  const [list, setList] = useState<Candidate[]>([])
  const [more, setMore] = useState(false)
  const [status, setStatus] = useState(picked ? REVIEW_LOADING : REVIEW_SEARCH_HINT)
  const [focus, setFocus] = useState<{ url: string; label: string } | null>(null)
  const [profileOk, setProfileOk] = useState(true)
  const [saving, setSaving] = useState(false)
  const seq = useRef(0)
  const nextOffset = useRef(0)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])

  const load = useCallback(async (id: number, offset: number, mine: number) => {
    setStatus(offset ? 'Loading more provider images…' : REVIEW_LOADING)
    try {
      const r = await api<Candidates>(candidatesUrl(id, offset))
      if (mine !== seq.current) return
      if (!r || r.ok === false) throw new ApiError(0, r?.detail || 'Could not load provider images.')
      const incoming = Array.isArray(r.candidates) ? r.candidates : []
      setList((cur) => { const next = offset ? cur.concat(incoming) : incoming; setStatus(loadedText(next)); return next })
      nextOffset.current = offset + REVIEW_PAGE
      setMore(Boolean(r.has_more))
    } catch (e) {
      if (mine !== seq.current) return
      setStatus(lookupFailed(e instanceof ApiError && e.detail ? e.detail : e instanceof Error ? e.message : String(e))); setMore(false)
    }
  }, [])
  useEffect(() => {
    if (!picked) return
    const mine = ++seq.current
    nextOffset.current = 0; setList([]); setMore(false); setFocus(null); setProfileOk(true)
    void load(picked.id, 0, mine)
  }, [picked?.id, load]) // eslint-disable-line react-hooks/exhaustive-deps

  const search = (v: string) => {
    setText(v); clearTimeout(timer.current)
    if (v.trim().length < 2) { setFound(null); return }
    timer.current = setTimeout(async () => {
      try { const r = await api<{ items?: Array<{ id: number; name: string; image_url?: string }> }>(`/api/photos/faces/performers?limit=12&q=${encodeURIComponent(v.trim())}`); setFound(Array.isArray(r?.items) ? r.items.slice(0, 12) : []) } catch { setFound([]) }
    }, 180)
  }
  const save = async () => { setSaving(true); try { if (await onSave()) onClose() } finally { setSaving(false) } }
  const name = picked?.name ?? null

  return (
    <Dialog wide open onClose={onClose} title={REVIEW_TITLE}
      footer={<div className="grid gap-2 border-t border-line pt-3"><p className="text-left text-[11px] text-muted">{REVIEW_NOTE}</p><div className="flex justify-end gap-2"><button type="button" className={BTN} onClick={onClose}>Cancel</button><button type="button" className={PRIMARY} disabled={!picked || saving} onClick={() => void save()}>{saveLabel(name)}</button></div></div>}>
      <div className="grid gap-4 text-left sm:grid-cols-2">
        <p className="text-xs text-muted sm:col-span-2">{reviewSubtitle(name)}</p>
        <section aria-label="Face group" className="grid content-start gap-2">
          <h3 className="text-sm font-bold">Face group</h3>
          <img src={group.representative_url ?? ''} alt="Representative anonymous face cluster" loading="lazy" className="aspect-square w-full max-w-xs rounded-xl bg-surface-3 object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} />
          <p className="text-xs text-muted">{reviewMeta(group)} · {groupLabel(group)}</p>
        </section>
        <section aria-label="Performer" className="grid content-start gap-2">
          <label htmlFor="ref-performer" className="text-xs font-semibold text-muted">Choose a performer to compare</label>
          <input id="ref-performer" type="search" value={text} onChange={(e) => search(e.target.value)} placeholder="Search performer names…" autoComplete="off" aria-label="Search performer names to compare" className="h-10 w-full rounded-xl border border-line-2 bg-surface px-3 text-sm" />
          {found ? (
            <div role="listbox" aria-label="Performers to compare" className="grid max-h-44 overflow-y-auto rounded-xl border border-line bg-surface p-1">
              {found.length ? found.map((p) => (
                <button key={p.id} type="button" role="option" aria-selected={picked?.id === p.id} onClick={() => { onPick({ id: p.id, name: p.name }); setText(p.name); setFound(null) }} className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-fg/5">
                  <img src={withWidth(p.image_url || `/api/performers/${p.id}/image`, 160)} alt="" loading="lazy" className="size-7 rounded-full bg-surface-2 object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} /><span className="flex-1 truncate">{p.name}</span>
                </button>
              )) : <div className="px-2 py-1.5 text-xs text-muted">No local performer matches.</div>}
            </div>
          ) : null}
          <h3 className="text-sm font-bold">{name ?? 'Choose a performer'}</h3>
          {picked && profileOk ? <figure className="m-0 grid gap-1"><img src={`/api/performers/${picked.id}/image`} alt="Saved performer profile image" loading="lazy" className="aspect-square w-full max-w-xs rounded-xl bg-surface-3 object-cover" onError={() => setProfileOk(false)} /><figcaption className="text-xs text-muted">Saved profile picture</figcaption></figure> : null}
          <h4 className="text-sm font-bold">Provider images</h4>
          <div role="status" aria-live="polite" className="text-xs text-muted">{picked ? status : REVIEW_SEARCH_HINT}</div>
          {picked && !list.length && !status.startsWith('Loading') && status.startsWith('No provider') ? <p className="text-xs text-muted">{NO_PROVIDER}</p> : null}
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
            {list.map((c, i) => (
              <button key={`${c.url}:${i}`} type="button" aria-pressed={focus?.url === c.url} title="Inspect this provider image" onClick={() => setFocus({ url: String(c.url || ''), label: candidateLabel(c) })} className={cn('overflow-hidden rounded-lg border-2 bg-surface-3', focus?.url === c.url ? 'border-accent' : 'border-transparent')}>
                <img src={String(c.url || '')} alt={`${providerName(c)} artwork`} loading="lazy" className="aspect-square w-full object-cover" onError={(e) => (e.currentTarget.style.visibility = 'hidden')} />
              </button>
            ))}
          </div>
          {more && picked ? <button type="button" className={cn(BTN, 'w-fit')} onClick={() => void load(picked.id, nextOffset.current, seq.current)}>Load more provider images</button> : null}
          <div className="grid gap-1">
            {focus ? <img src={focus.url} alt="Selected provider reference preview" loading="lazy" className="max-h-72 w-full max-w-sm rounded-xl bg-surface-3 object-contain" onError={() => setFocus(null)} /> : null}
            <span className="text-xs text-muted">{focus?.label ?? FOCUS_HINT}</span>
          </div>
          {!picked ? <span className="sr-only">{REVIEW_CHOOSE}</span> : null}
        </section>
      </div>
    </Dialog>
  )
}
