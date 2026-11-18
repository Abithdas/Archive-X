import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { useDiagnostics, useResources, useStatus } from '@/api/hooks'
import { Dialog } from '@/design/Dialog'
import { AnimatePresence, listItem, motion } from '@/design/motion'
import { Icon } from '@/design/Icon'
import { PageHead } from '@/design/PageHead'
import { Segmented } from '@/design/Segmented'
import { Skeleton, SkeletonGroup } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import { JobControls } from './JobControls'
import { JobDetails } from './JobDetails'
import { PerformerAudit } from './PerformerAudit'
import { AutoMatchReview, useAutoMatches } from './AutoMatchReview'
import { LibraryHealth } from './LibraryHealth'
import { SceneMatchCard } from './SceneMatchCard'
import { ChannelDuplicateCard } from './ChannelDuplicates'
import { ResourceControls } from './ResourceControls'
import { idOf, liveness } from './jobs'
import { clock, runningNow } from './runningNow'
import { downloadLogBundle, folderOf } from './logBundle'
import { attention, diagnosticTiles, filterJobs, filterLog, healthTiles, jobCounts, laneRows, policyLine, resourceCards, telemetryTiles, type JobFilter, type LogLevel, type Tile, type Tone } from './ops'
import { SubtleNumber } from '@/design/SubtleNumber'

type Obj = Record<string, unknown>
const CARD = 'rounded-[18px] border border-line bg-surface p-4 sm:p-5'
const TONE: Record<Tone, string> = { ok: 'text-good', warn: 'text-warn', bad: 'text-bad', neutral: 'text-fg' }
const stamp = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })

function Metric({ dot, value, label }: { dot: string; value: number; label: string }) {
  return (
    <div className="rounded-[14px] border border-line bg-surface px-4 py-3">
      <strong className="block text-2xl font-extrabold tabular-nums">{value}</strong>
      <span className="flex items-center gap-1.5 text-xs text-muted"><i className={cn('size-1.5 rounded-full', dot)} />{label}</span>
    </div>
  )
}
function TileGrid({ tiles, cols = 'grid-cols-2' }: { tiles: Tile[]; cols?: string }) {
  return (
    <dl className={cn('grid gap-2.5', cols)}>
      {tiles.map((t) => (
        <div key={t.label} className="min-w-0 rounded-xl bg-bg px-3.5 py-3">
          <dt className="text-xs text-muted">{t.label}</dt>
          <dd className={cn('mt-0.5 truncate text-[15px] font-bold', TONE[t.tone])} title={t.value}>{t.value}</dd>
        </div>
      ))}
    </dl>
  )
}
const Card = ({ title, aside, children, className }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) => (
  <section aria-label={title} className={cn(CARD, className)}>
    <div className="mb-3.5 flex items-center justify-between gap-3"><h3 className="text-lg font-extrabold">{title}</h3><span className="text-xs text-faint">{aside}</span></div>
    {children}
  </section>
)

const statusTone = (st: string): Tone => (/^(RUNNING|ACTIVE)$/.test(st) ? 'ok' : /^(FAILED|ERROR|CANCELLED)$/.test(st) ? 'bad' : /^(QUEUED|PAUSED|WAITING|RETRYING|COMPLETED_WITH_ERRORS)$/.test(st) ? 'warn' : 'neutral')

/** The jobs-list progress: files done of total when the job counts them, else the job's own percentage; "not yet measurable" only when it has neither. */
export function listProgress(j: Obj): { known: boolean; percent: number; text: string } {
  const total = Number(j.total) || 0
  const done = Math.max(0, Number(j.completed) || 0)
  if (total > 0) { const percent = Math.max(0, Math.min(100, Math.round((done / total) * 100))); return { known: true, percent, text: `${percent}% · ${done.toLocaleString()} / ${total.toLocaleString()}` } }
  const p = Number(j.progress)
  if (Number.isFinite(p) && p > 0) { const percent = Math.max(0, Math.min(100, p)); return { known: true, percent, text: `${percent.toFixed(percent < 10 ? 1 : 0)}%` } }
  return { known: false, percent: 0, text: 'progress not yet measurable' }
}

