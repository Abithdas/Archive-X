import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { usePreviewManifest } from '@/api/hooks'
import { Confirm } from '@/design/Confirm'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { ALL_FAILED, ALL_INDEXED, CHOOSE_SCOPE, DELETE_FAILED, SCAN_FAILED, SNAPSHOT_FRAMES, candidateKey, channelName, confirmCopy, deleteBody, deletePlan, doneToast, formatDuration, formatResolution, formatSize, frameUrl, introText, progressText, resultWarnings, scanSummary, scanTitles, scanUrl, scanningText, scopeOptions, selectionState, stoppedStatus, stoppedToast, validIds, type Candidate, type DeleteResult, type DupScan } from './dupes'

type Saved = Array<{ telegram_id?: string | number; title?: string | null }>
const BTN = 'inline-flex h-9 items-center gap-2 rounded-xl border border-line-2 px-3 text-sm font-semibold hover:bg-fg/5 disabled:opacity-50'
const reason = (e: unknown, fb: string) => (e instanceof ApiError && e.detail ? e.detail : e instanceof Error && e.message ? e.message : fb)

/** Four cached frames for one copy; they load only once the card scrolls into view. */
export function Frames({ cid, mid, title }: { cid: string; mid: number; title: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [seen, setSeen] = useState(typeof IntersectionObserver !== 'function')
  useEffect(() => {
    const el = ref.current
    if (seen || !el) return
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { setSeen(true); io.disconnect() } }, { rootMargin: '200px 0px', threshold: 0.01 })
    io.observe(el)
    return () => io.disconnect()
  }, [seen])
  const m = usePreviewManifest(cid, mid, seen)
  const rev = String(m.data?.revision ?? m.data?.updated_at ?? '')
  const note = !seen ? 'Loading cached frame…' : m.isPending ? 'Loading cached frame…' : m.isError ? 'Preview unavailable' : m.data?.frames_ready ? '' : 'No cached snapshot'
  return (
    <div ref={ref} className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Four cached snapshots">
      {SNAPSHOT_FRAMES.map((f, i) => (
        <figure key={f} className="m-0">
          <div className="relative grid aspect-video place-items-center overflow-hidden rounded-lg bg-surface-3 text-center text-[11px] text-muted">
            {m.data?.frames_ready ? <img alt={`Cached snapshot ${i + 1} of ${title}`} decoding="async" loading="lazy" src={frameUrl(cid, mid, f, rev)} className="absolute inset-0 size-full object-cover" onError={(e) => { e.currentTarget.style.display = 'none' }} /> : <span>{note}</span>}
          </div>
          <figcaption className="mt-1 text-[10px] text-faint">Frame {f + 1} / 36</figcaption>
        </figure>
      ))}
    </div>
  )
}

function Copy({ row, group, saved, picked, disabled, onToggle }: { row: Candidate; group: number; saved: Saved; picked: boolean; disabled: boolean; onToggle: () => void }) {
  const mid = Number(row.message_id || 0)
  const cid = String(row.channel_id ?? '')
  const title = String(row.title || row.filename || `Video ${mid}`)
  const channel = channelName(cid, row.channel_title, saved)
  return (
    <article data-group={group} className={cn('grid gap-3 rounded-xl border bg-bg p-3', picked ? 'border-bad' : 'border-line')}>
      <div className="flex flex-wrap items-start gap-3">
        <label className="inline-flex shrink-0 items-center gap-2 text-sm font-semibold">
          <input type="checkbox" checked={picked} disabled={disabled} onChange={onToggle} aria-label={`Select ${title} from ${channel}, message ${mid}, to delete`} />
          <span>Select to delete</span>
        </label>
        <div className="min-w-0 flex-1 text-left">
          <b className="block truncate text-sm" title={title}>{title}</b>
          <div className="text-xs text-muted">{channel} · Message ID <b>{mid}</b></div>
          <dl className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-label="Video properties">
            {[['Resolution', formatResolution(row.width, row.height)], ['File size', formatSize(row.size)], ['Duration', formatDuration(row.duration)]].map(([k, v]) => <div key={k}><dt className="inline text-faint">{k} </dt><dd className="inline font-semibold">{v}</dd></div>)}
          </dl>
        </div>
      </div>
      <Frames cid={cid} mid={mid} title={title} />
    </article>
  )
}

