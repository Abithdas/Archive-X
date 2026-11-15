import { attentionText } from './automatch'
/** Operations page logic, ported from the classic UI (renderOpsSummary, renderOpsHealth, refreshDiagnosticsStrip,
 *  renderOpsResourceDashboard, renderEvts). Pure functions over the status / diagnostics / resources payloads. */
type Obj = Record<string, unknown>
export type Tone = 'ok' | 'warn' | 'bad' | 'neutral'
export interface Tile { label: string; value: string; tone: Tone; hint?: string }

const o = (v: unknown): Obj => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Obj) : {})
const a = (v: unknown): Obj[] => (Array.isArray(v) ? (v as Obj[]) : [])
const n = (v: unknown) => (Number.isFinite(Number(v)) ? Number(v) : 0)
const s = (v: unknown) => String(v ?? '')

export function fmtRate(bytesPerSecond: number): string {
  let v = n(bytesPerSecond)
  if (!v) return '0 B/s'
  const u = ['B/s', 'KB/s', 'MB/s', 'GB/s']
  let i = 0
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`
}
export function fmtAge(seconds: number): string {
  const t = Math.max(0, n(seconds))
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = Math.floor(t % 60)
  return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`
}

export type JobFilter = 'all' | 'running' | 'queued' | 'failed' | 'completed'
export function jobCounts(jobs: Obj[]) {
  const c = { running: 0, queued: 0, failed: 0, completed: 0, withErrors: 0 }
  for (const j of jobs) {
    const k = s(j.status).toUpperCase()
    if (k === 'RUNNING') c.running++
    else if (k === 'QUEUED') c.queued++
    else if (k === 'FAILED') c.failed++
    else if (k === 'COMPLETED') c.completed++
    else if (k === 'COMPLETED_WITH_ERRORS') c.withErrors++
  }
  return c
}
export function filterJobs(jobs: Obj[], f: JobFilter): Obj[] {
  const want: Record<JobFilter, (k: string) => boolean> = {
    all: () => true,
    running: (k) => k === 'RUNNING' || k === 'PAUSED',
    queued: (k) => k === 'QUEUED',
    failed: (k) => k === 'FAILED' || k === 'CANCELLED',
    completed: (k) => k === 'COMPLETED' || k === 'COMPLETED_WITH_ERRORS',
  }
  return jobs.filter((j) => want[f](s(j.status).toUpperCase()))
}

export function healthTiles(st: Obj): Tile[] {
  const jobs = a(st.jobs)
  const running = jobs.filter((j) => j.status === 'RUNNING').length
  const queued = jobs.filter((j) => j.status === 'QUEUED').length
  const tg = Boolean(st.telegram_connected)
  const gov = o(st.governor)
  const auto = o(st.content_automation)
  const rt = o(auto.runtime)
  const orch = o(st.job_orchestration)
  const states = o(orch.states)
  const waiting = n(states.WAITING) + n(states.BLOCKED) + n(states.DELAYED)
  const retrying = n(orch.retrying)
  const flood = Math.max(n(gov.flood_wait_remaining), n(gov.download_flood_wait_remaining))
  return [
    { label: 'Telegram', value: tg ? 'Connected' : 'Offline', tone: tg ? 'ok' : 'bad' },
    { label: 'Workers', value: `${running} active · ${queued} queued · ${waiting} waiting · ${retrying} retrying`, tone: running || queued ? 'ok' : 'neutral' },
    { label: 'Database', value: 'Healthy', tone: st.stats ? 'ok' : 'warn' },
    { label: 'Rate control', value: flood > 0 ? `FloodWait ${Math.ceil(flood)}s` : `${n(gov.max_ranges)} range slots`, tone: flood > 0 ? 'warn' : 'ok' },
    { label: 'Playback', value: tg ? 'Ready' : 'Waiting for Telegram', tone: tg ? 'ok' : 'warn' },
    { label: 'Automation', value: rt.paused ? 'Paused' : auto.running || n(auto.active_count) ? 'Running' : 'Ready', tone: rt.paused ? 'warn' : 'ok' },
  ]
}

