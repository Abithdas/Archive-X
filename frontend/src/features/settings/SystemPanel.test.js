import { jsx as _jsx } from "react/jsx-runtime";
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { withRouter } from '@/test/withRouter';
import { SystemPanel } from './SystemPanel';
const json = (b, code = 200) => new Response(JSON.stringify(b), { status: code });
const HW = { ok: true, cpu: { name: 'x86_64', physical_cores: 4, logical_threads: 4 }, ram: { total_gb: 15.71, available_gb: 15.01 }, storage: { free_gb: 13.26 }, gpus: [], verified_encoders: {}, conversion_backend: 'libx264' };
const mount = (extra = () => undefined) => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => {
        calls.push({ url, method: init?.method ?? 'GET' });
        return extra(url, init) ?? (url === '/api/system/hardware' ? json(HW) : json({ ok: true }));
    }));
    withRouter(_jsx(SystemPanel, {}));
    return calls;
};
afterEach(() => vi.unstubAllGlobals());
describe('System & network', () => {
    it('shows browser capability rows with the classic wording', async () => {
        mount();
        const dl = await screen.findByLabelText('Browser capabilities');
        for (const label of ['Browser', 'OS', 'MP4 / H.264', 'WebM', 'HLS', 'Fetch', 'Abort', 'Pointer input'])
            expect(within(dl).getByText(label)).toBeInTheDocument();
        expect(screen.getByText('The player always uses the direct stream (no conversion).')).toBeInTheDocument();
    });
    it('shows the detected hardware', async () => {
        mount();
        const hw = await screen.findByLabelText('Hardware');
        expect(within(hw).getByText('x86_64 · 4 cores / 4 threads')).toBeInTheDocument();
        expect(within(hw).getByText('15.71 GB · 15.01 GB free')).toBeInTheDocument();
        expect(within(hw).getByText('No dedicated GPU detected')).toBeInTheDocument();
    });
    it('says so when hardware detection is unavailable', async () => {
        mount((u) => (u === '/api/system/hardware' ? json({ ok: false }) : undefined));
        expect(await screen.findByText('Hardware detection unavailable.')).toBeInTheDocument();
    });
    it('Refresh hardware re-reads the profile', async () => {
        const calls = mount();
        await screen.findByLabelText('Hardware');
        fireEvent.click(screen.getByRole('button', { name: 'Refresh hardware' }));
        await waitFor(() => expect(calls.filter((c) => c.url === '/api/system/hardware')).toHaveLength(2));
    });
    it('runs the speed test on demand and shows the result', async () => {
        const calls = mount((u) => (u === '/api/system/internet-test' ? json({ ok: true, download_mbps: 94.2, upload_mbps: 11, latency_ms: 23, quality: 'good', network_mode: 'balanced', background_capacity: 3 }) : undefined));
        await screen.findByLabelText('Hardware');
        expect(calls.some((c) => c.url === '/api/system/internet-test')).toBe(false);
        fireEvent.click(screen.getByRole('button', { name: 'Test internet speed' }));
        const out = await screen.findByLabelText('Internet speed');
        expect(within(out).getByText('94.2 Mbps')).toBeInTheDocument();
        expect(within(out).getByText('23 ms')).toBeInTheDocument();
        expect(screen.getByText('Test complete')).toBeInTheDocument();
        expect(screen.getByText('Network mode: balanced · Background capacity: 3')).toBeInTheDocument();
        expect(calls.find((c) => c.url === '/api/system/internet-test').method).toBe('POST');
    });
    it('shows the server error when the speed test fails', async () => {
        mount((u) => (u === '/api/system/internet-test' ? json({ detail: 'No route to host' }, 500) : undefined));
        await screen.findByLabelText('Hardware');
        fireEvent.click(screen.getByRole('button', { name: 'Test internet speed' }));
        expect(await screen.findByText('Internet test failed')).toBeInTheDocument();
        expect(screen.getByRole('alert')).toHaveTextContent('No route to host');
    });
});
