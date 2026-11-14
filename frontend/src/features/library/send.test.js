import { describe, expect, it } from 'vitest';
import { bulkJobId, bulkProgress, bulkSendBody, bulkSendLabel, bulkStarted, bulkSummary, bulkTitle, duplicatesPrompt, hasDuplicates, channelMatches, channelName, directTarget, isAlreadySent, isDisplayName, sendBody, sentMessage } from './send';
const ch = (telegram_id, title) => ({ id: 1, telegram_id, title });
describe('send to Telegram', () => {
    it('skips the dialog only when a fixed target is saved', () => {
        expect(directTarget({ fixed: true, target: '-100' })).toBe('-100');
        expect(directTarget({ fixed: true, target: '' })).toBe('');
        expect(directTarget({ fixed: false, target: '-100' })).toBe('');
        expect(directTarget(undefined)).toBe('');
    });
    it('does not treat placeholder titles as names', () => {
        expect(isDisplayName('Movies')).toBe(true);
        for (const t of ['', 'Unknown channel', 'channel -1001', '-1001', null])
            expect(isDisplayName(t)).toBe(false);
        expect(channelName([ch('-1', 'Movies'), ch('-2', 'channel -2')], '-1')).toBe('Movies');
        expect(channelName([ch('-2', 'channel -2')], '-2')).toBe('Channel -2');
        expect(channelName([], '-9')).toBe('Channel -9');
    });
    it('filters channels by title, case-insensitively', () => {
        const list = [ch('1', 'Alpha Videos'), ch('2', 'Beta')];
        expect(channelMatches(list, ' ALPHA ').map((c) => c.telegram_id)).toEqual(['1']);
        expect(channelMatches(list, '')).toHaveLength(2);
        expect(channelMatches(list, 'zzz')).toEqual([]);
    });
    it('builds the request body the server expects', () => {
        expect(sendBody({ _cid: '-100', id: 7 }, '-200', true, false)).toEqual({ channel_id: '-100', message_id: 7, target: '-200', remember: true, force: false });
    });
    it('recognises the already-sent response and words the success note', () => {
        expect(isAlreadySent(409)).toBe(true);
        expect(isAlreadySent(502)).toBe(false);
        expect(sentMessage('Movies', [])).toBe('Sent to Movies');
        expect(sentMessage('Movies', ['no cover', 'trimmed'])).toBe('Sent to Movies — no cover; trimmed');
        expect(sentMessage('Movies', undefined)).toBe('Sent to Movies');
    });
});
describe('caption markup', () => {
    it('turns the default template into bold text with decoded values', async () => {
        const { parseCaption } = await import('./send');
        expect(parseCaption('🎬 <b>A &amp; B</b>\n🏢 Studio')).toEqual(['🎬 ', { tag: 'b', children: ['A & B'] }, '\n🏢 Studio']);
    });
    it('never produces elements from escaped or unknown markup', async () => {
        const { parseCaption } = await import('./send');
        expect(parseCaption('x &lt;script&gt;alert(1)&lt;/script&gt;')).toEqual(['x <script>alert(1)</script>']);
        expect(parseCaption('<a href="javascript:alert(1)">link</a> <span onclick="x">t</span>')).toEqual(['link', ' ', 't']);
        expect(JSON.stringify(parseCaption('<script>x</script>'))).not.toContain('script"');
    });
    it('nests tags and tolerates stray or unclosed ones', async () => {
        const { parseCaption } = await import('./send');
        expect(parseCaption('<b>a <i>b</i></b>c')).toEqual([{ tag: 'b', children: ['a ', { tag: 'i', children: ['b'] }] }, 'c']);
        expect(parseCaption('</b>x<b>y')).toEqual(['x', { tag: 'b', children: ['y'] }]);
        expect(parseCaption('')).toEqual([]);
    });
    it('decodes numeric entities and leaves unknown ones alone', async () => {
        const { parseCaption } = await import('./send');
        expect(parseCaption('&#65;&#x42; &bogus; &#0;')).toEqual(['AB &bogus; &#0;']);
    });
});
describe('bulk send', () => {
    const v = [{ channel_id: '-1', message_id: 1 }, { channel_id: '-1', message_id: 2 }];
    it('builds the job request, adding the duplicate choice only when one was made', () => {
        expect(bulkSendBody(v, '-9', true, 'j1')).toEqual({ videos: v, target: '-9', remember: true, job_id: 'j1' });
        expect(bulkSendBody(v, '-9', false, 'j1', 'skip')).toMatchObject({ duplicate_action: 'skip' });
        expect(bulkJobId(35, 2)).toBe('bulk-z-2');
    });
    it('words the dialog, the start toast and the progress toast', () => {
        expect(bulkTitle(1)).toBe('Send 1 selected video to Telegram');
        expect(bulkTitle(3)).toBe('Send 3 selected videos to Telegram');
        expect(bulkSendLabel(2)).toBe('Send 2 videos');
        expect(bulkStarted(2, 'Beta')).toBe('Sending 2 selected videos to Beta. This may take a while…');
        expect(bulkProgress('Beta', 1, 4, 'Scene')).toBe('Sending to Beta · 1/4 complete · Scene');
        expect(bulkProgress('Beta', 1, 4, '')).toBe('Sending to Beta · 1/4 complete');
        expect(duplicatesPrompt(1, 'Beta')).toContain('1 of the selected videos has already been sent to Beta');
        expect(duplicatesPrompt(3, 'Beta')).toContain('3 of the selected videos have already');
    });
    it('spots a duplicate answer', () => {
        expect(hasDuplicates({ already_sent: true, duplicate_videos: [{}] })).toBe(true);
        expect(hasDuplicates({ already_sent: true, duplicate_videos: [] })).toBe(false);
        expect(hasDuplicates({ duplicate_videos: [{}] })).toBe(false);
    });
    it('summarises the run and names the first failure', () => {
        expect(bulkSummary({ sent: 2, skipped: 1, failed: 0 }, () => '')).toEqual({ text: 'Telegram send complete: 2 sent, 1 skipped, 0 failed.', failed: false });
        const r = bulkSummary({ sent: 1, failed: 1, results: [{ status: 'sent' }, { status: 'failed', message_id: 7, error: 'FloodWait' }] }, (id) => `message ${id}`);
        expect(r.failed).toBe(true);
        expect(r.text).toBe('Telegram send complete: 1 sent, 0 skipped, 1 failed. First failure: message 7 — FloodWait.');
    });
});
