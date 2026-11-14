import { describe, expect, it } from 'vitest';
import { AUTO_SYNC_FAILED, emptyMessage, headingFor, markerChips, refreshLabel, rowsFrom } from './markers';
describe('marker chips', () => {
    it('sorts by time and labels each chip like the classic page', () => {
        const chips = markerChips([{ position: 125, title: 'Second', note: 'a note', tag: 'pov' }, { position: 5, title: 'First' }]);
        expect(chips.map((c) => c.time)).toEqual(['0:05', '2:05']);
        expect(chips[1]).toMatchObject({ title: 'Second', tag: 'pov', tooltip: 'Go to 2:05 · a note', label: 'Go to 2:05: Second' });
        expect(chips[0]).toMatchObject({ tooltip: 'Go to 0:05', tag: '' });
    });
    it('calls an untitled marker "Marker" and trims titles', () => {
        expect(markerChips([{ position: 30, title: '   ' }])[0].title).toBe('Marker');
        expect(markerChips([{ position: 30 }])[0].title).toBe('Marker');
        expect(markerChips([{ position: 30, title: ' x ' }])[0].title).toBe('x');
    });
    it('clamps negative and unreadable positions to the start', () => {
        expect(markerChips([{ position: -4 }])[0].position).toBe(0);
        expect(markerChips([{ position: 'nope' }])[0].position).toBe(0);
        expect(markerChips([{ position: '30' }])[0].position).toBe(30);
    });
    it('keeps two markers at the same time as separate chips with separate keys', () => {
        const chips = markerChips([{ id: 1, position: 10, title: 'A' }, { id: 2, position: 10, title: 'B' }]);
        expect(new Set(chips.map((c) => c.key)).size).toBe(2);
    });
    it('handles nothing', () => {
        expect(markerChips(undefined)).toEqual([]);
        expect(markerChips([])).toEqual([]);
    });
    it('shows the count in the heading only when there are markers', () => {
        expect(headingFor(0)).toBe('Scene markers');
        expect(headingFor(3)).toBe('Scene markers (3)');
    });
});
describe('empty states', () => {
    it('explains why there are no markers', () => {
        expect(emptyMessage(undefined)).toBe('No markers yet.');
        expect(emptyMessage('NO_PROVIDER_TOKEN')).toContain('Configure the ThePornDB provider token');
        expect(emptyMessage('NO_TPDB_SCENE')).toBe('No matching provider scene was found.');
        expect(emptyMessage('PROVIDER_ERROR')).toBe('Provider markers could not be loaded. Try refreshing them.');
        expect(emptyMessage('NO_MARKERS')).toBe('No markers yet.');
    });
    it('an error message wins over the status', () => {
        expect(emptyMessage('NO_PROVIDER_TOKEN', AUTO_SYNC_FAILED)).toBe(AUTO_SYNC_FAILED);
    });
    it('labels the refresh button while it works', () => {
        expect(refreshLabel(false)).toBe('Refresh provider markers');
        expect(refreshLabel(true)).toBe('Checking provider…');
    });
    it('reads the markers out of a sync result', () => {
        expect(rowsFrom({ ok: true, markers: [{ position: 1 }] })).toHaveLength(1);
        expect(rowsFrom({ ok: false })).toEqual([]);
        expect(rowsFrom(undefined)).toEqual([]);
    });
});
