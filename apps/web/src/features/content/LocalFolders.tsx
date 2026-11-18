import { useState } from 'react'
import { toast } from 'sonner'
import { Confirm } from '@/design/Confirm'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { Segmented } from '@/design/Segmented'
import { cn } from '@/design/cn'
import { api } from '@/api/client'
import { modeLabel, scanText, withPatch, type LocalFolder, type Mode, type Patch } from './content'
import { SaveBar } from './SaveBar'
import { errorText, useBrowse, useContentMode, useLocalActions, useLocalFolders, useSaveMarks } from './useContentMode'

const HOLDS = [{ value: 'both', label: 'Videos + photos' }, { value: 'videos', label: 'Videos' }, { value: 'photos', label: 'Photos' }] as const
const BTN = 'h-9 rounded-lg border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5 disabled:opacity-50'

/** Settings, Local files: folders on this PC that become part of the library, each marked SFW or NSFW like a channel. */
export function LocalFolders() {
  const q = useLocalFolders()
  const mode = useContentMode().data?.mode ?? 'sfw'
  const act = useLocalActions()
  const save = useSaveMarks()
  const [draft, setDraft] = useState<Record<string, Patch>>({})
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<LocalFolder | null>(null)
  const folders = q.data?.folders ?? []
  const savedOf = (f: LocalFolder): Patch => ({ in_library: Boolean(f.in_library), content_rating: f.content_rating, holds: f.holds })
  const mark = (f: LocalFolder, patch: Patch) => setDraft((d) => withPatch(d, f.channel_id, savedOf(f), patch))   // kept aside until Save
  const commit = () => save.mutate(draft, {
    onSuccess: () => { setDraft({}); toast.success('Saved.') },
    onError: (e) => toast.error(errorText(e, 'Could not save. Your changes are still here.')),
  })
  const identify = async (f: LocalFolder) => {
    try {
      const r = await api<{ kind: string }>(`/api/local-folders/${f.id}/identify`, { method: 'POST', json: {} })
      toast(`Reading names${r.kind === 'scene databases' ? ' and file hashes' : ''}: asking ${r.kind}. Results appear as they come.`)
    } catch (e) { toast.error(errorText(e, 'Could not start the metadata scan.')) }
  }

  return (
    <div className="grid gap-4">
      <section aria-label="Local folders" className="rounded-[18px] border border-line bg-surface p-5">
        <div className="mb-1 flex flex-wrap items-center justify-between gap-3">
          <h3 className="flex items-center gap-3 text-xl font-extrabold"><span className="text-accent"><Icon name="folders" className="size-5" /></span>Local files</h3>
          <button type="button" onClick={() => setAdding(true)} className="h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg">Add a folder</button>
        </div>
        <p className="mb-4 text-sm text-muted">Folders on this PC join the library like channels do. Photos and videos stay where they are; Archive X reads them from there. Mark each folder SFW or NSFW, and it is shown only in that mode ({modeLabel(mode)} now).</p>
        {q.data?.hidden ? <p className="mb-3 rounded-xl bg-surface-2 px-3 py-2 text-xs text-muted">{q.data.hidden.toLocaleString()} folder{q.data.hidden === 1 ? ' is' : 's are'} in the library as NSFW and not listed in SFW mode.</p> : null}
        {q.isPending ? <p role="status" className="py-3 text-sm text-muted">Loading…</p> : q.isError ? <p role="alert" className="py-3 text-sm text-warn">Could not load the folders.</p> : !folders.length ? <p className="py-3 text-sm text-muted">No folders yet. Add one to see its photos in the Photos page.</p> : (
          <ul className="grid divide-y divide-line rounded-xl border border-line">
            {folders.map((f, i) => {
              const v = { ...savedOf(f), ...draft[f.channel_id] }
              return (
              <li key={f.id} style={{ '--i': i } as React.CSSProperties} className={cn('ax-rise grid gap-2 px-3 py-3 transition-[background-color,opacity] duration-200', !v.in_library && 'opacity-70', draft[f.channel_id] && 'bg-accent/10')}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0 flex-1 basis-60"><b className="block truncate text-sm">{f.title || f.path}</b><small className="block truncate text-xs text-muted" title={f.path}>{f.path}</small></div>
                  <div className="flex flex-wrap items-center gap-2">
                    <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={Boolean(v.in_library)} onChange={(e) => mark(f, { in_library: e.target.checked })} aria-label={`${f.title ?? f.path} is in the library`} />In library</label>
                    <Segmented<Mode> label={`${f.title ?? f.path} content`} value={v.content_rating ?? 'nsfw'} onChange={(m) => mark(f, { content_rating: m })} options={[{ value: 'sfw', label: 'SFW' }, { value: 'nsfw', label: 'NSFW' }]} />
                    <select aria-label={`${f.title ?? f.path} holds`} value={v.holds} onChange={(e) => mark(f, { holds: e.target.value })} className="h-9 rounded-lg border border-line-2 bg-bg px-2 text-sm">
                      {HOLDS.map((h) => <option key={h.value} value={h.value}>{h.label}</option>)}
                    </select>
                  </div>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p role="status" className={cn('text-xs', f.last_status === 'FAILED' || !f.exists ? 'text-bad' : 'text-muted')}>{scanText(f)}</p>
                  <div className="flex gap-2">
                    <button type="button" className={BTN} disabled={act.scan.isPending} onClick={() => act.scan.mutate(f.id)} title="Look for new, changed and removed files">Check again</button>
                    {f.videos ? <button type="button" className={BTN} onClick={() => void identify(f)} title={f.content_rating === 'sfw' ? 'Name the movies and shows with TMDB' : 'Look the videos up by file hash in StashDB / ThePornDB'}>Scan metadata</button> : null}
                    <button type="button" className={BTN} onClick={() => setRemoving(f)}>Remove</button>
                  </div>
                </div>
              </li>
              )
            })}
          </ul>
        )}
        <SaveBar count={Object.keys(draft).length} busy={save.isPending} onSave={commit} onDiscard={() => setDraft({})} />
      </section>
      {adding ? <AddFolder mode={mode} onClose={() => setAdding(false)} onAdd={(b) => act.add.mutate(b, { onSuccess: () => { setAdding(false); toast('Folder added; looking through it now') }, onError: (e) => toast.error(errorText(e, 'Could not add the folder.')) })} busy={act.add.isPending} /> : null}
      {removing ? <Confirm title="Stop using this folder?" text={`${removing.path}\n\nIts photos leave the library. The files on your PC are not touched.`} ok="Remove" danger onCancel={() => setRemoving(null)} onOk={() => act.remove.mutate(removing.id, { onSuccess: () => setRemoving(null), onError: (e) => toast.error(errorText(e, 'Could not remove it.')) })} /> : null}
    </div>
  )
}

