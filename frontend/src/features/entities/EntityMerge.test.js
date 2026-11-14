import { jsx as _jsx } from "react/jsx-runtime";
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { withRouter } from '@/test/withRouter';
import { EntityDirectory } from './EntityDirectory';
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(() => 'id'), { error: vi.fn(), success: vi.fn(), loading: vi.fn(() => 'p'), dismiss: vi.fn() }), Toaster: () => null }));
beforeAll(() => {
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });
const e = (id, name, video_count) => ({ id, name, video_count, gender: 'Female', image: '' });
const DATA = [e(1, 'Ava Stone', 9), e(2, 'Ava Stonee', 3), e(3, 'Mia Rivera', 5), e(4, 'Zed', 1)];
const mount = (kind = 'performers', merge = () => new Response('{"ok":true,"merged_count":2,"name":"Ava Stone"}', { status: 200 })) => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
        calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
        if (url === '/api/performers/merge-batch')
            return merge();
        return new Response(JSON.stringify(DATA), { status: 200 });
    }));
    withRouter(_jsx(EntityDirectory, { kind: kind }));
    return calls;
};
const merges = (c) => c.filter((x) => x.url === '/api/performers/merge-batch');
const card = (name) => screen.findByRole('button', { name });
describe('Performers: merge duplicates', () => {
    it('is only offered for performers', async () => {
        mount('studios');
        await screen.findByRole('article', { name: 'Zed' });
        expect(screen.queryByRole('button', { name: 'Merge duplicates' })).toBeNull();
        expect(screen.queryByLabelText('Sort performers')).toBeNull();
    });
    it('starts with a hint; the first card is the keeper, the next ones are duplicates', async () => {
        mount();
        fireEvent.click(await screen.findByRole('button', { name: 'Merge duplicates' }));
        expect(toast).toHaveBeenCalledWith('Select one performer to keep, then select every duplicate to merge into it.');
        expect(screen.getByRole('button', { name: 'Cancel merge' })).toBeInTheDocument();
        expect(screen.getByRole('status')).toHaveTextContent('Select the canonical performer to keep first.');
        expect(screen.getByRole('button', { name: 'Merge selected (0)' })).toBeDisabled();
        fireEvent.click(await card('Choose Ava Stone'));
        expect(toast).toHaveBeenCalledWith('Keep “Ava Stone”. Now select all duplicates to merge into it.');
        fireEvent.click(await card('Choose Ava Stonee'));
        expect(screen.getByRole('status')).toHaveTextContent('Keep: Ava Stone · 1 duplicate selected.');
        expect(screen.getByRole('button', { name: 'Merge selected (1)' })).toBeEnabled();
        expect(screen.getByRole('button', { name: 'Keeper: Ava Stone' })).toHaveAttribute('aria-pressed', 'true');
        expect(screen.getByRole('button', { name: 'Duplicate: Ava Stonee' })).toHaveAttribute('aria-pressed', 'true');
    });
    it('cards do not open the profile while merging', async () => {
        mount();
        await screen.findByRole('article', { name: 'Zed' });
        expect(screen.getAllByRole('link').length).toBeGreaterThan(0);
        fireEvent.click(screen.getByRole('button', { name: 'Merge duplicates' }));
        expect(screen.queryAllByRole('link')).toHaveLength(0);
    });
    it('asks first, naming exactly what will be merged, and sends nothing on cancel', async () => {
        const calls = mount();
        fireEvent.click(await screen.findByRole('button', { name: 'Merge duplicates' }));
        fireEvent.click(await card('Choose Ava Stone'));
        fireEvent.click(await card('Choose Ava Stonee'));
        fireEvent.click(await card('Choose Zed'));
        fireEvent.click(screen.getByRole('button', { name: 'Merge selected (2)' }));
        const dlg = await screen.findByRole('dialog', { name: 'Merge duplicate performers' });
        expect(dlg).toHaveTextContent('Merge 2 duplicate performer records into “Ava Stone”?');
        expect(dlg).toHaveTextContent('• Ava Stonee (ID 2)');
        expect(dlg).toHaveTextContent('• Zed (ID 4)');
        expect(dlg).toHaveTextContent('This cannot be undone.');
        expect(merges(calls)).toHaveLength(0);
        fireEvent.click(within(dlg).getByRole('button', { name: 'Cancel' }));
        expect(merges(calls)).toHaveLength(0);
        expect(screen.getByRole('button', { name: 'Cancel merge' })).toBeInTheDocument(); // still in merge mode
    });
    it('merges in one request, leaves merge mode, and reports the result', async () => {
        const calls = mount();
        fireEvent.click(await screen.findByRole('button', { name: 'Merge duplicates' }));
        fireEvent.click(await card('Choose Ava Stone'));
        fireEvent.click(await card('Choose Ava Stonee'));
        fireEvent.click(await card('Choose Mia Rivera'));
        fireEvent.click(screen.getByRole('button', { name: 'Merge selected (2)' }));
        fireEvent.click(within(await screen.findByRole('dialog', { name: 'Merge duplicate performers' })).getByRole('button', { name: 'Merge 2 selected' }));
        await waitFor(() => expect(merges(calls)).toHaveLength(1));
        expect(merges(calls)[0].body).toEqual({ keep_id: 1, merge_ids: [2, 3] });
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Merged 2 duplicates into “Ava Stone”. Video associations and provider IDs were preserved.'));
        expect(await screen.findByRole('button', { name: 'Merge duplicates' })).toBeInTheDocument();
    });
    it('stays in merge mode with the server reason when the merge fails', async () => {
        mount('performers', () => new Response('{"detail":"Conflicting provider IDs"}', { status: 409 }));
        fireEvent.click(await screen.findByRole('button', { name: 'Merge duplicates' }));
        fireEvent.click(await card('Choose Ava Stone'));
        fireEvent.click(await card('Choose Ava Stonee'));
        fireEvent.click(screen.getByRole('button', { name: 'Merge selected (1)' }));
        fireEvent.click(within(await screen.findByRole('dialog', { name: 'Merge duplicate performers' })).getByRole('button', { name: 'Merge 1 selected' }));
        await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Conflicting provider IDs'));
        expect(screen.getByRole('button', { name: 'Cancel merge' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Merge selected (1)' })).toBeEnabled(); // can try again
    });
    it('Cancel merge forgets the choices', async () => {
        mount();
        fireEvent.click(await screen.findByRole('button', { name: 'Merge duplicates' }));
        fireEvent.click(await card('Choose Ava Stone'));
        fireEvent.click(screen.getByRole('button', { name: 'Cancel merge' }));
        fireEvent.click(screen.getByRole('button', { name: 'Merge duplicates' }));
        expect(screen.getByRole('status')).toHaveTextContent('Select the canonical performer to keep first.');
    });
});
describe('Performers: sort', () => {
    const order = () => screen.getAllByRole('article').map((a) => a.getAttribute('aria-label'));
    it('defaults to most videos first, and can sort by name either way', async () => {
        mount();
        await screen.findByRole('article', { name: 'Zed' });
        expect(order()).toEqual(['Ava Stone', 'Mia Rivera', 'Ava Stonee', 'Zed']);
        fireEvent.change(screen.getByLabelText('Sort performers'), { target: { value: 'name_desc' } });
        expect(order()[0]).toBe('Zed');
        fireEvent.change(screen.getByLabelText('Sort performers'), { target: { value: 'name_asc' } });
        expect(order()[0]).toMatch(/^Ava/);
        expect(order().at(-1)).toBe('Zed');
    });
});
