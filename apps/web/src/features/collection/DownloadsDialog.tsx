import { useState } from 'react'
import { toast } from 'sonner'
import { Dialog } from '@/design/Dialog'
import { Switch } from '@/design/Switch'
import { cn } from '@/design/cn'
import { errorText } from '@/features/content/useContentMode'
import { StreamingActions } from '@/features/settings/StreamingSetup'
import { PLACEHOLDER } from '@/features/series/PosterCard'
import { useCollection, useCollectionActions, percent, summary, type CollectionItem, type CollectionTitle } from './useCollection'

const BTN = 'h-9 rounded-xl bg-fg/10 px-3.5 text-[13px] font-bold hover:bg-fg/15 disabled:opacity-50'
const SHOWN: ReadonlyArray<CollectionItem['state']> = ['DOWNLOADING', 'PAUSED', 'FAILED', 'DONE']
const STATE_TEXT: Partial<Record<CollectionItem['state'], string>> = { DOWNLOADING: 'Downloading', PAUSED: 'Paused', FAILED: 'Failed', DONE: 'Saved' }

/** The downloads Archive X is making or has made: every item of the Collection that is downloading, failed or saved. */
export function downloadsOf(titles: readonly CollectionTitle[]): Array<{ title: CollectionTitle; items: CollectionItem[] }> {
  return titles.map((title) => ({ title, items: title.items.filter((i) => SHOWN.includes(i.state)) })).filter((g) => g.items.length)
}

function Row({ title, it }: { title: CollectionTitle; it: CollectionItem }) {
  const act = useCollectionActions()
  const pct = percent(it.got, it.total)
  const live = it.state === 'DOWNLOADING' || it.state === 'PAUSED'
  const detail = live ? (pct === null ? 'Starting…' : `${pct}%`) + (it.total ? ` · of ${sizeOf(it.total)}` : '') : it.state === 'FAILED' ? it.note : it.total ? sizeOf(it.total) : ''
  return (
    <li className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-4 rounded-xl px-2 py-2">
      <img src={title.poster || PLACEHOLDER} alt="" loading="lazy" className="aspect-video h-[54px] rounded-md bg-surface-3 object-cover" />
      <div className="min-w-0">
        <p className="m-0 truncate text-sm font-extrabold" title={it.release || it.label}>{title.kind === 'movie' ? title.title : `${title.title} · ${it.label}`}</p>
        <p className={cn('m-0 truncate text-xs', it.state === 'FAILED' ? 'text-warn' : 'text-muted')} title={it.release}>{[STATE_TEXT[it.state], detail].filter(Boolean).join(' · ')}</p>
        {live && pct !== null ? <div role="progressbar" aria-label={`${title.title} download`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} className="mt-1.5 h-1 overflow-hidden rounded-full bg-fg/15"><span className="block h-full bg-accent transition-[width] duration-500" style={{ width: `${pct}%` }} /></div> : null}
      </div>
      <div className="flex gap-2">
        {it.state === 'DOWNLOADING' ? <button type="button" aria-label={`Pause ${title.title}`} onClick={() => act.item.mutate({ id: it.id, action: 'pause' })} className={BTN}>Pause</button> : null}
        {it.state === 'PAUSED' ? <button type="button" aria-label={`Resume ${title.title}`} onClick={() => act.item.mutate({ id: it.id, action: 'resume' })} className={BTN}>Resume</button> : null}
        {it.state === 'FAILED' ? <button type="button" onClick={() => act.item.mutate({ id: it.id, action: 'retry' })} className={BTN}>Try again</button> : null}
        <button type="button" aria-label={`Delete ${title.title}`} onClick={() => act.deleteItem.mutate(it.id, { onError: (e) => toast.error(errorText(e, 'Could not delete.')) })} className={BTN}>Delete</button>
      </div>
    </li>
  )
}

