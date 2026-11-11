import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Confirm } from '@/design/Confirm'
import { Dialog } from '@/design/Dialog'
import { Skeleton } from '@/design/Skeleton'
import { INTRO, LOCAL_ONLY, NEED_NAME, NOT_FOUND, READY, RESET_DONE, RESET_TEXT, SAVE_FAILED, SAVING, TITLE_MAX, chosenText, editBody, initialsOf, nothingToSave, photoProblem, previewUrl, resetPreviewUrl, shouldAskSync, startTitle, syncBody, syncChanges, syncCopy, syncDone, syncFailed, type ChannelRow, type Draft } from './channelEdit'

const BTN = 'h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'

/** Loads the channel, then edits it. */
export function ChannelEditor({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ['channel-edit', id] as const, queryFn: () => api<ChannelRow>(`/api/channels/${encodeURIComponent(id)}/edit`), retry: false, staleTime: 0, gcTime: 0 })
  if (q.isPending) return <Dialog open onClose={onClose} title="Edit Telegram channel"><Skeleton className="h-40 w-full rounded-xl" /></Dialog>
  if (q.isError || String(q.data?.telegram_id ?? '') !== id) return <Dialog open onClose={onClose} title="Edit Telegram channel"><p role="alert" className="text-sm text-bad">{q.error instanceof ApiError && q.error.detail ? q.error.detail : NOT_FOUND}</p></Dialog>
  return <Form channel={q.data} onClose={onClose} />
}

function Form({ channel, onClose }: { channel: ChannelRow; onClose: () => void }) {
  const qc = useQueryClient()
  const id = String(channel.telegram_id)
  const original = String(channel.title ?? '')
  const [title, setTitle] = useState(startTitle(channel))
  const [image, setImage] = useState('')
  const [reset, setReset] = useState(false)
  const [preview, setPreview] = useState(previewUrl(channel))
  const [previewOk, setPreviewOk] = useState(true)
  const [msg, setMsg] = useState({ text: READY, bad: false })
  const [busy, setBusy] = useState(false)
  const [ask, setAsk] = useState<null | Draft>(null)
  const file = useRef<HTMLInputElement>(null)
  const refresh = () => Promise.all(['channels', 'photo-channels', 'source-channels'].map((k) => qc.invalidateQueries({ queryKey: [k] })))

  const choose = (f: File | undefined) => {
    if (!f) return
    const p = photoProblem(f)
    if (p) { if (file.current) file.current.value = ''; return void setMsg({ text: p, bad: true }) }
    const r = new FileReader()
    r.onload = () => { const data = String(r.result || ''); setImage(data); setReset(false); setPreview(data); setPreviewOk(true); setMsg({ text: chosenText(f.name), bad: false }) }
    r.onerror = () => setMsg({ text: 'Could not read the selected image.', bad: true })
    r.readAsDataURL(f)
  }
  const useTelegramPhoto = () => { setImage(''); setReset(true); if (file.current) file.current.value = ''; setPreview(resetPreviewUrl(id)); setPreviewOk(true); setMsg({ text: RESET_TEXT, bad: false }) }
  const save = async () => {
    const draft: Draft = { title, original, imageData: image, reset }
    if (!title.trim()) return void setMsg({ text: NEED_NAME, bad: true })
    if (nothingToSave(draft)) return void onClose()
    setBusy(true); setMsg({ text: SAVING, bad: false })
    try {
      const r = await api<{ ok?: boolean; detail?: string }>(`/api/channels/${encodeURIComponent(id)}/edit`, { method: 'POST', json: editBody(draft) })
      if (!r || r.ok === false) throw new ApiError(0, r?.detail || SAVE_FAILED)
      await refresh()
      if (shouldAskSync(draft)) setAsk(draft)
      else { toast(RESET_DONE); onClose() }
    } catch (e) { setMsg({ text: e instanceof ApiError && e.detail ? e.detail : SAVE_FAILED, bad: true }); setBusy(false) }
  }
  const push = async (d: Draft) => {
    setAsk(null)
    try {
      const r = await api<{ ok?: boolean; detail?: string; error?: string; synced?: string[] }>(`/api/channels/${encodeURIComponent(id)}/sync-to-telegram`, { method: 'POST', json: syncBody(d) })
      if (!r || r.ok === false) throw new ApiError(0, r?.detail || r?.error || '')
      await refresh(); toast(syncDone(r.synced))
    } catch (e) { toast(syncFailed(e instanceof ApiError ? e.detail : '')) }
    onClose()
  }
  const copy = ask ? syncCopy(syncChanges(ask)) : null
  return (
    <>
      <Dialog open onClose={() => !busy && !ask && onClose()} title="Edit Telegram channel">
        <div className="grid gap-4 text-left">
          <p className="text-sm text-muted">{INTRO}</p>
          <label className="grid gap-1 text-xs font-semibold text-muted">Channel name
            <input value={title} maxLength={TITLE_MAX} autoComplete="off" placeholder="Enter channel name" onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void save() } }} className="h-11 rounded-xl border border-line-2 bg-bg px-3 text-sm font-normal text-fg" />
          </label>
          <section aria-label="Channel profile photo" className="flex items-center gap-4">
            <span className="relative grid size-20 shrink-0 place-items-center overflow-hidden rounded-full bg-surface-3 text-xl font-bold"><span aria-hidden="true">{initialsOf(title)}</span>
              {previewOk ? <img key={preview} src={preview} alt="Channel profile photo preview" className="absolute inset-0 size-full object-cover" onError={() => setPreviewOk(false)} /> : null}</span>
            <div className="grid gap-1.5"><strong className="text-sm">Profile photo</strong><span className="text-xs text-muted">PNG, JPEG, WebP or GIF · maximum 8 MiB</span>
              <input ref={file} type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden aria-label="Choose a photo file" onChange={(e) => choose(e.target.files?.[0])} />
              <div className="flex flex-wrap gap-2"><button type="button" className={BTN} disabled={busy} onClick={() => file.current?.click()}>Choose photo…</button><button type="button" className={BTN} disabled={busy} onClick={useTelegramPhoto}>Use Telegram photo</button></div></div>
          </section>
          <p role="status" aria-live="polite" className={msg.bad ? 'text-sm text-bad' : 'text-sm text-muted'}>{msg.text}</p>
          <div className="flex justify-end gap-2"><button type="button" className={BTN} disabled={busy} onClick={onClose}>Cancel</button><button type="button" className="h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60" disabled={busy} onClick={() => void save()}>Save changes</button></div>
        </div>
      </Dialog>
      {ask && copy ? <Confirm title={copy.title} text={copy.text} ok={copy.ok} cancel={copy.cancel} danger onCancel={() => { setAsk(null); toast(LOCAL_ONLY); onClose() }} onOk={() => void push(ask)} /> : null}
    </>
  )
}
