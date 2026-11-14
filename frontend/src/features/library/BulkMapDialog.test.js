import { jsx as _jsx } from "react/jsx-runtime";
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { SelectionBar } from './SelectionBar';
import { useSelection } from './selection';
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(() => 'id'), { error: vi.fn(), success: vi.fn(), loading: vi.fn(() => 'p'), dismiss: vi.fn() }), Toaster: () => null }));
beforeAll(() => {
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
    HTMLElement.prototype.hidePopover = function () { };
});
beforeEach(() => useSelection.getState().exit());
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });
const mk = (id, extra = {}) => ({ id, _cid: '-1', media_id: id, name: `V${id}`, thumbnail_id: id, performers: [], studios: [], tags: [], studio: '', ...extra });
const VISIBLE = [mk(1, { performers: ['Ava Stone'], studios: ['Northlight'], studio: 'Northlight' }), mk(2)];
const json = (b, code = 200) => new Response(JSON.stringify(b), { status: code });
const PERFORMERS = [{ id: 7, name: 'Ava Stone', video_count: 4 }, { id: 8, name: 'Avery', video_count: 1 }];
const PROVIDER = [{ name: 'Ava Stone', provider: 'stashdb', provider_id: 'sd-1', image: 'i.png', profile: 'p', exact: true }];
const STUDIOS = [{ id: 1, name: 'Northlight', video_count: 15 }];
const mount = (mapping = () => json({ ok: true, changed: 2 })) => {
    const calls = [];
    let n = 0;
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
        calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
        if (url.startsWith('/api/performers/provider-search'))
            return json(PROVIDER);
        if (url.startsWith('/api/performers?'))
            return json(PERFORMERS);
        if (url.startsWith('/api/studios?'))
            return json(STUDIOS);
        if (url === '/api/mapping/bulk')
            return mapping(++n);
        return json({ ok: true });
    }));
    act(() => { useSelection.getState().enter(); useSelection.getState().toggle('-1', 1); useSelection.getState().toggle('-1', 2); });
    render(_jsx(QueryClientProvider, { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }), children: _jsx(SelectionBar, { visible: VISIBLE }) }));
    return calls;
};
const item = (menu, name) => within(screen.getByRole('menu', { name, hidden: true })).getByRole('menuitem', { name: menu, hidden: true });
const saves = (c) => c.filter((x) => x.url === '/api/mapping/bulk');
const opt = (dlg, name) => within(dlg).findByRole('option', { name });
describe('bulk Set Performer', () => {
    it('lists local performers and API matches, and links a local one by id on click', async () => {
        const calls = mount();
        fireEvent.click(item('Set Performer', 'Performer / Studio'));
        const dlg = await screen.findByRole('dialog', { name: 'Select performer' });
        const local = await opt(dlg, /Avery/);
        expect(local).toHaveTextContent('Local performer · 1 video · link to this performer');
        expect(saves(calls)).toHaveLength(0);
        fireEvent.change(within(dlg).getByRole('combobox'), { target: { value: 'ava' } });
        expect(await opt(dlg, /API match · StashDB · exact name/)).toBeInTheDocument();
        fireEvent.change(within(dlg).getByRole('combobox'), { target: { value: '' } });
        fireEvent.mouseDown(await opt(dlg, /Avery/));
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(saves(calls)[0].body).toMatchObject({ field: 'performer', performer_id: 8, target_name: 'Avery', action: 'move', lock: true, provider: null });
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Avery linked to 2 selected videos.'));
        expect(useSelection.getState().picked).toEqual({});
        await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    });
    it('an API match carries its provider identity', async () => {
        const calls = mount();
        fireEvent.click(item('Set Performer', 'Performer / Studio'));
        const dlg = await screen.findByRole('dialog', { name: 'Select performer' });
        fireEvent.change(within(dlg).getByRole('combobox'), { target: { value: 'ava' } });
        fireEvent.mouseDown(await opt(dlg, /API match/));
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(saves(calls)[0].body).toMatchObject({ performer_id: null, provider: 'stashdb', provider_id: 'sd-1', image: 'i.png', profile: 'p' });
    });
    it('only asks the provider after two characters', async () => {
        const calls = mount();
        fireEvent.click(item('Set Performer', 'Performer / Studio'));
        const dlg = await screen.findByRole('dialog', { name: 'Select performer' });
        await opt(dlg, /Avery/);
        expect(calls.filter((c) => c.url.startsWith('/api/performers/provider-search'))).toHaveLength(0);
        fireEvent.change(within(dlg).getByRole('combobox'), { target: { value: 'a' } });
        await waitFor(() => expect(calls.some((c) => c.url.includes('q=a'))).toBe(true));
        expect(calls.filter((c) => c.url.startsWith('/api/performers/provider-search'))).toHaveLength(0);
        fireEvent.change(within(dlg).getByRole('combobox'), { target: { value: 'av' } });
        await waitFor(() => expect(calls.filter((c) => c.url.startsWith('/api/performers/provider-search'))).toHaveLength(1));
    });
    it('shows the server reason and keeps the dialog and selection when it fails', async () => {
        mount(() => json({ detail: 'Performer not found.' }, 404));
        fireEvent.click(item('Set Performer', 'Performer / Studio'));
        const dlg = await screen.findByRole('dialog', { name: 'Select performer' });
        fireEvent.mouseDown(await opt(dlg, /Avery/));
        await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Could not map performer: Performer not found.'));
        expect(screen.getByRole('dialog', { name: 'Select performer' })).toBeInTheDocument();
        expect(Object.keys(useSelection.getState().picked)).toHaveLength(2);
    });
});
describe('bulk Set Studio', () => {
    it('links an existing studio by id, or creates a new one with no id', async () => {
        const calls = mount();
        fireEvent.click(item('Set Studio', 'Performer / Studio'));
        const dlg = await screen.findByRole('dialog', { name: 'Select studio' });
        fireEvent.change(within(dlg).getByRole('combobox'), { target: { value: 'Brand New' } });
        fireEvent.mouseDown(await opt(dlg, /Create new studio “Brand New”/));
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(saves(calls)[0].body).toMatchObject({ field: 'site', site_id: null, target_name: 'Brand New', lock: true });
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Brand New linked to 2 videos.'));
    });
});
describe('bulk Set Performer + Studio', () => {
    it('needs both, prefills from the first picked video, and saves once', async () => {
        const calls = mount();
        fireEvent.click(item('Set Performer + Studio', 'Performer / Studio'));
        const dlg = await screen.findByRole('dialog', { name: 'Map performer + studio' });
        expect(within(dlg).getByText('Ava Stone', { selector: 'span.truncate' })).toBeInTheDocument();
        expect(within(dlg).getByText('Northlight', { selector: 'span.truncate' })).toBeInTheDocument();
        fireEvent.click(within(dlg).getByRole('button', { name: 'Remove studio' }));
        expect(within(dlg).getByRole('button', { name: 'Save performer + studio' })).toBeDisabled();
        fireEvent.mouseDown(await opt(within(dlg).getByRole('region', { name: 'Studio' }), /Northlight/));
        const save = within(dlg).getByRole('button', { name: 'Save performer + studio' });
        expect(save).toBeEnabled();
        fireEvent.click(save);
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(saves(calls)[0].body).toMatchObject({ field: 'performer_site', site_id: 1, studio_name: 'Northlight', performer_name: 'Ava Stone', lock: true });
        await waitFor(() => expect(toast).toHaveBeenCalledWith('2 videos mapped to performer + studio.'));
    });
});
describe('bulk Apply All Metadata', () => {
    it('is disabled until something is entered, then applies just the title', async () => {
        const calls = mount();
        fireEvent.click(item('Apply All Metadata', 'Metadata'));
        const dlg = await screen.findByRole('dialog', { name: 'Apply metadata' });
        const apply = within(dlg).getByRole('button', { name: 'Apply to selected videos' });
        expect(apply).toBeDisabled();
        expect(within(dlg).queryByText('Ava Stone', { selector: 'span.truncate' })).toBeNull(); // starts empty: only what you pick is applied
        fireEvent.change(within(dlg).getByPlaceholderText("Leave blank to keep each video's title"), { target: { value: 'Everything' } });
        expect(apply).toBeEnabled();
        fireEvent.click(apply);
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(saves(calls)[0].body).toMatchObject({ field: 'title', target_name: 'Everything' });
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Title applied to 2 videos.'));
    });
    it('applies the title first, then the studio', async () => {
        const calls = mount();
        fireEvent.click(item('Apply All Metadata', 'Metadata'));
        const dlg = await screen.findByRole('dialog', { name: 'Apply metadata' });
        fireEvent.change(within(dlg).getByPlaceholderText("Leave blank to keep each video's title"), { target: { value: 'T' } });
        fireEvent.mouseDown(await opt(within(dlg).getByRole('region', { name: 'Studio' }), /Northlight/));
        fireEvent.click(within(dlg).getByRole('button', { name: 'Apply to selected videos' }));
        await waitFor(() => expect(saves(calls)).toHaveLength(2));
        expect(saves(calls).map((c) => c.body.field)).toEqual(['title', 'site']);
        await waitFor(() => expect(toast).toHaveBeenCalledWith('2 videos updated.'));
    });
    it('says the title may already be saved if the second request fails', async () => {
        mount((n) => (n === 1 ? json({ ok: true, changed: 2 }) : json({ detail: 'Studio table locked' }, 500)));
        fireEvent.click(item('Apply All Metadata', 'Metadata'));
        const dlg = await screen.findByRole('dialog', { name: 'Apply metadata' });
        fireEvent.change(within(dlg).getByPlaceholderText("Leave blank to keep each video's title"), { target: { value: 'T' } });
        fireEvent.mouseDown(await opt(within(dlg).getByRole('region', { name: 'Studio' }), /Northlight/));
        fireEvent.click(within(dlg).getByRole('button', { name: 'Apply to selected videos' }));
        await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Studio table locked (The title may already be saved.)'));
        expect(screen.getByRole('dialog', { name: 'Apply metadata' })).toBeInTheDocument();
    });
});
