import { describe, expect, it } from 'vitest';
import { moveBody, moveConfirmText, moveMeta, moveResultMessage, optionLabel, resolveDestinations, uniqueChannels } from './move';
const ch = (telegram_id, title = 'T' + telegram_id) => ({ id: 1, telegram_id, title });
describe('move / delete source', () => {
    it('drops invalid and duplicate channels', () => {
        expect(uniqueChannels([ch('1'), ch('1'), { telegram_id: null }, { telegram_id: ' ' }, ch('2'), null]).map((c) => c.telegram_id)).toEqual(['1', '2']);
        expect(uniqueChannels('nope')).toEqual([]);
    });
    it('offers the Telegram inventory minus the source channel', () => {
        const d = resolveDestinations('1', [ch('1'), ch('2'), ch('3')], []);
        expect(d.options.map((c) => c.telegram_id)).toEqual(['2', '3']);
        expect(d).toMatchObject({ state: 'ready', message: '3 channels loaded.' });
        expect(resolveDestinations('1', [ch('1'), ch('2')], []).message).toBe('2 channels loaded.');
    });
    it('falls back to published channels with a clear warning when the inventory has nothing usable', () => {
        const d = resolveDestinations('1', [ch('1')], [ch('1'), ch('9')]);
        expect(d.options.map((c) => c.telegram_id)).toEqual(['9']);
        expect(d).toMatchObject({ state: 'warning', message: 'Using published Library channels because the Telegram channel inventory is empty.' });
    });
    it('says so when nothing is available, including after a failed load', () => {
        expect(resolveDestinations('1', [ch('1')], [])).toMatchObject({ options: [], state: 'warning', message: 'Telegram returned no usable destination channels. Refresh the channel list and retry.' });
        expect(resolveDestinations('1', undefined, [], 'offline')).toMatchObject({ options: [], message: 'Could not load Telegram channels: offline.' });
        expect(resolveDestinations('1', [ch('2')], [], 'offline').message).toBe('Could not refresh Telegram channels: offline. Showing the last loaded list.');
    });
    it('labels channels by title, never by a placeholder', () => {
        expect(optionLabel(ch('5', 'Movies'))).toBe('Movies');
        expect(optionLabel(ch('5', 'channel 5'))).toBe('Channel 5');
    });
    it('builds the request the server requires, including the explicit delete flag', () => {
        expect(moveBody({ _cid: '-1001', id: 7 }, '-1002')).toEqual({ channel_id: -1001, message_id: 7, destination_channel: '-1002', confirm_delete: true });
    });
    it('words the confirmation and the outcome', () => {
        expect(moveConfirmText('Scene', 'Beta')).toBe('Move “Scene” to “Beta” and delete the original Telegram message after verification? This cannot be undone.');
        expect(moveResultMessage({ ok: true, moved: { source_deleted: true } })).toBe('Move verified. The source message was deleted.');
        expect(moveResultMessage({ ok: true, moved: { source_deleted: false }, source_warning: 'w1' })).toBe('Destination verified. Source deletion is pending and will be retried. w1');
        expect(moveResultMessage({ ok: true, companion_warning: 'c' })).toBe('Move verified with a warning: c');
    });
    it('joins the meta line without empty parts', () => {
        expect(moveMeta('Main', 2048, (n) => `${n}B`)).toBe('Main · 2048B');
        expect(moveMeta('Main', 0, (n) => `${n}B`)).toBe('Main');
    });
});
