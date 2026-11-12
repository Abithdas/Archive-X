import { useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { useChannels, useSourceChannels, useLanes } from '@/api/hooks'
import type { Channel } from '@/api/types'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { PageHead } from '@/design/PageHead'
import { Segmented } from '@/design/Segmented'
import { Skeleton } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import { CARD, Check, Stepper, detail, videosHint } from './parts'
import { AutomationWorkflow } from './AutomationWorkflow'
import { TransferWorkflow } from './TransferWorkflow'
import { ChannelAvatar } from '@/features/channels/ChannelAvatar'
import { activeLaneCount, jobPath, laneChips, toLanes, type Lane } from './lanes'
import { DEFAULT_OPTIONS, joinLaunch, pendingBody, photoFailure, photoLaunch, photoMode, photoRequest, prerequisitePlan, processBody, selectedStages, stageLabels, expandStages, type Preflight, type RunOptions, type Stage } from './metadata'


function LaneCard({ lane, busy, onAct }: { lane: Lane; busy: boolean; onAct: (l: Lane, a: Lane['actions'][number]) => void }) {
  const label = { pause: 'Pause', resume: 'Resume', cancel: 'Stop', recover: 'Recover & Resume' } as const
  return (
    <article data-kind={lane.kind} className="rounded-xl bg-bg p-3.5">
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-surface-3 text-accent"><Icon name={lane.kind === 'metadata' ? 'database' : 'transfer'} /></span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2"><strong className="truncate text-[15px]">{lane.title}</strong><span className="shrink-0 rounded-full bg-surface-3 px-2.5 py-0.5 text-[11px] font-bold capitalize text-muted">{lane.stopping ? 'stopping…' : lane.statusLabel}</span></div>
          <p className="truncate text-xs text-muted" title={lane.subtitle}>{lane.subtitle}</p>
          {lane.detail ? <p className="truncate text-xs text-faint">{lane.detail}</p> : null}
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-label={`${lane.title} progress`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(lane.pct)}><span className="block h-full rounded-full bg-accent transition-[width]" style={{ width: `${lane.pct}%` }} /></div>
          <div className="mt-1 flex items-center justify-between gap-2"><span className="text-[11px] tabular-nums text-faint">{Math.round(lane.pct)}%</span>
            <span className="flex gap-1.5">{lane.actions.map((a) => <button key={a} type="button" disabled={busy} onClick={() => onAct(lane, a)} className={cn('h-8 rounded-lg px-3 text-xs font-bold disabled:opacity-50', a === 'cancel' ? 'border border-line-2 text-bad hover:bg-bad/10' : 'bg-accent text-accent-fg')}>{label[a]}</button>)}</span>
          </div>
        </div>
      </div>
    </article>
  )
}

function LiveLanes() {
  const lanes = useLanes()
  const channels = useSourceChannels()
  const qc = useQueryClient()
  const [busy, setBusy] = useState('')
  const [stop, setStop] = useState<Lane | null>(null)
  const nameOf = (ids: unknown) => String(ids ?? '').split(',').map((x) => x.trim()).filter(Boolean).map((id) => channels.data?.find((c) => c.telegram_id === id)?.title ?? id).join(', ')
  const jobs = lanes.data?.jobs ?? []
  const list = useMemo(() => toLanes(jobs, nameOf), [jobs, channels.data]) // eslint-disable-line react-hooks/exhaustive-deps
  const act = async (l: Lane, a: Lane['actions'][number]) => {
    if (a === 'cancel') return setStop(l)
    setBusy(l.id)
    try {
      const r = await api<{ ok?: boolean; detail?: string; status?: string }>(jobPath(l.id, a), { method: 'POST' }).catch((e): { ok?: boolean; detail?: string; status?: string } => ({ ok: false, detail: detail(e, '') }))
      if (r.ok === false) toast.error(r.detail || `Could not ${a} job`)
      else toast(a === 'pause' ? (r.status === 'PAUSED' ? 'Job paused.' : 'Pause requested; the current safe operation will finish first.') : a === 'resume' ? `Job ${String(r.status ?? 'resuming').toLowerCase()}.` : 'Job recovered and queued from its saved state.')
    } finally {
      setBusy('')
      void qc.invalidateQueries({ queryKey: ['lanes'] })
      void qc.invalidateQueries({ queryKey: ['status'] })
    }
  }
  const confirmStop = async () => {
    const l = stop
    if (!l) return
    setStop(null)
    setBusy(l.id)
    try {
      const r = await api<{ ok?: boolean; detail?: string; status?: string }>(`/api/jobs/${encodeURIComponent(l.id)}/cancel`, { method: 'POST' })
      if (r?.ok === false) toast.error(r.detail || 'Could not stop job')
      else toast(r?.status === 'CANCELLED' ? 'Job stopped.' : 'Stop requested; Archive X is finishing cancellation safely.')
    } catch (e) {
      toast.error(detail(e, 'Could not stop job'))
    } finally {
      setBusy('')
      void qc.invalidateQueries({ queryKey: ['lanes'] })
      void qc.invalidateQueries({ queryKey: ['status'] })
    }
  }
  const chips = laneChips(lanes.data?.governor ?? {}, lanes.data?.resources ?? {})
  const n = activeLaneCount(jobs)
  return (
    <section aria-label="Live lanes" className={cn(CARD, 'mb-4')}>
      <div className="mb-3 flex items-baseline justify-between gap-3"><h2 className="text-xl font-extrabold">Live lanes</h2><span className="text-xs text-faint">Playback keeps priority</span></div>
      {lanes.isPending ? (
        <div role="status" aria-label="Loading lanes" className="flex flex-wrap gap-2">{Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-9 w-40 rounded-full" />)}</div>
      ) : lanes.isError ? (
        <p role="alert" className="text-sm text-warn">Live lane data is unavailable. Retrying automatically.</p>
      ) : (
        <>
          <ul className="mb-3 flex flex-wrap gap-2" aria-label="Resource usage">
            {chips.map((c) => <li key={c.key} className="rounded-full bg-surface-3 px-3.5 py-2 text-xs text-muted">{c.label} <strong className="ml-1 text-fg">{c.value}</strong></li>)}
          </ul>
          <p className="mb-3 text-xs text-faint" role="status">{n} active/queued lane job{n === 1 ? '' : 's'}</p>
          {list.length ? <div className="grid grid-cols-[minmax(0,1fr)] gap-2.5 md:grid-cols-2">{list.map((l) => <LaneCard key={l.key} lane={l} busy={busy === l.id} onAct={(x, a) => void act(x, a)} />)}</div> : <p className="text-sm text-muted">No active lane jobs. Start a transfer, local upload, manual Metadata &amp; Assets scan, or Content Automation to see its independent progress here.</p>}
        </>
      )}
      {stop ? (
        <Dialog open onClose={() => setStop(null)} title="Stop job">
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <p className="text-sm text-muted">Stop this active job? Archive X will cancel it cooperatively first; if it does not stop within the grace period, the job supervisor will escalate cancellation. Verified completed items remain saved.</p>
            <div className="flex flex-wrap justify-end gap-2"><button type="button" onClick={() => setStop(null)} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button><button type="button" onClick={() => void confirmStop()} className="h-11 rounded-xl bg-bad px-4 text-sm font-bold text-white">Stop job</button></div>
          </div>
        </Dialog>
      ) : null}
    </section>
  )
}

const STEPS = ['Channels', 'Processing Stages', 'Review', 'Run'] as const
function MetadataWorkflow() {
  const channels = useSourceChannels()
  const library = useChannels()
  // a saved channel has no video count of its own; the library's count is used when the channel is published
  const libraryCount = useMemo(() => new Map((Array.isArray(library.data) ? library.data : []).map((c) => [c.telegram_id, c.video_count])), [library.data])
  const qc = useQueryClient()
  const [step, setStep] = useState(0)
  const [q, setQ] = useState('')
  const [picked, setPicked] = useState<string[]>([])
  const [o, setO] = useState<RunOptions>(DEFAULT_OPTIONS)
  const [busy, setBusy] = useState<'' | 'run' | 'pending'>('')
  const [msg, setMsg] = useState('')
  const [ask, setAsk] = useState<null | { labels: string[]; affected: string[]; run: () => void }>(null)
  const list = (channels.data ?? []).filter((c: Channel) => c.title.toLowerCase().includes(q.trim().toLowerCase()))
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))
  const set = <K extends keyof RunOptions>(k: K, v: RunOptions[K]) => setO({ ...o, [k]: v })
  const stages = selectedStages(o)
  const needChannels = !picked.length
  const needStages = !stages.length && !photoMode(o)

  const canNext = step === 0 ? !needChannels : step === 1 ? !needStages : true
  const refreshAll = () => { void qc.invalidateQueries({ queryKey: ['lanes'] }); void qc.invalidateQueries({ queryKey: ['status'] }) }

  /** Queues the image scan / reconciliation job (if chosen) and returns its launch sentence; a failure stops the run with the classic wording. */
  const launchPhotos = async (): Promise<string> => {
    const mode = photoMode(o)
    if (!mode) return ''
    const req = photoRequest(mode, picked)
    const r = await api<{ ok?: boolean; id?: unknown; detail?: string }>(req.url, { method: 'POST', json: req.body }).catch((e) => { throw new ApiError(0, photoFailure(mode, e instanceof ApiError ? e.detail : e instanceof Error ? e.message : '')) })
    if (!r || r.ok === false || !r.id) throw new ApiError(0, photoFailure(mode, r?.detail ?? ''))
    return photoLaunch(mode, r.id, picked.length)
  }
  const run = async () => {
    if (needChannels) return void setMsg('Select at least one channel.')
    if (needStages) return void setMsg('Select at least one processing stage.')
    setBusy('run')
    setMsg('')
    try {
      let metaText = ''
      const finish = async () => {
        let photoText = ''
        try { photoText = await launchPhotos() } catch (e) { const t = joinLaunch(metaText, detail(e, 'Could not queue the image job')); setMsg(t); toast.error(t); refreshAll(); return }
        const all = joinLaunch(metaText, photoText)
        if (photoText) { setMsg(all); toast(all) }
        refreshAll()
      }
      if (!stages.length) { await finish(); return }
      const pf = await api<Preflight>('/api/metadata-assets/preflight', { method: 'POST', json: { channel_ids: picked, stages } })
      if (!pf.ok) throw new ApiError(0, `Could not validate prerequisites: ${pf.detail || 'unknown error'}`)
      const start = async (runStages: Stage[], autoAdded: Record<string, string[]>) => {
        const r = await api<{ ok?: boolean; detail?: string; already_running?: boolean; message?: string; id?: string }>('/api/metadata-assets/process-channel', { method: 'POST', json: processBody(picked, runStages, o, autoAdded) })
        if (!r.ok) throw new ApiError(0, `Could not start Metadata & Assets: ${r.detail || 'unknown error'}`)
        metaText = r.already_running ? r.message || 'A Metadata & Assets job is already running.' : `Metadata & Assets queued${r.id ? ` as job ${r.id}` : ''}: ${stageLabels(runStages).join(' → ')}${o.fullInventory ? ' → older-message + uploaded-caption reconciliation' : ''}`
        setMsg(metaText)
        toast(metaText)
        if (!r.already_running) setO((x) => ({ ...x, forceEntity: false }))
        await finish()
      }
      if (pf.ready === false) {
        const plan = prerequisitePlan(pf, stages)
        setAsk({ labels: plan.labels, affected: plan.affected, run: () => void start(plan.stages, plan.autoAdded).catch((e) => { setMsg(detail(e, 'Could not start')); toast.error(detail(e, 'Could not start')) }).finally(() => setBusy('')) })
        return
      }
      await start(expandStages(stages), {})
    } catch (e) {
      const t = detail(e, 'Could not start Metadata & Assets')
      setMsg(t)
      toast.error(t)
    } finally {
      setBusy((b) => (b === 'run' && ask ? b : ''))
    }
  }
  const pending = async () => {
    if (needChannels) return void setMsg('Select at least one channel.')
    if (!selectedStages(o, true).length) return void setMsg('Select at least one processing stage (Inventory is intentionally skipped).')
    setBusy('pending')
    try {
      const r = await api<{ ok?: boolean; detail?: string; no_pending?: boolean; already_running?: boolean; message?: string }>('/api/metadata-assets/process-pending', { method: 'POST', json: pendingBody(picked, o) })
      const t = !r.ok ? `Could not start: ${r.detail || 'unknown error'}` : r.no_pending ? r.message || 'No pending Library items.' : r.already_running ? r.message || 'A Metadata & Assets job is already running.' : r.message || 'Pending Library items queued.'
      setMsg(t)
      r.ok ? toast(t) : toast.error(t)
      refreshAll()
    } catch (e) {
      const t = detail(e, 'Could not start pending processing')
      setMsg(t)
      toast.error(t)
    } finally {
      setBusy('')
    }
  }

  return (
    <section aria-label="Metadata and Assets Manager" className={CARD}>
      <div className="mb-1 flex items-start gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-xl bg-surface-3 text-accent"><Icon name="database" /></span>
        <div><h2 className="text-lg font-extrabold">Metadata &amp; Assets Manager</h2><p className="text-sm text-muted">Select channels and run the recommended refresh. Use Advanced options only when you need a custom workflow.</p></div></div>
      <Stepper steps={STEPS} step={step} onStep={setStep} canGo={canNext} />

      {step === 0 ? (
        <div>
          <h3 className="mb-1 text-[15px] font-bold">1 · Channels</h3>
          <p className="mb-3 text-sm text-muted">Choose the Telegram channels that should be inventoried and refreshed.</p>
          <div className="mb-2 flex gap-2"><input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search channels…" aria-label="Search channels" className="h-10 min-w-0 flex-1 rounded-xl border border-line-2 bg-bg px-3 text-sm" />
            <button type="button" onClick={() => setPicked((channels.data ?? []).map((c) => c.telegram_id))} className="h-10 rounded-xl border border-line-2 px-3 text-xs font-bold">Select all</button>
            <button type="button" onClick={() => setPicked([])} className="h-10 rounded-xl border border-line-2 px-3 text-xs font-bold">Clear</button></div>
          <div role="group" aria-label="Metadata and Assets channels" className="max-h-56 overflow-auto rounded-xl border border-line bg-bg p-1">
            {channels.isPending ? <p className="p-3 text-sm text-muted">Loading channels…</p> : list.length ? list.map((c) => <Check key={c.telegram_id} checked={picked.includes(c.telegram_id)} onChange={() => toggle(c.telegram_id)} title={c.title} icon={<ChannelAvatar id={c.telegram_id} title={c.title} image={c.image} />} hint={videosHint(libraryCount.get(c.telegram_id) ?? c.video_count)} />) : <p className="p-3 text-sm text-muted">No channels match.</p>}
          </div>
          <p className="mt-2 text-xs text-muted" aria-live="polite">Selected: {picked.length} channel{picked.length === 1 ? '' : 's'}</p>
        </div>
      ) : step === 1 ? (
        <div className="grid gap-4">
          <div><h3 className="mb-1 text-[15px] font-bold">2 · Processing Stages</h3><p className="text-sm text-muted">The recommended refresh updates metadata, performer and studio details, and video snapshots.</p></div>
          <label className="grid gap-1.5 text-xs font-semibold text-muted">Scope
            <select value={o.scope} onChange={(e) => set('scope', e.target.value as RunOptions['scope'])} aria-label="Videos to process" className="h-11 rounded-xl border border-line-2 bg-bg px-3 text-sm text-fg">
              <option value="all">Entire channel (recommended)</option>{['25', '50', '100', '250'].map((n) => <option key={n} value={n}>First {n} videos</option>)}
            </select></label>
          <div className="rounded-xl border border-line p-2"><p className="px-2 pb-1 pt-1 text-xs font-bold text-muted">Processing stages</p>
            <Check checked={o.inventory} onChange={(v) => set('inventory', v)} title="Inventory" hint="Collect filenames and Telegram media information." />
            <Check checked={o.entities} onChange={(v) => set('entities', v)} title="Metadata & Entity Data" hint="Match video metadata, then collect linked performer details, artwork, and studio/site data in one pipeline." />
            <Check checked={o.snapshots} onChange={(v) => set('snapshots', v)} title="Snapshots" hint="Generate or repair the 36-frame playback snapshots." />
            <Check checked={o.imageScan} onChange={(v) => set('imageScan', v)} title="Image scan" hint="Scan the selected Telegram channels' full message history for photos and image documents. This is read-only and runs independently of the video scope limit." />
            <Check checked={o.photoReconcile} onChange={(v) => set('photoReconcile', v)} title="Reconcile deleted Telegram images" hint="Check indexed image message IDs and remove local entries, cached files, thumbnails, and face-analysis records only when Telegram confirms the message is missing or no longer contains an image. This does not rescan channel history." /></div>
          <div className="rounded-xl border border-line p-2"><p className="px-2 pb-1 pt-1 text-xs font-bold text-muted">Recheck options</p>
            <Check checked={o.forceMetadata} onChange={(v) => set('forceMetadata', v)} title="Force metadata recheck" />
            <Check checked={o.forceEntity} onChange={(v) => set('forceEntity', v)} title="Force performer/studio recheck for this run" />
            <Check checked={o.fullInventory} onChange={(v) => set('fullInventory', v)} title="Reconcile" hint="Checks older Telegram history and reconciles captions of delivered posts. No media re-upload." /></div>
        </div>
      ) : step === 2 ? (
        <div>
          <h3 className="mb-2 text-[15px] font-bold">3 · Review</h3>
          <dl className="grid gap-1.5 rounded-xl bg-bg p-3 text-sm">
            {[['Channels', picked.map((id) => channels.data?.find((c) => c.telegram_id === id)?.title ?? id).join(', ') || '—'], ['Scope', o.scope === 'all' ? 'Entire channel' : `First ${o.scope} videos`], ['Stages', [...stageLabels(stages), ...(o.imageScan ? ['Image scan'] : []), ...(o.photoReconcile ? ['Reconcile deleted Telegram images'] : [])].join(' → ') || '—'], ['Force metadata recheck', o.forceMetadata ? 'Yes' : 'No'], ['Force performer/studio recheck', o.forceEntity ? 'Yes' : 'No'], ['Reconcile older history', o.fullInventory ? 'Yes' : 'No']].map(([k, v]) => (
              <div key={k} className="grid grid-cols-[minmax(0,160px)_minmax(0,1fr)] gap-3"><dt className="text-muted">{k}</dt><dd className="font-semibold [overflow-wrap:anywhere]">{v}</dd></div>
            ))}
          </dl>
          {needChannels || needStages ? <p role="alert" className="mt-2 text-sm text-warn">{needChannels ? 'Select at least one channel.' : 'Select at least one processing stage.'}</p> : <p className="mt-2 text-sm text-muted">Configuration looks valid. Continue to Run.</p>}
        </div>
      ) : (
        <div className="grid gap-3">
          <h3 className="text-[15px] font-bold">4 · Run</h3>
          <div className="flex flex-wrap items-start gap-4">
            <div className="grid gap-1"><button type="button" disabled={!!busy} onClick={() => void run()} className="h-11 rounded-xl bg-accent px-6 text-sm font-extrabold text-accent-fg disabled:opacity-60">{busy === 'run' ? 'Starting…' : 'Run'}</button><small className="max-w-[220px] text-xs text-muted">Runs the selected channels using the chosen scope and processing stages.</small></div>
            <div className="grid gap-1"><button type="button" disabled={!!busy} onClick={() => void pending()} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-bold disabled:opacity-60">{busy === 'pending' ? 'Starting…' : 'Run Pending (no scan)'}</button><small className="max-w-[240px] text-xs text-muted">Processes saved channel/message IDs only; it does not read Telegram history.</small></div>
          </div>
          <p className="min-h-5 text-xs text-muted" aria-live="polite">{msg}</p>
        </div>
      )}
      <div className="mt-auto flex justify-between gap-2 pt-5">
        <button type="button" disabled={step === 0} onClick={() => setStep(step - 1)} className="h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold disabled:opacity-40">Back</button>
        {step < 3 ? <button type="button" disabled={!canNext} onClick={() => setStep(step + 1)} className="h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-50">{['Next: Processing Stages →', 'Next: Review →', 'Next: Run →'][step]}</button> : <span />}
      </div>
      {ask ? (
        <Dialog open onClose={() => { setAsk(null); setBusy('') }} title="Add required stages">
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <p className="text-sm text-muted">The selected stages require persisted prerequisite data that is not available yet.</p>
            <dl className="grid gap-1 text-sm"><div><dt className="inline text-muted">Required stage(s): </dt><dd className="inline font-semibold">{ask.labels.join(', ')}</dd></div><div><dt className="inline text-muted">Affected channel(s): </dt><dd className="inline font-semibold">{ask.affected.join(', ')}</dd></div></dl>
            <p className="text-sm text-muted">Add the required stage(s) before running the selected stages?</p>
            <div className="flex flex-wrap justify-end gap-2"><button type="button" onClick={() => { setAsk(null); setBusy('') }} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Cancel</button><button type="button" onClick={() => { const a = ask; setAsk(null); a.run() }} className="h-11 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg">Add stages and continue</button></div>
          </div>
        </Dialog>
      ) : null}
    </section>
  )
}

