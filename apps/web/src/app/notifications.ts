/** Port of the classic UI's notification rules (25-input-adaptive-runtime.js): which status events and jobs are worth showing,
 *  and how to phrase them without leaking paths, ids, URLs or stack traces. Pure functions so they can be tested. */
export type Tone = 'error' | 'warning' | 'success' | 'match' | 'info'
export interface Notice {
  kind: 'event' | 'job'
  icon: 'alert' | 'check' | 'upload' | 'database' | 'transfer'
  tone: Tone
  title: string
  meta: string
  date: Date
  key: string
  jobId: string
  sourceText: string
}
type Row = Record<string, unknown>
const DAY = 86_400_000

function parseTime(value: unknown): { date: Date; time: string } {
  const n = typeof value === 'number' ? value : Number(value)
  const stamp = typeof value === 'number' || (String(value ?? '').trim() && Number.isFinite(n)) ? (n < 1e12 ? n * 1000 : n) : value ? Date.parse(String(value)) : NaN
  if (!Number.isFinite(stamp)) return { date: new Date(), time: '' }
  const date = new Date(stamp)
  return { date, time: date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }
}

/** First line only; JSON, tracebacks, URLs, file paths, ids and brackets are removed. Empty string means "do not show". */
export function safeText(value: unknown, max = 112): string {
  const original = String(value ?? '').split(/\r?\n/, 1)[0]!.trim()
  if (/^\s*[[{]\s*("|\{|\[)/.test(original)) {
    try {
      JSON.parse(original)
      return ''
    } catch {
      /* not JSON after all */
    }
  }
  if (/^\s*(?:traceback|at\s+\S+\s*\(|[A-Za-z_$][\w.$]*Error:)/i.test(original)) return ''
  const text = original
    .replace(/https?:\/\/\S+/gi, '')
    .replace(/(?:[A-Z]:\\|\/)(?:[^\s"'<>]+[\\/])*[^\s"'<>]*/g, '')
    .replace(/\b(?:id|(?:job|event|task)[-_ ]?id)\s*[:=#]\s*[\w-]+/gi, '')
    .replace(/\b[0-9a-f]{8}-[0-9a-f-]{27,}\b/gi, '')
    .replace(/\b(?:traceback|stack trace|exception stack)\b[\s\S]*/i, '')
    .replace(/\{[^{}]*\}|\[[^[\]]*\]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
  if (/^\s*(?:\{|\[|at\s+\S+|file\s+"|traceback)/i.test(text)) return ''
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text
}

const FAIL = /\b(fail(?:ed|ure)?|error|timeout|timed out|blocked|incomplete)\b/i
const DIAG_EVENT = /^(?:browser\.(?:telemetry\.snapshot|longtask|resource\.load_error)|api\.slow|http\.request\.exception|playback\.(?:engine\.started|first_frame|debug|range\.completed)|api\.(?:invalid_response|network_error|timeout|error)|js\.(?:exception|unhandled_rejection))$/
const ROUTINE_EVENT = /\.(?:started|progress|checkpoint|cache_hit|cache_ready|message_lookup_started|message_lookup_completed|fingerprint_started|fingerprint_completed|fingerprint_failed|item_started|item_checkpoint|item_failed|stage_started|stage_completed)$/
const LOW_SIGNAL = /\b(heartbeat|telemetry|cache hit|cache miss|progress(?: update)?|polling|keepalive|health check|retrying request|websocket ping)\b/i

export function eventToNotice(e: Row, now = Date.now()): Notice | null {
  const level = String(e.level ?? 'INFO').toUpperCase()
  if (level === 'DEBUG' || level === 'TRACE') return null
  const raw = typeof e.message === 'string' ? e.message : String(e.message ?? '')
  const code = String(e.event ?? '').toLowerCase()
  const source = `${raw} ${code.replace(/[._]+/g, ' ')}`.trim()
  if (DIAG_EVENT.test(code) || /^diagnostics\.report\./.test(code)) return null
  if (/resource load failed|main-thread long task|slow api request|playback engine started/i.test(raw)) return null
  if (ROUTINE_EVENT.test(code) || LOW_SIGNAL.test(source)) return null

  const flood = /flood[\s_-]?wait/i.test(source)
  const rangeTimeout = /range request/i.test(source) && /\b(fail(?:ed|ure)?|error|timeout|timed out)\b/i.test(source)
  const sendOk = level === 'SUCCESS' && (code === 'telegram.send.completed' || /\btelegram video sent to\b|\bvideos? sent to telegram channel\b|\bpreviously sent video is now available in\b/i.test(source))
  const libPending = (level === 'WARN' || level === 'ERROR') && /\btelegram send succeeded but (?:library registration is pending|the destination library preview is still pending)\b/i.test(source)
  const failure =
    (level === 'WARN' || level === 'ERROR') &&
    (flood || rangeTimeout || libPending || (/\btelegram send\b|\bsend to telegram\b|\bbulk send\b/i.test(source) && FAIL.test(source)) || (/\b(backup|restore|upload|transfer|download|metadata|scene match|job)\b/i.test(source) && FAIL.test(source)))
  const playbackFail = !rangeTimeout && /\b(playback|stream)\b/i.test(source) && /\b(fail(?:ed|ure)?|error|timeout|timed out)\b/i.test(source)
  const milestone =
    sendOk ||
    (/\b(backup|restore)\b/i.test(source) && /\b(verified|complete(?:d)?|success(?:ful)?|fail(?:ed|ure)?|restored)\b/i.test(source)) ||
    (/\b(upload(?:ed|ing)?|transfer(?:red|ring)?)\b/i.test(source) && /\b(started|uploading|uploaded|complete(?:d)?|finished|fail(?:ed|ure)?|error|timeout|timed out)\b/i.test(source)) ||
    /\bmatched\s+\d+\s+scenes\b|\bscene match(?:ed|es)?\b|\bjob (?:failed|blocked|recovered|retry scheduled)\b/i.test(source) ||
    (/\bmetadata\b/i.test(source) && /\b(saved|matched|updated|linked|complete(?:d)?|fail(?:ed|ure)?|error)\b/i.test(source)) ||
    playbackFail ||
    (/\b(range request|flood[\s_-]?wait)\b/i.test(source) && /\b(fail(?:ed|ure)?|error|timeout|timed out|flood[\s_-]?wait)\b/i.test(source))
  if (!milestone && !failure) return null

  const safe = safeText(raw || e.event || '')
  if (!safe) return null
  const t = parseTime(e.created_at ?? e.ts ?? e.time)
  if (now - t.date.getTime() > 7 * DAY) return null

  const bad = /fail|error/i.test(source)
  let title = safe
  if (sendOk) title = 'Sent to Telegram'
  else if (libPending) title = 'Sent, Library update pending'
  else if (/backup/i.test(source)) title = bad ? 'Backup needs attention' : /verif|complete|success/i.test(source) ? 'Backup verified' : 'Backup update'
  else if (/restore/i.test(source)) title = bad ? 'Restore needs attention' : /complete|success/i.test(source) ? 'Restore completed' : 'Restore update'
  else if (/upload|transfer/i.test(source)) title = /fail|error|timeout|incomplete/i.test(source) ? 'Transfer needs attention' : /download/i.test(source) && /complete|success|finished/i.test(source) ? 'Download complete' : /upload/i.test(source) && /complete|success|finished/i.test(source) ? 'Upload complete' : 'Transfer update'
  else if (/matched \d+ scenes/i.test(source)) title = source.match(/\bmatched\s+(\d+)\s+scenes\b/i)?.[0]?.replace(/^matched/i, 'Matched') ?? 'Scene matches found'
  else if (/scene match/i.test(source)) title = 'Scene matches found'
  else if (/metadata/i.test(source)) title = bad ? 'Metadata needs attention' : 'Metadata results ready'
  else if (flood) {
    const s = source.match(/flood[\s_-]?wait(?:\s|:|_)*(?:of\s*)?(\d+)\s*s?/i)?.[1]
    title = s ? `FloodWait ${s}s` : 'Telegram rate limit'
  } else if (rangeTimeout) title = 'Range request timed out'
  else if (playbackFail) title = 'Playback failed'

  const backupOk = /\bbackup\b/i.test(source) && /\bverified|complete(?:d)?|success(?:ful)?\b/i.test(source)
  const scene = /\bmatched\s+\d+\s+scenes\b|\bscene match(?:ed|es)?\b/i.test(source)
  const tone: Tone = playbackFail || (level === 'ERROR' && !libPending) ? 'error' : flood || rangeTimeout || libPending || level === 'WARN' ? 'warning' : backupOk || sendOk ? 'success' : scene ? 'match' : 'info'
  const icon: Notice['icon'] = tone === 'error' || tone === 'warning' ? 'alert' : sendOk || /backup/i.test(source) ? 'check' : /upload|transfer/i.test(source) ? 'upload' : scene || /metadata/i.test(source) ? 'database' : 'check'
  const media = String(e.playback_id ?? e.media_id ?? e.message_id ?? '').trim().toLowerCase()
  const key = playbackFail ? `playback.failed|${media || Math.floor(t.date.getTime() / 120_000)}` : `${title.toLowerCase()}|${safeText(raw, 80).toLowerCase()}`
  return { kind: 'event', icon, tone, title, meta: t.time, date: t.date, key, jobId: String(e.job_id ?? ''), sourceText: safe.toLowerCase() }
}

const ACTIVE = /^(RUNNING|ACTIVE|PAUSED|RECOVERING|RETRYING|RESUMING|STARTING)$/i
const FAILED = /^(FAILED|ERROR|CANCELLED)$/i

function jobToNotice(j: Row, now = Date.now()): (Notice & { sourceName: string }) | null {
  const status = String(j.status ?? '').toUpperCase()
  if (!ACTIVE.test(status) && !FAILED.test(status)) return null
  const t = parseTime(j.updated_at ?? j.finished_at ?? j.created_at ?? j.started_at ?? j.ts)
  if (FAILED.test(status) && now - t.date.getTime() > 7 * DAY) return null
  const name = safeText(j.name ?? j.type ?? j.operation ?? 'Library task', 48) || 'Library task'
  if (/playback engine|player engine|stream engine/i.test(name)) return null
  const p = Number(j.progress)
  const pct = Number.isFinite(p) && p > 0 ? `${Math.round(p <= 1 ? p * 100 : p)}%` : ''
  const label = status === 'FAILED' || status === 'ERROR' ? 'Failed' : status === 'CANCELLED' ? 'Stopped' : status.charAt(0) + status.slice(1).toLowerCase()
  const id = String(j.id ?? j.job_id ?? '')
  return { kind: 'job', icon: FAILED.test(status) ? 'alert' : 'transfer', tone: FAILED.test(status) ? 'error' : 'info', title: name, sourceName: name, jobId: id, meta: [label, pct, t.time].filter(Boolean).join(' · '), date: t.date, key: `job|${(id || name).toLowerCase()}|${status}`, sourceText: '' }
}

/** Newest first, at most eight: recent milestones and failures plus up to four active and three failed jobs. */
export function buildNotifications(events: Row[], jobs: Row[], now = Date.now()): Notice[] {
  const byKey = new Map<string, Notice>()
  for (const e of events.slice(-100)) {
    const n = eventToNotice(e, now)
    const prev = n && byKey.get(n.key)
    if (n && (!prev || n.date > prev.date)) byKey.set(n.key, n)
  }
  const cands = jobs.map((j) => jobToNotice(j, now)).filter((x): x is NonNullable<typeof x> => x !== null).sort((a, b) => b.date.getTime() - a.date.getTime())
  const events2 = [...byKey.values()].filter((r) => !cands.some((j) => (r.jobId && j.jobId && r.jobId === j.jobId) || (j.sourceName.length >= 5 && r.sourceText.includes(j.sourceName.toLowerCase()))))
  const seen = new Set(events2.map((r) => r.key))
  const picked = [...cands.filter((r) => !/(?:failed|stopped)/i.test(r.meta)).slice(0, 4), ...cands.filter((r) => /(?:failed|stopped)/i.test(r.meta)).slice(0, 3)]
  for (const r of picked) if (!seen.has(r.key)) { seen.add(r.key); events2.push(r) }
  return events2.sort((a, b) => b.date.getTime() - a.date.getTime()).slice(0, 8)
}

export function groupByDay(rows: Notice[], now = new Date()): Array<[string, Notice[]]> {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  const groups: Array<[string, Notice[]]> = [['Today', []], ['Yesterday', []], ['Earlier', []]]
  for (const r of rows) {
    const day = new Date(r.date.getFullYear(), r.date.getMonth(), r.date.getDate())
    groups[day >= today ? 0 : day >= yesterday ? 1 : 2]![1].push(r)
  }
  return groups.filter((g) => g[1].length)
}
