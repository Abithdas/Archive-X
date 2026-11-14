import { describe, expect, it } from 'vitest';
import { DEFAULT_LIMITS, clampLimit, isPaused, limitsFrom, manualBody, manualSaved, modeBody, modeChanged, modeLabel, modeOf, pauseBody, pauseDone, pauseLabel, playbackGate } from './resourcecontrol';
describe('current policy', () => {
    it('reads the mode, defaulting to automatic for anything unknown', () => {
        expect(modeOf({ control: { mode: 'Balanced' } })).toBe('balanced');
        expect(modeOf({ control: { mode: 'weird' } })).toBe('automatic');
        expect(modeOf({})).toBe('automatic');
    });
    it('labels it, preferring the server label', () => {
        expect(modeLabel({ control: { mode: 'playback', mode_label: 'Playback Priority (auto)' } })).toBe('Playback Priority (auto)');
        expect(modeLabel({ control: { mode: 'playback' } })).toBe('Playback Priority');
        expect(modeLabel({})).toBe('Automatic');
    });
    it('knows whether the background controller is paused and whether playback gates work', () => {
        expect(isPaused({ background_paused: true })).toBe(true);
        expect(isPaused({ control: { background_paused: true } })).toBe(true);
        expect(isPaused({})).toBe(false);
        expect(playbackGate({ control: { playback_gate: true } })).toBe(true);
        expect(playbackGate({})).toBe(false);
    });
});
describe('limits', () => {
    it('keeps whole numbers from 1 to 8', () => {
        expect(clampLimit(0)).toBe(1);
        expect(clampLimit('')).toBe(1);
        expect(clampLimit('abc')).toBe(1);
        expect(clampLimit(-3)).toBe(1);
        expect(clampLimit(99)).toBe(8);
        expect(clampLimit(4)).toBe(4);
        expect(clampLimit(2.6)).toBe(3);
        expect(clampLimit('5')).toBe(5);
    });
    it('uses the saved manual limits, and the defaults for any that are missing', () => {
        expect(limitsFrom({})).toEqual(DEFAULT_LIMITS);
        expect(limitsFrom({ control: { manual_limits: { metadata: 6, upload: 99 } } })).toEqual({ ...DEFAULT_LIMITS, metadata: 6, upload: 8 });
    });
});
describe('requests', () => {
    it('a normal policy change sends the mode with its operator label', () => {
        expect(modeBody('playback')).toEqual({ mode: 'playback', expires_seconds: 0, label: 'Operator selected Playback Priority' });
        expect(modeBody('background', 900).expires_seconds).toBe(900);
    });
    it('manual mode sends clamped limits, the playback gate and the expiry', () => {
        expect(manualBody({ metadata: 0, thumbnail: 9, preview: '3', conversion: 'x', upload: 2 }, true, 1800)).toEqual({ mode: 'manual', limits: { metadata: 1, thumbnail: 8, preview: 3, conversion: 1, upload: 2 }, playback_gate: true, expires_seconds: 1800, label: 'Manual resource limits' });
    });
    it('pausing toggles the current state', () => {
        expect(pauseBody(false)).toEqual({ background_paused: true, label: 'Background controller paused by operator' });
        expect(pauseBody(true)).toEqual({ background_paused: false, label: 'Background controller resumed' });
        expect(pauseLabel(false)).toBe('Pause background controller');
        expect(pauseLabel(true)).toBe('Resume background controller');
    });
});
describe('messages', () => {
    it('words the results like the classic page', () => {
        expect(modeChanged({ resources: { control: { mode_label: 'Balanced' } } }, 'balanced')).toBe('Resource policy: Balanced');
        expect(modeChanged({}, 'balanced')).toBe('Resource policy: balanced');
        expect(manualSaved(0)).toBe('Manual limits saved and active until changed.');
        expect(manualSaved(1800)).toBe('Manual limits saved and active for 30 minutes.');
        expect(pauseDone(false)).toBe('Background controller paused. Running work will stop at safe checkpoints.');
        expect(pauseDone(true)).toBe('Background controller resumed.');
    });
});
