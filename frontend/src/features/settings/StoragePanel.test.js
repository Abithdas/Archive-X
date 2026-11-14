import { jsx as _jsx } from "react/jsx-runtime";
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { withRouter } from '@/test/withRouter';
import { StoragePanel } from './StoragePanel';
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }));
beforeAll(() => {
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
});
const json = (b, code = 200) => new Response(JSON.stringify(b), { status: code });
const BACKUP = { settings: { enabled: false, repository: 'D:\\bk', daily: 7, weekly: 4, monthly: 3, schedule_time: '02:00' }, backups: [{ name: 'a.axbackup', path: 'D:\\bk\\a.axbackup', created_at: '2026-01-01', size: 10, files: 3, verified: true }] };
const mount = (storage = { current_path: 'D:\\data', bytes: 1000, files: 5 }, extra = () => undefined) => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
        calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
        const x = extra(url, init);
        if (x)
            return x;
        if (url === '/api/settings/cache-usage')
            return json({ budget_gib: 50, retention: 'idle_30m', readahead_seconds: 180, ram_mb: 128, connection_profile: 'balanced', kept_entries: 0 });
        if (url === '/api/settings/storage')
            return json(storage);
        if (url === '/api/settings/backup' && !init?.method)
            return json(BACKUP);
        return json({ ok: true });
    }));
    withRouter(_jsx(StoragePanel, {}));
    return calls;
};
const posts = (c) => c.filter((x) => x.method === 'POST');
afterEach(() => vi.unstubAllGlobals());
describe('Storage & Backup', () => {
    it('autosaves a cache setting with the server field names', async () => {
        const calls = mount();
        const sel = await screen.findByLabelText('Video byte-cache limit');
        await waitFor(() => expect(sel).toHaveValue('50'));
        fireEvent.change(sel, { target: { value: '100' } });
        await waitFor(() => expect(posts(calls)).toHaveLength(1));
        expect(posts(calls)[0].url).toBe('/api/settings/cache-policy');
        expect(posts(calls)[0].body).toMatchObject({ budget_gib: 100, retention: 'idle_30m', readahead_seconds: 180, ram_mb: 128, connection_profile: 'balanced' });
    });
    it('asks before clearing the cache, and sends nothing on cancel', async () => {
        const calls = mount();
        fireEvent.click(await screen.findByRole('button', { name: 'Clear downloaded video-byte cache' }));
        const dlg = await screen.findByRole('dialog');
        expect(posts(calls)).toHaveLength(0);
        fireEvent.click(within(dlg).getByRole('button', { name: 'Cancel' }));
        expect(posts(calls)).toHaveLength(0);
        fireEvent.click(screen.getByRole('button', { name: 'Clear downloaded video-byte cache' }));
        fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Clear cache' }));
        await waitFor(() => expect(posts(calls).map((c) => c.url)).toEqual(['/api/settings/clear-video-byte-cache']));
    });
    it('requires a destination and a confirmation before preparing a move', async () => {
        const calls = mount();
        const go = await screen.findByRole('button', { name: 'Move data on next restart' });
        fireEvent.click(go);
        expect(await screen.findByText('Select a destination folder first.')).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('New data folder'), { target: { value: 'E:\\new' } });
        fireEvent.click(go);
        const dlg = await screen.findByRole('dialog');
        expect(posts(calls)).toHaveLength(0);
        fireEvent.click(within(dlg).getByRole('button', { name: 'Prepare move' }));
        await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/settings/storage/move', body: { destination: 'E:\\new' } }));
    });
    it('blocks the move while jobs are running or the environment overrides the folder', async () => {
        mount({ current_path: 'D:\\data', blocking_jobs: 2 });
        const go = await screen.findByRole('button', { name: 'Move data on next restart' });
        await waitFor(() => expect(go).toBeDisabled());
        expect(screen.getByRole('note')).toHaveTextContent('2 running/queued job(s)');
    });
    it('creates a backup only when the password is empty or 8+ characters', async () => {
        const calls = mount();
        const pw = await screen.findByLabelText('Backup encryption password');
        fireEvent.change(pw, { target: { value: 'short' } });
        fireEvent.click(screen.getByRole('button', { name: 'Create backup' }));
        await waitFor(() => expect(screen.getAllByRole('status').some((e) => /8/.test(e.textContent ?? ''))).toBe(true));
        expect(posts(calls)).toHaveLength(0);
        fireEvent.change(pw, { target: { value: 'long-enough-pw' } });
        fireEvent.click(screen.getByRole('button', { name: 'Create backup' }));
        await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/backup/create', body: { password: 'long-enough-pw', repository: 'D:\\bk' } }));
    });
    it('verifies a backup with the typed password', async () => {
        const calls = mount();
        fireEvent.click(await screen.findByRole('button', { name: 'Verify' }));
        const dlg = await screen.findByRole('dialog');
        fireEvent.change(within(dlg).getByLabelText('Backup password'), { target: { value: 'pw' } });
        fireEvent.click(within(dlg).getByRole('button', { name: 'Verify backup' }));
        await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/backup/verify', body: { path: 'D:\\bk\\a.axbackup', password: 'pw' } }));
    });
    it('confirms before deleting a backup', async () => {
        const calls = mount();
        fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));
        const dlg = await screen.findByRole('dialog');
        expect(dlg).toHaveTextContent('a.axbackup');
        expect(posts(calls)).toHaveLength(0);
        fireEvent.click(within(dlg).getByRole('button', { name: 'Delete backup' }));
        await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/backup/delete', body: { path: 'D:\\bk\\a.axbackup' } }));
    });
    it('previews a restore and only stages it after confirmation', async () => {
        const calls = mount({ current_path: 'D:\\data' }, (u) => (u === '/api/backup/restore/preview' ? json({ manifest: { application_version: '29.5', files: [1, 2], encrypted: false }, active_jobs: 0 }) : undefined));
        const stage = await screen.findByRole('button', { name: 'Stage restore' });
        expect(stage).toBeDisabled();
        fireEvent.change(screen.getByLabelText('Backup file to restore'), { target: { value: 'D:\\bk\\a.axbackup' } });
        fireEvent.click(screen.getByRole('button', { name: 'Validate / Preview' }));
        expect(await screen.findByText(/Valid backup · 29\.5 · 2 files · not encrypted/)).toBeInTheDocument();
        fireEvent.click(stage);
        const dlg = await screen.findByRole('dialog');
        expect(posts(calls).filter((c) => c.url === '/api/backup/restore')).toHaveLength(0);
        fireEvent.click(within(dlg).getByRole('button', { name: 'Stage restore' }));
        await waitFor(() => expect(posts(calls).some((c) => c.url === '/api/backup/restore')).toBe(true));
    });
});
