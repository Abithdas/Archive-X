/** Live-lane logic, ported from the classic Transfers page (refreshTransferLanes, _axTransferLaneName, job controls). */
type Obj = Record<string, unknown>
const s = (v: unknown) => String(v ?? '')
const num = (v: unknown) => (Number.isFinite(Number(v)) ? Number(v) : 0)

export interface LaneChip { key: string; label: string; value: string }
export function laneChips(gov: Obj, res: Obj): LaneChip[] {
  const bits: string[] = []
  if (gov.telegram_file_active_small != null) bits.push(`${s(gov.telegram_file_active_small)}/${gov.telegram_file_limit_small ?? '—'} small-file slots`)
  if (gov.telegram_file_active_large != null) bits.push(`${s(gov.telegram_file_active_large)}/${gov.telegram_file_limit_large ?? '—'} large-file slots`)
  return [
    { key: 'uploads', label: 'Uploads / transfers', value: `${gov.transfer_active ?? 0}/${gov.transfer_capacity ?? 2}` },
    { key: 'downloads', label: 'Telegram downloads', value: bits.join(' · ') || 'shared file-count limits' },
    { key: 'playback', label: 'Playback', value: gov.playback_active ? 'priority active' : 'idle' },
    { key: 'workers', label: 'CPU/disk workers', value: `${res.background_active ?? 0}/${res.background_capacity ?? '—'}` },
    { key: 'media', label: 'Media workers', value: `${res.media_workers ?? '—'}` },
  ]
}

const LOCAL = ['local', 'local_files', 'upload_local']
export function laneName(type: unknown, source: unknown, workflow: unknown): string {
  if (s(workflow).toUpperCase() === 'CONTENT_AUTOMATION') return 'Content Automation · delivery'
  if (s(type) === 'TELEGRAM_TRANSFER' && LOCAL.includes(s(source).toLowerCase())) return 'Local → channel upload'
  if (s(type) === 'TELEGRAM_TRANSFER') return 'Telegram → Telegram transfer'
  const names: Obj = { LOCAL_SCAN: 'Local folder scan', UPLOAD_LOCAL: 'Local → channel upload', MEDIA_MANAGER: 'Telegram Media Manager', METADATA_CHANNEL_PIPELINE: 'Metadata & Assets scan', METADATA_ASSETS: 'Metadata & Assets', CONTENT_AUTOMATION: 'Content Automation' }
  return s(names[s(type)]) || s(type) || 'Background job'
}
export type LaneKind = 'automation' | 'metadata' | 'transfer'
export function laneKind(type: unknown, workflow: unknown): LaneKind {
  const t = s(type).toUpperCase()
  if (t === 'CONTENT_AUTOMATION' || s(workflow).toUpperCase() === 'CONTENT_AUTOMATION') return 'automation'
  return ['METADATA_ASSETS', 'METADATA_CHANNEL_PIPELINE', 'MEDIA_MANAGER', 'LOCAL_SCAN'].includes(t) ? 'metadata' : 'transfer'
}