function JobRow({ j }: { j: Obj }) {
  const prog = listProgress(j)
  const lead = /^(\d+)%/.exec(prog.text)   // a whole-number percentage at the start: its digits change in place
  const progText = prog.known && lead ? <><SubtleNumber value={Math.round(prog.percent)} />{prog.text.slice(lead[1]!.length)}</> : prog.text
  const st = String(j.status ?? '').toUpperCase()
  const live = liveness(j)
  const item = String(j.item || '').trim()
  const [open, setOpen] = useState(false)
  const id = idOf(j)
  const detailed = Boolean(id) && String(j.type ?? '') !== 'PLAYBACK'   // a playback session has no job record
  return (
    <motion.li {...listItem} onClick={(e) => { if (detailed && !(e.target as HTMLElement).closest('button,a,dialog,[role=group]')) setOpen(true) }} className={cn('flex flex-col rounded-xl bg-bg p-3.5', detailed && 'cursor-pointer hover:bg-fg/[0.03]')}>
      <div className="flex items-center justify-between gap-3">
        {detailed ? <button type="button" onClick={() => setOpen(true)} title="Open for pipeline, worker, timing and error details" aria-label={`Details for ${String(j.name || j.type || 'task')}`} className="min-w-0 truncate text-left font-bold hover:underline">{String(j.name || j.type || 'Task')}</button> : <span className="min-w-0 truncate font-bold">{String(j.name || j.type || 'Task')}</span>}
        <span className={cn('shrink-0 rounded-full bg-surface-3 px-2.5 py-0.5 text-[11px] font-bold', TONE[statusTone(st)])}>{st.replace(/_/g, ' ')}</span>
      </div>
      <p className="mt-0.5 h-4 truncate text-xs leading-4 text-muted" title={String(j.operation || '')}>{String(j.operation || '')}</p>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-label="Progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={prog.known ? prog.percent : undefined}>
        <span className={cn('block h-full rounded-full bg-accent transition-[width] duration-500', !prog.known && 'w-1/3 animate-pulse')} style={prog.known ? { width: `${prog.percent}%` } : undefined} />
      </div>
      <p className="mt-1 text-[11px] tabular-nums text-faint">{progText}</p>
      {live ? <p role="status" className={cn('mt-1.5 flex items-start gap-1.5 text-xs leading-4', TONE[live.tone])}><i aria-hidden="true" className={cn('mt-[5px] size-1.5 shrink-0 rounded-full bg-current', live.tone === 'ok' && 'animate-pulse')} /><span className="min-w-0 [overflow-wrap:anywhere]">{live.text}</span></p> : null}
      {item && item !== String(j.operation || '') ? <p className="mt-0.5 truncate text-[11px] text-faint" title={item}>Now: {item}</p> : null}
      <JobControls j={j} />
      {open ? <JobDetails id={id} onClose={() => setOpen(false)} /> : null}
    </motion.li>
  )
}

const JOB_TABS: { value: JobFilter; label: string }[] = [{ value: 'all', label: 'All' }, { value: 'running', label: 'Running' }, { value: 'queued', label: 'Queued' }, { value: 'failed', label: 'Failed' }, { value: 'completed', label: 'Completed' }]
const LEVEL_TABS: { value: LogLevel; label: string }[] = [{ value: '', label: 'All' }, { value: 'INFO', label: 'Info' }, { value: 'WARN', label: 'Warning' }, { value: 'ERROR', label: 'Error' }, { value: 'SUCCESS', label: 'Success' }]
const MODULES = ['Telegram Transfer Manager', 'Metadata & Assets Manager', 'Content Automation', 'Backup & Restore', 'playback', 'other']
const OUTCOMES = ['EXPECTED', 'SUCCESS', 'TRANSIENT', 'DEGRADED', 'FAILURE', 'FATAL']
const SELECT = 'h-10 rounded-xl border border-line bg-surface px-3 text-sm font-medium'
const dot = (lv: string) => (lv === 'ERROR' || lv === 'CRITICAL' ? 'bg-bad' : lv === 'WARN' || lv === 'WARNING' ? 'bg-warn' : lv === 'SUCCESS' ? 'bg-good' : 'bg-accent')

