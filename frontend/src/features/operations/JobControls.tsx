import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { queryKeys } from '@/api/hooks'
import { Confirm } from '@/design/Confirm'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { PLAYBACK_STOPPING, PLAYBACK_UNKNOWN, READ_FAILED, REFRESH_FAILED, STOP_ERROR, alreadyFinished, controlsFor, doneMessage, errorFor, idOf, isTerminal, jobPath, playbackConfirm, playbackDone, primaryLabel, primaryPath, stopConfirm, stopDone, type Primary } from './jobs'

type Obj = Record<string, unknown>
const BTN = 'inline-flex h-8 items-center gap-1.5 rounded-lg border border-line-2 px-3 text-xs font-bold hover:bg-fg/5 disabled:opacity-50'
const reason = (e: unknown, fallback: string) => (e instanceof ApiError && e.detail ? e.detail : fallback)
const inFlight = new Set<string>()   // one control request per job at a time

/** Pause / resume / recover, stop and refresh for one job, or just "Stop playback" for a playback session. */
export function JobControls({ j }: { j: Obj }) {
  const qc = useQueryClient()
  const [ask, setAsk] = useState<null | { kind: 'stop'; st: string } | { kind: 'playback' }>(null)
  const [busy, setBusy] = useState(false)
  const c = controlsFor(j)
  const id = idOf(j)
  const refetch = () => Promise.all([qc.invalidateQueries({ queryKey: queryKeys.status }), qc.invalidateQueries({ queryKey: ['lanes'] })])
  if (!c) return null

  /** Runs one request for this job, never two at once, and refreshes the page data afterwards. */
  const run = async (fn: () => Promise<void>) => {
    if (!id || inFlight.has(id)) return
    inFlight.add(id); setBusy(true)
    try { await fn() } finally { inFlight.delete(id); setBusy(false); void refetch() }
  }
  const post = (tail: string) => api<{ ok?: boolean; detail?: string; status?: string }>(jobPath(id, tail), { method: 'POST' })

  const primary = (p: Primary) => run(async () => {
    try {
      const r = await post(primaryPath(p))
      if (r?.ok === false) return void toast(r.detail || errorFor(p))
      toast(doneMessage(p, r ?? {}))
    } catch (e) { toast(reason(e, errorFor(p))) }
  })
  /** Stop first reads the job again: it may have finished since the list was drawn. */
  const stop = () => run(async () => {
    try {
      const cur = await api<{ status?: string }>(jobPath(id))
      const st = String(cur.status ?? '').trim().toUpperCase()
      if (isTerminal(st)) return void toast(alreadyFinished(st))
      setAsk({ kind: 'stop', st })
    } catch (e) { toast(reason(e, READ_FAILED)) }
  })
  const confirmedStop = () => { setAsk(null); void run(async () => {
    try {
      const r = await post('/cancel')
      if (r?.ok === false) return void toast(r.detail || STOP_ERROR)
      toast(stopDone(r ?? {}))
    } catch (e) { toast(reason(e, STOP_ERROR)) }
  }) }
  const refresh = () => run(async () => {
    try { await api(jobPath(id)) } catch (e) { toast(reason(e, REFRESH_FAILED)) }   // read-only; the list refetches afterwards
  })
  const confirmedPlayback = () => { setAsk(null); toast(PLAYBACK_STOPPING); void run(async () => {
    try {
      const r = await api<{ stopping?: boolean; detail?: string }>('/api/play/stop', { method: 'POST' })
      toast(r?.detail ? r.detail : playbackDone(r ?? {}))
    } catch (e) { toast(reason(e, PLAYBACK_UNKNOWN)) }
  }) }

  return (
    <div className="mt-2.5 flex flex-wrap gap-1.5" role="group" aria-label={`Controls for ${String(j.name || j.type || 'job')}`}>
      {c.kind === 'playback' ? (
        <>
          <button type="button" disabled={busy} onClick={() => setAsk({ kind: 'playback' })} className={cn(BTN, 'text-bad')}><Icon name="stop" className="size-3.5" />Stop playback</button>
          <button type="button" disabled={busy} onClick={() => void refetch()} className={BTN}><Icon name="refresh" className="size-3.5" />Refresh</button>
        </>
      ) : (
        <>
          <button type="button" disabled={busy} onClick={() => void primary(c.primary)} className={cn(BTN, (c.primary === 'recover' || c.primary === 'retry-recovery') && 'border-accent bg-accent text-accent-fg hover:bg-accent')}><Icon name={c.primary === 'pause' ? 'pause' : 'play'} className="size-3.5" />{primaryLabel(c.primary)}</button>
          <button type="button" disabled={busy} onClick={() => void stop()} className={cn(BTN, 'text-bad')}><Icon name="stop" className="size-3.5" />Stop</button>
          <button type="button" disabled={busy} onClick={() => void refresh()} className={BTN}><Icon name="refresh" className="size-3.5" />Refresh</button>
        </>
      )}
      {ask?.kind === 'stop' ? (() => { const s = stopConfirm(ask.st); return <Confirm title={s.title} text={s.text} ok={s.ok} danger onCancel={() => setAsk(null)} onOk={confirmedStop} /> })() : null}
      {ask?.kind === 'playback' ? <Confirm title={playbackConfirm.title} text={playbackConfirm.text} ok={playbackConfirm.ok} danger onCancel={() => setAsk(null)} onOk={confirmedPlayback} /> : null}
    </div>
  )
}
