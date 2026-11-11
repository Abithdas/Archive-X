import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import type { Channel, LibraryItem } from '@/api/types'
import { queryKeys, useChannels } from '@/api/hooks'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { ChannelPicker, SINGLE_CAPTION_LABEL } from './ChannelPicker'
import { channelName, directTarget, isAlreadySent, isDisplayName, noChannelMessage, sendBody, sentMessage, type SendSettings } from './send'

type Mode = { kind: 'resolving' } | { kind: 'closed' } | { kind: 'pick' } | { kind: 'again'; target: string }

/**
 * Send one video to a Telegram channel. With "Always send to this channel" saved it sends at once;
 * otherwise it asks which channel, shows the caption, and asks again before re-sending a duplicate.
 * Mounted by the card menu only while a send is in progress, so cards that never send cost nothing.
 */
export function SendToTelegram({ item, onDone }: { item: Pick<LibraryItem, '_cid' | 'id'>; onDone: () => void }) {
  const qc = useQueryClient()
  const channels = useChannels()
  const list = channels.data ?? []
  const [mode, setMode] = useState<Mode>({ kind: 'resolving' })
  const [q, setQ] = useState('')
  const [target, setTarget] = useState('')
  const [remember, setRemember] = useState(false)
  const [caption, setCaption] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Read the cache, not the render's `list`: this runs from a closure created before the channels had loaded.
  const nameOf = (id: string) => channelName(qc.getQueryData<Channel[]>(queryKeys.channels) ?? [], id)
  const send = async (to: string, rememberIt: boolean, force: boolean) => {
    const name = nameOf(to)
    setBusy(true)
    const pending = toast.loading(`Sending to ${name}…`)
    try {
      const r = await api<{ notes?: unknown; target_title?: string }>('/api/send', { method: 'POST', json: sendBody(item, to, rememberIt, force) })
      toast.success(sentMessage(isDisplayName(r.target_title) ? String(r.target_title) : name, r.notes), { id: pending })
      setMode({ kind: 'closed' })
      void qc.invalidateQueries({ queryKey: ['library'] })
      void qc.invalidateQueries({ queryKey: ['channels'] })
      if (rememberIt) void qc.invalidateQueries({ queryKey: ['settings', 'send'] })
    } catch (e) {
      if (e instanceof ApiError && isAlreadySent(e.status)) {
        toast.dismiss(pending)
        setMode({ kind: 'again', target: to })
      } else {
        toast.error(`Send failed: ${e instanceof ApiError ? e.detail || 'unknown error' : 'unknown error'}`, { id: pending })
        setMode((m) => (m.kind === 'pick' ? m : { kind: 'closed' }))   // keep the picker open so the user can retry
      }
    } finally { setBusy(false) }
  }

  const begun = useRef(false)
  const start = async () => {
    let c: SendSettings = {}
    try { await qc.ensureQueryData({ queryKey: queryKeys.channels, queryFn: () => api<Channel[]>('/api/channels'), staleTime: 30_000 }) } catch { /* names fall back to ids */ }
    try { c = await api<SendSettings>('/api/settings/send') } catch { /* fall back to the picker */ }
    const direct = directTarget(c)
    if (direct) return void send(direct, false, false)
    setQ(''); setRemember(false); setTarget(c.target ?? ''); setCaption(null)
    setMode({ kind: 'pick' })
  }

  useEffect(() => { if (!begun.current) { begun.current = true; void start() } })  // once, on mount
  useEffect(() => { if (mode.kind === 'closed') onDone() }, [mode.kind, onDone])
  useEffect(() => {
    if (mode.kind !== 'pick') return
    let live = true
    api<{ caption?: string }>('/api/send/preview', { method: 'POST', json: { channel_id: item._cid, message_id: item.id } })
      .then((r) => { if (live) setCaption(r.caption ?? '') })
      .catch(() => { if (live) setCaption('') })
    return () => { live = false }
  }, [mode.kind, item._cid, item.id])

  return (
    <>
      {mode.kind === 'pick' ? (
        <Dialog open onClose={() => !busy && setMode({ kind: 'closed' })} title="Send to Telegram">
          <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
            <ChannelPicker channels={list} q={q} onQ={setQ} target={target} onTarget={setTarget} caption={caption} captionLabel={SINGLE_CAPTION_LABEL} remember={remember} onRemember={setRemember} />
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" disabled={busy} onClick={() => setMode({ kind: 'closed' })} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button>
              <button type="button" disabled={busy} onClick={() => (target ? void send(target, remember, false) : toast(noChannelMessage))} className="flex h-11 items-center gap-2 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60"><Icon name="brand-telegram" className="size-4" />Send</button>
            </div>
          </div>
        </Dialog>
      ) : null}
      {mode.kind === 'again' ? (
        <Dialog open onClose={() => setMode({ kind: 'closed' })} title="Video already sent">
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <p className="text-sm text-muted [overflow-wrap:anywhere]">This video was already sent to {nameOf(mode.target)}. Send it again?</p>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" disabled={busy} onClick={() => { setMode({ kind: 'closed' }); toast('Not sent.') }} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button>
              <button type="button" disabled={busy} onClick={() => void send(mode.target, false, true)} className="h-11 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-60">Send again</button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </>
  )
}
