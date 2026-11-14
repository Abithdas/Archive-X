import { jsx as _jsx } from "react/jsx-runtime";
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { withRouter } from '@/test/withRouter';
import { StudioPage } from './StudioPage';
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }));
beforeAll(() => {
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
});
afterEach(() => vi.unstubAllGlobals());
const vid = (id, performers, studios = ['Northlight']) => ({ id, source_channel: '-100', title: `Scene ${id}`, filename: `f${id}.mp4`, size: 1, duration: 60, width: 1920, height: 1080, resolution_category: '1080p', thumbnail_id: id, favorite: 0, upload_date: null, channel_title: 'Main', performers, tags: [], studio: 'Northlight', studios });
const json = (b, code = 200) => new Response(JSON.stringify(b), { status: code });
const META = { providers: [{ provider: 'stashdb', normalized: JSON.stringify({ details: { country: 'US', founded_year: 1999, instagram: 'https://instagram.com/north' } }) }] };
const SUGGESTED = [{ suggestion_id: 11, channel_id: '-1001', message_id: 77, title: 'Maybe North', duration: 125, size: 5242880 }];
const detail = (site = {}, videos = [vid(1, ['Mia'], ['Northlight', 'Aurora']), vid(2, ['Mia', 'Zed'])]) => ({ site: { id: 3, name: 'Northlight', image: null, bio: '', provider_ids: {}, favorite: false, ...site }, videos, site_url: 'https://north.example' });
const mount = (body = detail(), code = 200) => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
        calls.push({ url, method: init?.method ?? 'GET' });
        if (url === '/api/studios/3/metadata')
            return json(META);
        if (url === '/api/studios/3/family')
            return json({ related: [{ id: 9, name: 'Sub Label', relation: 'Child' }] });
        if (url === '/api/studios/3/career-counts')
            return json({ career_counts: [{ provider: 'StashDB', count: 120 }] });
        if (url === '/api/studios/3/suggested')
            return json(SUGGESTED.slice());
        if (url.startsWith('/api/suggestions/') && init?.method === 'POST') {
            SUGGESTED.length = 0;
            return json({ ok: true });
        }
        if (url.startsWith('/api/entity-lookup?kind=studios'))
            return json([{ id: 3, name: 'Northlight' }, { id: 5, name: 'Aurora' }]);
        if (url === '/api/entities/studio/3/favorite')
            return new Response('{"ok":true,"favorite":true}', { status: 200 });
        if (url.startsWith('/api/entity-lookup?kind=performers'))
            return new Response('[{"id":8,"name":"Mia","video_count":2}]', { status: 200 });
        if (init?.method === 'POST')
            return new Response('{"ok":true,"player":"MPC-HC","count":2}', { status: 200 });
        return new Response(JSON.stringify(body), { status: code });
    }));
    withRouter(_jsx(StudioPage, { id: 3 }));
    return calls;
};
describe('StudioPage', () => {
    it('shows the studio, its counts and its videos', async () => {
        mount();
        expect(await screen.findByRole('heading', { level: 1, name: 'Northlight' })).toBeInTheDocument();
        expect(screen.getAllByText('2 videos').length).toBeGreaterThan(0);
        expect(screen.getByText('2 performers')).toBeInTheDocument();
        expect(screen.getAllByText('Local profile').length).toBeGreaterThan(0);
        expect(screen.getByRole('article', { name: 'Scene 1' })).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Open Official website' })).toHaveAttribute('href', 'https://north.example/');
    });
    it('collapses a long bio and shows provider pills', async () => {
        mount(detail({ bio: 'x'.repeat(400), provider_ids: { stashdb: 'abc' } }));
        expect(await screen.findByText('StashDB')).toBeInTheDocument();
        expect(screen.getByText('Metadata linked')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Read more' }));
        expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument();
    });
    it('lists performers on their tab, most videos first, linking known ones', async () => {
        mount();
        fireEvent.click(await screen.findByRole('tab', { name: /Performers/ }));
        const panel = await screen.findByRole('tabpanel', { name: 'Performers' });
        const names = Array.from(panel.querySelectorAll('li')).map((li) => li.textContent);
        expect(names[0]).toContain('Mia');
        expect(names[1]).toContain('Zed');
        await waitFor(() => expect(screen.getByRole('link', { name: /Mia/ })).toHaveAttribute('href', '/performers/8'));
        expect(screen.queryByRole('link', { name: /Zed/ })).not.toBeInTheDocument();
    });
    it('toggles the favourite using the server answer', async () => {
        const calls = mount();
        const header = await screen.findByRole('region', { name: 'Studio profile' });
        const btn = within(header).getByRole('button', { name: 'Add to favourites' });
        fireEvent.click(btn);
        await waitFor(() => expect(calls.some((c) => c.url === '/api/entities/studio/3/favorite' && c.method === 'POST')).toBe(true));
        await waitFor(() => expect(within(header).getByRole('button', { name: 'Remove from favourites' })).toHaveAttribute('aria-pressed', 'true'));
    });
    it('shuffles this studio\'s videos in MPC-HC', async () => {
        const calls = mount();
        fireEvent.click(await screen.findByRole('button', { name: 'Shuffle in MPC-HC' }));
        await waitFor(() => expect(calls.some((c) => c.url === '/api/play/external' && c.method === 'POST')).toBe(true));
    });
    it('opens the editor', async () => {
        mount();
        fireEvent.click(await screen.findByRole('button', { name: 'Edit studio details' }));
        expect(await screen.findByRole('dialog')).toBeInTheDocument();
    });
    it('says so when the studio does not exist, without a retry button', async () => {
        mount({ detail: 'Not found' }, 404);
        expect(await screen.findByText('Studio not found.')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    });
    it('shows Details with provider facts, career counts, related studios and links', async () => {
        mount();
        const card = await screen.findByRole('region', { name: 'Studio details' });
        expect(await within(card).findByText('US')).toBeInTheDocument();
        expect(within(card).getByText('1999')).toBeInTheDocument();
        expect(within(card).getByText('Scenes listed by StashDB')).toBeInTheDocument();
        expect(within(card).getByText('120')).toBeInTheDocument();
        expect(within(card).getByText('STASHDB')).toBeInTheDocument();
        expect(within(card).getByRole('link', { name: /Sub Label/ })).toHaveAttribute('href', '/studios/9');
        const social = screen.getByLabelText('Website and social links');
        await waitFor(() => expect(within(social).getByRole('link', { name: 'Open Instagram' })).toHaveAttribute('href', 'https://instagram.com/north'));
        expect(within(social).getByRole('link', { name: 'Open Official website' })).toBeInTheDocument();
    });
    it('lists similar studios from shared videos, linking to their pages', async () => {
        mount();
        fireEvent.click(await screen.findByRole('tab', { name: /Similar Studios/ }));
        const panel = await screen.findByRole('tabpanel', { name: 'Similar Studios' });
        const link = await within(panel).findByRole('link', { name: /Aurora/ });
        expect(link).toHaveAttribute('href', '/studios/5');
        expect(link).toHaveTextContent('1 video');
        expect(within(panel).queryByRole('link', { name: /Northlight/ })).not.toBeInTheDocument();
    });
    it('links or dismisses a suggested video, only when asked', async () => {
        SUGGESTED.splice(0, SUGGESTED.length, { suggestion_id: 11, channel_id: '-1001', message_id: 77, title: 'Maybe North', duration: 125, size: 5242880 });
        const calls = mount();
        const tab = await screen.findByRole('tab', { name: /Suggestions/ });
        await waitFor(() => expect(tab).toHaveTextContent('1'));
        fireEvent.click(tab);
        const panel = await screen.findByRole('tabpanel', { name: 'Suggestions' });
        expect(await within(panel).findByText('1 pending')).toBeInTheDocument();
        expect(within(panel).getByText('2:05 · 5 MB')).toBeInTheDocument();
        expect(within(panel).getByText('Does this video belong to Northlight?')).toBeInTheDocument();
        expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0);
        fireEvent.click(within(panel).getByRole('button', { name: /Yes, link studio/ }));
        await waitFor(() => expect(calls.find((c) => c.url === '/api/suggestions/11/confirm-entity')).toMatchObject({ method: 'POST' }));
        expect(await screen.findByText('No pending suggestions')).toBeInTheDocument();
    });
    it('says plainly when nothing is suggested', async () => {
        SUGGESTED.length = 0;
        mount();
        fireEvent.click(await screen.findByRole('tab', { name: /Suggestions/ }));
        expect(await screen.findByText('No pending suggestions')).toBeInTheDocument();
    });
});
describe('StudioPage profile card', () => {
    it('has a back button and a Studios › name trail', async () => {
        mount();
        const nav = await screen.findByRole('navigation', { name: 'Breadcrumb' });
        expect(within(nav).getByRole('button', { name: 'Go back' })).toBeInTheDocument();
        expect(nav).toHaveTextContent('Studios › Northlight');
    });
    it('offers Edit multiple videos above the video grid', async () => {
        mount();
        expect(await screen.findByText('Videos linked to this studio')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Edit multiple videos' }));
        expect(screen.getByRole('toolbar', { name: 'Edit multiple videos' })).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /Selecting · 0/ }));
    });
});
describe('StudioPage suggestion preview', () => {
    it('shows four verification frames, then links from the dialog', async () => {
        SUGGESTED.length = 0;
        SUGGESTED.push({ suggestion_id: 11, channel_id: '-1001', message_id: 77, title: 'Maybe North', duration: 125, size: 5242880 });
        const calls = mount();
        fireEvent.click(await screen.findByRole('tab', { name: /Suggestions/ }));
        fireEvent.click(await screen.findByRole('button', { name: 'Check frames: Maybe North' }));
        const dialog = await screen.findByRole('dialog', { name: 'Maybe North' });
        const imgs = within(dialog).getAllByRole('img');
        expect(imgs.map((i) => i.getAttribute('src'))).toEqual([3, 12, 21, 30].map((n) => `/api/play/preview-frame/-1001/77/${n}?v=8`));
        expect(within(dialog).getByText('Does this video belong to Northlight? Confirming links only this studio. Use Edit Video or Scene Match Review for full scene identification.')).toBeInTheDocument();
        // a snapshot that will not load is replaced by a live frame at the matching time
        fireEvent.error(imgs[1]);
        await waitFor(() => expect(within(dialog).getAllByRole('img')[1].getAttribute('src')).toBe('/api/play/preview/-1001/77?t=43.750&w=480&pv=17'));
        fireEvent.click(within(dialog).getByRole('button', { name: 'Yes, link studio' }));
        await waitFor(() => expect(calls.find((c) => c.url === '/api/suggestions/11/confirm-entity')).toMatchObject({ method: 'POST' }));
        await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Maybe North' })).not.toBeInTheDocument());
    });
});
