import { jsx as _jsx } from "react/jsx-runtime";
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { JobControls } from './JobControls';
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(() => 'id'), { error: vi.fn(), success: vi.fn(), loading: vi.fn(() => 'p'), dismiss: vi.fn() }), Toaster: () => null }));
beforeAll(() => {
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
    HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });
const json = (b, code = 200) => new Response(JSON.stringify(b), { status: code });
const mount = (job, reply = () => json({ ok: true })) => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => { const method = init?.method ?? 'GET'; calls.push({ url, method }); return reply(url, method); }));
    render(_jsx(QueryClientProvider, { client: new QueryClient(), children: _jsx(JobControls, { j: { id: 'J1', name: 'Transfer', type: 'TELEGRAM_TRANSFER', ...job } }) }));
    return calls;
};
const posts = (c) => c.filter((x) => x.method === 'POST');
const btn = (n) => screen.getByRole('button', { name: n });
describe('job controls by status', () => {
    it('a running job can be paused, stopped or refreshed', () => {
        mount({ status: 'RUNNING' });
        expect(btn('Pause')).toBeInTheDocument();
        expect(btn('Stop')).toBeInTheDocument();
        expect(btn('Refresh')).toBeInTheDocument();
    });
    it('a paused job offers Resume instead of Pause', () => {
        mount({ status: 'PAUSED' });
        expect(btn('Resume')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull();
    });
    it('an interrupted job offers recovery, and a recovering one a retry', () => {
        mount({ status: 'INTERRUPTED' });
        expect(btn('Recover & Resume')).toBeInTheDocument();
    });
    it('a recovering job offers to retry the recovery', () => {
        mount({ status: 'RECOVERING' });
        expect(btn('Retry recovery')).toBeInTheDocument();
    });
    it('a finished job shows no controls', () => {
        mount({ status: 'COMPLETED' });
        expect(screen.queryByRole('group')).toBeNull();
    });
    it('a playback session can only be stopped or refreshed', () => {
        mount({ type: 'PLAYBACK', status: 'RUNNING' });
        expect(btn('Stop playback')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull();
    });
});
describe('job actions', () => {
    it('pause, resume and recover post to the right endpoint and say what happened', async () => {
        const c1 = mount({ status: 'RUNNING' }, () => json({ ok: true, status: 'PAUSED' }));
        fireEvent.click(btn('Pause'));
        await waitFor(() => expect(posts(c1)).toEqual([{ url: '/api/jobs/J1/pause', method: 'POST' }]));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Job paused.'));
    });
    it('a pause that is not instant says the safe operation will finish first', async () => {
        mount({ status: 'RUNNING' }, () => json({ ok: true, status: 'PAUSING' }));
        fireEvent.click(btn('Pause'));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Pause requested; the current safe operation will finish first.'));
    });
    it('recover posts to /recover', async () => {
        const c = mount({ status: 'INTERRUPTED' });
        fireEvent.click(btn('Recover & Resume'));
        await waitFor(() => expect(posts(c)).toEqual([{ url: '/api/jobs/J1/recover', method: 'POST' }]));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Job recovered and queued from its saved state.'));
    });
    it('resume posts to /resume', async () => {
        const c = mount({ status: 'PAUSED' }, () => json({ ok: true, status: 'RUNNING' }));
        fireEvent.click(btn('Resume'));
        await waitFor(() => expect(posts(c)).toEqual([{ url: '/api/jobs/J1/resume', method: 'POST' }]));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Job running.'));
    });
    it('shows the server reason when an action fails', async () => {
        mount({ status: 'RUNNING' }, () => json({ detail: 'Job is locked by another worker' }, 409));
        fireEvent.click(btn('Pause'));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Job is locked by another worker'));
    });
    it('falls back to a plain message when the server says nothing useful', async () => {
        mount({ status: 'RUNNING' }, () => json({ ok: false }));
        fireEvent.click(btn('Pause'));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Could not pause job'));
    });
    it('sends only one request at a time for the same job', async () => {
        let release = () => { };
        const c = mount({ status: 'RUNNING' }, () => new Promise((r) => { release = r; }));
        fireEvent.click(btn('Pause'));
        await waitFor(() => expect(btn('Pause')).toBeDisabled());
        fireEvent.click(btn('Pause'));
        expect(posts(c)).toHaveLength(1);
        await act(async () => { release(json({ ok: true, status: 'PAUSED' })); });
        await waitFor(() => expect(btn('Pause')).toBeEnabled());
    });
    it('Refresh only reads the job', async () => {
        const c = mount({ status: 'RUNNING' });
        fireEvent.click(btn('Refresh'));
        await waitFor(() => expect(c.some((x) => x.url === '/api/jobs/J1' && x.method === 'GET')).toBe(true));
        expect(posts(c)).toHaveLength(0);
    });
});
describe('stopping a job', () => {
    it('reads the job again, asks first, and sends nothing on cancel', async () => {
        const c = mount({ status: 'RUNNING' }, (_u, m) => (m === 'GET' ? json({ status: 'RUNNING' }) : json({ ok: true, status: 'CANCELLED' })));
        fireEvent.click(btn('Stop'));
        const dlg = await screen.findByRole('dialog', { name: 'Stop job' });
        expect(dlg).toHaveTextContent('Verified completed items remain saved.');
        expect(posts(c)).toHaveLength(0);
        fireEvent.click(within(dlg).getByRole('button', { name: 'Cancel' }));
        expect(posts(c)).toHaveLength(0);
    });
    it('stops after confirmation', async () => {
        const c = mount({ status: 'RUNNING' }, (_u, m) => (m === 'GET' ? json({ status: 'RUNNING' }) : json({ ok: true, status: 'CANCELLED' })));
        fireEvent.click(btn('Stop'));
        fireEvent.click(within(await screen.findByRole('dialog', { name: 'Stop job' })).getByRole('button', { name: 'Stop job' }));
        await waitFor(() => expect(posts(c)).toEqual([{ url: '/api/jobs/J1/cancel', method: 'POST' }]));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Job stopped.'));
    });
    it('does not stop a job that already finished, and says so', async () => {
        const c = mount({ status: 'RUNNING' }, () => json({ status: 'COMPLETED_WITH_ERRORS' }));
        fireEvent.click(btn('Stop'));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Job is already completed with errors.'));
        expect(screen.queryByRole('dialog')).toBeNull();
        expect(posts(c)).toHaveLength(0);
    });
    it('uses the interrupted wording for an interrupted job', async () => {
        mount({ status: 'INTERRUPTED' }, () => json({ status: 'INTERRUPTED' }));
        fireEvent.click(btn('Stop'));
        expect(await screen.findByRole('dialog', { name: 'Stop interrupted job' })).toHaveTextContent('discard its pending recovery');
    });
    it('says so when the job cannot be read', async () => {
        mount({ status: 'RUNNING' }, () => json({ detail: 'Job not found' }, 404));
        fireEvent.click(btn('Stop'));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Job not found'));
        expect(screen.queryByRole('dialog')).toBeNull();
    });
});
describe('stopping playback', () => {
    it('asks first, then stops and reports the cleanup', async () => {
        const c = mount({ type: 'PLAYBACK', status: 'RUNNING' }, () => json({ stopping: true }));
        fireEvent.click(btn('Stop playback'));
        const dlg = await screen.findByRole('dialog', { name: 'Stop playback' });
        expect(posts(c)).toHaveLength(0);
        fireEvent.click(within(dlg).getByRole('button', { name: 'Stop playback' }));
        await waitFor(() => expect(posts(c)).toEqual([{ url: '/api/play/stop', method: 'POST' }]));
        expect(toast).toHaveBeenCalledWith('Stopping playback…');
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Playback stopped; resource cleanup is finishing.'));
    });
});