/** Verified duplicate video review. Scanning changes nothing; each delete is confirmed, checked against a kept copy, and run one at a time. */
function DuplicateDialog({ scope, saved, onClose }: { scope: string; saved: Saved; onClose: () => void }) {
  const [scan, setScan] = useState<DupScan | null>(null)
  const [status, setStatus] = useState(scanningText(scope === ALL_INDEXED))
  const [busy, setBusy] = useState(true)
  const [deleting, setDeleting] = useState(false)
  const [note, setNote] = useState('')
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [ask, setAsk] = useState<null | { title: string; text: string; ok: string; run: () => void }>(null)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const all = scope === ALL_INDEXED

  const run = useCallback(async () => {
    setBusy(true); setPicked(new Set()); setScan(null); setStatus(scanningText(all))
    try {
      const r = await api<DupScan>(scanUrl(scope))
      if (!alive.current) return
      if (!r || r.ok !== true) throw new ApiError(0, r?.detail || (all ? ALL_FAILED : SCAN_FAILED))
      setScan(r)
    } catch (e) { if (alive.current) setStatus(reason(e, all ? ALL_FAILED : SCAN_FAILED)) } finally { if (alive.current) setBusy(false) }
  }, [scope, all])
  useEffect(() => { void run() }, [run])

  const groups = scan?.groups ?? []
  const sel = selectionState(groups, picked, deleting || busy)
  const titles = scan ? scanTitles(scan, saved) : { title: all ? 'Duplicate videos across channels' : 'Duplicate videos', context: 'Verified duplicate review' }
  const sum = scan ? scanSummary(scan) : null
  const toggle = (key: string) => setPicked((s) => { const n = new Set(s); if (n.has(key)) n.delete(key); else n.add(key); return n })

  const start = () => {
    const plan = deletePlan(groups, picked, saved)
    if (!plan?.length) return void toast('Leave one directly matching copy unselected in each duplicate group.')
    const c = confirmCopy(plan)
    setAsk({ title: c.title, text: c.text, ok: c.ok, run: () => void (async () => {
      setDeleting(true)
      const warnings: string[] = []
      let deleted = 0, failure = '', failedAt: { channel: string; mid: number } | null = null, attempted = false
      for (let i = 0; i < plan.length; i++) {
        const item = plan[i]!
        const channel = channelName(item.row.channel_id, item.row.channel_title, saved)
        const mid = Number(item.row.message_id || 0)
        if (alive.current) setStatus(progressText(i, plan.length, channel, mid))
        try {
          const body = deleteBody(item)
          if (!validIds(body)) throw new Error('A selected channel or message ID is invalid.')
          attempted = true
          const r = await api<DeleteResult>('/api/channel-maintenance/delete-duplicate', { method: 'POST', json: body })
          if (!r || r.ok !== true) throw new Error(r?.detail || 'Telegram did not confirm the deletion.')
          deleted++; warnings.push(...resultWarnings(r))
        } catch (e) { failedAt = { channel, mid }; failure = reason(e, DELETE_FAILED); break }
      }
      if (alive.current) setDeleting(false)
      if (attempted && alive.current) await run()
      if (failure) {
        toast(stoppedToast(deleted, plan.length, failedAt?.channel ?? '', failedAt?.mid ?? '', failure), { duration: 10000 })
        if (alive.current) setNote(stoppedStatus(deleted, plan.length, failure))
      } else toast(doneToast(deleted, c.channels, warnings), { duration: 10000 })
    })() })
  }

  return (
    <Dialog wide open onClose={() => { if (deleting) toast('The delete batch is still running. Wait for it to finish before closing this review.'); else onClose() }} title={titles.title}
      footer={
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3">
          <div aria-live="polite" className="min-w-0 text-left"><b className="block text-sm">{sel.countText}</b><small className="text-xs text-muted">{sel.hint}</small></div>
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={!sel.canDelete} onClick={start} className="inline-flex h-9 items-center gap-2 rounded-xl bg-bad px-3 text-sm font-bold text-white disabled:opacity-50"><Icon name="trash" className="size-4" />Delete{sel.count ? ` ${sel.count}` : ''} selected</button>
            <button type="button" disabled={busy || deleting} onClick={() => void run()} className={BTN}><Icon name="refresh" className="size-4" />Scan again</button>
            <button type="button" disabled={deleting} onClick={onClose} className={BTN}>Close</button>
          </div>
        </div>
      }>
      <div className="grid gap-3 text-left">
        <p className="text-xs text-muted">{titles.context} — {introText(all)}</p>
        {sum ? (
          <div role="status" aria-live="polite" className="grid gap-1 text-sm">
            <div className="flex flex-wrap items-center gap-2 text-xs font-semibold">
              <span className={sum.warnings.length ? 'text-warn' : 'text-good'}>{deleting ? status : sum.state}</span>
              <span className="rounded-full bg-surface-2 px-2.5 py-1">{sum.groups} group{sum.groups === 1 ? '' : 's'}</span>
              <span className="rounded-full bg-surface-2 px-2.5 py-1">{sum.copies} {sum.copies === 1 ? 'copy' : 'copies'}</span>
              <span className="rounded-full bg-surface-2 px-2.5 py-1">{sum.all ? 'Across channels' : channelName(scan?.channel_id, scan?.channel_title, saved)}</span>
            </div>
            <div className="text-xs text-muted">{sum.detail}</div>
            {note ? <div className="text-xs font-semibold text-warn">{note}</div> : null}
            {sum.warnings.length ? <div className="rounded-lg border border-warn/50 p-2 text-xs text-muted">{sum.warnings.map((w, i) => <div key={i}>{w}</div>)}{scan?.fingerprint_warnings_truncated ? <div>Additional fingerprint warnings were omitted.</div> : null}</div> : null}
          </div>
        ) : <p role="status" aria-live="polite" className="text-sm text-muted">{status}</p>}
        {busy && !scan ? <div className="rounded-2xl border border-line p-3.5 text-sm text-muted">{all ? 'Reading saved video identities across channels…' : 'Reading the saved channel inventory and verifying duplicate content identities against Telegram…'}</div> : null}
        {sum && !groups.length ? <div className="rounded-2xl border border-line p-3.5"><b>No verified duplicate videos found.</b><p className="mt-1 text-xs text-muted">{sum.emptyNote} No Telegram messages were changed.</p></div> : null}
        {groups.map((g, gi) => {
          const rows = g.candidates ?? []
          return (
            <section key={gi} aria-label={`Group ${gi + 1}`} className="grid gap-2 rounded-2xl border border-line p-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2"><h5 className="text-sm font-bold">Group {gi + 1} <span className="font-normal text-muted">· {Number(g.duplicate_count || rows.length)} matching copies</span></h5><span className="text-[11px] text-accent">{(g.proof ?? []).join(' · ')}</span></div>
              {rows.map((r) => { const k = candidateKey(r); return <Copy key={k} row={r} group={gi} saved={saved} picked={picked.has(k)} disabled={deleting || busy || (!picked.has(k) && !sel.canSelectMore)} onToggle={() => toggle(k)} /> })}
            </section>
          )
        })}
      </div>
      {ask ? <Confirm title={ask.title} text={ask.text} ok={ask.ok} danger onCancel={() => setAsk(null)} onOk={() => { const go = ask.run; setAsk(null); go() }} /> : null}
    </Dialog>
  )
}