const ACTIVE = ['RUNNING', 'PAUSING', 'PAUSED', 'WAITING', 'CANCEL_REQUESTED']
const TERMINAL = ['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'CANCELLED', 'ERROR']
export const activeLaneCount = (jobs: Obj[]) => jobs.filter((j) => ACTIVE.includes(s(j.status).toUpperCase())).length

export interface Lane {
  id: string
  key: string
  kind: LaneKind
  title: string
  subtitle: string
  detail: string
  pct: number
  status: string
  statusLabel: string
  actions: Array<'pause' | 'resume' | 'cancel' | 'recover'>
  stopping: boolean
  /** Why a running lane is not moving bytes (FloodWait, silence, paused); empty when it is. */
  waiting: string
  /** How fast it goes, how long it has run and how long it should still take; empty when nothing is known. */
  timing: string
}

/** 75 → "1 min 15 s", 4000 → "1 h 6 min". */
export function span(seconds: number): string {
  const t = Math.max(0, Math.round(seconds))
  if (t < 60) return `${t} s`
  if (t < 3600) return `${Math.floor(t / 60)} min${t % 60 ? ` ${t % 60} s` : ''}`
  return `${Math.floor(t / 3600)} h ${Math.floor((t % 3600) / 60)} min`
}
export const rateText = (bps: number) => (bps >= 1048576 ? `${(bps / 1048576).toFixed(1)} MB/s` : `${Math.max(1, Math.round(bps / 1024))} KB/s`)
/** "2.4 MB/s · 1 min 15 s spent · about 3 min left", from whatever the job reports. */
export function timingText(m: Obj | undefined): string {
  if (!m) return ''
  const rate = num(m.rate_bytes_per_second)
  const eta = m.eta_seconds ?? m.eta_s
  return [rate > 0 ? rateText(rate) : '', m.elapsed_s != null ? `${span(num(m.elapsed_s))} so far` : '', eta != null && Number.isFinite(Number(eta)) ? `about ${span(num(eta))} left` : ''].filter(Boolean).join(' · ')
}

export interface Traffic { rate: number; count: number }
/** What is going up and what is coming down right now, for the Overview. */
export function traffic(jobs: Obj[]): { upload: Traffic; download: Traffic; items: Array<{ id: string; title: string; direction: 'upload' | 'download'; pct: number; text: string }> } {
  const out = { upload: { rate: 0, count: 0 }, download: { rate: 0, count: 0 }, items: [] as Array<{ id: string; title: string; direction: 'upload' | 'download'; pct: number; text: string }> }
  for (const j of jobs) {
    if (!ACTIVE.includes(s(j.status).toUpperCase())) continue
    const m = (j.metrics ?? {}) as Obj
    const dir = s(m.direction).toLowerCase()
    if (dir !== 'upload' && dir !== 'download') continue
    const rate = num(m.rate_bytes_per_second)
    out[dir].rate += rate
    out[dir].count += 1
    const done = num(m.bytes_done)
    const total = num(m.bytes_total)
    out.items.push({ id: s(j.id), title: s(m.filename || j.current_item).split(/[\\/]/).pop() || laneName(j.type, j.source, j.workflow), direction: dir, pct: total ? Math.min(100, (100 * done) / total) : num(j.progress), text: timingText(m) })
  }
  return out
}
/** One card per lane job. `nameOf` turns channel ids into titles. */
export function toLanes(jobs: Obj[], nameOf: (ids: unknown) => string): Lane[] {
  return jobs.map((j, index) => {
    const type = s(j.type).toUpperCase()
    const op = s(j.operation) || 'Queued'
    const status = s(j.status || 'QUEUED').toUpperCase()
    const kind = laneKind(j.type, j.workflow)
    const local = LOCAL.includes(s(j.source).toLowerCase())
    const shortName = type === 'LOCAL_SCAN' ? 'Local folder' : kind === 'automation' ? 'Automation' : kind === 'metadata' ? 'Metadata' : type === 'UPLOAD_LOCAL' || (type === 'TELEGRAM_TRANSFER' && local) ? 'Local upload' : 'Transfer'
    const file = s(j.current_item).split(/[\\/]/).pop()!.replace(/\s·\s\d+(?:\.\d+)?%$/, '')
    const from = nameOf(j.source_channel)
    const to = nameOf(j.destination_channel)
    const title = type === 'LOCAL_SCAN' ? ['Folder', from].filter(Boolean).join(' · ') : kind === 'metadata' ? ['Channel scan', from].filter(Boolean).join(' · ') : file ? `${/upload/i.test(op) ? 'Uploading ' : ''}${file}` : shortName
    const subtitle = [laneName(j.type, j.source, j.workflow), j.destination_channel ? `to ${to}` : '', j.source_channel && kind !== 'metadata' ? `from ${from}` : '', kind === 'metadata' || !file ? op : ''].filter(Boolean).join(' · ')
    const id = s(j.id)
    const interrupted = ['INTERRUPTED', 'RECOVERING'].includes(status)
    const paused = ['PAUSED', 'PAUSING'].includes(status)
    const terminal = TERMINAL.includes(status)
    const stopping = status === 'CANCEL_REQUESTED'
    const actions: Lane['actions'] = !id || type === 'LOCAL_SCAN' || terminal || stopping ? [] : interrupted ? ['recover', 'cancel'] : paused ? ['resume', 'cancel'] : ['pause', 'cancel']
    return { id, key: id || `${type}:${s(j.source)}:${index}`, kind, title, subtitle, detail: kind === 'metadata' ? file : '', pct: Math.max(0, Math.min(100, num(j.progress))), status, statusLabel: status.replaceAll('_', ' ').toLowerCase(), actions, stopping, waiting: s((j.metrics as Obj | undefined)?.waiting_reason), timing: timingText(j.metrics as Obj | undefined) }
  })
}
export const jobPath = (id: string, action: 'pause' | 'resume' | 'cancel' | 'recover') => `/api/jobs/${encodeURIComponent(id)}/${action}`
