import { describe, expect, it } from 'vitest';
import { activeLaneCount, jobPath, laneChips, laneKind, laneName, toLanes } from './lanes';
import { DEFAULT_OPTIONS, expandStages, pendingBody, prerequisitePlan, processBody, scopeBody, selectedStages, stageLabels } from './metadata';
describe('laneChips', () => {
    it('uses defaults when the governor reports nothing', () => {
        expect(laneChips({}, {}).map((c) => c.value)).toEqual(['0/2', 'shared file-count limits', 'idle', '0/—', '—']);
    });
    it('lists small and large file slots and playback priority', () => {
        const c = laneChips({ transfer_active: 1, transfer_capacity: 3, telegram_file_active_small: 0, telegram_file_limit_small: 3, telegram_file_active_large: 1, telegram_file_limit_large: 2, playback_active: true }, { background_active: 1, background_capacity: 2, media_workers: 2 });
        expect(c.map((x) => x.value)).toEqual(['1/3', '0/3 small-file slots · 1/2 large-file slots', 'priority active', '1/2', '2']);
    });
});
describe('lane naming', () => {
    it('names workflows like the classic page', () => {
        expect(laneName('TELEGRAM_TRANSFER', 'local', '')).toBe('Local → channel upload');
        expect(laneName('TELEGRAM_TRANSFER', 'telegram', '')).toBe('Telegram → Telegram transfer');
        expect(laneName('METADATA_CHANNEL_PIPELINE', '', '')).toBe('Metadata & Assets scan');
        expect(laneName('X', '', 'content_automation')).toBe('Content Automation · delivery');
        expect(laneKind('MEDIA_MANAGER', '')).toBe('metadata');
        expect(laneKind('CONTENT_AUTOMATION', '')).toBe('automation');
        expect(laneKind('UPLOAD_LOCAL', '')).toBe('transfer');
    });
});
describe('toLanes', () => {
    it('carries the server\'s reason a running lane is waiting, and is empty otherwise', () => {
        const [a, b] = toLanes([{ id: 'j1', type: 'UPLOAD_LOCAL', status: 'RUNNING', metrics: { waiting_reason: 'FloodWait 240s left' } }, { id: 'j2', type: 'UPLOAD_LOCAL', status: 'RUNNING' }], names);
        expect(a.waiting).toBe('FloodWait 240s left');
        expect(b.waiting).toBe('');
    });
    const names = (v) => (v ? `Chan ${String(v)}` : '');
    it('builds a titled card and clamps progress', () => {
        const [l] = toLanes([{ id: 'j1', type: 'UPLOAD_LOCAL', status: 'RUNNING', operation: 'Uploading video', current_item: 'C:\\clips\\a.mp4 · 40%', destination_channel: '5', progress: 140 }], names);
        expect(l).toMatchObject({ kind: 'transfer', title: 'Uploading a.mp4', pct: 100, status: 'RUNNING' });
        expect(l.subtitle).toBe('Local → channel upload · to Chan 5');
    });
    it('titles metadata lanes by channel and shows the file as detail', () => {
        const [l] = toLanes([{ id: 'm', type: 'METADATA_CHANNEL_PIPELINE', status: 'RUNNING', operation: 'Matching', source_channel: '9', current_item: 'x/y.mp4' }], names);
        expect(l).toMatchObject({ kind: 'metadata', title: 'Channel scan · Chan 9', detail: 'y.mp4' });
    });
    it('offers the right controls for each state', () => {
        const acts = (status) => toLanes([{ id: 'j', type: 'X', status }], names)[0].actions;
        expect(acts('RUNNING')).toEqual(['pause', 'cancel']);
        expect(acts('PAUSED')).toEqual(['resume', 'cancel']);
        expect(acts('INTERRUPTED')).toEqual(['recover', 'cancel']);
        expect(acts('CANCEL_REQUESTED')).toEqual([]);
        expect(acts('COMPLETED')).toEqual([]);
        expect(toLanes([{ type: 'X', status: 'RUNNING' }], names)[0].actions).toEqual([]);
    });
    it('counts active lanes and builds control URLs', () => {
        expect(activeLaneCount([{ status: 'RUNNING' }, { status: 'PAUSED' }, { status: 'COMPLETED' }])).toBe(2);
        expect(jobPath('a/b', 'pause')).toBe('/api/jobs/a%2Fb/pause');
    });
});
describe('metadata run options', () => {
    it('selects the recommended stages; pending runs skip inventory', () => {
        expect(selectedStages(DEFAULT_OPTIONS)).toEqual(['inventory', 'metadata', 'performers', 'channel_data', 'snapshots']);
        expect(selectedStages(DEFAULT_OPTIONS, true)).toEqual(['metadata', 'performers', 'channel_data', 'snapshots']);
        expect(selectedStages({ ...DEFAULT_OPTIONS, entities: false, snapshots: false })).toEqual(['inventory']);
    });
    it('expands any entity stage to the whole pipeline and labels it once', () => {
        expect(expandStages(['channel_data', 'snapshots'])).toEqual(['metadata', 'performers', 'channel_data', 'snapshots']);
        expect(stageLabels(['inventory', 'metadata', 'performers', 'snapshots'])).toEqual(['Inventory', 'Metadata & Entity Data', 'Build snapshots']);
    });
    it('maps scope to limit/unbounded', () => {
        expect(scopeBody(DEFAULT_OPTIONS)).toEqual({ limit: 50, unbounded: true });
        expect(scopeBody({ ...DEFAULT_OPTIONS, scope: '100' })).toEqual({ limit: 100, unbounded: false });
    });
    it('builds the process body, with full inventory also reconciling captions', () => {
        const b = processBody(['1', '2'], ['inventory'], { ...DEFAULT_OPTIONS, scope: '25', fullInventory: true, forceMetadata: true });
        expect(b).toMatchObject({ channel_ids: ['1', '2'], stages: ['inventory'], limit: 25, unbounded: false, force_metadata: true, full_inventory: true, reconcile_uploaded_captions: true });
    });
    it('builds the pending body without inventory', () => expect(pendingBody(['1'], DEFAULT_OPTIONS).stages).not.toContain('inventory'));
    it('plans prerequisites from a failed preflight', () => {
        const p = prerequisitePlan({ ready: false, required_stages: ['inventory'], missing_by_channel: [{ channel_id: 7, missing: ['inventory'] }, { channel_id: 8, missing: ['inventory'] }] }, ['snapshots']);
        expect(p.labels).toEqual(['Inventory']);
        expect(p.affected).toEqual(['7', '8']);
        expect(p.stages).toEqual(['inventory', 'snapshots']);
        expect(p.autoAdded).toEqual({ inventory: ['7', '8'] });
    });
});
