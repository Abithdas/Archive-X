import { jsx as _jsx } from "react/jsx-runtime";
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { withRouter } from '@/test/withRouter';
import { FaceReview, differentFaces, reviewUrl } from './FaceReview';
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }));
beforeAll(() => { HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); }; HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); }; });
afterEach(() => vi.unstubAllGlobals());
const json = (b) => new Response(JSON.stringify(b), { status: 200 });
const FACES = Array.from({ length: 24 }, (_, i) => ({ detection_id: i + 1, photo_id: i + 1, group_id: 1, crop_url: `/c/${i + 1}`, similarity: i < 4 ? 0.2 + i * 0.02 : 0.6 + i * 0.01 }));
const mount = (onClose = () => { }) => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
        calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
        if (url.startsWith('/api/photos/faces/review'))
            return json({ ok: true, total: FACES.length, items: FACES });
        if (url.startsWith('/api/photos/faces/performers/provider-search'))
            return json({ ok: true, items: [], configured: true });
        if (url.startsWith('/api/photos/faces/performers'))
            return json({ items: [{ id: 9, name: 'Zoe Ray' }] });
        return json({ ok: true, moved: 10, group_id: 99 });
    }));
    withRouter(_jsx(FaceReview, { performerId: 3, label: "Ava Stone", onClose: onClose }));
    return calls;
};
describe('review helpers', () => {
    it('builds the review address for a performer or for face groups', () => {
        expect(reviewUrl(3, [])).toBe('/api/photos/faces/review?performer_id=3');
        expect(reviewUrl(null, [4, 5])).toBe('/api/photos/faces/review?group_ids=4,5');
    });
    it('flags faces that look different from the rest', () => { expect(differentFaces(FACES).length).toBe(4); });
});
describe('Check faces', () => {
    it('shows every face and takes the wrong ones out together, in one request', async () => {
        const calls = mount();
        const grid = await screen.findByRole('group', { name: 'Faces' });
        expect(within(grid).getAllByRole('checkbox')).toHaveLength(24);
        fireEvent.click(screen.getByRole('button', { name: /Tick the 10 least alike/ }));
        expect(screen.getByText('10 ticked')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Not Ava Stone (10)' }));
        await waitFor(() => expect(calls.find((c) => c.url === '/api/photos/faces/detections/move')?.body).toEqual({ detection_ids: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], performer_id: null }));
        await waitFor(() => expect(vi.mocked(toast)).toHaveBeenCalledWith(expect.stringContaining('Took 10 faces out of Ava Stone')));
    });
    it('ticks only the faces that look different with one click', async () => {
        mount();
        await screen.findByRole('group', { name: 'Faces' });
        fireEvent.click(screen.getByRole('button', { name: /Tick all that look different \(4\)/ }));
        expect(screen.getByText('4 ticked')).toBeInTheDocument();
    });
    it('can move the ticked faces straight to the right person', async () => {
        const calls = mount();
        await screen.findByRole('group', { name: 'Faces' });
        fireEvent.click(screen.getByRole('checkbox', { name: 'Face 2, looks different' }));
        fireEvent.click(screen.getByRole('checkbox', { name: 'Face 3, looks different' }));
        fireEvent.click(screen.getByRole('button', { name: 'Move to someone else…' }));
        fireEvent.change(screen.getByRole('searchbox', { name: 'Performer name' }), { target: { value: 'zoe' } });
        fireEvent.click(await screen.findByRole('option', { name: /Zoe Ray/ }));
        await waitFor(() => expect(calls.find((c) => c.url === '/api/photos/faces/detections/move')?.body).toEqual({ detection_ids: [2, 3], performer_id: 9 }));
    });
});
