import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '@/api/client'
import { useMarkers, type MarkerRow } from '@/api/hooks'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { AUTO_SYNC_FAILED, MARKERS_UNAVAILABLE, NEEDS_INDEXING, REFRESH_FAILED, emptyMessage, headingFor, markerChips, refreshLabel, rowsFrom, type SyncResult } from './markers'

/**
 * "Scene markers": a chip per marker that jumps the player to that moment. If nothing is saved yet it asks the provider once
 * (quietly, as the classic page does) and offers "Refresh provider markers" to ask again.
 */
export function MarkersSection({ mediaId, onSeek, className }: { mediaId: number | undefined; onSeek: (seconds: number) => void; className?: string }) {
  const qc = useQueryClient()
  const markers = useMarkers(mediaId)
  const [open, setOpen] = useState(true)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const asked = useRef<number | undefined>(undefined)
  const rows = markers.data ?? []
  const chips = markerChips(rows)

  const sync = async (force: boolean) => {
    const id = mediaId
    if (!id) return
    const r = await api<SyncResult>(`/api/media/${id}/markers/sync`, { method: 'POST', json: { force } })
    qc.setQueryData<MarkerRow[]>(['markers', id], rowsFrom(r) as MarkerRow[])   // the timeline's chapters read the same list
    setStatus(String(r.status ?? ''))
  }

  // Nothing saved: ask the provider once for this video, without a button press.
  useEffect(() => {
    setStatus(''); setError('')
    if (!mediaId || markers.isPending || markers.isError || rows.length || asked.current === mediaId) return
    asked.current = mediaId
    void sync(false).catch(() => setError(AUTO_SYNC_FAILED))
  }, [mediaId, markers.isPending, markers.isError, rows.length]) // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = async () => {
    setBusy(true); setError('')
    try { await sync(true) } catch (e) { setError(e instanceof ApiError ? e.detail || REFRESH_FAILED : REFRESH_FAILED); setStatus('PROVIDER_ERROR') } finally { setBusy(false) }
  }

  return (
    <section aria-label="Scene markers" className={cn('rounded-[18px] border border-line bg-surface p-4', className)}>
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="flex w-full items-center justify-between gap-3 text-left">
        <h2 className="text-base font-bold">{headingFor(chips.length)}</h2>
        <Icon name="chevron-down" className={cn('size-4 text-muted transition-transform', !open && '-rotate-90')} />
      </button>
      {open ? (
        <div className="mt-3">
          {!mediaId ? <p className="text-sm text-muted">{NEEDS_INDEXING}</p>
            : markers.isPending ? <p role="status" className="text-sm text-muted">Loading scene markers…</p>
            : markers.isError ? <p className="text-sm text-muted">{MARKERS_UNAVAILABLE}</p>
            : chips.length ? (
              <ul className="flex flex-wrap gap-2">
                {chips.map((c) => (
                  <li key={c.key}>
                    <button type="button" title={c.tooltip} aria-label={c.label} onClick={() => onSeek(c.position)} className="inline-flex max-w-full items-center gap-2 rounded-full border border-line-2 bg-bg px-3 py-1.5 text-left text-sm hover:bg-fg/5">
                      <span className="shrink-0 font-semibold tabular-nums text-accent">{c.time}</span>
                      <b className="truncate">{c.title}</b>
                      {c.tag ? <span className="shrink-0 rounded-full bg-surface-3 px-2 py-0.5 text-[11px] text-muted">#{c.tag}</span> : null}
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="grid justify-items-start gap-2">
                <p className="text-sm text-muted">{emptyMessage(status, error)}</p>
                <button type="button" disabled={busy} onClick={() => void refresh()} className="h-9 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-60">{refreshLabel(busy)}</button>
              </div>
            )}
        </div>
      ) : null}
    </section>
  )
}
