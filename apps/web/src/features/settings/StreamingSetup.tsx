import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/api/client'

export interface SetupState { phase?: 'idle' | 'working' | 'done' | 'failed'; message?: string; percent?: number | null; error?: string }
export interface ServerInfo { url?: string; up?: boolean; installed?: boolean; ready?: boolean; download?: string; setup?: SetupState }
export const serverInfoKey = ['screen-streaming-server'] as const

const BTN = 'h-9 rounded-lg px-3 text-sm font-bold disabled:opacity-60'

/** What to do about a streaming server that is not running: start it (installed, or set up by Archive X), or set it up (downloads Stremio's streaming server and a Node.js to run it), with the progress while that happens. */
export function StreamingActions({ onUp }: { onUp?: () => void }) {
  const qc = useQueryClient()
  const info = useQuery({
    queryKey: serverInfoKey, retry: false, staleTime: 5_000,
    queryFn: () => api<ServerInfo>('/api/screen/streaming-server'),
    refetchInterval: (q) => (q.state.data?.setup?.phase === 'working' ? 1000 : false),
  })
  const [busy, setBusy] = useState(false)
  const d = info.data
  const working = d?.setup?.phase === 'working'
  const refresh = async () => { await qc.invalidateQueries({ queryKey: serverInfoKey }); await qc.invalidateQueries({ queryKey: ['addon-streams'] }) }
  const start = async () => {
    setBusy(true)
    try {
      const r = await api<{ up?: boolean; reason?: string }>('/api/screen/streaming-server/start', { method: 'POST', json: {} })
      toast(r.up ? 'Streaming server is running' : r.reason || 'It did not start.')
      await refresh()
      if (r.up) onUp?.()
    } catch { toast.error('Could not start the streaming server.') } finally { setBusy(false) }
  }
  const setup = async () => {
    setBusy(true)
    try { await api('/api/screen/streaming-server/setup', { method: 'POST', json: {} }); await refresh() } catch { toast.error('Could not start the setup.') } finally { setBusy(false) }
  }
  if (!d || d.up) return null
  const can = d.installed || d.ready
  return (
    <div className="grid gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        {can ? <button type="button" disabled={busy || working} onClick={() => void start()} className={`${BTN} bg-accent text-accent-fg`}>{busy ? 'Starting…' : 'Start it'}</button>
          : <button type="button" disabled={busy || working} onClick={() => void setup()} className={`${BTN} bg-accent text-accent-fg`} title="Downloads Stremio's streaming server and, if this PC has none, Node.js (about 35 MB)">{working ? 'Setting up…' : d.setup?.phase === 'failed' ? 'Try the setup again' : 'Set it up'}</button>}
        {!can ? <a href={d.download ?? 'https://github.com/Stremio/stremio-service/releases/latest'} target="_blank" rel="noreferrer" className={`${BTN} inline-flex items-center border border-line-2 font-semibold hover:bg-fg/5`}>Or install Stremio's own</a> : null}
      </div>
      {working ? <p role="status" aria-live="polite" className="m-0 text-xs text-muted">{d.setup?.message || 'Working…'}{typeof d.setup?.percent === 'number' ? ` ${d.setup.percent}%` : ''}</p> : null}
      {d.setup?.phase === 'failed' && d.setup.error ? <p role="alert" className="m-0 text-xs text-warn">{d.setup.error}</p> : null}
      {d.setup?.phase === 'done' && !d.up ? <p role="status" className="m-0 text-xs text-muted">Set up. Press Start it.</p> : null}
    </div>
  )
}
