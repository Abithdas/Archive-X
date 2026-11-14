import { jsx as _jsx } from "react/jsx-runtime";
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { withRouter } from '@/test/withRouter';
import { MetadataPanel } from './MetadataPanel';
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }));
const json = (b, code = 200) => new Response(JSON.stringify(b), { status: code });
const mount = (meta = { tpdb_token: 'abcd…wxyz', tpdb_configured: true, stashdb_api_key: '', stashdb_configured: false }, extra = () => undefined) => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
        calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
        const x = extra(url, init);
        if (x)
            return x;
        if (url === '/api/settings/metadata' && !init?.method)
            return json(meta);
        if (url === '/api/settings/scan-schedule' && !init?.method)
            return json({ enabled: true, start: '22:00', end: '06:30' });
        if (url === '/api/settings/providers')
            return json({ providers: [{ provider: 'tpdb', entity_type: 'scene', status: 'OK', request_count: 3 }], broker: {}, metadata_cache_entries: 1500, artwork_cache_entries: 4 });
        return json({ ok: true });
    }));
    withRouter(_jsx(MetadataPanel, {}));
    return calls;
};
const posts = (c) => c.filter((x) => x.method === 'POST');
afterEach(() => vi.unstubAllGlobals());
describe('Metadata panel', () => {
    it('shows saved keys as masked placeholders and a Saved status', async () => {
        mount();
        const tpdb = await screen.findByLabelText(/ThePornDB — API Token/);
        await waitFor(() => expect(tpdb).toHaveAttribute('placeholder', 'Saved token abcd…wxyz — leave blank to keep'));
        expect(screen.getByLabelText(/StashDB — API Key/)).toHaveAttribute('placeholder', 'saved key — leave blank to keep');
        expect(screen.getByText('Saved')).toBeInTheDocument();
    });
    it('saves only the typed key and clears the field', async () => {
        const calls = mount();
        const stash = await screen.findByLabelText(/StashDB — API Key/);
        fireEvent.change(stash, { target: { value: ' new-key ' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save all keys' }));
        expect(await screen.findByText('Saved.')).toBeInTheDocument();
        expect(posts(calls)[0]).toMatchObject({ url: '/api/settings/metadata', body: { stashdb_api_key: 'new-key' } });
        expect(stash).toHaveValue('');
    });
    it('Test saves a typed key first, then checks it and shows the server answer', async () => {
        const calls = mount(undefined, (u) => (u === '/api/settings/check-tpdb' ? json({ ok: false, error: 'Invalid or expired token' }) : undefined));
        fireEvent.change(await screen.findByLabelText(/ThePornDB — API Token/), { target: { value: 'tok' } });
        fireEvent.click(screen.getByRole('button', { name: 'Test ThePornDB' }));
        expect(await screen.findByText('Invalid or expired token')).toBeInTheDocument();
        expect(posts(calls).map((c) => c.url)).toEqual(['/api/settings/metadata', '/api/settings/check-tpdb']);
    });
    it('reports a working key', async () => {
        mount(undefined, (u) => (u === '/api/settings/check-stashdb' ? json({ ok: true }) : undefined));
        fireEvent.click(await screen.findByRole('button', { name: 'Test StashDB' }));
        expect(await screen.findByText('Working')).toBeInTheDocument();
    });
    it('loads the scan window and saves changes', async () => {
        const calls = mount(undefined, (u, i) => (u === '/api/settings/scan-schedule' && i?.method === 'POST' ? json({ ok: true, enabled: true, start: '23:00', end: '06:30' }) : undefined));
        expect(await screen.findByText('Enabled · 22:00–06:30')).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('Start time'), { target: { value: '23:00' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save scan window' }));
        expect(await screen.findByText('Saved · 23:00–06:30')).toBeInTheDocument();
        expect(posts(calls)[0]).toMatchObject({ url: '/api/settings/scan-schedule', body: { enabled: true, start: '23:00', end: '06:30' } });
    });
    it('refuses an empty time before sending', async () => {
        const calls = mount();
        await screen.findByText('Enabled · 22:00–06:30');
        fireEvent.change(screen.getByLabelText('End time'), { target: { value: '' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save scan window' }));
        expect(await screen.findByText('Enter the start and end times as HH:MM.')).toBeInTheDocument();
        expect(posts(calls)).toHaveLength(0);
    });
    it('lists provider status and the cache cards', async () => {
        mount();
        expect(await screen.findByText('TPDB · scene')).toBeInTheDocument();
        expect(screen.getByText('Local metadata cache')).toBeInTheDocument();
        expect(screen.getByText((1500).toLocaleString())).toBeInTheDocument();
        expect(screen.getByText('Artwork cache')).toBeInTheDocument();
    });
    it('says so when provider status cannot be read', async () => {
        mount(undefined, (u) => (u === '/api/settings/providers' ? json({ detail: 'x' }, 500) : undefined));
        expect(await screen.findByText('Provider status unavailable.')).toBeInTheDocument();
    });
});
