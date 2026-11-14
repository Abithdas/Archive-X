import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { queryKeys, useChannels } from '@/api/hooks'
import type { Channel } from '@/api/types'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { BULK_CAPTION_LABEL, ChannelPicker } from './ChannelPicker'
import { bulkJobId, bulkProgress, bulkSendBody, bulkSendLabel, bulkStarted, bulkSummary, bulkTitle, channelName, directTarget, duplicatesPrompt, hasDuplicates, noChannelMessage, type BulkResult, type DuplicateAction, type SendSettings } from './send'
import type { Picked } from './selection'

type Mode = { kind: 'resolving' } | { kind: 'closed' } | { kind: 'pick' } | { kind: 'dups'; target: string; remember: boolean; count: number }
let jobCounter = 0

/** Send every picked video to one channel as a single job, with live progress and a duplicate prompt. */
export function BulkSend({ picked, onDone }: { picked: Picked[]; onDone: () => void }) {
  const qc = useQueryClient()
  const channels = useChannels()
  const list = channels.data ?? []
  const [mode, setMode] = useState<Mode>({ kind: 'resolving' })
  const [q, setQ] = useState('')
  const [target, setTarget] = useState('')
  const [remember, setRemember] = useState(false)
  const [caption, setCaption] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const begun = useRef(false)
  const nameOf = (id: string) => channelName(qc.getQueryData<Channel[]>(queryKeys.channels) ?? [], id)

  const run = async (to: string, rememberIt: boolean, dup?: DuplicateAction) => {
    const dest = nameOf(to)
    const jobId = bulkJobId(Date.now(), ++jobCounter)
    const toastId = toast.loading(bulkStarted(picked.length, dest))
    setBusy(true)
    setMode({ kind: 'closed' })
    let polling = true
    let wake: () => void = () => {}
    const stopPolling = () => { polling = false; wake() }   // cut the sleep short so the summary is not delayed by up to a poll interval
    const poll = (async () => {
      while (polling) {
        await new Promise<void>((r) => { const t = setTimeout(r, 1600); wake = () => { clearTimeout(t); r() } })
        if (!polling) break
        try {
          const s = await api<{ job_id?: string; active?: boolean; done?: number; total?: number }>(`/api/send/bulk/status/${encodeURIComponent(jobId)}`)
          if (s.job_id === jobId && s.active) toast.loading(bulkProgress(dest, Number(s.done || 0), Number(s.total || picked.length), ''), { id: toastId })
        } catch { /* progress is best-effort */ }
      }
    })()
    try {
      const r = await api<BulkResult>('/api/send/bulk', { method: 'POST', json: bulkSendBody(picked, to, rememberIt, jobId, dup) })
      stopPolling()
      await poll
      if (hasDuplicates(r) && !dup) {
        toast.dismiss(toastId)
        setMode({ kind: 'dups', target: to, remember: rememberIt, count: r.duplicate_videos!.length })
        return
      }
      if (!r.ok) throw new ApiError(0, 'unknown error')
      const s = bulkSummary(r, (id) => `message ${id}`)
      toast[s.failed ? 'error' : 'success'](s.text, { id: toastId, duration: s.failed ? 12000 : 8000 })
      void qc.invalidateQueries({ queryKey: ['library'] })
      void qc.invalidateQueries({ queryKey: ['channels'] })
      if (rememberIt) void qc.invalidateQueries({ queryKey: ['settings', 'send'] })
      onDone()
    } catch (e) {
      stopPolling()
      toast.error(`Bulk send failed: ${e instanceof ApiError ? e.detail || 'unknown error' : 'unknown error'}`, { id: toastId, duration: 8000 })
      setMode({ kind: 'pick' })
    } finally { setBusy(false) }
  }

  const start = async () => {
    try { await qc.ensureQueryData({ queryKey: queryKeys.channels, queryFn: () => api<Channel[]>('/api/channels'), staleTime: 30_000 }) } catch { /* names fall back to ids */ }
    let c: SendSettings = {}
    try { c = await api<SendSettings>('/api/settings/send') } catch { /* picker */ }
    const direct = directTarget(c)
    if (direct) return void run(direct, false)
    setTarget(c.target ?? '')
    setMode({ kind: 'pick' })
  }
  useEffect(() => { if (!begun.current) { begun.current = true; void start() } })
  useEffect(() => { if (mode.kind === 'closed' && !busy) onDone() }, [mode.kind, busy, onDone])
  useEffect(() => {
    if (mode.kind !== 'pick') return
    let live = true
    const first = picked[0]
    if (!first) return
    api<{ caption?: string }>('/api/send/preview', { method: 'POST', json: { channel_id: first.channel_id, message_id: first.message_id } })
      .then((r) => { if (live) setCaption(r.caption ?? '') }).catch(() => { if (live) setCaption('') })
    return () => { live = false }
  }, [mode.kind, picked])

  return (
    <>
      {mode.kind === 'pick' ? (
        <Dialog open onClose={() => setMode({ kind: 'closed' })} title={bulkTitle(picked.length)}>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
            <ChannelPicker channels={list} q={q} onQ={setQ} target={target} onTarget={setTarget} caption={caption} captionLabel={BULK_CAPTION_LABEL} remember={remember} onRemember={setRemember} />
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" onClick={() => setMode({ kind: 'closed' })} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button>
              <button type="button" onClick={() => (target ? void run(target, remember) : toast(noChannelMessage))} className="flex h-11 items-center gap-2 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg"><Icon name="brand-telegram" className="size-4" />{bulkSendLabel(picked.length)}</button>
            </div>
          </div>
        </Dialog>
      ) : null}
      {mode.kind === 'dups' ? (
        <Dialog open onClose={() => { setMode({ kind: 'closed' }); toast('Not sent.') }} title="Some videos were already sent">
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <p className="text-sm text-muted [overflow-wrap:anywhere]">{duplicatesPrompt(mode.count, nameOf(mode.target))}</p>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" onClick={() => { setMode({ kind: 'closed' }); toast('Not sent.') }} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button>
              <button type="button" onClick={() => void run(mode.target, mode.remember, 'skip')} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Skip duplicates</button>
              <button type="button" onClick={() => void run(mode.target, mode.remember, 'resend')} className="h-11 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg">Send again</button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </>
  )
}