function ActivityLog({ events }: { events: unknown[] }) {
  const [level, setLevel] = useState<LogLevel>('')
  const [module, setModule] = useState('')
  const [outcome, setOutcome] = useState('')
  const [query, setQuery] = useState('')
  const [bundling, setBundling] = useState(false)
  const rows = useMemo(() => filterLog(events, { level, module, outcome, query }), [events, level, module, outcome, query])
  const box = useRef<HTMLUListElement>(null)
  const atEnd = useRef(true)
  // Oldest to newest like the classic log; stay on the newest entry unless the reader scrolled up.
  useEffect(() => {
    const el = box.current
    if (el && atEnd.current) el.scrollTop = el.scrollHeight
  }, [rows])
  return (
    <section aria-label="Recent activity" className={CARD}>
      <div className="mb-3.5 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-lg font-extrabold">Recent activity</h3>
        <button
          type="button"
          disabled={bundling}
          onClick={() => {
            setBundling(true)
            downloadLogBundle()
              .then((r) => (r.kind === 'saved'
                ? toast.success(`Saved ${r.name}`, { description: `In ${folderOf(r.path)} (${r.files} files: logs, activity and diagnostics)`, duration: 15000 })
                : toast.success(`Downloaded ${r.name}`)))
              .catch((e: unknown) => toast.error(`Could not make the log bundle: ${e instanceof ApiError ? e.detail || `error ${e.status}` : e instanceof Error ? e.message : 'unknown error'}`))
              .finally(() => setBundling(false))
          }}
          title="Every log and diagnostic in one zip named with today's date and time, with passwords and tokens blanked. Send it when reporting a problem."
          className="inline-flex h-9 items-center gap-2 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-60"
        >
          {bundling ? 'Preparing…' : 'Download logs (.zip)'}
        </button>
      </div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Segmented label="Log level" value={level} onChange={setLevel} options={LEVEL_TABS} />
        <select aria-label="Log source" value={module} onChange={(e) => setModule(e.target.value)} className={SELECT}><option value="">All modules</option>{MODULES.map((m) => <option key={m}>{m}</option>)}</select>
        <select aria-label="Filter diagnostic outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)} className={SELECT}><option value="">All outcomes</option>{OUTCOMES.map((m) => <option key={m} value={m}>{m[0] + m.slice(1).toLowerCase()}</option>)}</select>
        <label className="flex h-10 min-w-[200px] flex-1 items-center gap-2 rounded-xl border border-line bg-bg px-3 text-faint"><Icon name="search" className="size-4" /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search visible logs…" aria-label="Search visible logs" className="min-w-0 flex-1 bg-transparent text-sm text-fg outline-none placeholder:text-faint" /></label>
      </div>
      <ul ref={box} onScroll={(e) => { const t = e.currentTarget; atEnd.current = t.scrollTop + t.clientHeight >= t.scrollHeight - 30 }} className="max-h-[420px] overflow-y-auto rounded-xl bg-bg" aria-label="Log entries">
        {rows.map((e, i) => {
          const lv = String(e.level ?? 'INFO').toUpperCase()
          const t = e.created_at ?? e.ts
          const time = t ? new Date(String(t)).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) : ''
          return (
            <li key={`${String(e.event_key ?? i)}:${i}`} className="grid grid-cols-[48px_10px_minmax(70px,130px)_minmax(0,1fr)] items-center gap-3 border-b border-line px-3 py-2.5 text-[13px] last:border-0">
              <time className="tabular-nums text-faint">{time}</time><i className={cn('size-2 rounded-full', dot(lv))} aria-label={lv} />
              <span className="truncate text-muted">{String(e.module ?? '')}</span><span className="min-w-0 truncate" title={String(e.message ?? '')}>{String(e.message ?? '')}</span>
            </li>
          )
        })}
        {!rows.length ? <li className="p-6 text-center text-sm text-muted">No log entries match.</li> : null}
      </ul>
    </section>
  )
}

 function MaintenanceLink({ title, text, label, onOpen }: { title: string; text: string; label: string; onOpen?: () => void }) {
  return (
    <div className="flex flex-col rounded-xl border border-line bg-bg p-4">
      <h5 className="text-[15px] font-bold">{title}</h5>
      <p className="mb-3 mt-1 text-xs leading-relaxed text-muted">{text}</p>
      {onOpen ? <button type="button" onClick={onOpen} className="mt-auto inline-flex h-9 w-fit items-center gap-2 rounded-xl border border-line-2 px-3 text-[13px] font-semibold hover:bg-fg/5">{label}</button> : <a href="/#admin" className="inline-flex h-9 items-center gap-2 rounded-xl border border-line-2 px-3 text-[13px] font-semibold hover:bg-fg/5">{label}<Icon name="external" className="size-3.5" /></a>}
    </div>
  )
}

