import { describe, expect, it } from 'vitest'
import { ageText, alreadyFinished, controlsFor, doneMessage, errorFor, idOf, isTerminal, jobPath, liveness, playbackDone, primaryLabel, primaryPath, statusOf, stopConfirm, stopDone } from './jobs'

describe('which controls a job shows', () => {
  it('pauses a running or queued job', () => {
    expect(controlsFor({ status: 'RUNNING' })).toEqual({ kind: 'job', primary: 'pause' })
    expect(controlsFor({ status: 'queued' })).toEqual({ kind: 'job', primary: 'pause' })
  })
  it('resumes a paused or pausing job', () => {
    expect(controlsFor({ status: 'PAUSED' })).toEqual({ kind: 'job', primary: 'resume' })
    expect(controlsFor({ status: 'PAUSING' })).toEqual({ kind: 'job', primary: 'resume' })
  })
  it('offers recovery for interrupted jobs, and a retry when recovery is already running', () => {
    expect(controlsFor({ status: 'INTERRUPTED' })).toEqual({ kind: 'job', primary: 'recover' })
    expect(controlsFor({ status: 'RECOVERING' })).toEqual({ kind: 'job', primary: 'retry-recovery' })
  })
  it('shows nothing for a finished job', () => {
    for (const st of ['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'CANCELLED']) expect(controlsFor({ status: st })).toBeNull()
  })
  it('a playback session only stops, even when it looks finished', () => {
    expect(controlsFor({ type: 'PLAYBACK', status: 'RUNNING' })).toEqual({ kind: 'playback' })
  })
  it('reads the status tolerantly', () => {
    expect(statusOf({ status: ' running ' })).toBe('RUNNING')
    expect(statusOf({})).toBe('')
    expect(isTerminal('FAILED')).toBe(true)
    expect(isTerminal('RUNNING')).toBe(false)
  })
})

describe('wording and requests', () => {
  it('labels and routes each primary action', () => {
    expect(primaryLabel('recover')).toBe('Recover & Resume')
    expect(primaryLabel('retry-recovery')).toBe('Retry recovery')
    expect(primaryPath('pause')).toBe('/pause')
    expect(primaryPath('resume')).toBe('/resume')
    expect(primaryPath('recover')).toBe('/recover')
    expect(primaryPath('retry-recovery')).toBe('/recover')
    expect(jobPath('a/b', '/pause')).toBe('/api/jobs/a%2Fb/pause')
    expect(idOf({ id: 7 })).toBe('7')
    expect(idOf({ job_id: 'x' })).toBe('x')
  })
  it('says what happened after each action', () => {
    expect(doneMessage('pause', { status: 'PAUSED' })).toBe('Job paused.')
    expect(doneMessage('pause', { status: 'PAUSING' })).toBe('Pause requested; the current safe operation will finish first.')
    expect(doneMessage('resume', { status: 'RUNNING' })).toBe('Job running.')
    expect(doneMessage('resume', {})).toBe('Job resuming.')
    expect(doneMessage('recover', {})).toBe('Job recovered and queued from its saved state.')
    expect(errorFor('pause')).toBe('Could not pause job')
    expect(errorFor('recover')).toBe('Job could not be recovered')
  })
  it('asks before stopping, with different words for an interrupted job', () => {
    expect(stopConfirm('RUNNING')).toMatchObject({ title: 'Stop job', ok: 'Stop job' })
    expect(stopConfirm('RUNNING').text).toContain('Verified completed items remain saved.')
    expect(stopConfirm('INTERRUPTED')).toMatchObject({ title: 'Stop interrupted job' })
    expect(stopConfirm('RECOVERING').text).toContain('discard its pending recovery')
  })
  it('explains a job that has already finished, and the stop outcome', () => {
    expect(alreadyFinished('COMPLETED_WITH_ERRORS')).toBe('Job is already completed with errors.')
    expect(alreadyFinished('FAILED')).toBe('Job is already failed.')
    expect(stopDone({ status: 'CANCELLED' })).toBe('Job stopped.')
    expect(stopDone({ status: 'CANCELLING' })).toBe('Stop requested; Archive X is finishing cancellation safely.')
    expect(playbackDone({ stopping: true })).toBe('Playback stopped; resource cleanup is finishing.')
    expect(playbackDone({})).toBe('Playback stopped.')
  })
})

describe('liveness: working, waiting or stuck', () => {
  const running = (o: Record<string, unknown> = {}) => ({ id: 'a', type: 'METADATA_ASSETS', status: 'RUNNING', ...o })
  it('writes ages in seconds, minutes and hours', () => {
    expect(ageText(5)).toBe('5s'); expect(ageText(125)).toBe('2m 5s'); expect(ageText(3 * 3600 + 120)).toBe('3h 2m')
  })
  it('says working, with the last sign of life, for a job that is active', () => {
    expect(liveness(running({ watchdog_activity_age: 3, watchdog_progress_age: 3 }))).toEqual({ text: 'Working. Last activity 3s ago.', tone: 'ok' })
    expect(liveness(running())).toEqual({ text: 'Running', tone: 'ok' })
  })
  it('mentions missing counted progress when it is active but the counter has not moved', () => {
    const l = liveness(running({ watchdog_activity_age: 4, watchdog_progress_age: 130 }))!
    expect(l.text).toContain('No counted progress for 2m 10s'); expect(l.tone).toBe('neutral')
  })
  it('calls a long silence quiet, then possibly stuck, and says what to do', () => {
    expect(liveness(running({ watchdog_activity_age: 50 }))!.tone).toBe('warn')
    const l = liveness(running({ watchdog_activity_age: 200 }))!
    expect(l.tone).toBe('bad'); expect(l.text).toContain('may be stuck')
  })
  it('explains waiting, pausing and interrupted jobs', () => {
    expect(liveness({ status: 'QUEUED' })!.text).toBe('Waiting in the queue for its turn')
    expect(liveness({ status: 'QUEUED', admission_state: 'WAITING', admission_reason: 'lane_busy' })!.text).toBe('Waiting to start: lane busy')
    expect(liveness({ status: 'PAUSED' })!.text).toContain('Paused')
    expect(liveness({ status: 'INTERRUPTED' })!.text).toContain('Recover & Resume')
    expect(liveness(running({ rate_limit_message: 'Telegram asked to wait 30 s' }))!.text).toBe('Waiting: Telegram asked to wait 30 s')
  })
  it('says nothing for finished jobs and playback sessions', () => {
    expect(liveness({ status: 'COMPLETED' })).toBeNull()
    expect(liveness({ status: 'PLAYING', type: 'PLAYBACK' })).toBeNull()
  })
})
