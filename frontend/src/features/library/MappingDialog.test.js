import { jsx as _jsx } from "react/jsx-runtime";
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { CardMenu } from './CardMenu';
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(() => 'id'), { error: vi.fn(), success: vi.fn(), loading: vi.fn(() => 'p'), dismiss: vi.fn() }), Toaster: () => null }));
beforeAll(() => {
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
    HTMLElement.prototype.hidePopover = function () { };
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });
const item = { id: 77, _cid: '-1001', media_id: 5, name: 'Scene', filename: 'f.mp4', size: 1, duration: 60, width: 1, height: 1, resolution_category: '1080p', thumbnail_id: 77, favorite: 0, upload_date: null, channel_title: 'Main', performers: [], studios: ['Northlight'], tags: [], studio: 'Northlight' };
const json = (b, code = 200) => new Response(JSON.stringify(b), { status: code });
const STUDIOS = [{ id: 1, name: 'Northlight', video_count: 15 }, { id: 2, name: 'North Star', video_count: 1 }];
const mount = (mapping = () => json({ ok: true })) => {
    const calls = [];
    let n = 0;
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
        const body = init?.body ? JSON.parse(String(init.body)) : undefined;
        calls.push({ url, method: init?.method ?? 'GET', body });
        if (url.startsWith('/api/studios?'))
            return json(new URL(url, 'http://x').searchParams.get('q') === 'north' ? STUDIOS : []);
        if (url === '/api/mapping/bulk')
            return mapping(++n, body);
        return json({ ok: true });
    }));
    render(_jsx(QueryClientProvider, { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }), children: _jsx(CardMenu, { item: item }) }));
    return calls;
};
const saves = (c) => c.filter((x) => x.url === '/api/mapping/bulk');
const open = async () => { fireEvent.click(screen.getByRole('menuitem', { name: 'Mapping / report', hidden: true })); return screen.findByRole('dialog', { name: 'Correct video metadata' }); };
const save = (dlg) => fireEvent.click(within(dlg).getByRole('button', { name: 'Save changes' }));
describe('Mapping / report', () => {
    it('opens with the current title and studio and makes no request yet', async () => {
        const calls = mount();
        const dlg = await open();
        expect(within(dlg).getByDisplayValue('Scene')).toBeInTheDocument();
        expect(within(dlg).getByText('Northlight')).toBeInTheDocument();
        expect(within(dlg).getByText('Type a studio name to search.')).toBeInTheDocument();
        expect(calls).toHaveLength(0);
    });
    it('saving with no changes sends nothing', async () => {
        const calls = mount();
        const dlg = await open();
        save(dlg);
        expect(toast).toHaveBeenCalledWith('No changes to save.');
        expect(saves(calls)).toHaveLength(0);
    });
    it('sends only the title, locked, when only the title changed', async () => {
        const calls = mount();
        const dlg = await open();
        fireEvent.change(within(dlg).getByDisplayValue('Scene'), { target: { value: 'Better title' } });
        save(dlg);
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(saves(calls)[0].body).toEqual({ videos: [{ channel_id: '-1001', message_id: 77 }], field: 'title', target_name: 'Better title', action: 'move', lock: true });
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Video title/studio mapping updated.'));
        await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    });
    it('picks an existing studio from the search and links it by id', async () => {
        const calls = mount();
        const dlg = await open();
        fireEvent.change(within(dlg).getByRole('combobox'), { target: { value: 'north' } });
        const opt = await within(dlg).findByRole('option', { name: /North Star/ });
        expect(opt).toHaveTextContent('1 video · link to this studio');
        fireEvent.mouseDown(opt);
        expect(within(dlg).getByText('North Star')).toBeInTheDocument();
        save(dlg);
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(saves(calls)[0].body).toMatchObject({ field: 'site', site_id: 2, target_name: 'North Star', lock: true });
    });
    it('selects with the keyboard', async () => {
        const calls = mount();
        const dlg = await open();
        const box = within(dlg).getByRole('combobox');
        fireEvent.change(box, { target: { value: 'north' } });
        await within(dlg).findByRole('option', { name: /North Star/ });
        fireEvent.keyDown(box, { key: 'ArrowDown' });
        fireEvent.keyDown(box, { key: 'Enter' });
        save(dlg);
        await waitFor(() => expect(saves(calls)[0]?.body).toMatchObject({ site_id: 2 }));
    });
    it('offers to create a studio that does not exist yet', async () => {
        const calls = mount();
        const dlg = await open();
        fireEvent.change(within(dlg).getByRole('combobox'), { target: { value: 'Brand New' } });
        const opt = await within(dlg).findByRole('option', { name: /Create new studio “Brand New”/ });
        fireEvent.mouseDown(opt);
        save(dlg);
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(saves(calls)[0].body).toMatchObject({ field: 'site', site_id: null, target_name: 'Brand New' });
    });
    it('removing the studio clears it explicitly', async () => {
        const calls = mount();
        const dlg = await open();
        fireEvent.click(within(dlg).getByRole('button', { name: 'Remove studio' }));
        expect(within(dlg).getByText('No studio selected.')).toBeInTheDocument();
        save(dlg);
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(saves(calls)[0].body).toMatchObject({ field: 'site', site_id: null, target_name: '' });
    });
    it('says what was already saved when the studio save fails after the title', async () => {
        const calls = mount((n) => (n === 1 ? json({ ok: true }) : json({ detail: 'Studio table locked' }, 500)));
        const dlg = await open();
        fireEvent.change(within(dlg).getByDisplayValue('Scene'), { target: { value: 'New' } });
        fireEvent.click(within(dlg).getByRole('button', { name: 'Remove studio' }));
        save(dlg);
        await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Studio table locked (Already saved: title.)'));
        expect(saves(calls).map((c) => c.body.field)).toEqual(['title', 'site']);
        expect(screen.getByRole('dialog', { name: 'Correct video metadata' })).toBeInTheDocument();
    });
});
