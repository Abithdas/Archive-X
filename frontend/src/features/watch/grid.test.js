import { describe, expect, it } from 'vitest';
import { FRAME_COUNT, gridReady, gridTimes, layoutFor, showsLabel, spriteTile, tileLabel } from './grid';
describe('snapshot grid readiness and times', () => {
    it('needs a validated complete set of 36 frames', () => {
        expect(gridReady({ frames_ready: true, frame_count: 36 })).toBe(true);
        expect(gridReady({ frames_ready: true, frame_count: 40 })).toBe(true);
        expect(gridReady({ frames_ready: true, frame_count: 35 })).toBe(false);
        expect(gridReady({ frames_ready: false, frame_count: 36 })).toBe(false);
        expect(gridReady(undefined)).toBe(false);
    });
    it('uses the server times when all 36 are valid', () => {
        const times = Array.from({ length: 36 }, (_, i) => i * 10);
        expect(gridTimes({ frame_times: times }, 3600)).toEqual(times);
    });
    it('spreads 36 points across the video when the times are missing, short or invalid', () => {
        const t = gridTimes({ frame_times: [1, 2, 3] }, 3600);
        expect(t).toHaveLength(FRAME_COUNT);
        expect(t[0]).toBeCloseTo(72); // 2%
        expect(t[35]).toBeCloseTo(3528); // 98%
        expect(gridTimes({ frame_times: Array.from({ length: 36 }, (_, i) => (i === 5 ? -1 : i)) }, 100)[5]).toBeCloseTo(100 * (0.02 + 0.96 * 5 / 35));
        expect(gridTimes(undefined, 0)[3]).toBe(3); // no duration: just the index
    });
});
describe('layout', () => {
    it('always returns a factor pair of 36', () => {
        for (const [w, h] of [[1280, 720], [390, 220], [320, 600], [1920, 400], [50, 50]]) {
            const l = layoutFor(w, h, 16 / 9);
            expect(l.rows * l.cols).toBe(36);
        }
    });
    it('prefers a 6×6 grid for a 16:9 player', () => {
        expect(layoutFor(960, 540, 16 / 9)).toEqual({ rows: 6, cols: 6 });
    });
    it('uses fewer, wider rows for a very wide player and more rows for a tall one', () => {
        const wide = layoutFor(1800, 300, 16 / 9), tall = layoutFor(300, 900, 16 / 9);
        expect(wide.cols).toBeGreaterThan(wide.rows);
        expect(tall.rows).toBeGreaterThan(tall.cols);
    });
    it('tolerates nonsense input', () => {
        expect(layoutFor(0, 0, 0).rows * layoutFor(0, 0, 0).cols).toBe(36);
        expect(layoutFor(Number.NaN, 100, Number.NaN).rows * 1).toBeGreaterThan(0);
    });
});
describe('sprite tile cropping', () => {
    // A 1920×1080 sprite is 6×6 cells of 320×180.
    it('puts frame 0 at the origin when the tile has the cell\'s shape', () => {
        const s = spriteTile(0, 160, 90, 1920, 1080);
        expect(s.backgroundSize).toBe('960px 540px');
        expect(s.backgroundPosition).toBe('0px 0px');
    });
    it('offsets by column and row', () => {
        const s = spriteTile(7, 160, 90, 1920, 1080); // col 1, row 1
        expect(s.backgroundPosition).toBe('-160px -90px');
    });
    it('crops the sides evenly when the tile is narrower than the frame (cover, centred)', () => {
        const s = spriteTile(0, 90, 90, 1920, 1080); // scale = 0.5; displayed cell 160×90 into a 90×90 tile
        expect(s.backgroundSize).toBe('960px 540px');
        expect(s.backgroundPosition).toBe('-35px 0px'); // (160-90)/2
    });
    it('crops top and bottom when the tile is wider than the frame', () => {
        const s = spriteTile(0, 160, 45, 1920, 1080); // scale = 0.5 → cell 160×90 into 160×45
        expect(s.backgroundPosition).toBe('0px -22.5px');
    });
    it('degrades to harmless values for a zero-sized sprite or tile', () => {
        expect(spriteTile(0, 0, 0, 0, 0)).toEqual({ backgroundSize: 'auto', backgroundPosition: '0 0' });
    });
});
describe('tile labels', () => {
    it('labels the time as the tile\'s accessible name', () => {
        expect(tileLabel(0)).toBe('Play from 0:00');
        expect(tileLabel(125)).toBe('Play from 2:05');
        expect(tileLabel(3725)).toBe('Play from 1:02:05');
    });
    it('hides the visible label on tiles that are too small', () => {
        expect(showsLabel(160, 90, '12:34')).toBe(true);
        expect(showsLabel(40, 90, '12:34')).toBe(false);
        expect(showsLabel(160, 20, '12:34')).toBe(false);
    });
});
