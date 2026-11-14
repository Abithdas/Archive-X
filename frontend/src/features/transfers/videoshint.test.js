import { describe, expect, it } from 'vitest';
import { videosHint } from './parts';
describe('channel video count hint', () => {
    it('counts videos', () => { expect(videosHint(12)).toBe('12 videos'); expect(videosHint(1)).toBe('1 video'); expect(videosHint(1234)).toBe('1,234 videos'); });
    it('never says undefined for a channel that has not been scanned', () => {
        for (const v of [undefined, null, '', 'x', Number.NaN, -3, 0])
            expect(videosHint(v)).toBe('0 videos');
        expect(videosHint('7')).toBe('7 videos');
    });
});
