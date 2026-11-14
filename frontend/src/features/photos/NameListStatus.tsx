import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'

export interface NameList {
  ok?: boolean; total: number; ready: boolean
  providers: Record<string, { configured: boolean; status: string; count: number; last_finished: string; last_error: string; page: number }>
}

const NAMES: Record<string, string> = { stashdb: 'StashDB', theporndb: 'ThePornDB' }

/** One line about the performer names kept on this PC for the "Who is this?" search, with a button to refresh them now. */
export function nameListLine(d: NameList | undefined, now = Date.now()): string {
  if (!d || typeof d.total !== 'number' || !d.providers) return ''
  const used = Object.entries(d.providers).filter(([, p]) => p.configured)
  if (!used.length) return 'Add a StashDB or ThePornDB key in Settings to keep a searchable list of performer names on this PC.'
  const busy = used.find(([, p]) => p.status === 'running')
  const failed = used.find(([, p]) => p.status === 'error')
  const times = used.map(([, p]) => Date.parse(p.last_finished)).filter((t) => Number.isFinite(t))
  const last = times.length ? Math.max(...times) : 0
  const ago = last ? ageText(now - last) : ''
  const base = `${d.total.toLocaleString()} performer names on this PC`
  if (busy) return `${base} · updating ${NAMES[busy[0]] ?? busy[0]}… (page ${busy[1].page})`
  if (failed) return `${base} · ${NAMES[failed[0]] ?? failed[0]} update failed (${failed[1].last_error || 'error'}); it will retry`
  return d.total ? `${base} · updated ${ago || 'recently'}` : 'The performer name list is empty; it fills in the background after you add a key.'
}

export function ageText(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60000))
  if (m < 2) return 'just now'
  if (m < 90) return `${m} minutes ago`
  const h = Math.round(m / 60)
  if (h < 36) return `${h} hours ago`
  return `${Math.round(h / 24)} days ago`
}

export function NameListStatus() {
  const qc = useQueryClient()
  const [asking, setAsking] = useState(false)
  const q = useQuery({
    queryKey: ['name-list'] as const,
    queryFn: () => api<NameList>('/api/photos/faces/performers/name-list'),
    refetchInterval: (query) => (Object.values(query.state.data?.providers ?? {}).some((p) => p.status === 'running') ? 5000 : 60000),
  })
  const text = nameListLine(q.data)
  if (!text) return null
  const configured = Object.values(q.data?.providers ?? {}).some((p) => p.configured)
  const sync = async () => {
    setAsking(true)
    try { await api('/api/photos/faces/performers/name-list/sync', { method: 'POST' }); toast('Updating the performer names…'); await qc.invalidateQueries({ queryKey: ['name-list'] }) }
    catch (e) { toast.error(e instanceof ApiError && e.detail ? e.detail : 'Could not start the update.') } finally { setAsking(false) }
  }
  return (
    <p className="flex flex-wrap items-center gap-2 text-xs text-muted" role="status">
      <span>{text}</span>
      {configured ? <button type="button" disabled={asking} onClick={() => void sync()} className="font-semibold text-accent hover:underline disabled:opacity-60">Update now</button> : null}
    </p>
  )
}
