import { describe, expect, it } from 'vitest';
import { DEFAULT_FORM, automationState, configBody, fmtEta, fmtRate, formFromConfig, queueView, reviewWarnings, startProblem, stepProblem } from './automation';
describe('config ↔ form', () => {
    it('loads a saved config and never carries secrets', () => {
        const f = formFromConfig({ rss_url: 'https://x/feed.xml', destination_channel: '-5', download_path: 'D:\\dl', poll_interval: 300, qbittorrent_username: 'admin', qbittorrent_password: 'SECRET', only_1080p: false, rss_min_size_mb: 10, oversized_file_policy: 'skip', remove_torrent_after_delivery: true });
        expect(f).toMatchObject({ rss: 'https://x/feed.xml', destination: '-5', path: 'D:\\dl', poll: 300, qbtUser: 'admin', qbtPass: '', qbtKey: '', only1080: 'any', minSize: 10, oversized: 'skip' });
    });
    it('falls back to the classic defaults for an empty config', () => expect(formFromConfig({})).toEqual({ ...DEFAULT_FORM }));
    it('builds the exact request body, clamping the poll interval and sizes', () => {
        const b = configBody({ ...DEFAULT_FORM, rss: ' https://x/f ', destination: '-5', poll: 5, minSize: -3, maxSize: 20.9, qbtPass: 'pw' });
        expect(b).toMatchObject({ rss_url: 'https://x/f', destination_channel: '-5', torrent_backend: 'qbittorrent', poll_interval: 30, max_concurrent_torrents: 1, rss_min_size_mb: 0, rss_max_size_mb: 20, qbittorrent_password: 'pw', only_1080p: true, remove_torrent_after_delivery: true, delete_local_after_delivery: true });
    });
});
describe('validation', () => {
    const ok = { ...DEFAULT_FORM, rss: 'https://x/feed', destination: '-5' };
    it('step 1 needs a valid feed and a destination', () => {
        expect(stepProblem(0, DEFAULT_FORM)).toBe('RSS feed URL is required.');
        expect(stepProblem(0, { ...ok, rss: 'ftp://x' })).toBe('Enter a valid RSS/Feed URL before continuing.');
        expect(stepProblem(0, { ...ok, destination: '' })).toBe('Select a destination channel before continuing.');
        expect(stepProblem(0, ok)).toBe('');
    });
    it('step 2 bounds the poll interval', () => {
        expect(stepProblem(1, { ...ok, poll: 29 })).toContain('between 30 and 3600');
        expect(stepProblem(1, { ...ok, poll: 3601 })).toContain('between 30 and 3600');
        expect(stepProblem(1, { ...ok, poll: 30 })).toBe('');
    });
    it('step 3 rejects min above max, but allows an open max', () => {
        expect(stepProblem(2, { ...ok, minSize: 500, maxSize: 100 })).toBe('Minimum size cannot exceed maximum size.');
        expect(stepProblem(2, { ...ok, minSize: 500, maxSize: 0 })).toBe('');
    });
    it('review lists every problem; start names the first', () => {
        expect(reviewWarnings({ ...DEFAULT_FORM, minSize: 9, maxSize: 3 })).toEqual(['Minimum size is greater than maximum size.', 'RSS feed URL is required.', 'Destination channel is not selected.']);
        expect(reviewWarnings(ok)).toEqual([]);
        expect(startProblem(DEFAULT_FORM)).toBe('Enter an RSS feed URL before starting Content Automation.');
        expect(startProblem({ ...DEFAULT_FORM, rss: 'https://x' })).toBe('Select a destination Telegram channel before starting Content Automation.');
    });
});
describe('formatting', () => {
    it('formats rate and ETA, and hides unknowns', () => {
        expect([fmtRate(0), fmtRate(2048), fmtRate(3 * 1024 ** 2)]).toEqual(['', '2.0 KB/s', '3.0 MB/s']);
        expect([fmtEta(-1), fmtEta(NaN), fmtEta(75), fmtEta(3700)]).toEqual(['', '', '1m 15s', '1h 1m']);
    });
});
describe('queue view', () => {
    const data = {
        counts: { QUEUED: 2, COMPLETED: 5 }, runtime: { paused: false }, restart_pending: { id: 1 },
        items: [
            { item_key: 'a', title: 'Release A', status: 'DOWNLOADING', download_progress: 0.42, download_rate: 2 * 1024 ** 2, eta_seconds: 90 },
            { item_key: 'b', title: 'Release B', status: 'TRANSFERRING_VIDEO', download_progress: 1, upload_progress: 0.5, task_label: 'Uploading' },
            { item_key: 'c', title: 'Release C', status: 'QUEUED', created_at: '2026-01-02' },
            { item_key: 'd', title: 'Release D', status: 'QUEUED', created_at: '2026-01-01' },
            { item_key: 'e', title: 'Release E', status: 'COMPLETED' },
        ],
    };
    it('splits active, queued (oldest first) and recent', () => {
        const v = queueView(data);
        expect(v.summary).toBe('QUEUED: 2 · COMPLETED: 5');
        expect(v.active.map((x) => x.key)).toEqual(['a', 'b']);
        expect(v.queued.map((x) => x.key)).toEqual(['d', 'c']);
        expect(v.recent.map((x) => x.key)).toEqual(['e']);
        expect(v.restartPending).toBe(true);
        expect(v.downloads).toContain('Downloads: 1/1');
    });
    it('shows download telemetry only while downloading, and upload only while sending', () => {
        const [dl, up] = queueView(data).active;
        expect(dl).toMatchObject({ downloading: true, telemetry: '2.0 MB/s · ETA 1m 30s', download: 42, sending: false });
        expect(up).toMatchObject({ sending: true, upload: 50, telemetry: '', label: 'Uploading' });
    });
    it('caps the queue at eight and reports the rest', () => {
        const many = { items: Array.from({ length: 11 }, (_, i) => ({ item_key: `q${i}`, status: 'QUEUED', created_at: String(i).padStart(2, '0') })) };
        const v = queueView(many);
        expect(v.queued).toHaveLength(8);
        expect(v.queuedMore).toBe(3);
    });
    it('says so when nothing was recorded', () => expect(queueView({}).summary).toBe('No releases recorded.'));
    it('reports the header state', () => {
        expect(automationState({})).toBe('Idle');
        expect(automationState({ runtime: { operator_started: true } })).toBe('Running');
        expect(automationState({ items: [{ status: 'VERIFYING' }] })).toBe('Running');
        expect(automationState({ runtime: { paused: true }, items: [{ status: 'VERIFYING' }] })).toBe('Paused');
    });
});
