import { jsx as _jsx } from "react/jsx-runtime";
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { ChannelEditor } from './ChannelEditor';
const toast = vi.hoisted(() => Object.assign(vi.fn(), { error: vi.fn() }));
vi.mock('sonner', () => ({ toast, Toaster: () => null }));
beforeAll(() => {
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
});
afterEach(() => { vi.unstubAllGlobals(); toast.mockClear(); });
const json = (b, code = 200) => new Response(JSON.stringify(b), { status: code });
const mount = (over = () => undefined, id = '-1001') => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
        calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
        const o = over(url, init);
        if (o)
            return o;
        if (url === `/api/channels/${id}/edit` && !init?.method)
            return json({ telegram_id: id, title: 'Main Archive', image: null, updated_at: 't' });
        return json({ ok: true, synced: ['title'] });
    }));
    const onClose = vi.fn();
    render(_jsx(QueryClientProvider, { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }), children: _jsx(ChannelEditor, { id: id, onClose: onClose }) }));
    return { calls, onClose };
};
const posts = (c) => c.filter((x) => x.method === 'POST');
const nameBox = async () => screen.findByLabelText('Channel name');
describe('ChannelEditor', () => {
    it('opens with the channel\'s name and closes without a request when nothing changed', async () => {
        const { calls, onClose } = mount();
        expect(await nameBox()).toHaveValue('Main Archive');
        fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        expect(onClose).toHaveBeenCalled();
        expect(posts(calls)).toHaveLength(0);
    });
    it('requires a name', async () => {
        mount();
        fireEvent.change(await nameBox(), { target: { value: '  ' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        expect(await screen.findByText('Channel name is required.')).toBeInTheDocument();
    });
    it('saves locally, then asks before changing Telegram, and can stop at local only', async () => {
        const { calls, onClose } = mount();
        fireEvent.change(await nameBox(), { target: { value: 'Renamed' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        const ask = await screen.findByRole('dialog', { name: 'Sync changes to Telegram?' });
        expect(ask).toHaveTextContent('Push the changed channel name to Telegram now?');
        expect(posts(calls).map((c) => c.url)).toEqual(['/api/channels/-1001/edit']);
        expect(posts(calls)[0].body).toEqual({ title: 'Renamed' });
        fireEvent.click(within(ask).getByRole('button', { name: 'Save locally only' }));
        await waitFor(() => expect(onClose).toHaveBeenCalled());
        expect(toast).toHaveBeenCalledWith('Changes saved locally only. Telegram was not changed.');
        expect(posts(calls)).toHaveLength(1);
    });
    it('pushes to Telegram only when confirmed, and reports success', async () => {
        const { calls, onClose } = mount();
        fireEvent.change(await nameBox(), { target: { value: 'Renamed' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        fireEvent.click(within(await screen.findByRole('dialog', { name: 'Sync changes to Telegram?' })).getByRole('button', { name: 'Push to Telegram' }));
        await waitFor(() => expect(onClose).toHaveBeenCalled());
        expect(posts(calls)[1]).toMatchObject({ url: '/api/channels/-1001/sync-to-telegram', body: { sync_title: true, sync_photo: false } });
        expect(toast).toHaveBeenCalledWith('Saved locally and synchronized to Telegram: title.');
    });
    it('tells you when the local save worked but Telegram refused', async () => {
        mount((u) => (u.endsWith('/sync-to-telegram') ? json({ detail: 'Telegram requires channel owner or administrator change-info permission' }, 403) : undefined));
        fireEvent.change(await nameBox(), { target: { value: 'Renamed' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        fireEvent.click(within(await screen.findByRole('dialog', { name: 'Sync changes to Telegram?' })).getByRole('button', { name: 'Push to Telegram' }));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Local changes saved, but Telegram sync failed: Telegram requires channel owner or administrator change-info permission'));
    });
    it('resetting to the Telegram photo is local only and does not ask about Telegram', async () => {
        const { calls, onClose } = mount();
        await nameBox();
        fireEvent.click(screen.getByRole('button', { name: 'Use Telegram photo' }));
        fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        await waitFor(() => expect(onClose).toHaveBeenCalled());
        expect(posts(calls)[0].body).toEqual({ title: 'Main Archive', reset_image_to_api: true });
        expect(screen.queryByRole('dialog', { name: 'Sync changes to Telegram?' })).not.toBeInTheDocument();
        expect(toast).toHaveBeenCalledWith('Channel photo reset to the current Telegram profile photo locally.');
    });
    it('shows the server\'s reason when the local save fails', async () => {
        const { onClose } = mount((u, i) => (i?.method === 'POST' && u.endsWith('/edit') ? json({ detail: 'Channel title must be 255 characters or fewer' }, 400) : undefined));
        fireEvent.change(await nameBox(), { target: { value: 'x' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        expect(await screen.findByText('Channel title must be 255 characters or fewer')).toBeInTheDocument();
        expect(onClose).not.toHaveBeenCalled();
    });
    it('says so when the channel is missing', async () => {
        mount((u) => (u.endsWith('/edit') ? json({ detail: 'Channel not found' }, 404) : undefined));
        expect(await screen.findByText('Channel not found')).toBeInTheDocument();
    });
});
