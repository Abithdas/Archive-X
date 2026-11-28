import { useState } from 'react'
import { toast } from 'sonner'
import { Dialog } from '@/design/Dialog'
import { cn } from '@/design/cn'
import { errorText } from '@/features/content/useContentMode'
import { PLACEHOLDER } from '@/features/series/PosterCard'
import { useCollection, useCollectionActions, percent, type CollectionItem, type CollectionTitle } from './useCollection'

const BTN = 'h-9 rounded-xl bg-fg/10 px-3.5 text-[13px] font-bold hover:bg-fg/15 disabled:opacity-50'
const SHOWN: ReadonlyArray<CollectionItem['state']> = ['DOWNLOADING', 'FAILED', 'DONE']
const STATE_TEXT: Partial<Record<CollectionItem['state'], string>> = { DOWNLOADING: 'Downloading', FAILED: 'Failed', DONE: 'Saved' }

/** The downloads Archive X is making or has made: every item of the Collection that is downloading, failed or saved. */
export function downloadsOf(titles: readonly CollectionTitle[]): Array<{ title: CollectionTitle; items: CollectionItem[] }> {
  return titles.map((title) => ({ title, items: title.items.filter((i) => SHOWN.includes(i.state)) })).filter((g) => g.items.length)
}

function Row({ title, it }: { title: CollectionTitle; it: CollectionItem }) {
  const act = useCollectionActions()
  const pct = percent(it.got, it.total)
  const detail = it.state === 'DOWNLOADING' ? (pct === null ? 'Starting…' : `${pct}%`) + (it.total ? ` · of ${sizeOf(it.total)}` : '') : it.state === 'FAILED' ? it.note : it.total ? sizeOf(it.total) : ''
  return (
    <li className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-4 rounded-xl px-2 py-2">
      <img src={title.poster || PLACEHOLDER} alt="" loading="lazy" className="aspect-video h-[54px] rounded-md bg-surface-3 object-cover" />
      <div className="min-w-0">
        <p className="m-0 truncate text-sm font-extrabold" title={it.release || it.label}>{title.kind === 'movie' ? title.title : `${title.title} · ${it.label}`}</p>
        <p className={cn('m-0 truncate text-xs', it.state === 'FAILED' ? 'text-warn' : 'text-muted')} title={it.release}>{[STATE_TEXT[it.state], detail].filter(Boolean).join(' · ')}</p>
        {it.state === 'DOWNLOADING' && pct !== null ? <div role="progressbar" aria-label={`${title.title} download`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} className="mt-1.5 h-1 overflow-hidden rounded-full bg-fg/15"><span className="block h-full bg-accent transition-[width] duration-500" style={{ width: `${pct}%` }} /></div> : null}
      </div>
      <div className="flex gap-2">
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
      <p className="m-0 px-1 text-xs text-faint">Downloads are ordinary video files, so other players can open them too. Only download what you have the right to.</p>
    </div>
  )
}

/** dx.bi's Downloads window: what is downloading and saved (with Delete and Try again), and where and how new ones are made. */
export function DownloadsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const q = useCollection(open)
  const [tab, setTab] = useState<'downloads' | 'settings'>('downloads')
  const groups = downloadsOf(q.data?.titles ?? [])
  const count = groups.reduce((n, g) => n + g.items.length, 0)
  const server = q.data?.server
  return (
    <Dialog open={open} onClose={onClose} title="Downloads" wide>
      <p className="m-0 -mt-2 mb-3 text-xs text-muted">Films and episodes saved on this computer, to watch without internet</p>
      <div role="tablist" aria-label="Downloads" className="mb-3 flex gap-2">
        {([['downloads', `Downloads (${count})`], ['settings', 'Settings']] as const).map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)} className={cn('h-10 rounded-full px-5 text-sm font-bold', tab === id ? 'bg-fg text-bg' : 'text-fg/70 hover:text-fg')}>{label}</button>
        ))}
      </div>
      {tab === 'settings' ? <Settings /> : q.isPending ? <p role="status" className="py-6 text-sm text-muted">Loading…</p> : (
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
