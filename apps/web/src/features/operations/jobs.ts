/** Per-job controls on the Operations page. Ported from the classic job cards. */
type Obj = Record<string, unknown>

const TERMINAL = ['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'CANCELLED']
export const statusOf = (j: Obj) => String(j.status ?? '').trim().toUpperCase()
export const isTerminal = (st: string) => TERMINAL.includes(st)
const isInterrupted = (st: string) => st === 'INTERRUPTED' || st === 'RECOVERING'
const isPaused = (st: string) => st === 'PAUSED' || st === 'PAUSING'

export type Primary = 'recover' | 'retry-recovery' | 'resume' | 'pause'
export type Controls = { kind: 'playback' } | { kind: 'job'; primary: Primary }
/**
 * Which buttons a job shows. A playback session only stops. An active job pauses, resumes (when paused) or recovers (when
 * interrupted), and can always be stopped. Finished jobs show none (the classic page showed them and then answered "already completed").
 */
export function controlsFor(j: Obj): Controls | null {
  const st = statusOf(j)
  if (String(j.type ?? '') === 'PLAYBACK') return { kind: 'playback' }
  if (isTerminal(st)) return null
  return { kind: 'job', primary: st === 'RECOVERING' ? 'retry-recovery' : st === 'INTERRUPTED' ? 'recover' : isPaused(st) ? 'resume' : 'pause' }
}
export const primaryLabel = (p: Primary) => ({ recover: 'Recover & Resume', 'retry-recovery': 'Retry recovery', resume: 'Resume', pause: 'Pause' })[p]
export const primaryPath = (p: Primary) => ({ recover: '/recover', 'retry-recovery': '/recover', resume: '/resume', pause: '/pause' })[p]
export const jobPath = (id: string, tail = '') => `/api/jobs/${encodeURIComponent(id)}${tail}`

export const idOf = (j: Obj) => String(j.id ?? j.job_id ?? '')

export const errorFor = (p: Primary) => ({ recover: 'Job could not be recovered', 'retry-recovery': 'Job could not be recovered', resume: 'Could not resume job', pause: 'Could not pause job' })[p]
/** What to tell the user after a successful pause / resume / recover. */
export function doneMessage(p: Primary, r: { status?: string }): string {
  if (p === 'pause') return r.status === 'PAUSED' ? 'Job paused.' : 'Pause requested; the current safe operation will finish first.'
  if (p === 'resume') return `Job ${String(r.status || 'resuming').toLowerCase()}.`
  return 'Job recovered and queued from its saved state.'
}

export function stopConfirm(st: string) {
  const interrupted = isInterrupted(st)
  return {
    title: interrupted ? 'Stop interrupted job' : 'Stop job',
    ok: 'Stop job',
    text: interrupted
      ? 'Stop this interrupted job and discard its pending recovery? Saved checkpoints remain in the journal.'
      : 'Stop this active job? Archive X will cancel it cooperatively first; if it does not stop within the grace period, the job supervisor will escalate cancellation. Verified completed items remain saved.',
  }
}
export const alreadyFinished = (st: string) => `Job is already ${st.toLowerCase().replaceAll('_', ' ')}.`
export const STOP_ERROR = 'Could not stop job'
export const stopDone = (r: { status?: string }) => (r.status === 'CANCELLED' ? 'Job stopped.' : 'Stop requested; Archive X is finishing cancellation safely.')
export const READ_FAILED = 'Could not read current job state'
export const REFRESH_FAILED = 'Could not refresh job status'

export const playbackConfirm = { title: 'Stop playback', ok: 'Stop playback', text: 'Stop the current playback session? Active range requests will be cancelled and cleanup will finish in the background.' }
export const PLAYBACK_STOPPING = 'Stopping playback…'
export const playbackDone = (r: { stopping?: boolean }) => (r.stopping ? 'Playback stopped; resource cleanup is finishing.' : 'Playback stopped.')
export const PLAYBACK_UNKNOWN = 'Playback stop was requested, but cleanup status is unavailable.'

// ---------------------------------------------------------------- is it working or stuck? ----------------------------------------------------------
/** "5s", "2m 5s", "1h 3m". */
export function ageText(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}
const age = (v: unknown): number | null => (v === null || v === undefined || v === '' || !Number.isFinite(Number(v)) ? null : Number(v))
/** Seconds without any sign of life at which a running job is called possibly stuck, and at which it is called quiet. */
export const STUCK_AFTER = 120
export const QUIET_AFTER = 45
/** Seconds with no counted progress (but still active) at which that is mentioned. */
export const NO_PROGRESS_AFTER = 90

export type Liveness = { text: string; tone: 'ok' | 'warn' | 'bad' | 'neutral' }
/**
 * One plain sentence that says what an unfinished job is doing right now: working (and how recently it did something), busy without counted
 * progress, waiting (why), paused, interrupted, or quiet for so long that it may be stuck. null for a finished job and for a playback session.
 */
export function liveness(j: Obj): Liveness | null {
  const st = statusOf(j)
  if (isTerminal(st) || String(j.type ?? '') === 'PLAYBACK') return null
  if (isPaused(st)) return { text: st === 'PAUSING' ? 'Pausing after the current step' : 'Paused. Press Resume to continue.', tone: 'warn' }
  if (isInterrupted(st)) return { text: st === 'RECOVERING' ? 'Recovering from its saved state' : 'Interrupted. Press Recover & Resume to continue from where it stopped.', tone: 'warn' }
  if (st === 'CANCEL_REQUESTED') return { text: 'Stopping. Waiting for the current step to end.', tone: 'warn' }
  const limit = String(j.rate_limit_message ?? '').trim()
  if (limit) return { text: `Waiting: ${limit}`, tone: 'warn' }
  if (st === 'QUEUED') {
    const why = String(j.admission_reason ?? '').trim().replaceAll('_', ' ')
    return { text: why && String(j.admission_state ?? 'READY') !== 'READY' ? `Waiting to start: ${why}` : 'Waiting in the queue for its turn', tone: 'neutral' }
  }
  const activity = age(j.watchdog_activity_age) ?? age(j.watchdog_heartbeat_age)
  const progress = age(j.watchdog_progress_age)
  if (activity === null) return { text: 'Running', tone: 'ok' }
  if (activity >= STUCK_AFTER) return { text: `No activity for ${ageText(activity)}. It may be stuck: Stop it and start it again if this goes on.`, tone: 'bad' }
  if (activity >= QUIET_AFTER) return { text: `Quiet for ${ageText(activity)}. Probably waiting on Telegram, a provider or the disk.`, tone: 'warn' }
  if (progress !== null && progress >= NO_PROGRESS_AFTER) return { text: `Working. No counted progress for ${ageText(progress)}, last activity ${ageText(activity)} ago.`, tone: 'neutral' }
  return { text: `Working. Last activity ${ageText(activity)} ago.`, tone: 'ok' }
}
