import { describe, expect, it } from 'vitest';
import { BULK_MAX, allFirstSelected, appliedLine, attentionText, bulkBody, bulkDone, firstBatch, oneBody, oneDone } from './automatch';
const items = Array.from({ length: 120 }, (_, i) => ({ video_id: i + 1, channel_id: '-1', message_id: i + 1 }));
describe('auto-match review', () => {
    it('words the attention item and the applied match', () => {
        expect(attentionText(1)).toBe('1 earlier automatic match to review — open');
        expect(attentionText(1200)).toBe('1,200 earlier automatic matches to review — open');
        expect(appliedLine({ applied_title: 'T', applied_studio: 'S', applied_performers: ['A', 'B'] })).toBe('Applied: T · S · A, B');
        expect(appliedLine({})).toBe('Applied: —');
    });
    it('limits bulk selection to the first 100', () => {
        expect(firstBatch(items)).toHaveLength(BULK_MAX);
        expect(firstBatch(items)[99]).toBe(100);
        expect(allFirstSelected(items, new Set(firstBatch(items)))).toBe(true);
        expect(allFirstSelected(items, new Set([1]))).toBe(false);
        expect(allFirstSelected([], new Set())).toBe(false);
    });
    it('builds requests and messages', () => {
        expect(oneBody('undo')).toEqual({ action: 'undo' });
        expect(bulkBody('keep', [1, 2])).toEqual({ action: 'keep', video_ids: [1, 2] });
        expect(oneDone('keep')).toBe('Match kept.');
        expect(oneDone('undo')).toMatch(/now a suggestion/);
        expect(bulkDone('undo', { processed: 3, skipped: 1 })).toBe('Bulk undo: 3 updated, 1 skipped, 0 failed.');
    });
});
