import { describe, expect, it } from 'vitest';
import { alreadyFinished, controlsFor, doneMessage, errorFor, idOf, isTerminal, jobPath, playbackDone, primaryLabel, primaryPath, statusOf, stopConfirm, stopDone } from './jobs';
describe('which controls a job shows', () => {
    it('pauses a running or queued job', () => {
        expect(controlsFor({ status: 'RUNNING' })).toEqual({ kind: 'job', primary: 'pause' });
        expect(controlsFor({ status: 'queued' })).toEqual({ kind: 'job', primary: 'pause' });
    });
    it('resumes a paused or pausing job', () => {
        expect(controlsFor({ status: 'PAUSED' })).toEqual({ kind: 'job', primary: 'resume' });
        expect(controlsFor({ status: 'PAUSING' })).toEqual({ kind: 'job', primary: 'resume' });
    });
    it('offers recovery for interrupted jobs, and a retry when recovery is already running', () => {
        expect(controlsFor({ status: 'INTERRUPTED' })).toEqual({ kind: 'job', primary: 'recover' });
        expect(controlsFor({ status: 'RECOVERING' })).toEqual({ kind: 'job', primary: 'retry-recovery' });
    });
    it('shows nothing for a finished job', () => {
        for (const st of ['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'CANCELLED'])
            expect(controlsFor({ status: st })).toBeNull();
    });
    it('a playback session only stops, even when it looks finished', () => {
        expect(controlsFor({ type: 'PLAYBACK', status: 'RUNNING' })).toEqual({ kind: 'playback' });
    });
    it('reads the status tolerantly', () => {
        expect(statusOf({ status: ' running ' })).toBe('RUNNING');
        expect(statusOf({})).toBe('');
        expect(isTerminal('FAILED')).toBe(true);
        expect(isTerminal('RUNNING')).toBe(false);
    });
});
describe('wording and requests', () => {
    it('labels and routes each primary action', () => {
        expect(primaryLabel('recover')).toBe('Recover & Resume');
        expect(primaryLabel('retry-recovery')).toBe('Retry recovery');
        expect(primaryPath('pause')).toBe('/pause');
        expect(primaryPath('resume')).toBe('/resume');
        expect(primaryPath('recover')).toBe('/recover');
        expect(primaryPath('retry-recovery')).toBe('/recover');
        expect(jobPath('a/b', '/pause')).toBe('/api/jobs/a%2Fb/pause');
        expect(idOf({ id: 7 })).toBe('7');
        expect(idOf({ job_id: 'x' })).toBe('x');
    });
    it('says what happened after each action', () => {
        expect(doneMessage('pause', { status: 'PAUSED' })).toBe('Job paused.');
        expect(doneMessage('pause', { status: 'PAUSING' })).toBe('Pause requested; the current safe operation will finish first.');
        expect(doneMessage('resume', { status: 'RUNNING' })).toBe('Job running.');
        expect(doneMessage('resume', {})).toBe('Job resuming.');
        expect(doneMessage('recover', {})).toBe('Job recovered and queued from its saved state.');
        expect(errorFor('pause')).toBe('Could not pause job');
        expect(errorFor('recover')).toBe('Job could not be recovered');
    });
    it('asks before stopping, with different words for an interrupted job', () => {
        expect(stopConfirm('RUNNING')).toMatchObject({ title: 'Stop job', ok: 'Stop job' });
        expect(stopConfirm('RUNNING').text).toContain('Verified completed items remain saved.');
        expect(stopConfirm('INTERRUPTED')).toMatchObject({ title: 'Stop interrupted job' });
        expect(stopConfirm('RECOVERING').text).toContain('discard its pending recovery');
    });
    it('explains a job that has already finished, and the stop outcome', () => {
        expect(alreadyFinished('COMPLETED_WITH_ERRORS')).toBe('Job is already completed with errors.');
        expect(alreadyFinished('FAILED')).toBe('Job is already failed.');
        expect(stopDone({ status: 'CANCELLED' })).toBe('Job stopped.');
        expect(stopDone({ status: 'CANCELLING' })).toBe('Stop requested; Archive X is finishing cancellation safely.');
        expect(playbackDone({ stopping: true })).toBe('Playback stopped; resource cleanup is finishing.');
        expect(playbackDone({})).toBe('Playback stopped.');
    });
});
