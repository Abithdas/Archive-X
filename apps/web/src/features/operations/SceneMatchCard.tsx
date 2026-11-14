import { useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Icon } from '@/design/Icon'
import { SceneMatchReview } from './SceneMatchReview'
import { retryStatus, retryToast } from './scenematch'

/** The Operations card: open the review without scanning, or queue one manual provider recheck and open it. */
export function SceneMatchCard() {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const retry = async () => {
    setBusy(true)
    try {
      const r = await api<{ ok?: boolean; detail?: string; id?: unknown; count?: number; message?: string }>('/api/metadata/scene-match-review/retry-unmatched', { method: 'POST', json: {} })
      if (r?.ok) { setStatus(retryStatus(r)); toast(retryToast(r)) } else setStatus(r?.detail || 'Could not queue unmatched recheck.')
      setOpen(true)
    } catch (e) { setStatus(`Retry failed: ${e instanceof ApiError && e.detail ? e.detail : e instanceof Error ? e.message : String(e)}`) } finally { setBusy(false) }
  }
  return (
    <div className="flex flex-col rounded-xl border border-line bg-bg p-4">
      <h5 className="text-[15px] font-bold">Scene Match Review &amp; Retry</h5>
      <p className="mb-3 mt-1 text-xs leading-relaxed text-muted"><strong className="text-fg">Review missing metadata or recheck unmatched videos.</strong> Open the review without starting a scan, or queue one manual provider recheck. Approving or rejecting candidates remains a separate action.</p>
      <div className="mt-auto flex flex-wrap gap-2">
        <button type="button" onClick={() => setOpen(true)} title="Open saved suggestions and missing metadata without rechecking" className="inline-flex h-9 items-center gap-2 rounded-xl border border-line-2 px-3 text-[13px] font-semibold hover:bg-fg/5">Open review</button>
        <button type="button" disabled={busy} onClick={() => void retry()} title="Queue one manual recheck and open the scene-match review" className="inline-flex h-9 items-center gap-2 rounded-xl bg-accent px-3 text-[13px] font-bold text-accent-fg disabled:opacity-60"><Icon name="refresh" className="size-4" />{busy ? 'Queueing recheck…' : 'Retry & review'}</button>
      </div>
      {status ? <p role="status" aria-live="polite" className="mt-2 text-xs text-muted">{status}</p> : null}
      {open ? <SceneMatchReview onClose={() => setOpen(false)} /> : null}
    </div>
  )
}
