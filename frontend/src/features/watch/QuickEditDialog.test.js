import { jsx as _jsx } from "react/jsx-runtime";
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { QuickEditDialog } from './QuickEditDialog';
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(() => 'id'), { error: vi.fn(), success: vi.fn(), loading: vi.fn(() => 'p'), dismiss: vi.fn() }), Toaster: () => null }));
beforeAll(() => {
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });
const item = { id: 7, _cid: '-1', media_id: 5, name: 'Old title', filename: 'f.mp4', duration: 125, thumbnail_id: 7, performers: ['Ava Stone'], studios: ['Northlight'], studio: 'Northlight', tags: [] };
const json = (b, code = 200) => new Response(JSON.stringify(b), { status: code });
const mount = (o = {}) => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
        calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
        if (url === '/api/media/quick-edit/lookup')
            return (o.lookup ?? (() => json({ ok: true, matches: [], filename_hints: { performers: ['Ava Stone'], date: '2024-01-02' } })))();
        if (url === '/api/media/quick-edit')
            return (o.save ?? (() => json({ ok: true, caption_sync: { ok: true, status: 'synced' } })))();
        if (url.startsWith('/api/performers/provider-search'))
            return json(o.provider ?? []);
        if (url.startsWith('/api/performers?'))
            return json(o.performers ?? []);
        if (url.startsWith('/api/studios/provider-search'))
            return json([]);
        if (url.startsWith('/api/studios?'))
            return json(o.studios ?? []);
        return json({ ok: true });
    }));
    const onDone = vi.fn();
    render(_jsx(QueryClientProvider, { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }), children: _jsx(QuickEditDialog, { item: item, onDone: onDone }) }));
    return { calls, onDone };
};
const posts = (c, url) => c.filter((x) => x.url === url && x.method === 'POST');
const dlg = () => screen.getByRole('dialog', { name: 'Edit video' });
const pane = (n) => within(dlg()).getByRole('region', { name: n });
const chips = (n) => within(dlg()).getByRole('group', { name: n });
describe('Edit video', () => {
    it('opens with the current title, performers and studio, and looks up video data straight away', async () => {
        const { calls } = mount();
        expect(await screen.findByDisplayValue('Old title')).toBeInTheDocument();
        expect(within(chips('Selected performers')).getByText('Ava Stone')).toBeInTheDocument();
        expect(within(chips('Selected studio')).getByText('Northlight')).toBeInTheDocument();
        expect(dlg()).toHaveTextContent('f.mp4 · 2:05');
        await waitFor(() => expect(posts(calls, '/api/media/quick-edit/lookup')).toHaveLength(1));
        expect(posts(calls, '/api/media/quick-edit/lookup')[0].body).toMatchObject({ channel_id: '-1', message_id: 7, title: 'Old title', performers: ['Ava Stone'], studio: 'Northlight', performer_selected_explicit: false });
        expect(await screen.findByLabelText('Filename clues')).toHaveTextContent('Ava Stone');
    });
    it('saves nothing until Save changes, then sends the title, performers and studio locked', async () => {
        const { calls, onDone } = mount();
        fireEvent.change(await screen.findByDisplayValue('Old title'), { target: { value: ' New title ' } });
        expect(posts(calls, '/api/media/quick-edit')).toHaveLength(0);
        fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        await waitFor(() => expect(posts(calls, '/api/media/quick-edit')).toHaveLength(1));
        expect(posts(calls, '/api/media/quick-edit')[0].body).toMatchObject({ channel_id: '-1', message_id: 7, title: 'New title', performers: [expect.objectContaining({ name: 'Ava Stone' })], studio: expect.objectContaining({ name: 'Northlight' }), lock: true });
        await waitFor(() => expect(onDone).toHaveBeenCalled());
        expect(toast).toHaveBeenCalledWith('Metadata saved and Telegram caption synchronized.');
    });
    it('Cancel closes without saving', async () => {
        const { calls, onDone } = mount();
        await screen.findByDisplayValue('Old title');
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(onDone).toHaveBeenCalled();
        expect(posts(calls, '/api/media/quick-edit')).toHaveLength(0);
    });
    it('removes a performer and the studio from what will be saved', async () => {
        const { calls } = mount();
        await screen.findByDisplayValue('Old title');
        fireEvent.click(screen.getByRole('button', { name: 'Remove Ava Stone' }));
        fireEvent.click(screen.getByRole('button', { name: 'Remove studio' }));
        expect(within(chips('Selected performers')).getByText('No performers selected')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        await waitFor(() => expect(posts(calls, '/api/media/quick-edit')).toHaveLength(1));
        expect(posts(calls, '/api/media/quick-edit')[0].body).toMatchObject({ performers: [], studio: null });
    });
    it('searches performers (local and API), adds one, and offers "create" for a new name', async () => {
        const { calls } = mount({ performers: [{ id: 2, name: 'Mia Rivera', video_count: 3 }], provider: [{ name: 'Mia Rivera', provider: 'stashdb', provider_id: 'sd9', exact: true }] });
        await screen.findByDisplayValue('Old title');
        fireEvent.change(within(pane('Performer')).getByLabelText('Search performer'), { target: { value: 'mia' } });
        const local = await within(pane('Performer')).findByRole('option', { name: /Mia Rivera.*Local catalogue/ });
        expect(await within(pane('Performer')).findByRole('option', { name: /STASHDB · Exact name · matches local record/ })).toBeInTheDocument();
        expect(within(pane('Performer')).getByRole('option', { name: /Create “mia”/ })).toBeInTheDocument();
        fireEvent.click(local);
        expect(within(chips('Selected performers')).getAllByText('Mia Rivera')).toHaveLength(1);
        fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        await waitFor(() => expect(posts(calls, '/api/media/quick-edit')).toHaveLength(1));
        expect(posts(calls, '/api/media/quick-edit')[0].body.performers.map((p) => p.name)).toEqual(['Ava Stone', 'Mia Rivera']);
    });
    it('picking an API match for someone already chosen adds its identity instead of removing them', async () => {
        const { calls } = mount({ provider: [{ name: 'Ava Stone', provider: 'stashdb', provider_id: 'sd1', image: 'https://i/a.jpg' }] });
        await screen.findByDisplayValue('Old title');
        fireEvent.change(within(pane('Performer')).getByLabelText('Search performer'), { target: { value: 'ava' } });
        fireEvent.click(await within(pane('Performer')).findByRole('option', { name: /STASHDB/ }));
        expect(within(chips('Selected performers')).getAllByText('Ava Stone')).toHaveLength(1);
        fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        await waitFor(() => expect(posts(calls, '/api/media/quick-edit')).toHaveLength(1));
        expect(posts(calls, '/api/media/quick-edit')[0].body.performers).toEqual([expect.objectContaining({ name: 'Ava Stone', provider: 'stashdb', provider_id: 'sd1' })]);
    });
    it('sets a studio from the search, including a brand-new one', async () => {
        const { calls } = mount({ studios: [{ id: 3, name: 'Big Studio', video_count: 20 }] });
        await screen.findByDisplayValue('Old title');
        fireEvent.change(within(pane('Studio')).getByLabelText('Search studio'), { target: { value: 'big' } });
        fireEvent.click(await within(pane('Studio')).findByRole('option', { name: /Big Studio/ }));
        expect(within(chips('Selected studio')).getByText('Big Studio')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        await waitFor(() => expect(posts(calls, '/api/media/quick-edit')).toHaveLength(1));
        expect(posts(calls, '/api/media/quick-edit')[0].body).toMatchObject({ studio: { name: 'Big Studio', id: 3 } });
    });
    it('shows provider matches; Apply stages title, cast and studio but does not save', async () => {
        const { calls } = mount({ lookup: () => json({ ok: true, method: 'filename search', matches: [{ title: 'Provider Title', provider: 'tpdb', studio: 'ProvStudio', date: '2024-05-06', performer_details: [{ name: 'Zed Zee', id: 'p9' }, { name: 'Amy' }], score_percent: 94 }] }) });
        const candidate = await screen.findByRole('article');
        expect(candidate).toHaveTextContent('Provider Title');
        expect(candidate).toHaveTextContent('TPDB · ProvStudio · 2024-05-06');
        expect(candidate).toHaveTextContent('94%');
        expect(await screen.findByText(/1 video-data candidate found via filename search/)).toBeInTheDocument();
        fireEvent.click(within(candidate).getByRole('button', { name: 'Apply match: Provider Title' }));
        expect(screen.getByDisplayValue('Provider Title')).toBeInTheDocument();
        expect(within(chips('Selected performers')).getByText('Zed Zee')).toBeInTheDocument();
        expect(within(chips('Selected performers')).queryByText('Ava Stone')).toBeNull();
        expect(within(chips('Selected studio')).getByText('ProvStudio')).toBeInTheDocument();
        expect(screen.getByText('Provider cast staged (2 performers). No database change has been made yet.')).toBeInTheDocument();
        expect(posts(calls, '/api/media/quick-edit')).toHaveLength(0);
    });
    it('never uses a non-http thumbnail from a provider', async () => {
        mount({ lookup: () => json({ ok: true, matches: [{ title: 'T', thumbnail: 'javascript:alert(1)' }] }) });
        const candidate = await screen.findByRole('article');
        expect(candidate.querySelector('img')).toBeNull();
    });
    it('says so when the lookup fails, or when the provider reports a problem', async () => {
        mount({ lookup: () => json({ detail: 'Provider not configured' }, 500) });
        expect(await screen.findByText('Video-data lookup failed. Check the network and provider configuration, then try again.')).toBeInTheDocument();
    });
    it('shows the server reason when the lookup answers ok:false', async () => {
        mount({ lookup: () => json({ ok: false, detail: 'No provider token' }) });
        expect(await screen.findByText('No provider token')).toBeInTheDocument();
    });
    it('keeps the dialog open and explains when only the Telegram caption sync failed', async () => {
        const { onDone } = mount({ save: () => json({ ok: true, caption_sync: { ok: false, status: 'permission_denied', detail: 'No edit rights.' } }) });
        fireEvent.click(await screen.findByRole('button', { name: 'Save changes' }));
        expect(await screen.findByText('Local metadata was saved, but Telegram caption sync failed. No edit rights.')).toBeInTheDocument();
        expect(toast).toHaveBeenCalledWith('Metadata saved locally; Telegram edit permissions are missing.');
        expect(onDone).not.toHaveBeenCalled();
        expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled(); // can retry
    });
    it('shows the server reason when saving fails', async () => {
        const { onDone } = mount({ save: () => json({ ok: false, detail: 'Title is required' }) });
        fireEvent.click(await screen.findByRole('button', { name: 'Save changes' }));
        await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Title is required'));
        expect(onDone).not.toHaveBeenCalled();
    });
    it('shows a connection message when the request itself fails', async () => {
        mount({ save: () => json({}, 500) });
        fireEvent.click(await screen.findByRole('button', { name: 'Save changes' }));
        await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Save failed. Check the connection and try again.'));
    });
});
