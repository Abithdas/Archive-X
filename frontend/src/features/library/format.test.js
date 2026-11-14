import { describe, expect, it } from 'vitest';
import { formatBytes, formatDuration } from './format';
describe('formatDuration', () => {
    it.each([
        [0, ''], [null, ''], [95, '1:35'], [600, '10:00'], [3725, '1:02:05'], [7300, '2:01:40'],
    ])('%s -> %j', (input, out) => expect(formatDuration(input)).toBe(out));
});
describe('formatBytes', () => {
    it('formats', () => {
        expect(formatBytes(0)).toBe('');
        expect(formatBytes(512)).toBe('512 B');
        expect(formatBytes(2944112455)).toBe('2.7 GB');
    });
});
