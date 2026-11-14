const TERMINAL = ['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'CANCELLED'];
export const statusOf = (j) => String(j.status ?? '').trim().toUpperCase();
export const isTerminal = (st) => TERMINAL.includes(st);
export const isInterrupted = (st) => st === 'INTERRUPTED' || st === 'RECOVERING';
export const isPaused = (st) => st === 'PAUSED' || st === 'PAUSING';
/**
 * Which buttons a job shows. A playback session only stops. An active job pauses, resumes (when paused) or recovers (when
 * interrupted), and can always be stopped. Finished jobs show none (the classic page showed them and then answered "already completed").
 */
export function controlsFor(j) {
    const st = statusOf(j);
    if (String(j.type ?? '') === 'PLAYBACK')
        return { kind: 'playback' };
    if (isTerminal(st))
        return null;
    return { kind: 'job', primary: st === 'RECOVERING' ? 'retry-recovery' : st === 'INTERRUPTED' ? 'recover' : isPaused(st) ? 'resume' : 'pause' };
}
export const primaryLabel = (p) => ({ recover: 'Recover & Resume', 'retry-recovery': 'Retry recovery', resume: 'Resume', pause: 'Pause' })[p];
export const primaryPath = (p) => ({ recover: '/recover', 'retry-recovery': '/recover', resume: '/resume', pause: '/pause' })[p];
export const jobPath = (id, tail = '') => `/api/jobs/${encodeURIComponent(id)}${tail}`;
export const idOf = (j) => String(j.id ?? j.job_id ?? '');
export const errorFor = (p) => ({ recover: 'Job could not be recovered', 'retry-recovery': 'Job could not be recovered', resume: 'Could not resume job', pause: 'Could not pause job' })[p];
/** What to tell the user after a successful pause / resume / recover. */
export function doneMessage(p, r) {
    if (p === 'pause')
        return r.status === 'PAUSED' ? 'Job paused.' : 'Pause requested; the current safe operation will finish first.';
    if (p === 'resume')
        return `Job ${String(r.status || 'resuming').toLowerCase()}.`;
    return 'Job recovered and queued from its saved state.';
}
export function stopConfirm(st) {
    const interrupted = isInterrupted(st);
    return {
        title: interrupted ? 'Stop interrupted job' : 'Stop job',
        ok: 'Stop job',
        text: interrupted
            ? 'Stop this interrupted job and discard its pending recovery? Saved checkpoints remain in the journal.'
            : 'Stop this active job? Archive X will cancel it cooperatively first; if it does not stop within the grace period, the job supervisor will escalate cancellation. Verified completed items remain saved.',
    };
}
export const alreadyFinished = (st) => `Job is already ${st.toLowerCase().replaceAll('_', ' ')}.`;
export const STOP_ERROR = 'Could not stop job';
export const stopDone = (r) => (r.status === 'CANCELLED' ? 'Job stopped.' : 'Stop requested; Archive X is finishing cancellation safely.');
export const READ_FAILED = 'Could not read current job state';
export const REFRESH_FAILED = 'Could not refresh job status';
export const playbackConfirm = { title: 'Stop playback', ok: 'Stop playback', text: 'Stop the current playback session? Active range requests will be cancelled and cleanup will finish in the background.' };
export const PLAYBACK_STOPPING = 'Stopping playback…';
export const playbackDone = (r) => (r.stopping ? 'Playback stopped; resource cleanup is finishing.' : 'Playback stopped.');
export const PLAYBACK_UNKNOWN = 'Playback stop was requested, but cleanup status is unavailable.';