export interface Attention { key: string; text: string; tone: 'bad' | 'warn' | 'ok'; action?: 'auto-match' }
export function attention(st: Obj, autoMatches = 0): Attention[] {
  const out: Attention[] = []
  const gov = o(st.governor)
  const rt = o(o(st.content_automation).runtime)
  const failed = n(o(st.stats).failed)
  const flood = Math.max(n(gov.flood_wait_remaining), n(gov.download_flood_wait_remaining))
  if (!st.telegram_connected) out.push({ key: 'telegram-offline', tone: 'bad', text: 'Telegram is offline. Login or reconnect before starting Telegram-heavy jobs.' })
  if (failed > 0) out.push({ key: 'failed-jobs', tone: 'bad', text: `${failed.toLocaleString()} failed job${failed === 1 ? '' : 's'} require review.` })
  if (flood > 0) out.push({ key: 'flood-wait', tone: 'warn', text: s(gov.download_flood_wait_message) || `Telegram FloodWait active for about ${Math.ceil(flood)}s. Download concurrency is reduced.` })
  if (rt.restart_pending) out.push({ key: 'automation-restart', tone: 'warn', text: 'Content Automation has an interrupted task waiting for an operator decision.' })
  if (autoMatches > 0) out.push({ key: 'automatic-matches-review', tone: 'warn', text: attentionText(autoMatches), action: 'auto-match' })
  if (!out.length) out.push({ key: 'all-clear', tone: 'ok', text: 'No immediate operator action required.' })
  return out.slice(0, 5)
}

export function telemetryTiles(st: Obj): { rate: string; tiles: Tile[] } {
  const gov = o(st.governor)
  const flood = Math.max(n(gov.flood_wait_remaining), n(gov.download_flood_wait_remaining))
  return {
    rate: fmtRate(n(gov.bytes_per_second)),
    tiles: [
      { label: 'Active ranges', value: `${n(gov.active_ranges)} / ${n(gov.max_ranges)}`, tone: 'neutral' },
      { label: 'RPC slots', value: `${n(gov.active_rpc)} / ${n(gov.max_rpc)}`, tone: 'neutral' },
      { label: 'FloodWait', value: flood > 0 ? `${Math.ceil(flood)}s` : 'None', tone: flood > 0 ? 'warn' : 'ok' },
      { label: 'Governor', value: gov.last_error ? 'Degraded' : 'Healthy', tone: gov.last_error ? 'warn' : 'ok' },
    ],
  }
}

export function diagnosticTiles(r: Obj): Tile[] {
  const levels = o(r.levels)
  const errors = n(levels.ERROR) + n(levels.CRITICAL)
  const wdSnap = o(r.watchdogs)
  const wdJobs = a(wdSnap.jobs)
  const bad = wdJobs.filter((x) => ['DEGRADED', 'SUSPECTED', 'STALLED', 'FAILED', 'RECOVERING'].includes(s(x.state).toUpperCase())).length
  const hs = s(o(r.diagnostic_health).state || 'UNKNOWN')
  const events = Object.values(o(r.events)).reduce<number>((t, v) => t + n(v), 0)
  const latest = a(r.recent_errors).at(-1)
  const ma = o(r.metadata_assets)
  const tr = o(r.telegram_transfer)
  const ca = o(r.content_automation)
  const line = (m: Obj, label: string) => {
    const act = a(m.active)
    const issues = a(m.issues)
    const j = act[0]
    if (j) return `${s(j.status) || 'RUNNING'}${j.total ? ` · ${n(j.completed)}/${n(j.total)}` : ''}${issues.length ? ` · ${issues.length} issue(s)` : ''}`
    return issues.length ? `${issues.length} issue(s)` : label
  }
  return [
    { label: 'Health', value: hs, tone: hs === 'HEALTHY' ? 'ok' : hs === 'ATTENTION' ? 'warn' : 'bad' },
    { label: 'Events', value: String(events), tone: 'neutral' },
    { label: 'Errors', value: String(errors), tone: errors ? 'warn' : 'ok' },
    { label: 'Incidents', value: String(n(o(r.incident_analysis).active_incident_count)), tone: 'neutral' },
    { label: 'Repeated events', value: String(n(r.duplicate_events)), tone: 'neutral' },
    { label: 'Watchdogs', value: bad ? `${bad} attention` : 'Healthy', tone: bad ? 'warn' : 'ok' },
    { label: 'Metadata & Assets', value: line(ma, 'Idle'), tone: 'neutral' },
    { label: 'Telegram Transfer', value: line(tr, 'Idle'), tone: 'neutral' },
    { label: 'Content Automation', value: line(ca, 'Idle'), tone: 'neutral' },
    { label: 'Last issue', value: latest ? `${s(latest.error_type) || 'Error'} · ${s(latest.message) || '—'}` : 'None', tone: 'neutral' },
    { label: 'HTTP p95', value: `${n(o(o(r.http).latency_ms).p95).toFixed(0)} ms`, tone: 'neutral' },
  ]
}

