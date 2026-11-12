import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { CHECKING, HEALTH_URL, LAST_KEY, NOT_CHECKED_DETAIL, REPAIRING, REPAIR_FAILED, REPAIR_URL, chipText, counts, failedText, lastChecked, overviewText, repairFailedText, repairToast, reportLines, stateOf, type HealthReport, type HealthState, type Summary } from './health'

const read = () => { try { return window.localStorage.getItem(LAST_KEY) } catch { return null } }
const write = (v: string) => { try { window.localStorage.setItem(LAST_KEY, v) } catch { /* private mode */ } }
const BTN = 'inline-flex h-9 items-center gap-2 rounded-xl border border-line-2 px-3 text-[13px] font-semibold hover:bg-fg/5 disabled:opacity-50'

/** "Health & Previews": library totals, a full check (read-only) and the local-only artwork cache repair. */
export function LibraryHealth() {
  const qc = useQueryClient()
  const summary = useQuery({ queryKey: ['library-summary'] as const, queryFn: () => api<Summary>('/api/operations/library-summary'), retry: false, staleTime: 30_000 })
  const [state, setState] = useState<HealthState>('unchecked')
  const [report, setReport] = useState<HealthReport | null>(null)
  const [detail, setDetail] = useState(NOT_CHECKED_DETAIL)
  const [last, setLast] = useState('Never')
  const [busy, setBusy] = useState(false)
  useEffect(() => setLast(lastChecked(read())), [])

  const check = async () => {
    if (busy) return
    setBusy(true); setState('checking'); setDetail(CHECKING)
    try {
      const r = await api<HealthReport>(HEALTH_URL)
      if (!r || r.detail) throw new ApiError(0, r?.detail || 'Library health endpoint did not return a report.')
      setReport(r); setState(stateOf(r)); setDetail(reportLines(r).join('\n'))
      const at = r.checked_at || new Date().toISOString(); write(at); setLast(lastChecked(at))
      void qc.invalidateQueries({ queryKey: ['library-summary'] })
    } catch (e) { setState('error'); setDetail(failedText(e instanceof ApiError && e.detail ? e.detail : e instanceof Error ? e.message : String(e))) } finally { setBusy(false) }
  }
  const repair = async () => {
    setDetail(REPAIRING)
    try {
      const r = await api<{ ok?: boolean; detail?: string; repaired?: number; skipped?: number; failed?: number }>(REPAIR_URL, { method: 'POST', json: { limit: 100 } })
      if (!r?.ok) return void setDetail(r?.detail || REPAIR_FAILED)
      toast(repairToast(r)); await check()
    } catch (e) { setDetail(repairFailedText(e instanceof ApiError && e.detail ? e.detail : e instanceof Error ? e.message : String(e))) }
  }
  return (
    <section aria-label="Library health" className="flex flex-col gap-3 rounded-xl border border-line bg-bg p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h5 className="text-[15px] font-bold">Library health</h5>
        <span data-health={state} className={cn('rounded-full px-2.5 py-0.5 text-[11px] font-bold', state === 'healthy' ? 'bg-good/15 text-good' : state === 'review' || state === 'error' ? 'bg-warn/15 text-warn' : 'bg-surface-3 text-muted')}>{chipText(state, report ?? undefined)}</span>
      </div>
      <p className="text-xs text-muted">{overviewText(state)}</p>
      <div className="grid grid-cols-3 gap-2 rounded-xl border border-line p-3 text-center" aria-label="Library totals">
        {counts(summary.data).map(([l, v]) => <div key={l}><strong className="block text-xl font-extrabold tabular-nums">{v}</strong><span className="text-xs text-muted">{l}</span></div>)}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled={busy} aria-busy={busy} onClick={() => void check()} className="inline-flex h-9 items-center gap-2 rounded-xl bg-accent px-3 text-[13px] font-bold text-accent-fg disabled:opacity-60" title="Check local previews, artwork caches and stale jobs"><Icon name="refresh" className={cn('size-4', busy && 'animate-spin')} />Run full check</button>
        <button type="button" disabled={busy} onClick={() => void repair()} className={BTN} title="Repair artwork bytes from matching local files">Repair artwork cache</button>
        <Link to="/" className={BTN}><Icon name="library" className="size-4" />Open library</Link>
        <span className="ml-auto text-xs text-muted">Last checked <strong className="text-fg">{last}</strong></span>
      </div>
      <details className="mt-auto text-xs"><summary className="cursor-pointer font-semibold">View check details</summary><div role="status" aria-live="polite" className="mt-2 whitespace-pre-wrap text-muted">{detail}</div></details>
    </section>
  )
}
