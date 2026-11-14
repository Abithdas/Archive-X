/** Content Automation (RSS → qBittorrent → verify → Telegram): form model, validation and queue view, ported from the classic UI. */
type Obj = Record<string, unknown>
const s = (v: unknown) => String(v ?? '')
const n = (v: unknown) => (Number.isFinite(Number(v)) ? Number(v) : 0)
const o = (v: unknown): Obj => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Obj) : {})

export interface AutomationForm {
  rss: string
  destination: string
  path: string
  poll: number
  qbtUrl: string
  qbtUser: string
  qbtPass: string
  qbtKey: string
  must: string
  only1080: '1080p' | 'any'
  minSize: number
  maxSize: number
  regex: boolean
  rmTorrent: boolean
  rmLocal: boolean
  oversized: 'optimize' | 'skip'
}
export const DEFAULT_FORM: AutomationForm = { rss: '', destination: '', path: '', poll: 120, qbtUrl: 'http://127.0.0.1:8080', qbtUser: '', qbtPass: '', qbtKey: '', must: '1080p', only1080: '1080p', minSize: 0, maxSize: 0, regex: false, rmTorrent: true, rmLocal: true, oversized: 'optimize' }

/** Saved config → form. Secrets never come back from the server, so they start empty ("enter to replace"). */
export function formFromConfig(c: Obj): AutomationForm {
  return {
    rss: s(c.rss_url), destination: s(c.destination_channel), path: s(c.download_path), poll: n(c.poll_interval) || 120,
    qbtUrl: s(c.qbittorrent_url) || DEFAULT_FORM.qbtUrl, qbtUser: s(c.qbittorrent_username), qbtPass: '', qbtKey: '',
    must: c.rss_must_contain == null || c.rss_must_contain === '' ? '1080p' : s(c.rss_must_contain),
    only1080: c.only_1080p === false ? 'any' : '1080p', minSize: n(c.rss_min_size_mb), maxSize: n(c.rss_max_size_mb), regex: Boolean(c.rss_use_regex),
    rmTorrent: c.remove_torrent_after_delivery !== false, rmLocal: c.delete_local_after_delivery !== false, oversized: c.oversized_file_policy === 'skip' ? 'skip' : 'optimize',
  }
}
/** Form → the exact body the config endpoint takes (one torrent at a time, qBittorrent only; poll ≥ 30 s). */
export function configBody(f: AutomationForm) {
  return {
    rss_url: f.rss.trim(), destination_channel: f.destination, torrent_backend: 'qbittorrent', qbittorrent_url: (f.qbtUrl || DEFAULT_FORM.qbtUrl).trim(),
    qbittorrent_username: f.qbtUser.trim(), qbittorrent_password: f.qbtPass, qbittorrent_api_key: f.qbtKey, download_path: f.path.trim(),
    poll_interval: Math.max(30, Math.trunc(f.poll) || 120), max_concurrent_torrents: 1, rss_must_contain: (f.must || '1080p').trim(),
    rss_min_size_mb: Math.max(0, Math.trunc(f.minSize) || 0), rss_max_size_mb: Math.max(0, Math.trunc(f.maxSize) || 0), rss_use_regex: f.regex,
    only_1080p: f.only1080 === '1080p', remove_torrent_after_delivery: f.rmTorrent, delete_local_after_delivery: f.rmLocal, oversized_file_policy: f.oversized,
  }
}

