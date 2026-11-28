import { useState } from 'react'
import { toast } from 'sonner'
import { Empty } from '@/design/Empty'
import { Icon } from '@/design/Icon'
import { Switch } from '@/design/Switch'
import { cn } from '@/design/cn'
import { errorText } from '@/features/content/useContentMode'
import { StreamingActions } from '@/features/settings/StreamingSetup'
import { PLACEHOLDER } from '@/features/series/PosterCard'
import { useCollection, useCollectionActions, percent, summary, type CollectionItem, type CollectionTitle } from './useCollection'

const BTN = 'h-9 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const CHIP: Record<CollectionItem['state'], string> = { WANTED: 'bg-warn/15 text-warn', DOWNLOADING: 'bg-accent/15 text-accent', DONE: 'bg-good/15 text-good', SKIPPED: 'bg-surface-3 text-muted', FAILED: 'bg-warn/15 text-warn', PAUSED: 'bg-surface-3 text-muted' }
const STATE_TEXT: Record<CollectionItem['state'], string> = { WANTED: 'Waiting', DOWNLOADING: 'Downloading', DONE: 'Done', SKIPPED: 'Skipped', FAILED: 'Failed', PAUSED: 'Paused' }

function Item({ it }: { it: CollectionItem }) {
  const act = useCollectionActions()
  const pct = percent(it.got, it.total)
  return (
    <li className="grid gap-1 rounded-xl bg-bg px-3 py-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 font-semibold [overflow-wrap:anywhere]">{it.label}</span>
        <span className={cn('rounded-full px-2 py-0.5 text-[11px] font-bold', CHIP[it.state])}>{it.state === 'DOWNLOADING' && pct !== null ? `Downloading ${pct}%` : STATE_TEXT[it.state]}</span>
        {it.state === 'WANTED' || it.state === 'DOWNLOADING' ? <button type="button" onClick={() => act.item.mutate({ id: it.id, action: 'skip' })} className="text-xs font-semibold text-muted hover:text-fg">Skip</button> : null}
        {it.state === 'FAILED' ? <button type="button" onClick={() => act.item.mutate({ id: it.id, action: 'retry' })} className="text-xs font-semibold text-accent hover:underline">Try again</button> : null}
        {it.state === 'SKIPPED' || (it.state === 'WANTED' && it.attempts > 0) ? <button type="button" onClick={() => act.item.mutate({ id: it.id, action: 'retry' })} className="text-xs font-semibold text-accent hover:underline">Look again now</button> : null}
      </div>
      {it.state === 'DOWNLOADING' && pct !== null ? <div className="h-1.5 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-label={`${it.label} download`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}><span className="block h-full rounded-full bg-accent" style={{ width: `${pct}%` }} /></div> : null}
      {it.release && it.state === 'DOWNLOADING' ? <p className="m-0 truncate text-[11px] text-muted" title={it.release}>{it.release}</p> : null}
      {it.note && it.state !== 'DONE' ? <p className="m-0 text-xs text-muted">{it.note}</p> : null}
    </li>
  )
}

function TitleRow({ t }: { t: CollectionTitle }) {
  const act = useCollectionActions()
  const [open, setOpen] = useState(false)
  return (
    <li className="grid gap-3 rounded-2xl border border-line bg-surface p-3.5">
      <div className="flex gap-3">
        <img src={t.poster || PLACEHOLDER} alt="" loading="lazy" className="aspect-[2/3] w-16 shrink-0 rounded-lg bg-surface-3 object-cover" />
        <div className="min-w-0 flex-1">
          <h3 className="m-0 text-base font-extrabold [overflow-wrap:anywhere]">{t.title}{t.year ? <span className="ml-2 text-sm font-medium text-muted">{t.year}</span> : null}</h3>
          <p className="m-0 mt-0.5 text-xs text-muted">{t.kind === 'movie' ? 'Movie' : t.scope === 'new' ? 'Series · new episodes only' : 'Series · all episodes'}</p>
          <p className="m-0 mt-1.5 text-sm">{summary(t)}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {t.items.length ? <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className={BTN}>{open ? 'Hide' : `Show ${t.items.length} ${t.kind === 'movie' ? 'item' : 'episodes'}`}</button> : null}
            <button type="button" onClick={() => act.remove.mutate(t.id, { onSuccess: () => toast(`${t.title} is no longer kept. Files already downloaded stay.`) })} className={BTN}>Stop keeping</button>
          </div>
        </div>
      </div>
      {open ? <ul aria-label={`${t.title} episodes`} className="m-0 grid max-h-96 list-none gap-1.5 overflow-y-auto p-0">{t.items.map((it) => <Item key={it.id} it={it} />)}</ul> : null}
    </li>
  )
}

/** The Collection: what Archive X keeps up to date by itself, in plain terms. Sonarr and Radarr, with the setup taken out. */
export function CollectionPage() {
  const q = useCollection()
  const act = useCollectionActions()
  const [dir, setDir] = useState('')
  const s = q.data?.settings
  const save = (b: Parameters<typeof act.settings.mutate>[0], ok?: string) => act.settings.mutate(b, { onSuccess: () => ok && toast(ok), onError: (e) => toast.error(errorText(e, 'Could not save.')) })
  if (q.isPending) return <p role="status" className="py-6 text-sm text-muted">Loading the Collection…</p>
  if (q.isError || !q.data || !s) return <p role="alert" className="py-6 text-sm text-warn">The Collection could not be loaded.</p>
  const needServer = q.data.titles.length > 0 && !q.data.server.up
  return (
    <section aria-label="Collection" className="grid gap-5">
      <div className="rounded-2xl border border-line bg-surface p-4">
        <div className="flex flex-wrap items-center gap-3">
          <Switch checked={s.on} onChange={(v) => save({ on: v }, v ? 'Automatic downloads are on.' : 'Automatic downloads are off.')} label="Download automatically" />
          <div className="min-w-0 flex-1"><p className="m-0 text-sm font-semibold">Download automatically</p><p className="m-0 text-xs text-muted">Archive X looks for what is missing of the movies and shows below, from your add-ons, and downloads it by itself. It checks every ten minutes.</p></div>
          <button type="button" disabled={!s.on || act.run.isPending || q.data.running} onClick={() => act.run.mutate(undefined, { onSuccess: () => toast('Looking now.') })} className={BTN}>{q.data.running ? 'Working…' : 'Look now'}</button>
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1 text-xs font-semibold text-muted">Quality
            <select value={s.quality} onChange={(e) => save({ quality: Number(e.target.value) })} aria-label="Quality" className="h-10 rounded-xl border border-line-2 bg-bg px-3 text-sm text-fg">
              <option value={720}>720p</option><option value={1080}>1080p</option><option value={2160}>4K</option>
            </select>
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="grid gap-1 text-xs font-semibold text-muted">Largest movie (GB)<input type="number" min={1} max={200} defaultValue={s.max_gb_movie} onBlur={(e) => e.target.value && Number(e.target.value) !== s.max_gb_movie && save({ max_gb_movie: Number(e.target.value) })} aria-label="Largest movie in GB" className="h-10 rounded-xl border border-line-2 bg-bg px-3 text-sm text-fg" /></label>
            <label className="grid gap-1 text-xs font-semibold text-muted">Largest episode (GB)<input type="number" min={0.5} max={200} step={0.5} defaultValue={s.max_gb_episode} onBlur={(e) => e.target.value && Number(e.target.value) !== s.max_gb_episode && save({ max_gb_episode: Number(e.target.value) })} aria-label="Largest episode in GB" className="h-10 rounded-xl border border-line-2 bg-bg px-3 text-sm text-fg" /></label>
          </div>
          <form className="grid gap-1 sm:col-span-2" onSubmit={(e) => { e.preventDefault(); if (dir.trim()) save({ dir: dir.trim() }, 'Folder saved.') }}>
            <label htmlFor="collection-dir" className="text-xs font-semibold text-muted">Folder (movies and shows are saved here, and it is a folder of your library)</label>
            <div className="flex flex-wrap gap-2">
              <input id="collection-dir" value={dir} onChange={(e) => setDir(e.target.value)} placeholder={s.dir} autoComplete="off" spellCheck={false} className="h-10 min-w-0 flex-1 rounded-xl border border-line-2 bg-bg px-3 text-sm text-fg" />
              <button type="submit" disabled={!dir.trim() || act.settings.isPending} className={BTN}>Save folder</button>
            </div>
          </form>
        </div>
        {needServer ? (
          <div role="status" className="mt-3 grid gap-1.5 rounded-xl border border-warn/40 bg-warn/10 p-2.5 text-xs"><p className="m-0 font-semibold">Downloads need Stremio&apos;s streaming server, which is not running.</p><StreamingActions /></div>
        ) : null}
      </div>
      {!q.data.titles.length ? (
        <Empty icon="download" title="Nothing kept yet">Open a movie or show in Discover and press <b>Keep this</b>. Archive X then finds it in your add-ons, downloads it and keeps a show up to date as new episodes air. For a show in your library with missing episodes, use <b>Get the missing episodes</b> on its page.</Empty>
      ) : (
        <ul aria-label="Kept movies and shows" className="m-0 grid list-none gap-3 p-0 lg:grid-cols-2">{q.data.titles.map((t) => <TitleRow key={t.id} t={t} />)}</ul>
      )}
      <p className="m-0 text-xs text-faint"><Icon name="alert" className="mr-1 inline size-3.5" />Only use this for things you have the right to download. What it finds comes from the add-ons you installed.</p>
    </section>
  )
}