function AddFolder({ mode, onClose, onAdd, busy }: { mode: Mode; onClose: () => void; onAdd: (b: { path: string; content_rating: Mode; holds: string }) => void; busy: boolean }) {
  const [path, setPath] = useState('')
  const [rating, setRating] = useState<Mode>(mode)
  const [holds, setHolds] = useState('both')
  const here = useBrowse(path, true)
  const current = here.data?.path ?? ''
  return (
    <Dialog open onClose={onClose} title="Add a folder" wide>
      <div className="grid gap-3">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <button type="button" className={BTN} onClick={() => setPath(here.data?.parent ?? '')} disabled={!current}>Up</button>
          <input aria-label="Folder path" value={current} onChange={(e) => setPath(e.target.value)} placeholder="Choose a drive, then a folder" className="h-9 min-w-0 flex-1 rounded-lg border border-line-2 bg-bg px-3" />
        </div>
        <ul aria-label="Folders" className="grid max-h-64 gap-0.5 overflow-y-auto rounded-xl border border-line p-1">
          {here.isPending ? <li className="p-2 text-sm text-muted">Loading…</li> : here.isError ? <li role="alert" className="p-2 text-sm text-warn">That folder cannot be opened.</li> : !here.data?.folders.length ? <li className="p-2 text-sm text-muted">No folders inside.</li> : here.data.folders.map((f) => (
            <li key={f.path}><button type="button" onClick={() => setPath(f.path)} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-fg/5"><Icon name="folders" className="size-4 text-muted" />{f.name}</button></li>
          ))}
        </ul>
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm font-semibold">This folder is</span>
          <Segmented<Mode> label="Content of the new folder" value={rating} onChange={setRating} options={[{ value: 'sfw', label: 'SFW' }, { value: 'nsfw', label: 'NSFW' }]} />
          <select aria-label="What the new folder holds" value={holds} onChange={(e) => setHolds(e.target.value)} className="h-9 rounded-lg border border-line-2 bg-bg px-2 text-sm">
            {HOLDS.map((h) => <option key={h.value} value={h.value}>{h.label}</option>)}
          </select>
        </div>
        <p className="text-xs text-muted">Subfolders are included. A folder is wholly SFW or wholly NSFW: to mix, add the subfolders as separate folders.</p>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button>
          <button type="button" disabled={!current || busy} onClick={() => onAdd({ path: current, content_rating: rating, holds })} className="h-11 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60">Add this folder</button>
        </div>
      </div>
    </Dialog>
  )
}
