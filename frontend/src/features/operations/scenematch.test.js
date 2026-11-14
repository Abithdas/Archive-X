import { describe, expect, it } from 'vitest';
import { NO_FILTERS, approveAboveCopy, approvedToast, clamp100, countText, distinctReason, editItem, isFiltered, merge, missingLabel, nextOffsets, rejectedToast, retryStatus, retryToast, reviewUrl, safeHttp, thresholdOf } from './scenematch';
describe('scene match review', () => {
    it('builds the request and knows about filters', () => {
        expect(reviewUrl(NO_FILTERS, 0, 0)).toBe('/api/metadata/scene-match-review?limit=24&offset=0&unresolved_limit=24&unresolved_offset=0&unresolved_channel_id=&unresolved_missing_field=all');
        expect(reviewUrl({ channel: '-1', field: 'studio' }, 24, 48)).toContain('offset=24&unresolved_limit=24&unresolved_offset=48&unresolved_channel_id=-1&unresolved_missing_field=studio');
        expect(isFiltered(NO_FILTERS)).toBe(false);
        expect(isFiltered({ channel: '', field: 'performer' })).toBe(true);
    });
    it('words counts and missing fields', () => {
        expect(countText(1, 2000, true)).toBe('1 suggestion · 2,000 need metadata · filtered');
        expect(countText(3, 0, false)).toBe('3 suggestions · 0 need metadata');
        expect(missingLabel(['performer', 'studio'])).toBe('performer · studio');
        expect(missingLabel([])).toBe('scene identity');
        expect(missingLabel(undefined)).toBe('scene identity');
    });
    it('merges pages without repeats and finds the next offsets', () => {
        const p1 = { items: [{ suggestion_id: 1 }, { suggestion_id: 2 }], count: 5, has_more: true, unresolved_items: [{ media_id: 1 }], unresolved_count: 9, unresolved_has_more: true, unresolved_channels: [{ channel_id: '-1' }] };
        const p2 = { items: [{ suggestion_id: 2 }, { suggestion_id: 3 }], count: 5, has_more: false, unresolved_items: [{ media_id: 2 }], unresolved_count: 9, unresolved_has_more: false };
        const m = merge([p1, p2]);
        expect(m.suggestions.map((s) => s.suggestion_id)).toEqual([1, 2, 3]);
        expect(m.unresolved).toHaveLength(2);
        expect(m.hasMore).toBe(false);
        expect(m.total).toBe(5);
        expect(m.channels).toHaveLength(1);
        expect(merge([p1]).hasMore).toBe(true);
        expect(nextOffsets([p1, p2])).toEqual({ so: 4, uo: 2 });
        expect(merge([]).suggestions).toEqual([]);
    });
    it('shows a reason only when it adds something', () => {
        expect(distinctReason('title match, studio match', 'Title match')).toBe(false);
        expect(distinctReason('a', 'b')).toBe(true);
        expect(distinctReason('a', '')).toBe(false);
        expect(distinctReason('x', 'x')).toBe(false);
    });
    it('only trusts http(s) provider links and clamps numbers', () => { expect(safeHttp('https://x.test/a')).toBe('https://x.test/a'); expect(safeHttp('javascript:alert(1)')).toBe(''); expect(clamp100(150)).toBe(100); expect(clamp100(-3)).toBe(0); expect(thresholdOf(undefined)).toBe(95); expect(thresholdOf('80')).toBe(80); expect(thresholdOf(500)).toBe(100); });
    it('words bulk results like classic', () => {
        expect(approveAboveCopy(95).text).toBe('Approve all pending candidates scored 95.0% or higher? Performer conflicts and safety checks will still block approval.');
        expect(approvedToast({ approved: 4, ambiguous_skipped: 1, website_variants_skipped: 2, performer_conflicts: 3 })).toBe('Approved 4; 1 ambiguous video(s) left for review; 2 cross-site variant(s) need individual review; 3 blocked by performer conflicts.');
        expect(rejectedToast({ rejected: 2, not_pending: 1 })).toBe('Rejected 2 selected suggestion(s); 1 were no longer pending; 0 failed.');
        expect(retryToast({ id: 'j', count: 1500 })).toBe('Retry queued for 1,500 video(s).');
        expect(retryToast({ message: 'None to do' })).toBe('None to do');
        expect(retryToast({})).toBe('No unmatched videos found.');
        expect(retryStatus({ count: 3 })).toMatch(/Queued 3 unmatched/);
    });
    it('turns an entry into an editor item', () => { expect(editItem({ media_id: 5, channel_id: '-1', message_id: 9, filename: 'f.mp4', studio_name: 'S', performers: ['A'] })).toMatchObject({ id: 9, _cid: '-1', media_id: 5, name: 'f.mp4', studio: 'S', studios: ['S'], performers: ['A'] }); });
});
