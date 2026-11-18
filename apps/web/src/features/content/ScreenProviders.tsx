import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/api/client'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { Switch } from '@/design/Switch'
import { PROVIDERS, candidateText, configuredOf, statusText, typedKind, type Candidate, type Provider, type ReviewItem, type ScreenSettings, type ScreenStatus } from './screen'
import { errorText, useContentMode } from './useContentMode'

const BOX = 'rounded-[18px] border border-line bg-surface p-5'
const INPUT = 'h-12 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60'
const BTN = 'h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const PRIMARY = 'h-10 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60'

/** Settings, Metadata, in SFW mode: the keys for the movie and TV providers, a button to name the videos, and the matches to check. */
export function ScreenProviders() {
  const mode = useContentMode()
  const qc = useQueryClient()
  const settings = useQuery({ queryKey: ['screen-settings'], queryFn: () => api<ScreenSettings>('/api/screen/settings'), enabled: mode.data?.mode === 'sfw' })
  const status = useQuery({ queryKey: ['screen-status'], queryFn: () => api<ScreenStatus>('/api/screen/status'), enabled: mode.data?.mode === 'sfw', refetchInterval: (q) => (q.state.data?.progress.running ? 2000 : false) })
  const [vals, setVals] = useState<Record<Provider, string>>({ tmdb: '', omdb: '', opensubtitles: '' })
  const [tests, setTests] = useState<Partial<Record<Provider, { state: 'checking' | 'ok' | 'bad'; text: string }>>>({})
  const [busy, setBusy] = useState(false)
  if (mode.data?.mode !== 'sfw') return null
  const refresh = () => void qc.invalidateQueries({ predicate: (q) => ['screen-settings', 'screen-status', 'screen-review'].includes(String(q.queryKey[0])) })

  const note = (p: Provider, state: 'checking' | 'ok' | 'bad', text: string) => setTests((t) => ({ ...t, [p]: { state, text } }))
  /** Asks the provider whether the saved key is accepted, and says so under its box. */
  const verify = async (p: Provider) => {
    note(p, 'checking', 'Checking the key with the provider…')
    try {
      const r = await api<{ ok: boolean; error?: string }>('/api/screen/check', { method: 'POST', json: { provider: p } })
      note(p, r.ok ? 'ok' : 'bad', r.ok ? 'Saved. The provider accepts this key.' : r.error || 'The provider did not accept this key.')
    } catch (e) { note(p, 'bad', errorText(e, 'Could not check the key.')) }
  }
  /** Saves what was typed (the server cleans a pasted link or sentence down to the key) and returns the providers that were saved. */
  const saveTyped = async (only?: Provider): Promise<Provider[]> => {
    const typed = PROVIDERS.filter((p) => (!only || p.id === only) && vals[p.id].trim())
    if (!typed.length) return []
    await api('/api/screen/settings', { method: 'POST', json: Object.fromEntries(typed.map((p) => [p.field, vals[p.id].trim()])) })
    setVals((v) => ({ ...v, ...Object.fromEntries(typed.map((p) => [p.id, ''])) }))
    refresh()
    return typed.map((p) => p.id)
  }
  const save = async () => {
    setBusy(true)
    try {
      const saved = await saveTyped()
      toast.success(saved.length ? 'Keys saved. Checking them now.' : 'Nothing new to save.')
      await Promise.all(saved.map(verify))
    } catch (e) { toast.error(errorText(e, 'Could not save the keys.')) } finally { setBusy(false) }
  }
  const check = async (p: Provider) => {
    try {
      await saveTyped(p)   // a key that was typed is saved first: testing what is on screen, not what was saved before
    } catch (e) { return note(p, 'bad', errorText(e, 'Could not save the key.')) }
    await verify(p)
  }
  const remove = async (p: Provider) => {
    try {
      await api('/api/screen/settings', { method: 'POST', json: { clear: [PROVIDERS.find((x) => x.id === p)!.field] } })
      setTests((t) => ({ ...t, [p]: undefined }))
      refresh()
      toast('Key removed.')
    } catch (e) { toast.error(errorText(e, 'Could not remove the key.')) }
  }
  const run = async () => {
    try { await api('/api/screen/identify', { method: 'POST', json: {} }); refresh() } catch (e) { toast.error(errorText(e, 'Could not start.')) }
  }

  return (
    <section aria-label="Movies and TV providers" className={BOX}>
      <h3 className="mb-1 flex items-center gap-3 text-xl font-extrabold"><span className="text-accent"><Icon name="library" className="size-5" /></span>Movies &amp; TV providers</h3>
      <p className="mb-4 text-sm text-muted">Archive X reads each file's name, asks these services what it is, and fills in the title, cast, studios or networks and genres. Keys stay on this PC.</p>
      <div className="grid gap-4 md:grid-cols-3">
        {PROVIDERS.map((p) => {
          const saved = configuredOf(settings.data, p.id)
          const mask = String(settings.data?.[p.field] ?? '')
          const t = tests[p.id]
          return (
            <div key={p.id} className="grid content-start gap-2 rounded-2xl border border-line p-4">
              <label className="grid gap-1.5 text-xs font-semibold text-muted"><span className="flex items-center justify-between gap-2">{p.label}{saved ? <span className="rounded-full bg-good/15 px-2 py-0.5 text-[11px] font-bold text-good">Saved</span> : null}</span>
                <input type="password" autoComplete="off" value={vals[p.id]} onChange={(e) => setVals((v) => ({ ...v, [p.id]: e.target.value }))} placeholder={saved ? `Saved${mask && mask !== 'set' ? ` ${mask}` : ''} · paste to replace` : 'Paste the key'} className={INPUT} />
              </label>
              <p className="text-xs text-faint">{p.help}</p>
              <div className="flex flex-wrap items-center gap-2">
                <button type="button" disabled={!saved && !vals[p.id].trim()} onClick={() => void check(p.id)} className={BTN}>Test</button>
                {saved ? <button type="button" onClick={() => void remove(p.id)} className={cn(BTN, 'text-muted')} aria-label={`Remove the ${p.id} key`}>Remove</button> : null}
              </div>
              <p role="status" aria-live="polite" className={cn('min-h-4 text-xs [overflow-wrap:anywhere]', t?.state === 'ok' ? 'text-good' : t?.state === 'bad' ? 'text-warn' : 'text-muted')}>{t?.text}</p>
            </div>
          )
        })}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3 rounded-2xl border border-line p-3">
        <Switch checked={Boolean(settings.data?.tvmaze_enabled)} onChange={(v) => void api('/api/screen/settings', { method: 'POST', json: { tvmaze_enabled: v } }).then(refresh).catch((e) => toast.error(errorText(e, 'Could not save.')))} label="Use TVmaze for TV shows" />
        <div className="min-w-0 flex-1"><p className="text-sm font-semibold">Use TVmaze for TV shows</p><p className="text-xs text-muted">No key needed. Show names from your files are sent to tvmaze.com, as TMDB would get them. Used when TMDB has no answer or has no key.</p></div>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button type="button" disabled={busy || !Object.values(vals).some((v) => v.trim())} onClick={() => void save()} className={PRIMARY}>Save keys</button>
        <button type="button" disabled={!status.data?.configured || Boolean(status.data?.progress.running) || !status.data?.pending} onClick={() => void run()} className={BTN}>Identify what is waiting</button>
        <span role="status" aria-live="polite" className="text-sm text-muted">{statusText(status.data)}</span>
      </div>
      <ReviewList />
    </section>
  )
}

