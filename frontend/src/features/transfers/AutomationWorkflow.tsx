import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { api } from '@/api/client'
import { useAutomationConfig, useChannels, useStatus } from '@/api/hooks'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { DEFAULT_FORM, automationState, configBody, formFromConfig, queueView, reviewWarnings, startProblem, stepProblem, type AutomationForm, type QueueItem } from './automation'
import { CARD, Check, FIELD, Field, Stepper, detail } from './parts'

const STEPS = ['Source', 'Runtime & connectivity', 'Release rules', 'Queue & activity', 'Review', 'Run'] as const
type Resp = { ok?: boolean; detail?: string; id?: string; version?: string; mode?: string; url?: string; session_notes?: string; path?: string; cancelled?: boolean; config?: Record<string, unknown>; queue_items?: number; torrents?: number; title?: string }

function Saved({ on }: { on: boolean }) {
  return <span className={cn('ml-2 inline-flex items-center gap-1 text-[11px] font-normal', on ? 'text-good' : 'text-faint')}><i className={cn('size-1.5 rounded-full', on ? 'bg-good' : 'bg-surface-3')} />{on ? 'Saved' : 'Not saved'}</span>
}
const Bar = ({ pct }: { pct: number }) => <div className="h-1.5 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}><span className={cn('block h-full rounded-full transition-[width]', pct >= 100 ? 'bg-good' : 'bg-accent')} style={{ width: `${pct}%` }} /></div>

function ActiveCard({ x, onSkip }: { x: QueueItem; onSkip: (x: QueueItem) => void }) {
  return (
    <li className="rounded-xl border border-accent/40 bg-accent/5 p-3">
      <p className="truncate font-bold" title={x.title}>{x.title}</p>
      <p className="mt-0.5 text-xs text-muted">{x.label}{x.telemetry ? ` · ${x.telemetry}` : ''}</p>
      <div className="mb-1 mt-2 flex items-center justify-between gap-2 text-[11px] text-muted"><span>DOWNLOAD · {x.download.toFixed(0)}%</span>{x.downloading ? <button type="button" onClick={() => onSkip(x)} className="h-7 rounded-lg border border-line-2 px-2.5 text-[11px] font-bold text-fg">Skip</button> : null}</div>
      <Bar pct={x.download} />
      {x.sending ? <><p className="mb-1 mt-2 text-[11px] text-muted">UPLOAD · {x.upload.toFixed(0)}%</p><Bar pct={x.upload} /></> : null}
      {x.processing ? <p className="mt-2 text-[11px] text-muted">PROCESSING · {x.label}</p> : null}
    </li>
  )
}