export interface ResourceCard { title: string; value: string; sub: string; tone: Tone }
export function resourceCards(res: Obj, st: Obj): ResourceCard[] {
  const gov = o(st.governor)
  const orch = o(st.job_orchestration)
  const jobs = a(st.jobs)
  const states = o(orch.states)
  const waiting = n(states.WAITING) + n(states.BLOCKED) + n(states.DELAYED)
  const dur = o(st.resource_leases ?? orch.resource_leases)
  const streams = n(res.playback_streams)
  return [
    { title: 'Playback', value: res.playback_active ? 'ACTIVE' : 'IDLE', tone: res.playback_active ? 'warn' : 'neutral', sub: res.playback_active ? `${streams} stream${streams === 1 ? '' : 's'}` : 'No active stream' },
    { title: 'Background', value: `${n(res.background_active)} / ${n(res.background_capacity)}`, tone: 'neutral', sub: `${n(res.background_waiting)} waiting · metadata ${n(res.metadata_active)}/${n(res.metadata_capacity)} · other ${n(res.non_metadata_active)}/${n(res.non_metadata_capacity)}` },
    { title: 'Telegram Governor', value: s(gov.traffic_mode) || 'NORMAL', tone: 'neutral', sub: `RPC ${n(gov.active_rpc)} / ${n(gov.max_rpc)} · Range ${n(gov.active_ranges)} / ${n(gov.max_ranges)} · Upload X${n(gov.upload_x) || 4}/Y${n(gov.upload_y) || 1} (${n(gov.upload_active_parts)}/${n(gov.upload_max_parts) || 4} parts)` },
    { title: 'Job Manager', value: `${jobs.filter((j) => j.status === 'RUNNING').length} active`, tone: 'neutral', sub: `${jobs.filter((j) => j.status === 'QUEUED').length} queued · ${waiting} waiting · ${n(orch.retrying)} retrying · ${n(orch.active_leases)} executions · ${n(dur.active)} resource leases${n(dur.expired) ? ` · ${n(dur.expired)} expired` : ''}` },
  ]
}

const LANES = [['metadata', 'Metadata'], ['thumbnail', 'Thumbnail'], ['preview', 'Preview'], ['conversion', 'Conversion'], ['upload', 'Upload']] as const
export function laneRows(res: Obj) {
  const active = o(res.category_active)
  const limits = o(o(res.control).effective_limits)
  const policy = o(res.media_policy)
  return LANES.map(([key, label]) => {
    const act = n(active[key])
    const limit = n(limits[key]) || n(policy[`${key}_workers`])
    return { key, label, active: act, limit, pct: limit ? Math.min(100, Math.round((act * 100) / limit)) : 0 }
  })
}
export function policyLine(res: Obj, st: Obj, updated: string): string {
  const c = o(res.control)
  const dur = o(st.resource_leases ?? o(st.job_orchestration).resource_leases)
  let t = `Policy: ${s(c.mode_label) || 'Automatic'} · Durable resource leases: ${n(dur.active)} active`
  if (n(dur.expired)) t += ` · ${n(dur.expired)} expired`
  if (n(c.override_seconds_remaining)) t += ` · expires in ${Math.ceil(n(c.override_seconds_remaining) / 60)} min`
  return `${t} · Updated ${updated}`
}

/** Module a log line belongs to (classic _evtModule). */
export function evtModule(msg: unknown): string {
  const m = s(msg)
  for (const name of ['Telegram Transfer Manager', 'Metadata & Assets Manager', 'Content Automation', 'Backup & Restore']) if (m.startsWith(name)) return name
  return /playback|compatibility|vlc|mpc|player|stream/i.test(m) ? 'playback' : 'other'
}
export type LogLevel = '' | 'INFO' | 'WARN' | 'ERROR' | 'SUCCESS'
export function filterLog(events: unknown[], f: { level: LogLevel; module: string; outcome: string; query: string }, limit = 80): Obj[] {
  const q = f.query.trim().toLowerCase()
  return events
    .filter((e) => {
      if (!e || typeof e !== 'object') return !f.level && !f.module
      const ev = e as Obj
      const lv = s(ev.level || 'INFO').toUpperCase()
      if (f.outcome && s(ev.outcome).toUpperCase() !== f.outcome) return false
      if (f.level === 'INFO' && !(lv === 'INFO' || lv === 'SUCCESS')) return false
      if (f.level && f.level !== 'INFO' && lv !== f.level) return false
      if (f.module && evtModule(ev.message) !== f.module) return false
      if (q && !`${s(ev.message)} ${s(ev.module)} ${s(ev.level)} ${s(ev.event)} ${s(ev.outcome)}`.toLowerCase().includes(q)) return false
      return true
    })
    .slice(-limit) as Obj[]
}