/** The Operations card: pick a channel (or all indexed videos) and open the review. */
export function ChannelDuplicateCard() {
  const q = useQuery({ queryKey: ['source-channels'] as const, queryFn: () => api<Saved>('/api/source-channels'), staleTime: 30_000 })
  const [scope, setScope] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const saved = Array.isArray(q.data) ? q.data : []
  const options = scopeOptions(saved)
  const go = () => { if (!scope) return void toast(CHOOSE_SCOPE); setOpen(scope) }
  return (
    <div className="rounded-xl border border-line bg-bg p-4">
      <h5 className="text-[15px] font-bold">Duplicate video review</h5>
      <p className="mb-3 mt-1 text-xs leading-relaxed text-muted">Choose a channel or all indexed videos to scan for verified duplicate video posts. Nothing is changed until you select copies and confirm.</p>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="dup-scope" className="text-xs font-semibold text-muted">Channel to scan</label>
        <select id="dup-scope" aria-label="Duplicate scan scope" value={scope} disabled={q.isPending} onChange={(e) => setScope(e.target.value)} className="h-9 min-w-0 max-w-full rounded-xl border border-line-2 bg-surface px-2 text-sm">
          {q.isPending ? <option value="">Loading channels…</option> : q.isError ? <option value="">Channel list unavailable — retry</option> : options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <button type="button" onClick={go} className="inline-flex h-9 items-center gap-2 rounded-xl border border-line-2 px-3 text-[13px] font-semibold hover:bg-fg/5">Scan for duplicates<Icon name="arrow-right" className="size-3.5" /></button>
      </div>
      {q.isError ? <button type="button" onClick={() => void q.refetch()} className="mt-2 text-xs font-semibold text-accent underline">Retry</button> : null}
      {open ? <DuplicateDialog key={open} scope={open} saved={saved} onClose={() => setOpen(null)} /> : null}
    </div>
  )
}
