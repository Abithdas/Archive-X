import { jsx as _jsx } from "react/jsx-runtime";
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { AutoMatchReview } from './AutoMatchReview';
import { attention } from './ops';
const toast = vi.hoisted(() => Object.assign(vi.fn(), { error: vi.fn() }));
vi.mock('sonner', () => ({ toast, Toaster: () => null }));
beforeAll(() => {
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
});
afterEach(() => { vi.unstubAllGlobals(); toast.mockClear(); });
const json = (b, code = 200) => new Response(JSON.stringify(b), { status: code });
const item = (n) => ({ video_id: n, channel_id: '-1001', message_id: n, filename: `f${n}.mp4`, applied_title: `Title ${n}`, applied_studio: 'Studio', applied_performers: ['Ava'], reason: `Matched ${n}` });
const mount = (list = [item(1), item(2), item(3)], over = () => undefined) => {
    const calls = [];
    let current = list;
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
        calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
        const o = over(url, init);
        if (o)
            return o;
        if (url === '/api/metadata/auto-match-review')
            return json({ count: current.length, items: current });
        if (url === '/api/metadata/auto-match-review/bulk') {
            current = [];
            return json({ ok: true, processed: 2, skipped: 1, failed: 0 });
        }
        if (url.startsWith('/api/metadata/auto-match-review/')) {
            const id = Number(url.split('/').pop());
            current = current.filter((x) => x.video_id !== id);
            return json({ ok: true });
        }
        return json({});
    }));
    render(_jsx(QueryClientProvider, { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }), children: _jsx(AutoMatchReview, { onClose: () => { } }) }));
    return calls;
};
const posts = (c) => c.filter((x) => x.method === 'POST');
describe('AutoMatchReview', () => {
    it('lists the matches with what was applied and why', async () => {
        mount();
        expect(await screen.findByText('Applied: Title 1 · Studio · Ava')).toBeInTheDocument();
        expect(screen.getByText('Matched 2')).toBeInTheDocument();
    });
    it('keeps or undoes one match and removes it from the list', async () => {
        const calls = mount();
        fireEvent.click(await screen.findByRole('button', { name: 'Undo match for f1.mp4' }));
        await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/metadata/auto-match-review/1', body: { action: 'undo' } }));
        await waitFor(() => expect(screen.queryByText('f1.mp4')).not.toBeInTheDocument());
        expect(toast).toHaveBeenCalledWith('Match removed; it is now a suggestion.');
        fireEvent.click(screen.getByRole('button', { name: 'Keep match for f2.mp4' }));
        await waitFor(() => expect(posts(calls)[1].body).toEqual({ action: 'keep' }));
        expect(toast).toHaveBeenCalledWith('Match kept.');
    });
    it('needs a selection for bulk actions, then sends the chosen ids and reports the counts', async () => {
        const calls = mount();
        await screen.findByText('f1.mp4');
        fireEvent.click(screen.getByRole('button', { name: 'Keep selected' }));
        expect(toast).toHaveBeenCalledWith('Select at least one video first.');
        expect(posts(calls)).toHaveLength(0);
        fireEvent.click(screen.getByLabelText('Select f1.mp4 for bulk review'));
        fireEvent.click(screen.getByLabelText('Select f3.mp4 for bulk review'));
        expect(screen.getByText('2 selected')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Undo selected' }));
        await waitFor(() => expect(posts(calls)[0]).toMatchObject({ url: '/api/metadata/auto-match-review/bulk', body: { action: 'undo', video_ids: [1, 3] } }));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Bulk undo: 2 updated, 1 skipped, 0 failed.'));
        expect(await screen.findByText('Nothing left to review.')).toBeInTheDocument();
    });
    it('"Select first 100" picks at most 100 and says so', async () => {
        mount(Array.from({ length: 120 }, (_, i) => item(i + 1)));
        await screen.findByText('f1.mp4');
        fireEvent.click(screen.getByLabelText('Select first 100'));
        expect(screen.getByText('100 selected')).toBeInTheDocument();
        expect(toast).toHaveBeenCalledWith('Selected the first 100 items. Review bulk actions are limited to 100 at a time.');
    });
    it('shows the server\'s reason when one update fails and keeps the row', async () => {
        mount([item(1)], (_u, i) => (i?.method === 'POST' ? json({ detail: 'Video changed since' }, 409) : undefined));
        fireEvent.click(await screen.findByRole('button', { name: 'Keep match for f1.mp4' }));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Video changed since'));
        expect(screen.getByText('f1.mp4')).toBeInTheDocument();
    });
    it('says plainly when there is nothing to review', async () => {
        mount([]);
        expect(await screen.findByText('Nothing left to review.')).toBeInTheDocument();
    });
});
describe('attention item', () => {
    it('appears when matches wait, before the all-clear, and points at the review', () => {
        expect(attention({ telegram_connected: true }, 2)).toEqual([{ key: 'automatic-matches-review', tone: 'warn', text: '2 earlier automatic matches to review — open', action: 'auto-match' }]);
        expect(attention({ telegram_connected: true }, 0)[0].key).toBe('all-clear');
    });
});