function RunningNowCard({ data }: { data: ReturnType<typeof runningNow> }) {
  if (!data.orphanTransfer && !data.processes.length) return null
  return (
    <Card title="Running right now" className="mb-4" aside={<span className="rounded-full bg-surface-3 px-2.5 py-1 text-xs font-bold text-muted">{data.processes.length} helper process{data.processes.length === 1 ? '' : 'es'}</span>}>
      {data.orphanTransfer && (
        <p role="status" className="mb-3 rounded-xl bg-warn/10 p-3 text-sm text-warn">
          A Telegram transfer is still working but has no job row: <b>{data.orphanTransfer.operation}</b>
          {data.orphanTransfer.quietSeconds > 60 ? ` (no signal for ${clock(data.orphanTransfer.quietSeconds)})` : ''}
        </p>
      )}
      <ul className="grid gap-2 md:grid-cols-2">
        {data.processes.map((p) => (
          <li key={`${p.pid}-${p.label}`} className="rounded-xl bg-bg p-3 text-sm">
            <div className="flex items-center justify-between gap-2"><span className="font-semibold">{p.label}</span><span className="text-xs text-muted">pid {p.pid} · {clock(p.seconds)}</span></div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-3" aria-label={p.progress === null ? 'Working, no percentage available' : `${p.progress}%`}>
              <div className={cn('h-full rounded-full bg-accent', p.progress === null && 'w-1/3 animate-pulse')} style={p.progress === null ? undefined : { width: `${p.progress}%` }} />
            </div>
          </li>
        ))}
      </ul>
    </Card>
  )
}

