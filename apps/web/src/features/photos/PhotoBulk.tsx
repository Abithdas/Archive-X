import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { NamePicker } from './NamePicker'
import { deleteBody, deletePath, isLocalPhoto, photoName, type Photo, type Removal } from './photos'

const BTN = 'inline-flex h-10 items-center gap-2 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const reasonOf = (e: unknown, fb: string) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : fb)
export const photoKey = (p: Pick<Photo, 'channel_id' | 'message_id'>) => `${p.channel_id}:${p.message_id}`
const refresh = (qc: ReturnType<typeof useQueryClient>) => Promise.all(['photos', 'photo-channels', 'face-groups', 'performer-photos', 'performer-gallery', 'photo-faces'].map((k) => qc.invalidateQueries({ queryKey: [k] })))

/** What each way of removing means for a set of photos, and which of them are on offer (a Telegram message can only be deleted for a photo that came from Telegram). */
export function removalOptions(photos: readonly Photo[]): Array<{ mode: Removal; label: string; text: string; count: number; danger: boolean }> {
  const local = photos.filter(isLocalPhoto).length
  const remote = photos.length - local
  const out: Array<{ mode: Removal; label: string; text: string; count: number; danger: boolean }> = [
    { mode: 'archive', label: 'Remove from Archive X', text: 'The files stay on the PC and the messages stay in Telegram; they come back if their folder or channel is scanned again.', count: photos.length, danger: false },
  ]
  if (local) out.push({ mode: 'file', label: `Delete ${local} file${local === 1 ? '' : 's'} from this PC`, text: 'Permanently deletes the files of the photos that are in folders on this PC. This does not use the Recycle Bin and cannot be undone.', count: local, danger: true })
  if (remote) out.push({ mode: 'telegram', label: `Delete ${remote} from Telegram too`, text: 'Permanently deletes the Telegram messages of the photos that came from Telegram. This cannot be undone.', count: remote, danger: true })
  return out
}

/** The bar that appears while photos are selected. */
export function SelectionBar({ count, loaded, onAll, onNone, onPerson, onDelete, onDone }: { count: number; loaded: number; onAll: () => void; onNone: () => void; onPerson: () => void; onDelete: () => void; onDone: () => void }) {
  return (
    <div role="toolbar" aria-label="Selected photos" className="sticky bottom-3 z-30 mx-auto mt-4 flex w-fit max-w-full flex-wrap items-center gap-2 rounded-2xl border border-line-2 bg-surface/95 p-2 shadow-2xl backdrop-blur-xl">
      <span className="px-2 text-sm font-bold tabular-nums">{count.toLocaleString()} selected</span>
      <button type="button" className={BTN} onClick={count >= loaded ? onNone : onAll}>{count >= loaded ? 'Clear' : `Select all ${loaded.toLocaleString()} shown`}</button>
      <button type="button" className={BTN} disabled={!count} onClick={onPerson}><Icon name="user" className="size-4" />Add to a person…</button>
      <button type="button" className={cn(BTN, 'text-bad')} disabled={!count} onClick={onDelete}><Icon name="trash" className="size-4" />Delete…</button>
      <button type="button" className={BTN} onClick={onDone}>Done</button>
    </div>
  )
}

