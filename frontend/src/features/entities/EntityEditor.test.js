import { jsx as _jsx } from "react/jsx-runtime";
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { EntityEditor } from './EntityEditor';
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(() => 'id'), { error: vi.fn(), success: vi.fn(), loading: vi.fn(() => 'p'), dismiss: vi.fn() }), Toaster: () => null }));
beforeAll(() => {
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });
const json = (b, code = 200) => new Response(JSON.stringify(b), { status: code });
const CANDS = [{ url: 'https://i/a.jpg', provider: 'theporndb', width: 800, height: 1200 }, { url: 'https://i/b.jpg', provider: 'stashdb' }];
const mount = (kind = 'performers', o = {}) => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
        calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
        if (url.includes('/image-candidates'))
            return (o.candidates ?? (() => json({ candidates: CANDS, has_more: false })))();
        if (url.endsWith('/edit') && init?.method === 'POST')
            return (o.save ?? (() => json({ ok: true })))();
        if (url.endsWith('/edit'))
            return (o.edit ?? (() => json({ ok: true, name: 'Ava Stone', image: 'x.jpg', image_source: 'provider', image_locked: false, profile: 'https://p/ava', bio: 'Hello', ...o.entity })))();
        return json({ ok: true });
    }));
    const onDone = vi.fn();
    render(_jsx(QueryClientProvider, { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }), children: _jsx(EntityEditor, { kind: kind, id: 2, onDone: onDone }) }));
    return { calls, onDone };
};
const saves = (c) => c.filter((x) => x.url.endsWith('/edit') && x.method === 'POST');
const open = async () => { const d = await screen.findByRole('dialog', { name: /Profile picture|Studio image/ }); await screen.findByText('Ava Stone'); return d; };
const saveBtn = () => screen.getByRole('button', { name: /^Save (profile picture|studio image)$/ });
describe('Edit performer', () => {
    it('opens with the current name, loads provider images by itself, and shows their groups', async () => {
        const { calls } = mount();
        const d = await open();
        expect(await within(d).findByText('THEPORNDB: 1 · STASHDB: 1')).toBeInTheDocument();
        expect(within(d).getByRole('button', { name: /option 1: ThePornDB · 800×1200/ })).toBeInTheDocument();
        expect(within(d).getByRole('button', { name: /option 2: STASHDB/ })).toBeInTheDocument();
        expect(within(d).getByText('Current provider image')).toBeInTheDocument();
        expect(calls.find((c) => c.url.includes('image-candidates')).url).toBe('/api/performers/2/image-candidates?limit=10&offset=0');
        expect(saves(calls)).toHaveLength(0);
    });
    it('says the current picture is locked when it is', async () => {
        mount('performers', { entity: { image_locked: true } });
        await open();
        expect(await screen.findByText('Locked current choice')).toBeInTheDocument();
    });
    it('saving without changing the picture sends the name and details and no new image', async () => {
        const { calls, onDone } = mount();
        await open();
        fireEvent.click(saveBtn());
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(saves(calls)[0].url).toBe('/api/performers/2/edit');
        expect(saves(calls)[0].body).toEqual({ name: 'Ava Stone', image_url: '', reset_image_to_api: false, profile: 'https://p/ava', bio: 'Hello', gender: '' });
        await waitFor(() => expect(onDone).toHaveBeenCalled());
        expect(toast).toHaveBeenCalledWith('Profile picture saved.');
    });
    it('choosing a provider image previews it and saves it', async () => {
        const { calls } = mount();
        const d = await open();
        fireEvent.click(await within(d).findByRole('button', { name: /option 1/ }));
        expect(within(d).getByText('Selected THEPORNDB image — save to apply')).toBeInTheDocument();
        expect(within(d).getByRole('img', { name: 'Profile picture preview' })).toHaveAttribute('src', 'https://i/a.jpg');
        expect(within(d).getByRole('button', { name: /option 1/ })).toHaveAttribute('aria-pressed', 'true');
        fireEvent.click(saveBtn());
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(saves(calls)[0].body).toMatchObject({ provider_image_url: 'https://i/a.jpg', provider_image_provider: 'theporndb' });
        expect(toast).toHaveBeenCalledWith('Selected profile image saved.');
    });
    it('a typed image URL replaces a chosen provider image', async () => {
        const { calls } = mount();
        const d = await open();
        fireEvent.click(await within(d).findByRole('button', { name: /option 1/ }));
        fireEvent.change(within(d).getByLabelText('Image URL'), { target: { value: ' https://m/x.png ' } });
        expect(within(d).getByRole('button', { name: /option 1/ })).toHaveAttribute('aria-pressed', 'false');
        fireEvent.click(saveBtn());
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(saves(calls)[0].body).toMatchObject({ image_url: 'https://m/x.png' });
        expect(saves(calls)[0].body).not.toHaveProperty('provider_image_url');
    });
    it('"Use provider image again" asks the server to restore it', async () => {
        const { calls } = mount();
        const d = await open();
        fireEvent.click(within(d).getByRole('button', { name: 'Use provider image again' }));
        expect(within(d).getByText('Provider image will be restored on save')).toBeInTheDocument();
        fireEvent.click(saveBtn());
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(saves(calls)[0].body).toMatchObject({ reset_image_to_api: true, image_url: '' });
        expect(toast).toHaveBeenCalledWith('Provider profile image restored.');
    });
    it('a chosen file is sent as image data, and one over 8 MiB is refused', async () => {
        const { calls } = mount();
        const d = await open();
        const input = within(d).getByLabelText('Choose an image from this PC');
        const big = new File(['x'], 'big.png', { type: 'image/png' });
        Object.defineProperty(big, 'size', { value: 9 * 1024 * 1024 });
        fireEvent.change(input, { target: { files: [big] } });
        expect(toast).toHaveBeenCalledWith('Image must be 8 MiB or smaller.');
        fireEvent.change(input, { target: { files: [new File(['abc'], 'a.png', { type: 'image/png' })] } });
        expect(await within(d).findByText('Local image selected — save to apply')).toBeInTheDocument();
        fireEvent.click(saveBtn());
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(String(saves(calls)[0].body.image_data)).toMatch(/^data:/);
    });
    it('changes the name, profile and biography', async () => {
        const { calls } = mount();
        const d = await open();
        fireEvent.click(within(d).getByText('Profile details'));
        fireEvent.change(within(d).getByLabelText('Name'), { target: { value: ' Ava S. ' } });
        fireEvent.change(within(d).getByLabelText('Biography'), { target: { value: 'New bio' } });
        fireEvent.change(within(d).getByLabelText('Profile URL'), { target: { value: 'https://new' } });
        fireEvent.click(saveBtn());
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(saves(calls)[0].body).toMatchObject({ name: 'Ava S.', bio: 'New bio', profile: 'https://new' });
    });
    it('will not save without a name', async () => {
        const { calls } = mount();
        const d = await open();
        fireEvent.click(within(d).getByText('Profile details'));
        fireEvent.change(within(d).getByLabelText('Name'), { target: { value: '  ' } });
        fireEvent.submit(d.querySelector('form'));
        expect(await within(d).findByText('Enter a name.')).toBeInTheDocument();
        expect(saves(calls)).toHaveLength(0);
    });
    it('shows the server reason and stays open when saving fails', async () => {
        const { onDone } = mount('performers', { save: () => json({ detail: 'Image could not be fetched' }, 400) });
        await open();
        fireEvent.click(saveBtn());
        await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Save failed: Image could not be fetched'));
        expect(onDone).not.toHaveBeenCalled();
        expect(saveBtn()).toBeEnabled();
    });
    it('explains when the provider lookup fails and still allows a manual image', async () => {
        mount('performers', { candidates: () => json({ detail: 'No provider token' }, 400) });
        const d = await open();
        expect(await within(d).findByText('Provider image lookup failed: No provider token')).toBeInTheDocument();
        expect(within(d).getByLabelText('Image URL')).toBeEnabled();
    });
    it('loads more images when the server has more', async () => {
        let page = 0;
        const { calls } = mount('performers', { candidates: () => (page++ === 0 ? json({ candidates: [CANDS[0]], has_more: true }) : json({ candidates: [CANDS[1]], has_more: false })) });
        const d = await open();
        fireEvent.click(await within(d).findByRole('button', { name: 'Load more images' }));
        expect(await within(d).findByRole('button', { name: /option 2: STASHDB/ })).toBeInTheDocument();
        expect(calls.filter((c) => c.url.includes('image-candidates')).map((c) => c.url.split('offset=')[1])).toEqual(['0', '10']);
        expect(within(d).queryByRole('button', { name: 'Load more images' })).toBeNull();
    });
    it('closes without saving when the entity cannot be loaded', async () => {
        const { onDone } = mount('performers', { edit: () => json({ detail: 'Performer not found' }, 404) });
        await waitFor(() => expect(onDone).toHaveBeenCalled());
        expect(toast.error).toHaveBeenCalledWith('Could not load entity: Performer not found');
    });
});
describe('Edit studio', () => {
    it('uses the studio endpoints, wording and fields', async () => {
        const { calls } = mount('studios', { entity: { name: 'Ava Stone' } });
        const d = await screen.findByRole('dialog', { name: 'Studio image' });
        await screen.findByText('Ava Stone');
        fireEvent.click(within(d).getByText('Profile details'));
        expect(within(d).queryByLabelText('Biography')).toBeNull();
        expect(within(d).queryByLabelText('Profile URL')).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Save studio image' }));
        await waitFor(() => expect(saves(calls)).toHaveLength(1));
        expect(saves(calls)[0].url).toBe('/api/studios/2/edit');
        expect(saves(calls)[0].body).toEqual({ name: 'Ava Stone', image_url: '', reset_image_to_api: false });
    });
});
