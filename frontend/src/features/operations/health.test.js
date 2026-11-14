import { describe, expect, it } from 'vitest';
import { chipText, counts, findingCount, lastChecked, overviewText, repairToast, reportLines, stateOf } from './health';
const report = { checked_at: '2026-01-01T00:00:00Z', summary: { active_media: 72, missing_snapshot_cache: 72, artwork_cache_missing_bytes: { a: 2, b: 3 }, stale_jobs: 1, finding_count: 2 }, findings: [{ code: 'SNAP', severity: 'warning', count: 72, message: 'Missing snapshots' }, { code: 'X', count: 1, message: 'Other' }] };
describe('library health', () => {
    it('judges and words the result', () => {
        expect(findingCount(report)).toBe(2);
        expect(stateOf(report)).toBe('review');
        expect(stateOf({ summary: { finding_count: 0 } })).toBe('healthy');
        expect(stateOf({ findings: [] })).toBe('healthy');
        expect(chipText('review', report)).toBe('2 findings');
        expect(chipText('review', { findings: [{}] })).toBe('1 finding');
        expect(chipText('healthy')).toBe('Healthy');
        expect(chipText('unchecked')).toBe('Not checked');
        expect(chipText('error')).toBe('Check failed');
        expect(overviewText('healthy')).toMatch(/look good/);
        expect(overviewText('review')).toMatch(/Review the reported findings/);
    });
    it('writes the details report', () => {
        const lines = reportLines(report);
        expect(lines.slice(0, 7)).toEqual(['Checked 2026-01-01T00:00:00Z.', '72 active media item(s).', '72 missing representative snapshot(s).', '0 empty snapshot-cache row(s).', '0 incomplete preview cache(s).', '1 stale background job(s).', 'Artwork cache misses: 5.']);
        expect(lines.slice(7)).toEqual(['warning · SNAP: 72 — Missing snapshots', 'info · X: 1 — Other']);
        expect(reportLines({})[0]).toBe('Checked now.');
    });
    it('formats counts, dates and the repair result', () => {
        expect(counts({ videos: 1200, performers: 3 })).toEqual([['Videos', '1,200'], ['Performers', '3'], ['Studios', '—']]);
        expect(counts(undefined)[0]).toEqual(['Videos', '—']);
        expect(lastChecked(null)).toBe('Never');
        expect(lastChecked('2026-01-05T10:30:00')).toMatch(/2026/);
        expect(lastChecked('garbage')).toBe('garbage');
        expect(repairToast({ repaired: 2, skipped: 1 })).toBe('Artwork cache: 2 repaired, 1 unavailable locally, 0 errors.');
    });
});
