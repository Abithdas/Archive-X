import { useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { ApiError, api } from '@/api/client'
import { Dialog } from '@/design/Dialog'
import { Skeleton, SkeletonLine } from '@/design/Skeleton'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { useJobActions } from './JobControls'
import { JOB_LOAD_FAILED, activityRows, displayName, footerFor, mergeActivity, activityKey, parseDetail, pipeline, progressSummary, runInfo, shortId, toneOf, type Activity } from './jobdetail'
import { fmtAge } from './ops'
import { jobPath, primaryLabel } from './jobs'
import { Disclosure } from '@/design/Accordion'

type Obj = Record<string, unknown>
const fmtTime = (iso: string) => { const d = iso ? new Date(iso) : null; return d && !Number.isNaN(d.getTime()) ? d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '' }
const TONE: Record<string, string> = { good: 'bg-good', bad: 'bg-bad', warn: 'bg-warn', accent: 'bg-accent', muted: 'bg-muted' }
const LEVEL: Record<string, string> = { ERROR: 'text-bad', WARN: 'text-warn' }
const GROUP = 'rounded-2xl border border-line p-3.5'

/** Pipeline, run information, errors and activity for one job; refreshes every 2.5 s while open. */
export function JobDetails({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ['job', id], queryFn: () => api<Obj>(`${jobPath(id)}?activity_limit=40`), refetchInterval: 2500, retry: false })
  const [older, setOlder] = useState<Map<string, Activity>>(new Map())
  const [hasOlder, setHasOlder] = useState<boolean | null>(null)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const j = useMemo<Obj | null>(() => (q.data ? { ...q.data, id } : null), [q.data, id])
  const st = j ? String(j.status ?? 'UNKNOWN').toUpperCase() : ''
  const actions = useJobActions(j ?? { id }, onClose)

  // Once an older page has been loaded, only its cursor says whether more history remains.
  useEffect(() => { if (j && !older.size) setHasOlder(Boolean(j.activity_has_older)) }, [j, older.size])
  const latest = useMemo(() => new Map(((j?.activity as Activity[] | undefined) ?? []).map((x) => [activityKey(x), x])), [j])
  const rows = useMemo(() => activityRows(mergeActivity(older, latest), fmtTime), [older, latest])

  const loadOlder = async () => {
    const combined = mergeActivity(older, latest)
    const first = combined.reduce((min, x) => Math.min(min, Number(x.id ?? Infinity)), Infinity)
    if (!Number.isFinite(first) || loadingOlder) return
    setLoadingOlder(true)
    try {
      const r = await api<Obj>(`${jobPath(id)}?activity_before_id=${encodeURIComponent(String(first))}&activity_limit=500`)
      setOlder((m) => { const next = new Map(m); for (const x of (r.activity as Activity[] | undefined) ?? []) next.set(activityKey(x), x); return next })
      setHasOlder(Boolean(r.activity_has_older))
    } catch { /* keep what is shown; the button stays so it can be retried */ } finally { setLoadingOlder(false) }
  }

  const progress = j ? progressSummary(j) : { percent: 0, label: '' }
  const parsed = j ? parseDetail(j) : null
  const names = j ? displayName(j, id) : null
  const pipe = j ? pipeline(j, st, progress.percent) : null
  const info = j ? runInfo(j, parsed, shortId(id), fmtAge) : []
  const footer = j ? footerFor(st) : null

  return (
    <Dialog open side="right" onClose={onClose} title={names?.name ?? 'Job details'}
      footer={footer ? (
        <div className="flex flex-wrap justify-end gap-2 border-t border-line pt-3">
          {footer.primary ? <button type="button" disabled={actions.busy} onClick={() => void actions.primary(footer.primary!)} className={cn('h-10 rounded-xl px-4 text-sm font-bold disabled:opacity-60', footer.primary === 'pause' ? 'border border-line-2' : 'bg-accent text-accent-fg')}>{primaryLabel(footer.primary)}</button> : null}
          <button type="button" disabled={actions.busy || footer.cancel === 'Cancelling…'} onClick={() => void actions.stop()} className="h-10 rounded-xl bg-bad px-4 text-sm font-bold text-white disabled:opacity-60">{footer.cancel}</button>
        </div>
      ) : undefined}>
      {q.isPending ? (
        <div role="status" aria-label="Loading job details" className="grid gap-3 py-2">{Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-10 rounded-xl" />)}<SkeletonLine width="80%" /></div>
      ) : q.isError || !j ? (
        <p role="alert" className="py-2 text-sm text-bad">{JOB_LOAD_FAILED(q.error instanceof ApiError ? q.error.detail || 'Job not found' : 'Job not found')}</p>
      ) : (
        <div className="grid gap-3 text-left">
          <header className="flex items-start gap-3">
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent/15 text-accent" aria-hidden="true"><Icon name="activity" className="size-5" /></span>
            <div className="min-w-0">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-faint">Job details</p>
              <p className="truncate text-xs text-muted" title={id}>{`${names!.kind || names!.type || 'Job'} · #${shortId(id)}`}</p>
            </div>
          </header>

          <section className={GROUP} aria-label="Job status and progress">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span data-status={st.toLowerCase()} className="inline-flex items-center gap-1.5 rounded-full bg-surface-3 px-2.5 py-0.5 font-bold"><span className={cn('size-1.5 rounded-full', TONE[toneOf(st)])} />{st}</span>
              <span className="text-muted">Started {fmtTime(String(j.created_at || j.started_at || j.started || '')) || '—'}</span>
            </div>
            <div className="mt-2.5 flex items-baseline justify-between gap-3 text-sm"><span className="min-w-0 truncate">{String(j.current_operation || j.operation || 'Waiting for work')}</span><strong className="shrink-0 tabular-nums">{progress.label}</strong></div>
            <div role="progressbar" aria-label="Job progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress.percent)} className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-3"><span className={cn('block h-full rounded-full', TONE[toneOf(st)])} style={{ width: `${progress.percent}%` }} /></div>
            <div className="mt-1.5 flex justify-between gap-3 text-[11px] text-faint"><span className="truncate">{String(j.current_item || j.item || 'No current item')}</span><span className="shrink-0">Updated {fmtTime(String(j.updated_at || '')) || '—'}</span></div>
          </section>

          <section className={GROUP} aria-label="Pipeline">
            <div className="mb-2 flex items-center justify-between"><h3 className="text-sm font-bold">Pipeline</h3><span className="text-xs text-muted">{pipe!.completed} / {pipe!.total} stages</span></div>
            <ol className="grid gap-1.5">
              {pipe!.stages.map((x, i) => (
                <li key={x.label} data-state={x.state} className={cn('flex items-center gap-2.5 text-sm', x.state === 'todo' && 'text-muted')}>
                  <span className={cn('grid size-6 shrink-0 place-items-center rounded-full text-[11px] font-bold', x.state === 'done' ? 'bg-good/20 text-good' : x.state === 'active' ? 'bg-accent/20 text-accent' : x.state === 'failed' ? 'bg-bad/20 text-bad' : 'bg-surface-3 text-muted')}>
                    {x.state === 'done' ? <Icon name="check" className="size-3.5" /> : x.state === 'failed' ? <Icon name="alert" className="size-3.5" /> : x.state === 'active' ? <span className="size-2 animate-pulse rounded-full bg-current" /> : i + 1}
                  </span>
                  <span className={x.state === 'active' ? 'font-bold' : ''}>{x.label}</span>
                </li>
              ))}
            </ol>
          </section>

          {info.length ? (
            <section className={GROUP} aria-label="Run information">
              <h3 className="mb-2 text-sm font-bold">Run information</h3>
              <dl className="grid gap-2 sm:grid-cols-2">{info.map((i) => <div key={i.label} className="min-w-0"><dt className="text-[11px] text-muted">{i.label}</dt><dd className="text-sm font-semibold [overflow-wrap:anywhere]">{i.value}</dd></div>)}</dl>
            </section>
          ) : null}

          {j.error ? <section className={cn(GROUP, 'border-bad/50')} aria-label="Error"><h3 className="mb-1 text-sm font-bold text-bad">Error</h3><p className="text-sm [overflow-wrap:anywhere]">{String(j.error)}</p></section> : null}

          <Disclosure className={GROUP} summaryClassName="text-sm font-bold" summary={<><span>Activity</span><span className="ml-auto inline-flex items-center gap-1.5 text-[11px] font-semibold text-good"><span className="size-1.5 animate-pulse rounded-full bg-good" />Live</span></>}>
            <p className="mt-1 text-[11px] text-muted">Recent steps and important updates</p>
            <ul aria-label="Job activity" className="mt-2 max-h-80 overflow-y-auto">
              {rows.length ? rows.map((r) => (
                <li key={r.key} className="border-b border-line py-2">
                  <div className="flex items-start gap-2">
                    <span className={cn('min-w-[42px] font-mono text-[10px]', LEVEL[r.level] ?? 'text-faint')}>{r.level}</span>
                    <div className="min-w-0 flex-1 text-[11px] leading-snug">
                      <p className="[overflow-wrap:anywhere]">{r.message}</p>
                      {r.operation ? <p className="mt-0.5 text-muted">{r.operation}</p> : null}
                      <p className="mt-1 text-[10.5px] text-muted">{r.meta}</p>
                    </div>
                  </div>
                  <Disclosure className="ml-[50px] mt-1" summaryClassName="text-[11px] text-muted" summary="Metadata / paths / frame details"><pre className="mt-1 whitespace-pre-wrap text-[11px] [overflow-wrap:anywhere]">{r.details}</pre></Disclosure>
                </li>
              )) : <li className="py-2 text-[11px] text-muted">No detailed activity events yet. This panel updates while the job runs.</li>}
            </ul>
            {hasOlder ? <div className="flex justify-center pt-2"><button type="button" disabled={loadingOlder} onClick={() => void loadOlder()} className="h-9 rounded-xl border border-line-2 px-4 text-sm font-semibold disabled:opacity-60">{loadingOlder ? 'Loading older activity…' : 'Load older activity'}</button></div> : null}
          </Disclosure>

          {parsed ? <Disclosure className={GROUP} summaryClassName="text-sm font-bold" summary="Technical details"><div className="mt-2"><h4 className="mb-1 text-xs font-semibold text-muted">Job input</h4><pre className="max-h-64 overflow-auto whitespace-pre-wrap text-[11px] [overflow-wrap:anywhere]">{JSON.stringify(parsed, null, 2)}</pre></div></Disclosure> : null}
        </div>
      )}
      {actions.dialogs}
    </Dialog>
  )
}
