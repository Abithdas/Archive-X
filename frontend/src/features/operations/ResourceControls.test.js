import { jsx as _jsx } from "react/jsx-runtime";
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { ResourceControls } from './ResourceControls';
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(() => 'id'), { error: vi.fn(), success: vi.fn(), loading: vi.fn(() => 'p'), dismiss: vi.fn() }), Toaster: () => null }));
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });
const json = (b, code = 200) => new Response(JSON.stringify(b), { status: code });
const AUTO = { control: { mode: 'automatic', manual_limits: { metadata: 4 }, playback_gate: true } };
const setup = (resources = AUTO, reply = () => json({ ok: true, resources: { control: { mode_label: 'Balanced' } } })) => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => { calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined }); return reply(); }));
    const qc = new QueryClient();
    const ui = (r) => _jsx(QueryClientProvider, { client: qc, children: _jsx(ResourceControls, { resources: r }) });
    const view = render(ui(resources));
    return { calls, rerender: (r) => view.rerender(ui(r)) };
};
const posts = (c) => c.filter((x) => x.method === 'POST');
const mode = () => screen.getByLabelText('Resource mode');
describe('Resource controls', () => {
    it('shows the current mode and no manual limits when not in manual mode', () => {
        setup();
        expect(mode().value).toBe('automatic');
        expect(screen.queryByRole('region', { name: 'Manual resource limits' })).toBeNull();
        expect(screen.getByRole('button', { name: 'Pause background controller' })).toBeInTheDocument();
    });
    it('choosing a policy applies it straight away', async () => {
        const { calls } = setup();
        fireEvent.change(mode(), { target: { value: 'balanced' } });
        await waitFor(() => expect(posts(calls)).toHaveLength(1));
        expect(posts(calls)[0]).toMatchObject({ url: '/api/operations/resources', body: { mode: 'balanced', expires_seconds: 0, label: 'Operator selected Balanced' } });
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Resource policy: Balanced'));
    });
    it('choosing Manual only opens a draft: nothing is sent until the limits are saved', () => {
        const { calls } = setup();
        fireEvent.change(mode(), { target: { value: 'manual' } });
        expect(posts(calls)).toHaveLength(0);
        const region = screen.getByRole('region', { name: 'Manual resource limits' });
        expect(region).toHaveTextContent('Manual mode is not active yet.');
        expect(mode().value).toBe('manual');
    });
    it('starts the draft from the saved limits and the defaults', () => {
        setup();
        fireEvent.change(mode(), { target: { value: 'manual' } });
        expect(screen.getByLabelText('Metadata')).toHaveValue(4); // saved
        expect(screen.getByLabelText('Conversion')).toHaveValue(2); // default
        expect(screen.getByLabelText(/Pause transfer-heavy background work/)).toBeChecked();
    });
    it('saves the limits, the gate and the expiry as Manual mode', async () => {
        const { calls } = setup();
        fireEvent.change(mode(), { target: { value: 'manual' } });
        fireEvent.change(screen.getByLabelText('Metadata'), { target: { value: '6' } });
        fireEvent.change(screen.getByLabelText('Upload'), { target: { value: '99' } });
        fireEvent.click(screen.getByLabelText(/Pause transfer-heavy background work/));
        fireEvent.change(screen.getByLabelText('Keep these limits for'), { target: { value: '1800' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save & use Manual mode' }));
        await waitFor(() => expect(posts(calls)).toHaveLength(1));
        expect(posts(calls)[0].body).toEqual({ mode: 'manual', limits: { metadata: 6, thumbnail: 1, preview: 1, conversion: 2, upload: 8 }, playback_gate: false, expires_seconds: 1800, label: 'Manual resource limits' });
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Manual limits saved and active for 30 minutes.'));
    });
    it('live updates do not overwrite a draft in progress', () => {
        const { rerender } = setup();
        fireEvent.change(mode(), { target: { value: 'manual' } });
        fireEvent.change(screen.getByLabelText('Metadata'), { target: { value: '7' } });
        rerender({ control: { mode: 'automatic', manual_limits: { metadata: 2 }, playback_gate: false } }); // the next poll
        expect(screen.getByLabelText('Metadata')).toHaveValue(7);
        expect(mode().value).toBe('manual');
    });
    it('keeps the edits and shows the server reason when saving fails', async () => {
        setup(AUTO, () => json({ detail: 'Controller unavailable' }, 503));
        fireEvent.change(mode(), { target: { value: 'manual' } });
        fireEvent.change(screen.getByLabelText('Metadata'), { target: { value: '5' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save & use Manual mode' }));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Controller unavailable'));
        expect(screen.getByLabelText('Metadata')).toHaveValue(5);
        expect(screen.getByRole('region', { name: 'Manual resource limits' })).toBeInTheDocument();
    });
    it('Cancel changes drops the draft and goes back to the server\'s values', () => {
        setup();
        fireEvent.change(mode(), { target: { value: 'manual' } });
        fireEvent.change(screen.getByLabelText('Metadata'), { target: { value: '7' } });
        fireEvent.click(screen.getByRole('button', { name: 'Cancel changes' }));
        expect(screen.queryByRole('region', { name: 'Manual resource limits' })).toBeNull();
        expect(mode().value).toBe('automatic');
    });
    it('clamps a typed limit to 1–8 when leaving the field', () => {
        setup();
        fireEvent.change(mode(), { target: { value: 'manual' } });
        const f = screen.getByLabelText('Preview');
        fireEvent.change(f, { target: { value: '40' } });
        fireEvent.blur(f);
        expect(f).toHaveValue(8);
        fireEvent.change(f, { target: { value: '0' } });
        fireEvent.blur(f);
        expect(f).toHaveValue(1);
    });
    it('Return to Automatic sends the automatic policy', async () => {
        const { calls } = setup({ control: { mode: 'manual', manual_limits: {} } });
        fireEvent.click(screen.getByRole('button', { name: 'Return to Automatic' }));
        await waitFor(() => expect(posts(calls)[0].body).toMatchObject({ mode: 'automatic', label: 'Operator selected Automatic' }));
    });
    it('pauses and resumes the background controller', async () => {
        const { calls } = setup();
        fireEvent.click(screen.getByRole('button', { name: 'Pause background controller' }));
        await waitFor(() => expect(posts(calls)[0].body).toEqual({ background_paused: true, label: 'Background controller paused by operator' }));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Background controller paused. Running work will stop at safe checkpoints.'));
    });
    it('offers to resume when it is paused', async () => {
        const { calls } = setup({ ...AUTO, background_paused: true });
        fireEvent.click(screen.getByRole('button', { name: 'Resume background controller' }));
        await waitFor(() => expect(posts(calls)[0].body).toMatchObject({ background_paused: false }));
    });
    it('says when a policy change is refused', async () => {
        setup(AUTO, () => json({ detail: 'Not allowed while a restore is staged' }, 409));
        fireEvent.change(mode(), { target: { value: 'playback' } });
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Not allowed while a restore is staged'));
    });
});
