import { jsx as _jsx } from "react/jsx-runtime";
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { withRouter } from '@/test/withRouter';
import { ModelCatalog } from './ModelCatalog';
import { anyBusy, modelsSummary, sizeText, stateTone, testLine } from './ai';
afterEach(() => vi.unstubAllGlobals());
const json = (b, code = 200) => new Response(JSON.stringify(b), { status: code });
const M = (id, o = {}) => ({ id, name: `Model ${id}`, detail: 'what it does', size_mb: 39, licence: 'MIT', state: 'ready', installable: false, powers: ['People'], hint: '', ...o });
const LIST = { ok: true, ready: 2, total: 3, models: [M('faces'), M('looks', { state: 'not_downloaded', installable: true, size_mb: 155, hint: 'Downloads about 155 MB once.' }), M('content', { state: 'needs_package', hint: 'Needs the nudenet package.' })] };
const mount = (over = () => undefined) => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => { calls.push({ url, method: init?.method ?? 'GET' }); return over(url) ?? (url === '/api/ai/models' ? json(LIST) : json({ ok: true, ms: 41, detail: 'Loaded and ran.' })); }));
    withRouter(_jsx(ModelCatalog, {}));
    return calls;
};
describe('model catalog helpers', () => {
    it('words states, sizes and test results', () => {
        expect(sizeText(39)).toBe('39 MB');
        expect(sizeText(2500)).toBe('2.5 GB');
        expect(modelsSummary({ ready: 2, total: 5 })).toBe('2 of 5 local models ready');
        expect(modelsSummary(undefined)).toBe('Checking local models…');
        expect(stateTone('ready')).toBe('good');
        expect(stateTone('downloading')).toBe('busy');
        expect(stateTone('needs_package')).toBe('warn');
        expect(testLine({ ok: true, ms: 41, detail: 'Loaded.' })).toBe('Working · 41 ms. Loaded.');
        expect(testLine({ ok: false, detail: 'Not downloaded.' })).toBe('Not working: Not downloaded.');
        expect(anyBusy({ ...LIST, models: [M('a', { busy: true })] })).toBe(true);
        expect(anyBusy(LIST)).toBe(false);
    });
});
describe('Local AI models', () => {
    it('lists every model with its state, what it powers, size and a hint when it is not ready', async () => {
        mount();
        expect(await screen.findByText('2 of 3 local models ready')).toBeInTheDocument();
        const looks = screen.getByRole('article', { name: 'Model looks' });
        expect(within(looks).getByText('Not downloaded')).toBeInTheDocument();
        expect(within(looks).getByText('Downloads about 155 MB once.')).toBeInTheDocument();
        expect(within(screen.getByRole('article', { name: 'Model faces' })).getByText('Ready')).toBeInTheDocument();
        expect(within(screen.getByRole('article', { name: 'Model content' })).getByText('Needs install')).toBeInTheDocument();
    });
    it('Test runs the self-test and says whether it works, with the time', async () => {
        const calls = mount();
        fireEvent.click(within(await screen.findByRole('article', { name: 'Model faces' })).getByRole('button', { name: 'Test' }));
        expect(await screen.findByText('Working · 41 ms. Loaded and ran.')).toBeInTheDocument();
        expect(calls.some((c) => c.url === '/api/ai/models/faces/test' && c.method === 'POST')).toBe(true);
    });
    it('a failing test says why', async () => {
        mount((u) => (u.endsWith('/test') ? json({ ok: false, detail: 'The look model is not downloaded yet.' }) : undefined));
        fireEvent.click(within(await screen.findByRole('article', { name: 'Model looks' })).getByRole('button', { name: 'Test' }));
        expect(await screen.findByText('Not working: The look model is not downloaded yet.')).toBeInTheDocument();
    });
    it('only models that can be downloaded from here offer Download, and it starts the download', async () => {
        const calls = mount();
        expect(within(await screen.findByRole('article', { name: 'Model faces' })).queryByRole('button', { name: 'Download' })).toBeNull();
        fireEvent.click(within(screen.getByRole('article', { name: 'Model looks' })).getByRole('button', { name: 'Download' }));
        await waitFor(() => expect(calls.some((c) => c.url === '/api/ai/models/looks/install' && c.method === 'POST')).toBe(true));
    });
    it('offers Retry when the list cannot be loaded', async () => {
        mount((u) => (u === '/api/ai/models' ? json({ detail: 'x' }, 500) : undefined));
        expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument();
    });
});
