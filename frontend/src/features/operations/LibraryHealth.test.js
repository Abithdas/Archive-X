import { jsx as _jsx } from "react/jsx-runtime";
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { withRouter } from '@/test/withRouter';
import { LibraryHealth } from './LibraryHealth';
const toast = vi.hoisted(() => Object.assign(vi.fn(), { error: vi.fn() }));
vi.mock('sonner', () => ({ toast, Toaster: () => null }));
beforeEach(() => localStorage.clear());
afterEach(() => { vi.unstubAllGlobals(); toast.mockClear(); });
const json = (b, code = 200) => new Response(JSON.stringify(b), { status: code });
const REPORT = { checked_at: '2026-01-01T00:00:00+00:00', summary: { active_media: 72, missing_snapshot_cache: 72, finding_count: 1 }, findings: [{ code: 'SNAPSHOT_CACHE_MISSING', severity: 'warning', count: 72, message: 'Active media items missing an Archive X representative snapshot.' }] };
const mount = (over = () => undefined, report = REPORT) => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
        calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined });
        const o = over(url, init);
        if (o)
            return o;
        if (url === '/api/operations/library-summary')
            return json({ videos: 72, performers: 12, studios: 6 });
        if (url.startsWith('/api/operations/library-health?'))
            return json(report);
        if (url === '/api/operations/library-health/repair-artwork')
            return json({ ok: true, repaired: 3, skipped: 1, failed: 0 });
        return json({});
    }));
    withRouter(_jsx(LibraryHealth, {}));
    return calls;
};
describe('LibraryHealth', () => {
    it('shows the library totals and an unchecked state', async () => {
        mount();
        expect(await screen.findByText('72')).toBeInTheDocument();
        expect(screen.getByText('12')).toBeInTheDocument();
        expect(screen.getByText('Not checked')).toBeInTheDocument();
        expect(screen.getByText('Never')).toBeInTheDocument();
    });
    it('runs the full check, shows findings and the details, and remembers when', async () => {
        const calls = mount();
        fireEvent.click(await screen.findByRole('button', { name: /Run full check/ }));
        expect(await screen.findByText('1 finding')).toBeInTheDocument();
        expect(calls.some((c) => c.url === '/api/operations/library-health?limit=100&stale_job_hours=24')).toBe(true);
        expect(screen.getByText(/warning · SNAPSHOT_CACHE_MISSING: 72/)).toBeInTheDocument();
        expect(localStorage.getItem('ax-library-health-last-checked')).toBe(REPORT.checked_at);
    });
    it('says healthy when nothing is found', async () => {
        mount(() => undefined, { checked_at: 'x', summary: { finding_count: 0 }, findings: [] });
        fireEvent.click(await screen.findByRole('button', { name: /Run full check/ }));
        expect(await screen.findByText('Healthy')).toBeInTheDocument();
        expect(screen.getByText(/No issues were found/)).toBeInTheDocument();
    });
    it('shows why a check failed', async () => {
        mount((u) => (u.startsWith('/api/operations/library-health?') ? json({ detail: 'Database busy' }, 503) : undefined));
        fireEvent.click(await screen.findByRole('button', { name: /Run full check/ }));
        expect(await screen.findByText('Check failed')).toBeInTheDocument();
        expect(screen.getByText('Library health check failed: Database busy')).toBeInTheDocument();
    });
    it('repairs the artwork cache (local only), reports it, then re-checks', async () => {
        const calls = mount();
        fireEvent.click(await screen.findByRole('button', { name: 'Repair artwork cache' }));
        await waitFor(() => expect(calls.find((c) => c.url === '/api/operations/library-health/repair-artwork')).toMatchObject({ method: 'POST', body: { limit: 100 } }));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('Artwork cache: 3 repaired, 1 unavailable locally, 0 errors.'));
        await waitFor(() => expect(calls.some((c) => c.url.startsWith('/api/operations/library-health?'))).toBe(true));
    });
});
