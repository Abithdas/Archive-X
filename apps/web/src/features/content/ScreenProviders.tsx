import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/api/client'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { PROVIDERS, candidateText, configuredOf, statusText, type Provider, type ReviewItem, type ScreenSettings, type ScreenStatus } from './screen'
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
  const [tests, setTests] = useState<Partial<Record<Provider, string>>>({})
  const [busy, setBusy] = useState(false)
  if (mode.data?.mode !== 'sfw') return null
  const refresh = () => void qc.invalidateQueries({ predicate: (q) => ['screen-settings', 'screen-status', 'screen-review'].includes(String(q.queryKey[0])) })

  const save = async () => {
    setBusy(true)
    try {
      const body = Object.fromEntries(PROVIDERS.map((p) => [p.field, vals[p.id].trim()]).filter(([, v]) => v))
      await api('/api/screen/settings', { method: 'POST', json: body })
      setVals({ tmdb: '', omdb: '', opensubtitles: '' })
      toast.success('Keys saved.')
      refresh()
    } catch (e) { toast.error(errorText(e, 'Could not save the keys.')) } finally { setBusy(false) }
  }
  const check = async (p: Provider) => {
    setTests((t) => ({ ...t, [p]: 'Testing…' }))
    try {
      const r = await api<{ ok: boolean; error?: string }>('/api/screen/check', { method: 'POST', json: { provider: p } })
      setTests((t) => ({ ...t, [p]: r.ok ? 'The key works.' : r.error || 'The key was not accepted.' }))
    } catch (e) { setTests((t) => ({ ...t, [p]: errorText(e, 'Could not test the key.') })) }
  }
  const run = async () => {
    try { await api('/api/screen/identify', { method: 'POST', json: {} }); refresh() } catch (e) { toast.error(errorText(e, 'Could not start.')) }
  }

  return (
    <section aria-label="Movies and TV providers" className={BOX}>
      <h3 className="mb-1 flex items-center gap-3 text-xl font-extrabold"><span className="text-accent"><Icon name="library" className="size-5" /></span>Movies &amp; TV providers</h3>
      <p className="mb-4 text-sm text-muted">Archive X reads each file's name, asks these services what it is, and fills in the title, cast, studios or networks and genres. Keys stay on this PC.</p>
      <div className="grid gap-4 md:grid-cols-3">
        {PROVIDERS.map((p) => (
          <div key={p.id} className="grid content-start gap-2 rounded-2xl border border-line p-4">
            <label className="grid gap-1.5 text-xs font-semibold text-muted">{p.label}
              <input type="password" autoComplete="off" value={vals[p.id]} onChange={(e) => setVals((v) => ({ ...v, [p.id]: e.target.value }))} placeholder={configuredOf(settings.data, p.id) ? `Saved ${String(settings.data?.[p.field] ?? '')} — leave blank to keep` : 'Paste the key'} className={INPUT} />
            </label>
            <p className="text-xs text-faint">{p.help}</p>
            <div className="flex items-center gap-3">
              <button type="button" disabled={!configuredOf(settings.data, p.id)} onClick={() => void check(p.id)} className={BTN}>Test</button>
              <span role="status" aria-live="polite" className="text-xs text-muted">{tests[p.id]}</span>
            </div>
          </div>
        ))}
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
  const choose = async (it: ReviewItem, c: ReviewItem['candidates'][number]) => {
    setPending(it.id)
    try {
      await api(`/api/screen/review/${it.id}`, { method: 'POST', json: { kind: c.kind, tmdb_id: c.tmdb_id, season: it.parsed.season, episode: it.parsed.episode } })
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
                <button key={`${c.kind}${c.tmdb_id}`} type="button" disabled={pending === it.id} onClick={() => void choose(it, c)} className={cn(BTN, 'h-9 text-xs')}>{candidateText(c)}</button>
              ))}
            </div>
          ) : <p className="mt-1 text-xs text-muted">Nothing found for “{it.parsed.title}”. Rename the file closer to the title and rescan.</p>}
        </div>
      ))}
    </div>
  )
}