/** Matches the providers could not settle: pick the right title, or leave it. */
function ReviewList() {
  const qc = useQueryClient()
  const review = useQuery({ queryKey: ['screen-review'], queryFn: () => api<{ items: ReviewItem[] }>('/api/screen/review') })
  const items = review.data?.items ?? []
  const [pending, setPending] = useState<number | null>(null)
  if (!items.length) return null
  const choose = async (it: ReviewItem, pick: { kind: 'movie' | 'tv'; tmdb_id?: number; imdb_id?: string }) => {
    setPending(it.id)
    try {
      await api(`/api/screen/review/${it.id}`, { method: 'POST', json: { ...pick, season: it.parsed.season, episode: it.parsed.episode } })
      await qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== 'content-mode' })
    } catch (e) { toast.error(errorText(e, 'Could not set that title.')) } finally { setPending(null) }
  }
  return (
    <div className="mt-5 grid gap-2" aria-label="Matches to check">
      <h4 className="text-sm font-bold">To check ({items.length.toLocaleString()})</h4>
      {items.map((it) => (
        <div key={it.id} className="rounded-2xl border border-line p-3">
          <p className="truncate text-sm font-semibold" title={it.filename}>{it.filename}</p>
          {it.candidates.length ? (
            <div className="mt-2 flex flex-wrap gap-2">
              {it.candidates.slice(0, 4).map((c) => (
                <button key={`${c.kind}${c.tmdb_id}`} type="button" disabled={pending === it.id} onClick={() => void choose(it, { kind: c.kind, tmdb_id: c.tmdb_id })} className={cn(BTN, 'h-9 text-xs')}>{candidateText(c)}</button>
              ))}
            </div>
          ) : <p className="mt-1 text-xs text-muted">Nothing found for “{it.parsed.title}”.</p>}
          <ManualFind item={it} busy={pending === it.id} onChoose={(pick) => void choose(it, pick)} />
        </div>
      ))}
    </div>
  )
}