export function sizeOf(bytes: number): string {
  if (!(bytes > 0)) return ''
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  const v = bytes / 1024 ** i
  return `${v >= 100 || i < 2 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

/** One thing Archive X keeps up to date (Keep this on a movie or show): what is wanted, downloading or done of it, with Skip and Look again for each episode. */
function KeptRow({ t }: { t: CollectionTitle }) {
  const act = useCollectionActions()
  const [open, setOpen] = useState(false)
  return (
    <li className="grid gap-2 rounded-xl px-2 py-2.5">
      <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-4">
        <img src={t.poster || PLACEHOLDER} alt="" loading="lazy" className="aspect-[2/3] h-[72px] rounded-md bg-surface-3 object-cover" />
        <div className="min-w-0">
          <p className="m-0 truncate text-sm font-extrabold">{t.title}{t.year ? <span className="ml-2 font-medium text-muted">{t.year}</span> : null}</p>
          <p className="m-0 text-xs text-muted">{t.kind === 'movie' ? 'Movie' : t.scope === 'new' ? 'Series · new episodes only' : 'Series · all episodes'}</p>
          <p className="m-0 mt-0.5 text-xs">{summary(t)}</p>
        </div>
        <div className="flex gap-2">
          {t.items.length ? <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className={BTN}>{open ? 'Hide' : `Show ${t.items.length} ${t.kind === 'movie' ? 'item' : 'episodes'}`}</button> : null}
          <button type="button" onClick={() => act.remove.mutate(t.id, { onSuccess: () => toast(`${t.title} is no longer kept. Files already downloaded stay.`) })} className={BTN}>Stop keeping</button>
        </div>
      </div>
      {open ? (
        <ul aria-label={`${t.title} episodes`} className="m-0 grid max-h-72 list-none gap-1 overflow-y-auto p-0">
          {t.items.map((it) => {
            const pct = percent(it.got, it.total)
            return (
              <li key={it.id} className="grid gap-1 rounded-lg bg-fg/[0.04] px-3 py-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="min-w-0 flex-1 font-semibold [overflow-wrap:anywhere]">{it.label}</span>
                  <span className="text-xs font-bold text-muted">{it.state === 'DOWNLOADING' && pct !== null ? `Downloading ${pct}%` : it.state === 'WANTED' ? 'Waiting' : it.state === 'DONE' ? 'Done' : it.state === 'SKIPPED' ? 'Skipped' : it.state === 'PAUSED' ? 'Paused' : it.state === 'FAILED' ? 'Failed' : 'Downloading'}</span>
                  {it.state === 'WANTED' || it.state === 'DOWNLOADING' ? <button type="button" onClick={() => act.item.mutate({ id: it.id, action: 'skip' })} className="text-xs font-semibold text-muted hover:text-fg">Skip</button> : null}
                  {it.state === 'SKIPPED' || (it.state === 'WANTED' && it.attempts > 0) ? <button type="button" onClick={() => act.item.mutate({ id: it.id, action: 'retry' })} className="text-xs font-semibold text-accent hover:underline">Look again now</button> : null}
                </div>
                {it.state === 'DOWNLOADING' && pct !== null ? <div role="progressbar" aria-label={`${it.label} download`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} className="h-1 overflow-hidden rounded-full bg-fg/15"><span className="block h-full bg-accent" style={{ width: `${pct}%` }} /></div> : null}
                {it.note && it.state !== 'DONE' ? <p className="m-0 text-xs text-muted">{it.note}</p> : null}
              </li>
            )
          })}
        </ul>
      ) : null}
    </li>
  )
}

function Settings() {
  const q = useCollection()
  const act = useCollectionActions()
  const [dir, setDir] = useState('')
  const s = q.data?.settings
  if (!s) return <p role="status" className="py-4 text-sm text-muted">Loading…</p>
  const save = (b: Parameters<typeof act.settings.mutate>[0], ok: string) => act.settings.mutate(b, { onSuccess: () => toast(ok), onError: (e) => toast.error(errorText(e, 'Could not save.')) })
  const FIELD = 'grid gap-3 rounded-2xl bg-fg/[0.04] p-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center'
  return (
    <div className="grid gap-3">
      <div className={FIELD}>
        <div className="min-w-0"><p className="m-0 text-sm font-extrabold">Download automatically</p><p className="m-0 text-xs text-muted">Archive X looks for what is missing of the movies and shows you keep (Keep this), from your add-ons, and downloads it by itself.</p></div>
        <div className="flex items-center gap-3">
          <button type="button" disabled={!s.on || act.run.isPending || q.data?.running} onClick={() => act.run.mutate(undefined, { onSuccess: () => toast('Looking now.') })} className={BTN}>{q.data?.running ? 'Working…' : 'Look now'}</button>
          <Switch checked={s.on} onChange={(v) => save({ on: v }, v ? 'Automatic downloads are on.' : 'Automatic downloads are off.')} label="Download automatically" />
        </div>
      </div>
      <form className={FIELD} onSubmit={(e) => { e.preventDefault(); if (dir.trim()) save({ dir: dir.trim() }, 'Folder saved.') }}>
        <div className="min-w-0">
          <label htmlFor="downloads-dir" className="text-sm font-extrabold">Download folder</label>
          <p className="m-0 text-xs text-muted">Where new downloads are saved. Ones already saved stay where they are. It is also a folder of your library, so what finishes shows up there.</p>
          <input id="downloads-dir" value={dir} onChange={(e) => setDir(e.target.value)} placeholder={s.dir} autoComplete="off" spellCheck={false} className="mt-2 h-10 w-full rounded-lg border border-line-2 bg-bg px-3 font-mono text-xs text-fg" />
        </div>
        <button type="submit" disabled={!dir.trim() || act.settings.isPending} className={BTN}>Change…</button>
      </form>
      <div className={FIELD}>
        <div><p className="m-0 text-sm font-extrabold">Quality</p><p className="m-0 text-xs text-muted">What Archive X picks when it finds a release itself (Collection). A stream you press Download on is saved as it is.</p></div>
        <select aria-label="Quality" value={s.quality} onChange={(e) => save({ quality: Number(e.target.value) }, 'Saved.')} className="h-10 rounded-lg border border-line-2 bg-bg px-3 text-sm font-semibold text-fg"><option value={720}>720p</option><option value={1080}>1080p</option><option value={2160}>4K</option></select>
      </div>
      <div className={FIELD}>
        <div><p className="m-0 text-sm font-extrabold">Largest download</p><p className="m-0 text-xs text-muted">What Archive X picks by itself is never bigger than this (in GB).</p></div>
        <div className="flex gap-2">
          <label className="grid gap-0.5 text-[11px] font-semibold text-muted">Movie<input aria-label="Largest movie (GB)" type="number" min={1} max={200} defaultValue={s.max_gb_movie} onBlur={(e) => e.target.value && Number(e.target.value) !== s.max_gb_movie && save({ max_gb_movie: Number(e.target.value) }, 'Saved.')} className="h-10 w-24 rounded-lg border border-line-2 bg-bg px-3 text-sm font-semibold text-fg" /></label>
          <label className="grid gap-0.5 text-[11px] font-semibold text-muted">Episode<input aria-label="Largest episode (GB)" type="number" min={0.5} max={200} step={0.5} defaultValue={s.max_gb_episode} onBlur={(e) => e.target.value && Number(e.target.value) !== s.max_gb_episode && save({ max_gb_episode: Number(e.target.value) }, 'Saved.')} className="h-10 w-24 rounded-lg border border-line-2 bg-bg px-3 text-sm font-semibold text-fg" /></label>
        </div>
      </div>
      <div className={FIELD}>
        <div><p className="m-0 text-sm font-extrabold">Downloads at once</p><p className="m-0 text-xs text-muted">More at once can be quicker on a fast connection.</p></div>
        <select aria-label="Downloads at once" value={s.parallel} onChange={(e) => save({ parallel: Number(e.target.value) }, 'Saved.')} className="h-10 rounded-lg border border-line-2 bg-bg px-3 text-sm font-semibold text-fg">{[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}</select>
      </div>
      <p className="m-0 px-1 text-xs text-faint">Downloads are ordinary video files, so other players can open them too. Only download what you have the right to.</p>
    </div>
  )
}

/** dx.bi's Downloads window: what is downloading and saved (with Delete and Try again), and where and how new ones are made. */
export function DownloadsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const q = useCollection(open)
  const [tab, setTab] = useState<'downloads' | 'kept' | 'settings'>('downloads')
  const groups = downloadsOf(q.data?.titles ?? [])
  const count = groups.reduce((n, g) => n + g.items.length, 0)
  const server = q.data?.server
  const kept = (q.data?.titles ?? []).filter((t) => t.monitored)
  return (
    <Dialog open={open} onClose={onClose} title="Downloads" wide>
      <p className="m-0 -mt-2 mb-3 text-xs text-muted">Films and episodes saved on this computer, to watch without internet</p>
      <div role="tablist" aria-label="Downloads" className="mb-3 flex gap-2">
        {([['downloads', `Downloads (${count})`], ['kept', `Kept (${kept.length})`], ['settings', 'Settings']] as const).map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)} className={cn('h-10 rounded-full px-5 text-sm font-bold', tab === id ? 'bg-fg text-bg' : 'text-fg/70 hover:text-fg')}>{label}</button>
        ))}
      </div>
      {tab === 'settings' ? <Settings /> : tab === 'kept' ? (
        <>
          {kept.length && server && !server.up ? <div role="status" className="mb-2 grid gap-1.5 rounded-xl border border-warn/40 bg-warn/10 p-2.5 text-xs"><p className="m-0 font-semibold">Downloads need Stremio&apos;s streaming server, which is not running.</p><StreamingActions /></div> : null}
          {!kept.length ? <p className="m-0 py-10 text-center text-sm text-muted">Nothing kept yet. Press <b>Keep this</b> on a movie or show and Archive X finds it in your add-ons, downloads it and keeps a show up to date as new episodes air.</p>
            : <ul aria-label="Kept movies and shows" className="m-0 grid list-none gap-1 p-0">{kept.map((t) => <KeptRow key={t.id} t={t} />)}</ul>}
        </>
      ) : q.isPending ? <p role="status" className="py-6 text-sm text-muted">Loading…</p> : (
        <>
          {server && !server.up && count ? <p role="status" className="mb-2 rounded-xl border border-warn/40 bg-warn/10 p-2.5 text-xs font-semibold">Downloads need Stremio&apos;s streaming server, which is not running. Set it up in Settings, Addons.</p> : null}
          {!groups.length ? <p className="m-0 py-10 text-center text-sm text-muted">Nothing downloaded yet. Press the download button on a stream to save it here.</p> : (
            <ul aria-label="Downloads list" className="m-0 grid list-none gap-1 p-0">
              {groups.flatMap((g) => g.items.map((it) => <Row key={it.id} title={g.title} it={it} />))}
            </ul>
          )}
          {q.data ? <p className="m-0 mt-3 truncate px-1 text-xs text-faint" title={q.data.settings.dir}>Saved to {q.data.settings.dir}</p> : null}
        </>
      )}
    </Dialog>
  )
}