/** Content Automation: RSS → qBittorrent → verify → Telegram, as the classic six-step flow with a live queue. */
export function AutomationWorkflow() {
  const cfg = useAutomationConfig()
  const channels = useChannels()
  const status = useStatus()
  const qc = useQueryClient()
  const [step, setStep] = useState(0)
  const [form, setForm] = useState<AutomationForm>(DEFAULT_FORM)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')
  const [conn, setConn] = useState<null | { ok: boolean; text: string }>(null)
  const [confirm, setConfirm] = useState<null | { kind: 'clear' | 'skip' | 'cancel'; item?: QueueItem }>(null)
  const loaded = useRef(false)
  const set = <K extends keyof AutomationForm>(k: K, v: AutomationForm[K]) => setForm((f) => ({ ...f, [k]: v }))
  const saved = (k: string) => Boolean(cfg.data?.[k])

  useEffect(() => {
    if (cfg.data && !loaded.current) {
      loaded.current = true
      setForm(formFromConfig(cfg.data))
    }
  }, [cfg.data])

  const ca = (status.data?.content_automation ?? {}) as Record<string, unknown>
  const view = queueView(ca, Number(cfg.data?.max_concurrent_torrents) || 1)
  const problem = stepProblem(step, form)
  const warnings = reviewWarnings(form)
  const canNext = step >= 4 || problem === ''
  const refresh = () => { void qc.invalidateQueries({ queryKey: ['status'] }); void qc.invalidateQueries({ queryKey: ['lanes'] }) }
  const post = (path: string, body?: object) => api<Resp>(`/api/operations/automation/${path}`, { method: 'POST', json: body ?? {} }).catch((e): Resp => ({ ok: false, detail: detail(e, 'request failed') }))

  const save = async (): Promise<Resp> => {
    const r = await post('config', configBody(form))
    if (r.ok) {
      setMsg('Saved.')
      loaded.current = false
      setForm((f) => ({ ...f, qbtPass: '', qbtKey: '' }))
      await qc.invalidateQueries({ queryKey: ['automation-config'] })
    } else setMsg(`Save failed: ${r.detail || 'unknown error'}`)
    return r
  }
  const act = async (name: string, fn: () => Promise<void>) => {
    setBusy(name)
    try { await fn() } finally { setBusy('') }
  }
  const browse = () => act('browse', async () => {
    const r = await post('pick-folder', { title: 'Select Content Automation download folder', initial_path: form.path, allow_create: true })
    if (r.cancelled) return
    if (r.ok && r.path) { set('path', r.path); setMsg('Download folder selected. Click Save configuration to apply it.') } else setMsg(`Folder selection failed: ${r.detail || 'unknown error'}`)
  })
  const saveFolder = () => act('folder', async () => {
    if (!form.path.trim()) return void setMsg('Enter or browse to a torrent download folder first.')
    const r = await post('config', { download_path: form.path.trim() })
    if (r.ok) { set('path', String(r.config?.download_path ?? form.path.trim())); setMsg('Torrent download folder saved.') } else setMsg(`Save folder failed: ${r.detail || 'unknown error'}`)
  })
  const validate = () => act('test', async () => {
    const s = await save()
    if (!s.ok) return void setMsg('Save engine selection before testing.')
    setMsg('Testing qBittorrent...')
    const r = await post('test-engine')
    if (r.ok) {
      setMsg(`qBittorrent: ${r.version || '?'} · ${r.mode || 'ready'}${r.url ? ` · ${r.url}` : ''}${r.session_notes ? ` · ${r.session_notes}` : ''}`)
      setConn({ ok: true, text: `Connected${r.version ? ` · ${r.version}` : ''}` })
    } else {
      setMsg(`Download engine test failed: ${r.detail || 'unknown error'}`)
      setConn({ ok: false, text: 'Not connected' })
    }
  })
  const start = () => act('start', async () => {
    const p = startProblem(form)
    if (p) return void setMsg(p)
    const s = await save()
    if (!s.ok) return void setMsg('Save configuration before starting.')
    const r = await post('start')
    setMsg(r.ok ? `Started job ${r.id}.` : `Start failed: ${r.detail || 'unknown error'}`)
    setConn(r.ok ? { ok: true, text: `Connected${r.version ? ` · ${r.version}` : ''}` } : { ok: false, text: 'Not connected' })
    r.ok ? toast('Content Automation started') : toast.error(r.detail || 'Start failed')
    refresh()
  })
  const stop = () => act('stop', async () => {
    const r = await post('stop')
    setMsg(r.ok ? 'Stopped.' : `Stop failed: ${r.detail || 'unknown error'}`)
    refresh()
  })
  const resume = () => act('resume', async () => {
    const r = await post('resume')
    setMsg(r.ok ? 'Previous Content Automation task resumed.' : `Resume failed: ${r.detail || 'unknown error'}`)
    refresh()
  })
  const confirmed = () => act('confirm', async () => {
    const c = confirm
    setConfirm(null)
    if (!c) return
    if (c.kind === 'clear') {
      const r = await post('clear-all')
      setMsg(r.ok ? `Cleared ${Number(r.queue_items || 0).toLocaleString()} queue item(s), ${Number(r.torrents || 0).toLocaleString()} torrent(s), and automation cache.` : r.detail || 'Could not clear Content Automation.')
    } else if (c.kind === 'skip' && c.item) {
      const r = await post('skip-item', { item_key: c.item.key })
      setMsg(r.ok ? `Skipped ${r.title || c.item.title}.` : `Skip failed: ${r.detail || 'unknown error'}`)
    } else {
      const r = await post('stop')
      setMsg(r.ok ? 'Previous Content Automation task cancelled.' : `Cancel failed: ${r.detail || 'unknown error'}`)
    }
    refresh()
  })

  const row = (label: string, value: ReactNode) => <div key={label} className="grid grid-cols-[minmax(0,150px)_minmax(0,1fr)] gap-3"><dt className="text-muted">{label}</dt><dd className="font-semibold [overflow-wrap:anywhere]">{value}</dd></div>
  const chanName = (id: string) => channels.data?.find((c) => c.telegram_id === id)?.title ?? id
  const numField = (label: string, k: 'poll' | 'minSize' | 'maxSize', suffix: string, min: number, max: number) => (
    <Field label={label}><span className="flex items-center gap-2"><input type="number" min={min} max={max} value={form[k]} onChange={(e) => set(k, Number(e.target.value))} className={FIELD} /><span className="text-xs text-faint">{suffix}</span></span></Field>
  )
  const confirmText = { clear: 'Clear all Content Automation queue, qBittorrent automation torrents, download/cache files, engine cache, jobs, and activity? This does not remove Telegram library data.', skip: `Skip "${confirm?.item?.title ?? ''}" and remove its torrent/download? It will not be processed or uploaded.`, cancel: 'Cancel the previous Content Automation task? The queue and downloaded files will be kept; use Clear all to remove them.' }
  const state = automationState(ca)

  return (
    <section aria-label="Content Automation" className={CARD}>
      <div className="mb-1 flex items-start gap-3"><span className="grid size-10 shrink-0 place-items-center rounded-xl bg-warn/15 text-warn"><Icon name="automation" /></span>
        <div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-2"><h2 className="text-lg font-extrabold">Content Automation</h2><span className="rounded-full bg-surface-3 px-3 py-1 text-xs text-muted" role="status">{state}</span></div>
          <p className="text-sm text-muted">RSS → qBittorrent → validate → identify local media → reuse/build individual snapshots → Telegram. Existing snapshots are reused only for the same unchanged local media; new or changed downloads build a new snapshot. qBittorrent is managed automatically by Archive X.</p></div></div>
      <Stepper steps={STEPS} step={step} onStep={setStep} canGo={canNext} />

      {step === 0 ? (
        <div className="grid gap-4">
          <h3 className="text-[15px] font-bold">1 · Source &amp; destination</h3>
          <Field label="RSS feed URL"><input type="text" value={form.rss} onChange={(e) => set('rss', e.target.value)} placeholder="https://example.com/feed.xml" aria-label="RSS feed URL" className={FIELD} /></Field>
          <Field label="Destination channel"><select value={form.destination} onChange={(e) => set('destination', e.target.value)} aria-label="Destination channel" className={FIELD}><option value="">—</option>{form.destination && !channels.data?.some((c) => c.telegram_id === form.destination) ? <option value={form.destination}>{form.destination}</option> : null}{(channels.data ?? []).map((c) => <option key={c.telegram_id} value={c.telegram_id}>{c.title}</option>)}</select></Field>
          <div className="rounded-xl border border-line bg-bg p-3 text-xs text-muted"><b className="block text-sm text-fg">Telegram Delivery Policy</b>Content Automation uses the shared Telegram delivery policy for canonical playback assets, delivery verification and cleanup. Workflow-specific destinations and source rules remain here.</div>
          <Field label="Torrent download folder" hint="qBittorrent will save Content Automation torrents here. The folder is created if needed.">
            <span className="flex flex-wrap gap-2"><input type="text" value={form.path} onChange={(e) => set('path', e.target.value)} placeholder="Downloads\ContentAutomation" aria-label="Torrent download folder" className={cn(FIELD, 'min-w-[200px] flex-1')} />
              <button type="button" disabled={!!busy} onClick={() => void browse()} className="h-11 rounded-xl border border-line-2 px-3 text-sm font-semibold">Browse…</button>
              <button type="button" disabled={!!busy} onClick={() => void saveFolder()} className="h-11 rounded-xl border border-line-2 px-3 text-sm font-semibold">Save folder</button></span>
          </Field>
        </div>
      ) : step === 1 ? (
        <div className="grid gap-4">
          <h3 className="text-[15px] font-bold">2 · Runtime &amp; connectivity</h3>
          <Field label="Engine"><select value="qbittorrent" disabled aria-label="Engine" className={FIELD}><option value="qbittorrent">qBittorrent (Web API)</option></select></Field>
          {numField('Poll interval', 'poll', 'sec', 30, 3600)}
          <p className="text-sm text-muted">Each completed torrent is verified, then runs Inventory → Metadata Match → Performer Data → Channel Data → Snapshots. The database is committed before Telegram Transfer Manager delivery; source torrent/file cleanup happens only after verified delivery.</p>
          <div className="flex items-center gap-2 rounded-xl bg-bg p-3 text-sm"><i className="size-2 rounded-full bg-good" /><b>qBittorrent</b><span className="text-muted">External process · recommended for stability testing</span></div>
          <details className="rounded-xl border border-line px-3 py-2"><summary className="cursor-pointer text-sm font-semibold">qBittorrent connection</summary>
            <div className="mt-3 grid gap-3">
              <Field label="WebUI"><input type="text" value={form.qbtUrl} onChange={(e) => set('qbtUrl', e.target.value)} aria-label="qBittorrent WebUI address" className={FIELD} /></Field>
              <Field label="Username"><input type="text" value={form.qbtUser} autoComplete="username" placeholder="optional" onChange={(e) => set('qbtUser', e.target.value)} aria-label="qBittorrent username" className={FIELD} /></Field>
              <Field label="Password"><input type="password" value={form.qbtPass} autoComplete="current-password" placeholder="Enter to replace saved password" onChange={(e) => set('qbtPass', e.target.value)} aria-label="qBittorrent password" className={FIELD} /></Field>
              <Field label="API key"><input type="password" value={form.qbtKey} autoComplete="off" placeholder="Enter to replace saved key (qBittorrent 5.2+)" onChange={(e) => set('qbtKey', e.target.value)} aria-label="qBittorrent API key" className={FIELD} /></Field>
              <p className="text-xs text-muted">Credentials: username<Saved on={saved('qbittorrent_username_saved')} /> · password<Saved on={saved('qbittorrent_password_saved')} /> · API key<Saved on={saved('qbittorrent_api_key_saved')} /></p>
            </div></details>
          {problem ? <p role="alert" className="text-sm text-warn">{problem}</p> : null}
        </div>
      ) : step === 2 ? (
        <div className="grid gap-4">
          <h3 className="text-[15px] font-bold">3 · Release rules</h3>
          <Field label="Name contains"><input type="text" value={form.must} onChange={(e) => set('must', e.target.value)} placeholder="Optional release-name text" aria-label="Name contains" className={FIELD} /></Field>
          <Field label="Resolution"><select value={form.only1080} onChange={(e) => set('only1080', e.target.value as AutomationForm['only1080'])} aria-label="Resolution" className={FIELD}><option value="1080p">1080p releases</option><option value="any">Any resolution</option></select></Field>
          <div className="grid grid-cols-2 gap-3">{numField('Min size', 'minSize', 'MiB', 0, 10240)}{numField('Max size', 'maxSize', 'MiB', 0, 10240)}</div>
          <div className="rounded-xl border border-line p-2">
            <Check checked={form.regex} onChange={(v) => set('regex', v)} title="Regex matching" />
            <Check checked disabled onChange={() => undefined} title="Remove completed torrent after verified delivery (required)" />
            <Check checked disabled onChange={() => undefined} title="Delete local download after verified delivery (required)" />
          </div>
          <Field label="When a file exceeds Telegram's safe upload limit" hint="Optimize may re-encode the video while preserving the original. Skip leaves the completed download on disk and does not remove its torrent.">
            <select value={form.oversized} onChange={(e) => set('oversized', e.target.value as AutomationForm['oversized'])} aria-label="Oversized file handling" className={FIELD}><option value="optimize">Optimize to fit (may reduce quality; original preserved)</option><option value="skip">Skip oversized files (keep local download)</option></select>
          </Field>
          {problem ? <p role="alert" className="text-sm text-warn">{problem}</p> : null}
        </div>
      ) : step === 3 ? (
        <div className="grid gap-3">
          <div className="flex items-center justify-between gap-2"><h3 className="text-[15px] font-bold">4 · Queue &amp; activity</h3><button type="button" disabled={!!busy} onClick={() => setConfirm({ kind: 'clear' })} className="h-9 rounded-xl border border-line-2 px-3 text-xs font-bold text-bad hover:bg-bad/10">Clear all</button></div>
          <div className="rounded-xl bg-bg p-3 text-sm" aria-live="polite">
            <p>{view.summary}</p>
            {view.restartPending ? (
              <div className="mt-3 rounded-xl border border-warn/50 bg-warn/10 p-3"><b>Previous Content Automation task is paused</b><p className="mt-1 text-xs text-muted">The application was closed while it was running. It will not start automatically.</p>
                <div className="mt-2 flex flex-wrap gap-2"><button type="button" onClick={() => void resume()} className="h-9 rounded-lg bg-accent px-3 text-xs font-bold text-accent-fg">Resume previous task</button><button type="button" onClick={() => setConfirm({ kind: 'cancel' })} className="h-9 rounded-lg border border-line-2 px-3 text-xs font-bold text-bad">Cancel previous task</button></div></div>
            ) : null}
            <p className="mt-2 text-xs text-muted">{view.downloads}</p>
            {view.paused ? <p className="mt-2 text-xs font-bold text-warn">Automation paused by operator</p> : null}
            {view.active.length ? <ul className="mt-3 grid gap-2">{view.active.map((x) => <ActiveCard key={x.key} x={x} onSkip={(i) => setConfirm({ kind: 'skip', item: i })} />)}</ul> : null}
            {view.queued.length ? (<><p className="mt-3 text-[11px] font-bold text-faint">QUEUED ({view.queued.length + view.queuedMore})</p><ol className="mt-1 grid gap-1">{view.queued.map((x, i) => <li key={x.key} className="flex gap-2 text-xs"><span className="rounded bg-surface-3 px-1.5">#{i + 1}</span><span className="truncate">{x.title}</span></li>)}</ol>{view.queuedMore ? <p className="text-xs text-faint">+{view.queuedMore} more</p> : null}</>) : null}
            {view.recent.length ? (<><p className="mt-3 text-[11px] font-bold text-faint">RECENT</p><ul className="mt-1 grid gap-1">{view.recent.map((x) => <li key={x.key} className="flex gap-2 text-xs"><span className="rounded bg-surface-3 px-1.5">{x.status}</span><span className="truncate">{x.title}</span></li>)}</ul></>) : null}
          </div>
        </div>
      ) : step === 4 ? (
        <div>
          <h3 className="mb-2 text-[15px] font-bold">5 · Review</h3>
          <p className={cn('mb-3 text-sm', warnings.length ? 'text-warn' : 'text-muted')} role={warnings.length ? 'alert' : undefined}>{warnings.length ? warnings.join(' ') : 'Configuration looks valid. Confirm the settings, then continue to Run.'}</p>
          <dl className="grid gap-1.5 rounded-xl bg-bg p-3 text-sm">
            {row('RSS feed', form.rss || '—')}{row('Destination', form.destination ? chanName(form.destination) : '—')}{row('Download folder', form.path || '—')}{row('Download adapter', 'qBittorrent')}{row('Delivery mode', 'Serial')}{row('Poll interval', `${form.poll} sec`)}
            {row('Resolution', form.only1080 === '1080p' ? '1080p only' : 'Any')}{row('Must contain', form.must || '—')}{row('Min size', `${form.minSize} MiB`)}{row('Max size', `${form.maxSize} MiB`)}
          </dl>
        </div>
      ) : (
        <div className="grid gap-3">
          <h3 className="text-[15px] font-bold">6 · Run</h3>
          <p className="text-sm text-muted">Start the configured automation or test the selected download engine. Active work remains visible in Operations and the task tray.</p>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" disabled={!!busy} onClick={() => void save()} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Save configuration</button>
            <button type="button" disabled={!!busy} onClick={() => void validate()} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">{busy === 'test' ? 'Validating…' : 'Validate qBittorrent'}</button>
            <button type="button" disabled={!!busy} onClick={() => void start()} className="h-11 rounded-xl bg-accent px-5 text-sm font-extrabold text-accent-fg disabled:opacity-60">{busy === 'start' ? 'Starting…' : 'Start automation'}</button>
            <button type="button" disabled={!!busy} onClick={() => void stop()} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold text-bad">Stop</button>
          </div>
          <p className="flex items-center gap-2 text-xs text-muted" role="status"><i className={cn('size-2 rounded-full', conn ? (conn.ok ? 'bg-good' : 'bg-bad') : 'bg-surface-3')} />{conn?.text ?? 'Not verified'}</p>
        </div>
      )}
      <p className="mt-3 min-h-5 text-xs text-muted" aria-live="polite">{msg}</p>
      <div className="mt-3 flex items-center justify-between gap-2">
        <button type="button" disabled={step === 0} onClick={() => setStep(step - 1)} className="h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold disabled:opacity-40">Back</button>
        <span className="flex gap-2">{step < 5 ? <button type="button" disabled={!!busy} onClick={() => void save()} className="h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold">Save configuration</button> : null}
          {step < 5 ? <button type="button" disabled={!canNext} onClick={() => setStep(step + 1)} className="h-10 rounded-xl bg-accent px-4 text-sm font-bold text-accent-fg disabled:opacity-50">Next</button> : null}</span>
      </div>
      {confirm ? (
        <Dialog open onClose={() => setConfirm(null)} title={confirm.kind === 'clear' ? 'Clear all automation data' : confirm.kind === 'skip' ? 'Skip this download' : 'Cancel previous automation'}>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4"><p className="text-sm text-muted">{confirmText[confirm.kind]}</p>
            <div className="flex flex-wrap justify-end gap-2"><button type="button" onClick={() => setConfirm(null)} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Keep</button>
              <button type="button" onClick={() => void confirmed()} className="h-11 rounded-xl bg-bad px-4 text-sm font-bold text-white">{confirm.kind === 'clear' ? 'Clear all' : confirm.kind === 'skip' ? 'Skip download' : 'Cancel task'}</button></div></div>
        </Dialog>
      ) : null}
    </section>
  )
}
