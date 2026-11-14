import { jsxs as _jsxs, jsx as _jsx } from "react/jsx-runtime";
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { useShuffle } from './useShuffle';
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(() => 'id'), { error: vi.fn(), success: vi.fn(), loading: vi.fn(() => 'p'), dismiss: vi.fn() }), Toaster: () => null }));
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });
const VIDEOS = [1, 2, 3, 4, 5].map((id) => ({ _cid: '-1', id, name: `V${id}` }));
const json = (b, code = 200) => new Response(JSON.stringify(b), { status: code });
function Harness({ list = VIDEOS, player = 'mpc', empty = 'Nothing here.' }) {
    const s = useShuffle();
    return _jsxs("button", { disabled: false, onClick: () => void s.run(player, () => list, empty), children: ["go", s.busy ? ' (busy)' : ''] });
}
const stub = (reply) => {
    const calls = [];
    vi.stubGlobal('fetch', vi.fn(async (url, init) => { calls.push({ url, body: JSON.parse(String(init?.body ?? '{}')) }); return reply(); }));
    return calls;
};
describe('useShuffle (launching on this PC)', () => {
    it('sends every video once, in a shuffled order, to the chosen player', async () => {
        const calls = stub(() => json({ ok: true, player: 'MPC-HC', count: 5 }));
        render(_jsx(Harness, {}));
        fireEvent.click(screen.getByRole('button'));
        await waitFor(() => expect(calls).toHaveLength(1));
        expect(calls[0].url).toBe('/api/play/external');
        expect(calls[0].body).toMatchObject({ player: 'mpc', random: true });
        const ids = calls[0].body.playlist.map((i) => i.message_id);
        expect([...ids].sort()).toEqual([1, 2, 3, 4, 5]);
        expect(toast).toHaveBeenCalledWith('Starting random MPC-HC playlist: 5 videos…');
        await waitFor(() => expect(toast).toHaveBeenCalledWith('MPC-HC shuffled playlist started: 5 videos', expect.anything()));
    });
    it('says so, and sends nothing, when there is nothing to play', async () => {
        const calls = stub(() => json({ ok: true }));
        render(_jsx(Harness, { list: [], empty: "This performer has no videos in the local catalogue yet." }));
        fireEvent.click(screen.getByRole('button'));
        await waitFor(() => expect(toast).toHaveBeenCalledWith('This performer has no videos in the local catalogue yet.'));
        expect(calls).toHaveLength(0);
    });
    it('shows the server reason when the player cannot start', async () => {
        stub(() => json({ detail: 'MPC-HC was not found on this PC' }, 400));
        render(_jsx(Harness, {}));
        fireEvent.click(screen.getByRole('button'));
        await waitFor(() => expect(toast.error).toHaveBeenCalledWith('MPC-HC shuffle could not start: MPC-HC was not found on this PC'));
    });
    it('treats ok:false as a failure too', async () => {
        stub(() => json({ ok: false, detail: 'No window' }));
        render(_jsx(Harness, { player: "vlc" }));
        fireEvent.click(screen.getByRole('button'));
        await waitFor(() => expect(toast.error).toHaveBeenCalledWith('VLC shuffle could not start: No window'));
    });
    it('ignores a second click while one shuffle is starting', async () => {
        let release = () => { };
        const calls = stub(() => new Promise((r) => { release = r; }));
        render(_jsx(Harness, {}));
        fireEvent.click(screen.getByRole('button'));
        await waitFor(() => expect(screen.getByRole('button')).toHaveTextContent('(busy)'));
        fireEvent.click(screen.getByRole('button'));
        expect(calls).toHaveLength(1);
        await act(async () => { release(json({ ok: true, count: 5 })); });
        await waitFor(() => expect(screen.getByRole('button')).not.toHaveTextContent('(busy)'));
    });
    it('skips videos that have no channel or id', async () => {
        const calls = stub(() => json({ ok: true, count: 1 }));
        render(_jsx(Harness, { list: [{ _cid: '-1', id: 9 }, { _cid: undefined, id: 10 }] }));
        fireEvent.click(screen.getByRole('button'));
        await waitFor(() => expect(calls).toHaveLength(1));
        expect(calls[0].body.playlist.length).toBe(1);
    });
});