/** Delete or remove the selected photos, one after another, saying where it is and what failed. */
export function BulkDelete({ photos, onClose, onDone }: { photos: Photo[]; onClose: () => void; onDone: (removed: string[]) => void }) {
  const qc = useQueryClient()
  const [mode, setMode] = useState<Removal | null>(null)
  const [progress, setProgress] = useState<{ at: number; of: number; failed: number } | null>(null)
  const options = removalOptions(photos)
  const chosen = options.find((o) => o.mode === mode)

  const run = async () => {
    if (!mode) return
    const targets = mode === 'file' ? photos.filter(isLocalPhoto) : mode === 'telegram' ? photos.filter((p) => !isLocalPhoto(p)) : photos
    const removed: string[] = []
    let failed = 0
    let firstError = ''
    for (let i = 0; i < targets.length; i++) {
      setProgress({ at: i + 1, of: targets.length, failed })
      const p = targets[i]!
      try {
        const r = await api<{ ok?: boolean; detail?: string }>(deletePath(p), { method: 'POST', json: deleteBody(mode) })
        if (!r || r.ok !== true) throw new ApiError(0, r?.detail || 'The photo was not deleted.')
        removed.push(photoKey(p))
      } catch (e) { failed++; firstError ||= `${photoName(p)}: ${reasonOf(e, 'not deleted')}` }
    }
    await refresh(qc)
    onDone(removed)
    if (failed) toast.error(`${removed.length} done, ${failed} failed. ${firstError}`, { duration: 12_000 }); else toast(`${removed.length} photo${removed.length === 1 ? '' : 's'} ${mode === 'archive' ? 'removed from Archive X' : 'deleted'}.`)
    onClose()
  }

  return (
    <Dialog open onClose={() => { if (!progress) onClose() }} title={`Delete ${photos.length.toLocaleString()} photo${photos.length === 1 ? '' : 's'}?`}>
      <div className="grid gap-3">
        {progress ? <p role="status" className="text-sm">{mode === 'archive' ? 'Removing' : 'Deleting'} {progress.at.toLocaleString()} of {progress.of.toLocaleString()}…{progress.failed ? ` (${progress.failed} failed)` : ''}</p> : (
          <>
            <p className="text-sm text-muted">Choose what happens to the {photos.length.toLocaleString()} selected photos.</p>
            <ul className="grid gap-2" aria-label="What to do">
              {options.map((o) => (
                <li key={o.mode}>
                  <label className={cn('flex cursor-pointer items-start gap-3 rounded-xl border p-3 text-sm', mode === o.mode ? (o.danger ? 'border-bad bg-bad/10' : 'border-accent bg-accent/10') : 'border-line hover:bg-fg/5')}>
                    <input type="radio" name="bulk-delete" checked={mode === o.mode} onChange={() => setMode(o.mode)} className="mt-1" />
                    <span><b className={o.danger ? 'text-bad' : ''}>{o.label}</b><span className="mt-0.5 block text-xs text-muted">{o.text}</span></span>
                  </label>
                </li>
              ))}
            </ul>
            <div className="flex justify-end gap-2">
              <button type="button" className={BTN} onClick={onClose}>Cancel</button>
              <button type="button" disabled={!chosen} onClick={() => void run()} className={cn('h-10 rounded-xl px-5 text-sm font-bold text-white disabled:opacity-50', chosen?.danger ? 'bg-bad' : 'bg-accent text-accent-fg')}>{chosen ? (chosen.mode === 'archive' ? 'Remove' : `Delete ${chosen.count.toLocaleString()}`) : 'Choose one'}</button>
            </div>
          </>
        )}
      </div>
    </Dialog>
  )
}

interface FaceRow { detection_id: number }

/** Add the selected photos to a person: the one face of each photo is named after them; a photo with no face, or with several, is left out and said so. */
export function BulkAddToPerson({ photos, onClose, onDone }: { photos: Photo[]; onClose: () => void; onDone: () => void }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const add = async (person: { id: number; name: string }) => {
    setBusy(true)
    const ids: number[] = []
    let skipped = 0
    for (const p of photos) {
      try {
        const r = await api<{ faces: FaceRow[] }>(`/api/photos/faces/of-photo?channel_id=${encodeURIComponent(p.channel_id)}&message_id=${p.message_id}`)
        if ((r.faces ?? []).length === 1) ids.push(r.faces[0]!.detection_id); else skipped++
      } catch { skipped++ }
    }
    try {
      if (ids.length) await api('/api/photos/faces/detections/move', { method: 'POST', json: { detection_ids: ids, performer_id: person.id } })
      await refresh(qc)
      const left = skipped ? ` ${skipped} had no face or several faces: open those one by one.` : ''
      if (ids.length) toast(`${ids.length} photo${ids.length === 1 ? '' : 's'} added to ${person.name}.${left}`, { duration: skipped ? 10_000 : 4000 }); else toast.error(`Nothing was added: none of the selected photos has exactly one face.${left ? '' : ''}`)
      if (ids.length) onDone()
      onClose()
    } catch (e) { toast.error(reasonOf(e, 'Could not add the photos to that person.')); setBusy(false) }
  }
  return (
    <Dialog open onClose={() => { if (!busy) onClose() }} title={`Add ${photos.length.toLocaleString()} photo${photos.length === 1 ? '' : 's'} to a person`}>
      <div className={cn('grid gap-3', busy && 'pointer-events-none opacity-60')}>
        <p className="text-sm text-muted">Each photo with a single face is added to the person you choose. A photo with no face yet or with several faces is left out, because Archive X cannot tell which face you mean.</p>
        {busy ? <p role="status" className="text-sm">Adding…</p> : null}
        <NamePicker onPick={(p) => void add(p)} onCancel={onClose} />
      </div>
    </Dialog>
  )
}