/** Settle a video by hand: type words to search for, or paste an IMDb id (tt0133093) or a TMDB id (603). */
function ManualFind({ item, busy, onChoose }: { item: ReviewItem; busy: boolean; onChoose: (pick: { kind: 'movie' | 'tv'; tmdb_id?: number; imdb_id?: string }) => void }) {
  const [text, setText] = useState('')
  const [kind, setKind] = useState<'movie' | 'tv'>(item.parsed.kind === 'episode' ? 'tv' : 'movie')
  const [found, setFound] = useState<Candidate[] | null>(null)
  const [searching, setSearching] = useState(false)
  const typed = typedKind(text)
  const go = async () => {
    if (typed === 'imdb') return onChoose({ kind, imdb_id: text.trim().toLowerCase() })
    if (typed === 'tmdb') return onChoose({ kind, tmdb_id: Number(text.trim()) })
    if (typed !== 'words') return
    setSearching(true)
    try { setFound((await api<{ items: Candidate[] }>(`/api/screen/search?q=${encodeURIComponent(text.trim())}&kind=${kind}`)).items) }
    catch (e) { toast.error(errorText(e, 'Could not search.')); setFound(null) } finally { setSearching(false) }
  }
  return (
    <form className="mt-2 flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); void go() }}>
      <input type="text" value={text} onChange={(e) => { setText(e.target.value); setFound(null) }} aria-label={`Find a title for ${item.filename}`} placeholder="Search a title, or paste an IMDb or TMDB id" className="h-9 min-w-0 flex-1 basis-56 rounded-lg border border-line-2 bg-bg px-3 text-sm" />
      <select aria-label="Movie or show" value={kind} onChange={(e) => { setKind(e.target.value as 'movie' | 'tv'); setFound(null) }} className="h-9 rounded-lg border border-line-2 bg-bg px-2 text-sm"><option value="movie">Movie</option><option value="tv">TV show</option></select>
      <button type="submit" disabled={busy || searching || typed === 'none'} className={cn(BTN, 'h-9 text-xs')}>{typed === 'imdb' ? 'Use this IMDb id' : typed === 'tmdb' ? 'Use this TMDB id' : searching ? 'Searching…' : 'Search'}</button>
      {found ? (found.length ? <div className="flex w-full flex-wrap gap-2">{found.map((c) => <button key={`${c.kind}${c.tmdb_id}`} type="button" disabled={busy} onClick={() => onChoose({ kind: c.kind, tmdb_id: c.tmdb_id })} className={cn(BTN, 'h-9 text-xs')}>{candidateText(c)}</button>)}</div> : <p className="w-full text-xs text-muted">Nothing found. Try other words, or an id.</p>) : null}
    </form>
  )
}
