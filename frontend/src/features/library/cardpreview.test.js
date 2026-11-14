import { describe, expect, it } from 'vitest';
import { FRAME_COUNT, frameSeconds, frameSrc, indexFromPointer, nextIndex, previewReady, ratioFromPointer, shouldCommit, watchedPercent } from './cardpreview';
const rect = { left: 100, width: 360 };
describe('card hover preview', () => {
    it('maps the pointer across the picture to one of the 36 frames', () => {
        expect(indexFromPointer(100, rect)).toBe(0);
        expect(indexFromPointer(279, rect)).toBe(17);
        expect(indexFromPointer(280, rect)).toBe(18);
        expect(indexFromPointer(460, rect)).toBe(35);
        expect(indexFromPointer(9999, rect)).toBe(35);
        expect(indexFromPointer(-50, rect)).toBe(0);
        expect(ratioFromPointer(280, rect)).toBeCloseTo(0.5);
        expect(ratioFromPointer(280, { left: 0, width: 0 })).toBe(0);
    });
    it('plays on and wraps after the last frame', () => {
        expect(nextIndex(0)).toBe(1);
        expect(nextIndex(FRAME_COUNT - 1)).toBe(0);
    });
    it('ignores tiny mouse movements but follows real ones', () => {
        expect(shouldCommit(null, 200, 5, 9)).toBe(true);
        expect(shouldCommit(200, 204, 5, 9)).toBe(false);
        expect(shouldCommit(200, 215, 5, 9)).toBe(true);
        expect(shouldCommit(200, 400, 9, 9)).toBe(false);
    });
    it('labels a frame with its time, falling back to an even spread, never past the end', () => {
        expect(frameSeconds([0, 10, 20], 1, 100)).toBe(10);
        expect(frameSeconds(undefined, 35, 350)).toBe(350);
        expect(frameSeconds(undefined, 7, 350)).toBeCloseTo(70);
        expect(frameSeconds([0, 999], 1, 100)).toBe(100);
        expect(frameSeconds([Number.NaN], 0, 0)).toBe(0);
    });
    it('only previews when every frame is cached', () => {
        expect(previewReady({ frames_ready: true, frame_count: 36 })).toBe(true);
        expect(previewReady({ frames_ready: true, frame_count: 20 })).toBe(false);
        expect(previewReady({ frames_ready: false, frame_count: 36 })).toBe(false);
        expect(previewReady(undefined)).toBe(false);
    });
    it('addresses a cached frame, with its revision', () => {
        expect(frameSrc('-1001', 7, 3)).toBe('/api/play/preview-frame/-1001/7/3');
        expect(frameSrc('-1001', 7, 99, 'r 1')).toBe('/api/play/preview-frame/-1001/7/35?v=r%201');
    });
    it('turns a saved position into a percentage', () => {
        expect(watchedPercent({ position: 30, duration: 120 })).toBe(25);
        expect(watchedPercent({ completed: true })).toBe(100);
        expect(watchedPercent({ position: 30 }, 60)).toBe(50);
        expect(watchedPercent({ position: 500, duration: 100 })).toBe(100);
        expect(watchedPercent(null)).toBe(0);
        expect(watchedPercent({ position: 0, duration: 100 })).toBe(0);
    });
});