/** Why the user cannot leave a step yet ('' = fine). Same rules and wording as the classic wizard. */
export function stepProblem(step: number, f: AutomationForm): string {
  if (step === 0) {
    const rss = f.rss.trim()
    if (!rss) return 'RSS feed URL is required.'
    if (!/^https?:\/\//i.test(rss)) return 'Enter a valid RSS/Feed URL before continuing.'
    if (!f.destination) return 'Select a destination channel before continuing.'
  }
  if (step === 1 && (f.poll < 30 || f.poll > 3600)) return 'Poll interval must be between 30 and 3600 seconds.'
  if (step === 2 && f.minSize && f.maxSize && f.minSize > f.maxSize) return 'Minimum size cannot exceed maximum size.'
  return ''
}
/** All problems for the Review step (it validates the whole form, not one step). */
export function reviewWarnings(f: AutomationForm): string[] {
  const out: string[] = []
  if (f.minSize && f.maxSize && f.minSize > f.maxSize) out.push('Minimum size is greater than maximum size.')
  if (!f.rss.trim()) out.push('RSS feed URL is required.')
  if (!f.destination) out.push('Destination channel is not selected.')
  return out
}
export function startProblem(f: AutomationForm): string {
  if (!f.rss.trim()) return 'Enter an RSS feed URL before starting Content Automation.'
  if (!f.destination) return 'Select a destination Telegram channel before starting Content Automation.'
  return ''
}

export const fmtRate = (v: unknown) => {
  let x = n(v)
  if (!x) return ''
  const u = ['B/s', 'KB/s', 'MB/s', 'GB/s']
  let i = 0
  while (x >= 1024 && i < u.length - 1) { x /= 1024; i++ }
  return `${x.toFixed(i ? 1 : 0)} ${u[i]}`
}
export const fmtEta = (v: unknown) => {
  const x = Number(v)
  if (!Number.isFinite(x) || x < 0) return ''
  const h = Math.floor(x / 3600), m = Math.floor((x % 3600) / 60), sec = Math.floor(x % 60)
  return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`
}

const ACTIVE = new Set(['DOWNLOADING', 'CONVERTING', 'VALIDATING', 'METADATA_ASSETS', 'READY_TO_TRANSFER', 'TRANSFERRING_VIDEO', 'TRANSFERRING_THUMBNAIL', 'VERIFYING'])
export interface QueueItem { key: string; title: string; status: string; label: string; telemetry: string; download: number; upload: number; sending: boolean; processing: boolean; downloading: boolean }
export interface QueueView { summary: string; restartPending: boolean; paused: boolean; downloads: string; active: QueueItem[]; queued: QueueItem[]; queuedMore: number; recent: QueueItem[] }
const pct = (v: unknown) => Math.max(0, Math.min(100, n(v) * 100))

/** Status payload (content_automation) → what the Queue & activity step shows. */
export function queueView(data: Obj, maxConcurrent = 1): QueueView {
  const items = (Array.isArray(data.items) ? data.items : []) as Obj[]
  const counts = Object.entries(o(data.counts)).map(([k, v]) => `${k}: ${n(v).toLocaleString()}`)
  const mk = (x: Obj, i: number): QueueItem => {
    const status = s(x.status)
    const downloading = status === 'DOWNLOADING'
    const tele = [downloading ? fmtRate(x.download_rate) : '', downloading && fmtEta(x.eta_seconds) ? `ETA ${fmtEta(x.eta_seconds)}` : ''].filter(Boolean).join(' · ')
    return { key: s(x.item_key || x.id || x.title || `${status}:${i}`), title: s(x.title || x.item_key), status, label: s(x.task_label || status), telemetry: tele, download: pct(x.download_progress), upload: pct(x.upload_progress), sending: ['TRANSFERRING_VIDEO', 'TRANSFERRING_THUMBNAIL'].includes(status), processing: ['CONVERTING', 'VALIDATING', 'VERIFYING', 'CLEANUP'].includes(status), downloading }
  }
  const active = items.filter((x) => ACTIVE.has(s(x.status))).map(mk)
  const queuedAll = items.filter((x) => s(x.status) === 'QUEUED').sort((a, b) => s(a.created_at).localeCompare(s(b.created_at))).map(mk)
  const recent = items.filter((x) => !ACTIVE.has(s(x.status)) && s(x.status) !== 'QUEUED').slice(0, 6).map(mk)
  const slots = Math.max(1, Math.min(10, maxConcurrent))
  const dl = items.filter((x) => s(x.status) === 'DOWNLOADING').length
  return {
    summary: counts.length ? counts.join(' · ') : 'No releases recorded.', restartPending: Boolean(data.restart_pending), paused: Boolean(o(data.runtime).paused),
    downloads: `Downloads: ${dl}/${slots} · download slots; post-download processing is serialized (Metadata & Assets → Transfer → verify → cleanup)`,
    active, queued: queuedAll.slice(0, 8), queuedMore: Math.max(0, queuedAll.length - 8), recent,
  }
}
/** Header pill: running, paused, or idle. */
export function automationState(data: Obj): string {
  const items = (Array.isArray(data.items) ? data.items : []) as Obj[]
  const rt = o(data.runtime)
  if (rt.paused) return 'Paused'
  return items.some((x) => ACTIVE.has(s(x.status))) || rt.operator_started ? 'Running' : 'Idle'
}