type Tab = 'transfer' | 'metadata' | 'automation'
export function TransfersPage() {
  const [tab, setTab] = useState<Tab>('transfer')
  const show = (t: Tab): string => (tab === t ? 'flex min-w-0 flex-col [&>section]:flex-1' : 'hidden min-w-0 flex-col lg:flex [&>section]:flex-1')
  return (
    <section className="mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
      <PageHead eyebrow="Transfer Workspace" title="Transfers" blurb="Upload local videos or images, move Telegram media and run release automation.">
        <span className="inline-flex h-7 items-center gap-1.5 rounded-full bg-accent/15 px-3 text-xs font-bold text-accent"><i className="size-1.5 rounded-full bg-accent" />Shared job queue</span>
      </PageHead>
      <LiveLanes />
      <div className="mb-4 lg:hidden"><Segmented<Tab> label="Workflow" value={tab} onChange={setTab} options={[{ value: 'transfer', label: 'Transfer' }, { value: 'metadata', label: 'Metadata' }, { value: 'automation', label: 'Automation' }]} /></div>
      <div className="grid grid-cols-[minmax(0,1fr)] items-stretch gap-4 lg:grid-cols-2">
        <div className={show('transfer')}><TransferWorkflow /></div>
        <div className={show('metadata')}><MetadataWorkflow /></div>
      </div>
      <div className={cn('mt-4', show('automation'))}><AutomationWorkflow /></div>
    </section>
  )
}