export function OperationsPage() {
  const status = useStatus()
  const diag = useDiagnostics()
  const res = useResources()
  const qc = useQueryClient()
  const [tab, setTab] = useState<JobFilter>('all')
  const [confirmReset, setConfirmReset] = useState(false)
  const [audit, setAudit] = useState(false)
  const [review, setReview] = useState(false)
  const autoMatches = useAutoMatches()
  const st: Obj = (status.data ?? {}) as Obj
  const jobs = (Array.isArray(st.jobs) ? st.jobs : []) as Obj[]
  const counts = jobCounts(jobs)
  const shown = filterJobs(jobs, tab)
  const resources = (res.data?.resources ?? {}) as Obj
  const updated = stamp()

  const exportHttp = async () => {
    try {
      const r = await fetch('/api/diagnostics/metrics/export', { credentials: 'same-origin', cache: 'no-store' })
      if (!r.ok) throw new Error('export failed')
      const url = URL.createObjectURL(await r.blob())
      const a = document.createElement('a')
      a.href = url
      a.download = /filename="?([^";]+)"?/i.exec(r.headers.get('content-disposition') ?? '')?.[1] ?? 'archive-x-http-telemetry.json'
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      toast('HTTP telemetry window exported.')
    } catch {
      toast.error('Could not export HTTP telemetry.')
    }
  }
  const resetHttp = async () => {
    try {
      await api('/api/diagnostics/metrics/reset', { method: 'POST', json: {} })
      await qc.invalidateQueries({ queryKey: ['diagnostics'] })
      toast('HTTP telemetry window reset.')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.detail || 'Could not reset HTTP telemetry.' : 'Could not reset HTTP telemetry.')
    } finally {
      setConfirmReset(false)
    }
  }

  if (status.isPending)
    return (
      <section className="mx-auto w-full max-w-[calc(1800px/var(--ax-ui-zoom,1))] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
        <SkeletonGroup label="Loading operations" className="grid gap-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">{Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-[68px] rounded-[14px]" />)}</div>
          <Skeleton className="h-52 rounded-[18px]" />
          <div className="grid gap-4 lg:grid-cols-3">{Array.from({ length: 3 }, (_, i) => <Skeleton key={i} className="h-60 rounded-[18px]" />)}</div>
        </SkeletonGroup>
      </section>
    )

  const tele = telemetryTiles(st)
  return (
    <section className="mx-auto w-full max-w-[calc(1800px/var(--ax-ui-zoom,1))] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
      <PageHead eyebrow="Control Center" title="Operations" blurb="Service health, resources, diagnostics and live jobs">
        <span className="inline-flex h-7 items-center gap-1.5 rounded-full bg-good/15 px-3 text-xs font-bold text-good"><i className="size-1.5 rounded-full bg-good" />Live</span>
      </PageHead>

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5" role="group" aria-label="Job summary">
        <Metric dot="bg-accent" value={counts.running} label="Running" />
        <Metric dot="bg-warn" value={counts.queued} label="Queued" />
        <Metric dot="bg-bad" value={counts.failed} label="Failed" />
        <Metric dot="bg-good" value={counts.completed} label="Completed" />
        <Metric dot="bg-accent" value={counts.withErrors} label="With errors" />
      </div>

      <RunningNowCard data={runningNow(res.data, jobs)} />

      <Card title="Jobs" className="mb-4">
        <div className="mb-3"><Segmented label="Job filter" value={tab} onChange={setTab} options={JOB_TABS} /></div>
        {shown.length ? <ul className="grid grid-cols-[minmax(0,1fr)] auto-rows-fr gap-2.5 md:grid-cols-2"><AnimatePresence initial={false}>{shown.map((j, i) => <JobRow key={String(j.id ?? j.job_id ?? i)} j={j} />)}</AnimatePresence></ul> : <p className="py-3 text-sm text-muted">{tab === 'all' ? 'No active jobs.' : 'No jobs match this filter.'}</p>}
      </Card>

      <div className="mb-4 grid gap-4 lg:grid-cols-3">
        <Card title="System health" aside={`Updated ${updated}`}><TileGrid tiles={healthTiles(st)} /></Card>
        <Card title="Attention" aside="operator action">
          <ul className="grid gap-2">
            {attention(st, autoMatches.data?.count ?? autoMatches.data?.items?.length ?? 0).map((x) => (
              <li key={x.key} className="flex items-start gap-3 rounded-xl bg-bg p-3 text-sm">
                <span className={cn('mt-0.5 grid size-8 shrink-0 place-items-center rounded-full', x.tone === 'bad' ? 'bg-bad/15 text-bad' : x.tone === 'warn' ? 'bg-warn/15 text-warn' : 'bg-good/15 text-good')}><Icon name={x.tone === 'ok' ? 'check' : 'alert'} className="size-4" /></span>
                {x.action ? <button type="button" onClick={() => setReview(true)} className="self-center text-left font-semibold text-accent underline-offset-2 hover:underline">{x.text}</button> : <span className="self-center">{x.text}</span>}
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Transfer telemetry" aside={<span className="rounded-full bg-surface-3 px-2.5 py-0.5 text-[11px] font-bold text-muted">{tele.rate}</span>}><TileGrid tiles={tele.tiles} /></Card>
      </div>

      <Card title="Diagnostics" className="mb-4" aside={
        <span className="flex gap-2">
          <button type="button" onClick={() => void exportHttp()} className="h-8 rounded-lg border border-line-2 px-3 text-xs font-bold text-fg hover:bg-fg/5">Export HTTP window</button>
          <button type="button" onClick={() => setConfirmReset(true)} className="h-8 rounded-lg border border-line-2 px-3 text-xs font-bold text-fg hover:bg-fg/5">Reset HTTP window</button>
        </span>
      }>
        {diag.isPending ? <p className="text-sm text-muted">Loading diagnostics…</p> : diag.isError ? <p role="alert" className="text-sm text-warn">Diagnostics are unavailable right now.</p> : <TileGrid tiles={diagnosticTiles((diag.data ?? {}) as Obj)} cols="grid-cols-2 md:grid-cols-4" />}
      </Card>

      <section aria-label="Library maintenance" className={cn(CARD, 'mb-4')}>
        <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-accent">Library care</p>
        <h2 className="text-2xl font-extrabold tracking-tight">Library Maintenance</h2>
        <p className="mb-4 mt-1 max-w-3xl text-[15px] text-muted">Check library health and review duplicates or unmatched records. Fix issues to keep your library organized and up to date.</p>
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="flex flex-col gap-3 rounded-2xl border border-line p-4 [&>*:not(h4)]:flex-1">
            <h4 className="text-lg font-extrabold lg:min-h-[3.4rem] xl:min-h-0">Health &amp; Previews</h4>
            <LibraryHealth />
          </div>
          <div className="flex flex-col gap-3 rounded-2xl border border-line p-4 [&>*:not(h4)]:flex-1">
            <h4 className="text-lg font-extrabold lg:min-h-[3.4rem] xl:min-h-0">Duplicates</h4>
            <MaintenanceLink title="Duplicate performers" text="Compare names, aliases and artwork, then review the evidence before merging." label="Open performer review" onOpen={() => setAudit(true)} />
            <ChannelDuplicateCard />
          </div>
          <div className="flex flex-col gap-3 rounded-2xl border border-line p-4 [&>*:not(h4)]:flex-1">
            <h4 className="text-lg font-extrabold lg:min-h-[3.4rem] xl:min-h-0">Metadata &amp; scene matches</h4>
            <SceneMatchCard />
          </div>
        </div>
      </section>

      <Card title="Resource Controller" className="mb-4" aside={<span className="rounded-full bg-surface-3 px-2.5 py-1 text-xs font-bold text-muted">{String(o2(resources.control).mode_label || 'Automatic')}</span>}>
        <p className="-mt-2 mb-3 text-sm text-muted">Control how playback, background work, Telegram transfers and worker capacity share the machine.</p>
        {res.isError || !Object.keys(resources).length ? (
          <p role={res.isError ? 'alert' : 'status'} className="text-sm text-warn">{res.isPending ? 'Loading resource status…' : 'Resource status is unavailable. Retrying automatically.'}</p>
        ) : (
          <>
            <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {resourceCards(resources, st).map((c) => (
                <div key={c.title} className="min-w-0 rounded-xl bg-bg p-4">
                  <p className="text-xs text-muted">{c.title}</p>
                  <p className={cn('my-1 text-xl font-extrabold', TONE[c.tone])}>{c.value}</p>
                  <p className="text-xs leading-relaxed text-muted [overflow-wrap:anywhere]">{c.sub}</p>
                </div>
              ))}
            </div>
            <ul className="grid gap-2.5" aria-label="Resource lanes">
              {laneRows(resources).map((l) => (
                <li key={l.key} className="grid grid-cols-[88px_minmax(0,1fr)_44px] items-center gap-3 text-sm">
                  <b className="font-semibold">{l.label}</b>
                  <span className="h-1.5 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-label={`${l.label} lane`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={l.pct}><i className="block h-full rounded-full bg-accent transition-[width]" style={{ width: `${l.pct}%` }} /></span>
                  <span className="text-right text-xs tabular-nums text-muted">{l.active} / {l.limit}</span>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-faint">{policyLine(resources, st, updated)}</p>
            <ResourceControls resources={resources} />
          </>
        )}
      </Card>

      <ActivityLog events={Array.isArray(st.events) ? st.events : []} />

      {audit ? <PerformerAudit onClose={() => setAudit(false)} /> : null}
      {review ? <AutoMatchReview onClose={() => setReview(false)} /> : null}
      {confirmReset ? (
        <Dialog open onClose={() => setConfirmReset(false)} title="Reset HTTP telemetry">
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <p className="text-sm text-muted">Reset the bounded HTTP telemetry window? This clears only the in-memory HTTP request counters and latency samples.</p>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" onClick={() => setConfirmReset(false)} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button>
              <button type="button" onClick={() => void resetHttp()} className="h-11 rounded-xl bg-bad px-4 text-sm font-bold text-white">Reset telemetry</button>
            </div>
          </div>
        </Dialog>
      ) : null}
    </section>
  )
}
const o2 = (v: unknown): Obj => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Obj) : {})
